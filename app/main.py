from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from enum import Enum
import pytz
from datetime import datetime
import logging
import json
import os
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Ensure log directory exists
Path("logs").mkdir(exist_ok=True)

app = FastAPI(title="Maya – Autism Hounslow Assistant", version="2.0.0")

_rag_system = None
_startup_complete = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ──────────────────────────────────────────────────────────────────────
# Startup / background RAG initialisation
# ──────────────────────────────────────────────────────────────────────

async def initialize_rag_background():
    global _rag_system, _startup_complete
    import asyncio
    await asyncio.sleep(0.1)
    logger.info("🚀 Maya background initialisation starting...")
    start_time = datetime.now()
    try:
        _rag_system = await asyncio.to_thread(_initialize_rag_sync)
        elapsed = (datetime.now() - start_time).total_seconds()
        _startup_complete = True
        logger.info(f"✅ Maya ready! Background initialisation completed in {elapsed:.1f}s")
    except Exception as e:
        logger.error(f"❌ Background initialisation failed: {str(e)}", exc_info=True)
        _startup_complete = False


def _initialize_rag_sync():
    from rag.rag_system import get_rag_system
    logger.info("📦 Loading RAG system...")
    rag_system = get_rag_system()
    rag_system.initialize()
    logger.info("🔥 Warming up RAG components...")
    try:
        rag_system.vector_store.embedder.encode(["warmup"])
        logger.info("✅ SentenceTransformer warmed")
    except Exception as e:
        logger.warning(f"Embedder warmup failed (non-critical): {e}")
    try:
        rag_system.vector_store.search("warmup test", n_results=1)
        logger.info("✅ ChromaDB warmed")
    except Exception as e:
        logger.warning(f"ChromaDB warmup failed (non-critical): {e}")
    return rag_system


@app.on_event("startup")
async def startup_event():
    import asyncio
    logger.info("🚀 Maya server starting – spawning background RAG initialisation task...")
    asyncio.create_task(initialize_rag_background())
    logger.info("✅ Server startup event complete – background task spawned")


def _load_rag_system():
    global _rag_system
    if _rag_system is not None:
        return _rag_system
    logger.warning("⚠️ RAG system not pre-loaded – loading now (slower first response)...")
    start_time = datetime.now()
    from rag.rag_system import get_rag_system
    _rag_system = get_rag_system()
    _rag_system.initialize()
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"✅ RAG system loaded in {elapsed:.1f}s")
    return _rag_system


# ──────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────

class ComprehensionLevel(str, Enum):
    clear    = "clear"
    standard = "standard"
    complex  = "complex"


class Query(BaseModel):
    question: str
    comprehension_level: ComprehensionLevel = ComprehensionLevel.standard


class Source(BaseModel):
    title: str
    url: str
    publisher: str


class Answer(BaseModel):
    answer: str
    timestamp: str
    sources: list[Source] = []
    disclaimer: str = (
        "Maya provides general guidance only — not medical, legal, or financial advice. "
        "For medical concerns contact your GP or NHS 111. Emergencies: call 999. "
        "For SEND/legal advice: IPSEA or Citizens Advice."
    )


class FeedbackPayload(BaseModel):
    question: str = ""
    response_id: str = ""
    issue_type: str = ""
    comment: str = ""


# ──────────────────────────────────────────────────────────────────────
# Logging helpers
# ──────────────────────────────────────────────────────────────────────

def _log_question(question: str, source_ids: list):
    """Append question + retrieved source IDs to logs/questions.log (no PII)."""
    try:
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "q_len": len(question),
            "source_ids": source_ids[:6]
        }
        with open("logs/questions.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning(f"Question log write failed: {e}")


def _log_feedback(payload: FeedbackPayload):
    """Append feedback submission to logs/feedback.log (no PII)."""
    try:
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "issue_type": payload.issue_type,
            "response_id": payload.response_id,
            "q_len": len(payload.question),
            "has_comment": bool(payload.comment.strip())
        }
        with open("logs/feedback.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning(f"Feedback log write failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(
        "static/index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@app.get("/health")
async def health():
    """Ultra-lightweight health check — responds immediately."""
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "status": "ok",
        "service": "maya-autism-hounslow",
        "rag_ready": _startup_complete
    }


@app.get("/warmup")
async def warmup():
    global _rag_system
    result = {"ok": True, "startup_complete": _startup_complete, "components": {}}
    if _rag_system is None:
        try:
            _rag_system = _load_rag_system()
            result["components"]["rag_loaded"] = True
        except Exception as e:
            result["ok"] = False
            result["error"] = str(e)
            return result
    try:
        _rag_system.vector_store.embedder.encode(["hello"])
        result["components"]["embedder"] = "ok"
    except Exception as e:
        result["components"]["embedder"] = f"error: {e}"
        result["ok"] = False
    try:
        _rag_system.vector_store.search("hello", n_results=1)
        result["components"]["chromadb"] = "ok"
    except Exception as e:
        result["components"]["chromadb"] = f"error: {e}"
        result["ok"] = False
    return result


@app.post("/chat", response_model=Answer)
async def chat(query: Query):
    """Answer questions using the RAG pipeline with safety guardrails."""
    london_tz = pytz.timezone("Europe/London")
    timestamp = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    logger.info(f"📖 Comprehension: {query.comprehension_level.value}  Q-len: {len(query.question)}")

    try:
        global _rag_system
        if _rag_system is None:
            logger.info("RAG system not ready, initialising now...")
            _rag_system = _load_rag_system()

        if not _rag_system.initialized:
            logger.warning("RAG system exists but not initialised, initialising now...")
            _rag_system.initialize()

        result = _rag_system.answer_question(
            query.question,
            comprehension_level=query.comprehension_level.value
        )

        answer_text  = result.get("answer", "")
        sources_list = result.get("sources", [])
        source_ids   = [s.get("url", "") for s in sources_list]

        _log_question(query.question, source_ids)

        sources = [Source(**src) for src in sources_list]
        return Answer(answer=answer_text, timestamp=f"Last checked: {timestamp}", sources=sources)

    except Exception as e:
        logger.error(f"Error processing question: {str(e)}", exc_info=True)
        from rag import answerer
        text = answerer.apply_guardrails(query.question) or (
            "Sorry, I encountered an error. Please try your question again."
        )
        return Answer(answer=text, timestamp=f"Last checked: {timestamp}", sources=[])


@app.post("/feedback")
async def feedback(payload: FeedbackPayload):
    """
    Accept a feedback or issue report from the UI.
    Logs the submission to logs/feedback.log — no personal information stored.
    """
    _log_feedback(payload)
    logger.info(f"Feedback received: type={payload.issue_type!r}")
    return {"status": "received", "message": "Thank you for your feedback."}
