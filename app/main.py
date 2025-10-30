from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pytz
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="Maya Autism Facts Assistant", version="0.1.0")

# Global RAG system instance (loaded on first request)
_rag_system = None

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
    """Health check endpoint - responds immediately"""
    return {"status": "ok", "service": "maya-autism-assistant"}

def _load_rag_system():
    """Load and initialize RAG system (happens on first request)"""
    global _rag_system
    
    if _rag_system is not None:
        return _rag_system
    
    logger.info("🚀 Loading RAG system (first request)...")
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
