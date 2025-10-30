from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pytz
from datetime import datetime
import logging
import asyncio

logger = logging.getLogger(__name__)

app = FastAPI(title="Maya Autism Facts Assistant", version="0.1.0")

# Global state for RAG system initialization (lightweight tracking)
_rag_system = None
_initialization_task = None
_initialization_complete = False

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

class Query(BaseModel):
    question: str

class Answer(BaseModel):
    answer: str
    timestamp: str
    disclaimer: str = "Information only — not a diagnosis or treatment plan. For medical concerns contact your GP or NHS 111; emergencies: call 999. For legal advice, contact a qualified professional."

@app.get("/")
async def root():
    """Serve the main chat interface"""
    from fastapi.responses import FileResponse
    return FileResponse('static/index.html')

@app.get("/health")
async def health():
    """Simple health check - responds immediately for deployment"""
    return {"status": "ok", "service": "maya-autism-assistant"}

async def _initialize_rag_system():
    """Initialize RAG system in background (heavy imports happen here)"""
    global _rag_system, _initialization_complete
    
    try:
        logger.info("🚀 Starting RAG system initialization...")
        start_time = datetime.now()
        
        # Heavy imports happen here (ChromaDB, SentenceTransformers, etc.)
        from rag import answerer
        from rag.rag_system import get_rag_system, InitializationState
        
        logger.info("📦 Modules imported, creating RAG instance...")
        _rag_system = get_rag_system()
        
        # Initialize the system
        await _rag_system.initialize_async()
        
        # Only mark complete if initialization succeeded
        if _rag_system.init_state == InitializationState.READY:
            _initialization_complete = True
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"✅ RAG system fully initialized in {elapsed:.1f}s")
        else:
            logger.error(f"❌ RAG system initialization did not reach READY state: {_rag_system.init_state}")
            # Keep _initialization_complete = False so next request can retry
            _rag_system = None  # Clear failed instance
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize RAG system: {str(e)}")
        # Do NOT mark complete - allow retry on next request
        _rag_system = None  # Clear failed instance

@app.post("/chat", response_model=Answer)
async def chat(query: Query):
    global _rag_system, _initialization_task, _initialization_complete
    
    # Generate timestamp in Europe/London timezone
    london_tz = pytz.timezone('Europe/London')
    timestamp = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    # Check if RAG system needs initialization
    if not _initialization_complete:
        if _initialization_task is None or _initialization_task.done():
            # Check if previous task failed and log it
            if _initialization_task is not None and _initialization_task.done():
                try:
                    exc = _initialization_task.exception()
                    if exc:
                        logger.warning(f"Previous initialization failed: {exc}. Retrying...")
                except Exception:
                    pass  # Task was cancelled or hasn't finished
            
            # Start or restart initialization
            logger.info("⚡ Starting RAG system initialization...")
            try:
                _initialization_task = asyncio.create_task(_initialize_rag_system())
            except Exception as e:
                logger.error(f"❌ Failed to create initialization task: {str(e)}")
                from rag import answerer
                text = answerer.apply_guardrails(query.question) or (
                    "Sorry, the system cannot initialize. Please try again later."
                )
                return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
        
        # Wait for initialization to complete (with timeout)
        # Increased timeout to 120s for deployment environments which may be slower
        logger.info("⏳ Waiting for RAG system initialization to complete...")
        try:
            await asyncio.wait_for(_initialization_task, timeout=120.0)
        except asyncio.TimeoutError:
            logger.error("⏰ Initialization timeout after 120 seconds")
            # Reset task to allow retry
            _initialization_task = None
            from rag import answerer
            text = answerer.apply_guardrails(query.question) or (
                "The system is still loading. This can take up to 2 minutes on first use. "
                "Please wait 30 seconds and try your question again."
            )
            return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
        except Exception as e:
            logger.error(f"❌ Initialization failed with error: {str(e)}")
            from rag import answerer
            text = answerer.apply_guardrails(query.question) or (
                "Sorry, the system encountered an error during initialization. "
                "Please try asking your question again."
            )
            return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
    
    # RAG system is ready - use it to answer
    if _rag_system is not None:
        from rag.rag_system import InitializationState
        if _rag_system.init_state == InitializationState.READY:
            text = _rag_system.answer_question(query.question)
            return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
    
    # Fallback if system is not ready
    from rag import answerer
    logger.warning("⚠️ RAG system not ready, using fallback response")
    text = answerer.apply_guardrails(query.question) or (
        "The system is not fully initialized yet. Please try asking your question again in a moment."
    )
    return Answer(answer=text, timestamp=f"Last checked: {timestamp}")