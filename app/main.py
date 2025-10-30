from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rag import answerer
import pytz
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="Maya Autism Facts Assistant", version="0.1.0")

# Pre-create RAG system instance on startup for instant first response
# (models are not loaded yet - that happens on first chat request)
@app.on_event("startup")
async def startup_event():
    """Pre-create RAG system instance without loading heavy models"""
    from rag.rag_system import get_rag_system
    logger.info("Pre-creating RAG system instance for fast first response...")
    _ = get_rag_system()  # Just create the instance, don't initialize
    logger.info("RAG system instance ready")

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

@app.post("/chat", response_model=Answer)
async def chat(query: Query):
    # Generate timestamp in Europe/London timezone
    london_tz = pytz.timezone('Europe/London')
    timestamp = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    # Use async answer function for background initialization
    text = await answerer.answer_async(query.question)
    return Answer(answer=text, timestamp=f"Last checked: {timestamp}")