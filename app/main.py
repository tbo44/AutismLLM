from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pytz
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="Maya Autism Facts Assistant", version="0.2.0")

# Global RAG system instance (pre-loaded at startup for faster responses)
_rag_system = None
_startup_complete = False

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

async def initialize_rag_background():
    """Background task to initialize RAG system without blocking server startup"""
    global _rag_system, _startup_complete
    
    logger.info("🚀 Maya background initialization starting...")
    start_time = datetime.now()
    
    try:
        # Run initialization in thread pool to avoid blocking event loop
        import asyncio
        from rag.rag_system import get_rag_system
        
        # Use asyncio.to_thread to run blocking operations without blocking the event loop
        _rag_system = await asyncio.to_thread(_initialize_rag_sync)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        _startup_complete = True
        logger.info(f"✅ Maya ready! Background initialization completed in {elapsed:.1f}s")
        
    except Exception as e:
        logger.error(f"❌ Background initialization failed: {str(e)}", exc_info=True)
        _startup_complete = False

def _initialize_rag_sync():
    """Synchronous RAG initialization - runs in thread pool"""
    from rag.rag_system import get_rag_system
    
    logger.info("📦 Loading RAG system...")
    rag_system = get_rag_system()
    rag_system.initialize()
    
    # Warm up the system with test queries
    logger.info("🔥 Warming up RAG components...")
    try:
        # Warm embedder (most critical for fast response)
        rag_system.vector_store.embedder.encode(["warmup"])
        logger.info("✅ SentenceTransformer warmed")
    except Exception as e:
        logger.warning(f"Embedder warmup failed (non-critical): {e}")
    
    try:
        # Warm ChromaDB with actual search
        rag_system.vector_store.search("warmup test", n_results=1)
        logger.info("✅ ChromaDB warmed")
    except Exception as e:
        logger.warning(f"ChromaDB warmup failed (non-critical): {e}")
    
    return rag_system

@app.on_event("startup")
async def startup_event():
    """Start background initialization without blocking server startup"""
    import asyncio
    logger.info("🚀 Maya server starting - RAG initialization running in background...")
    # Create task without awaiting - allows server to start immediately
    asyncio.create_task(initialize_rag_background())

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
    return FileResponse(
        'static/index.html',
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/health")
async def health():
    """Ultra-lightweight health check endpoint - responds immediately without dependencies"""
    return {"status": "ok"}

@app.get("/status")
async def status():
    """Extended status endpoint with RAG readiness info (for frontend)"""
    return {
        "status": "ok",
        "service": "maya-autism-assistant",
        "rag_ready": _startup_complete
    }

@app.get("/warmup")
async def warmup():
    """Warmup endpoint for keep-alive pings - exercises all critical components"""
    global _rag_system
    
    status = {
        "ok": True,
        "startup_complete": _startup_complete,
        "components": {}
    }
    
    # If RAG system not loaded, try to load it
    if _rag_system is None:
        try:
            _rag_system = _load_rag_system()
            status["components"]["rag_loaded"] = True
        except Exception as e:
            status["ok"] = False
            status["components"]["rag_loaded"] = False
            status["error"] = str(e)
            return status
    
    # Warm embedder
    try:
        _rag_system.vector_store.embedder.encode(["hello"])
        status["components"]["embedder"] = "ok"
    except Exception as e:
        status["components"]["embedder"] = f"error: {str(e)}"
        status["ok"] = False
    
    # Warm ChromaDB
    try:
        _rag_system.vector_store.search("hello", n_results=1)
        status["components"]["chromadb"] = "ok"
    except Exception as e:
        status["components"]["chromadb"] = f"error: {str(e)}"
        status["ok"] = False
    
    return status

def _load_rag_system():
    """Load and initialize RAG system (fallback if startup failed)"""
    global _rag_system
    
    if _rag_system is not None:
        return _rag_system
    
    logger.warning("⚠️ RAG system not pre-loaded - loading now (slower first response)...")
    start_time = datetime.now()
    
    try:
        from rag.rag_system import get_rag_system
        _rag_system = get_rag_system()
        _rag_system.initialize()
        
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"✅ RAG system loaded in {elapsed:.1f}s")
        return _rag_system
        
    except Exception as e:
        logger.error(f"❌ Failed to load RAG system: {str(e)}", exc_info=True)
        raise

@app.post("/chat", response_model=Answer)
async def chat(query: Query):
    """Answer questions using RAG system with safety guardrails"""
    
    # Generate timestamp in Europe/London timezone
    london_tz = pytz.timezone('Europe/London')
    timestamp = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    try:
        # Load RAG system if needed (lazy initialization on first request)
        rag_system = _load_rag_system()
        
        # Answer the question
        text = rag_system.answer_question(query.question)
        return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
        
    except Exception as e:
        logger.error(f"Error processing question: {str(e)}", exc_info=True)
        
        # Fallback to guardrails only
        from rag import answerer
        text = answerer.apply_guardrails(query.question) or (
            "Sorry, I encountered an error. Please try your question again."
        )
        return Answer(answer=text, timestamp=f"Last checked: {timestamp}")
