from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rag import answerer
from contextlib import asynccontextmanager
import pytz
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup for the FastAPI app"""
    # Startup: Pre-initialize RAG system to eliminate cold start
    logger.info("🚀 Starting Maya - Pre-initializing RAG system...")
    
    try:
        from rag.rag_system import get_rag_system
        
        # Get and initialize the RAG system
        rag_system = get_rag_system()
        if not rag_system.initialized:
            rag_system.initialize()
            logger.info("✅ RAG system initialized successfully")
        
        # Warmup: Run a dummy query to cache embeddings and models
        logger.info("🔥 Warming up embedding model...")
        warmup_response = rag_system.answer_question("warmup test")
        logger.info("✅ RAG system warmed up - first responses will now be fast!")
        
    except Exception as e:
        logger.error(f"❌ RAG initialization failed: {e}")
        logger.info("📋 Maya will fall back to basic guardrails")
    
    yield  # App runs here
    
    # Shutdown: cleanup if needed
    logger.info("👋 Maya shutting down")

app = FastAPI(
    title="Maya Autism Facts Assistant", 
    version="0.1.0",
    lifespan=lifespan
)

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
    """Health check with RAG system status"""
    try:
        from rag.rag_system import get_rag_system
        rag_system = get_rag_system()
        return {
            "status": "ok", 
            "rag_initialized": rag_system.initialized,
            "startup_optimization": "enabled"
        }
    except Exception as e:
        return {"status": "ok", "rag_initialized": False, "error": str(e)}

@app.post("/chat", response_model=Answer)
async def chat(query: Query):
    # Generate timestamp in Europe/London timezone
    london_tz = pytz.timezone('Europe/London')
    timestamp = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    text = answerer.answer(query.question)
    return Answer(answer=text, timestamp=f"Last checked: {timestamp}")