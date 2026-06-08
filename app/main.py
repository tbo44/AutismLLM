from fastapi import FastAPI, HTTPException, Header
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
import asyncio
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
    """Append question text + retrieved source IDs to logs/questions.log.
    No user identity (IP, name, session) is ever stored."""
    try:
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "question": question,
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


# ──────────────────────────────────────────────────────────────────────
# Admin: /admin/crawl — web crawl trusted UK sources + re-index
#        /admin/reindex — re-index from seed JSONL files only (faster)
# Both protected by ADMIN_CRAWL_TOKEN env var (Bearer token).
# ──────────────────────────────────────────────────────────────────────

_crawl_task: asyncio.Task | None = None
_crawl_status: dict = {"running": False, "last_run": None, "last_result": None}


def _verify_admin_token(authorization: str | None):
    """Raise 401/403 if the Authorization header doesn't match ADMIN_CRAWL_TOKEN."""
    expected = os.environ.get("ADMIN_CRAWL_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin endpoint not configured (ADMIN_CRAWL_TOKEN not set).")
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token.")


async def _run_crawl_and_reindex_background():
    """
    Full pipeline: crawl trusted UK autism sources → add to ChromaDB alongside
    seed JSONL data → hot-reload RAG system.
    """
    global _rag_system, _startup_complete, _crawl_status
    _crawl_status["running"] = True
    _crawl_status["last_result"] = None
    start = datetime.utcnow()
    logger.info("🌐 Admin crawl+reindex starting — fetching live UK sources...")

    docs_crawled = 0
    chunks_from_crawl = 0
    reindex_success = False

    try:
        # Step 1: crawl live web sources
        from rag.crawler import crawl_and_chunk_all
        from rag.vector_store import UKAutismVectorStore
        from rag.structured_importer import StructuredKnowledgeImporter

        try:
            crawled_chunks = await crawl_and_chunk_all()
            docs_crawled = len(crawled_chunks)
            chunks_from_crawl = len(crawled_chunks)
            logger.info(f"✅ Crawled {docs_crawled} chunks from live sources")
        except Exception as crawl_err:
            logger.warning(f"⚠️ Web crawl failed or partially failed: {crawl_err} — continuing with seed data only")
            crawled_chunks = []

        # Step 2: load seed JSONL data
        def _rebuild_index():
            vs = UKAutismVectorStore()
            vs.initialize()
            vs.reset_collection()

            importer = StructuredKnowledgeImporter()
            seed_chunks = importer.import_file("data/maya_hounslow_knowledge_seed.jsonl")
            all_chunks = seed_chunks + crawled_chunks
            vs.add_documents(all_chunks)
            stats = vs.get_collection_stats()
            return stats.get("total_chunks", len(all_chunks)), len(seed_chunks)

        total_chunks, seed_count = await asyncio.to_thread(_rebuild_index)
        reindex_success = True
        elapsed = (datetime.utcnow() - start).total_seconds()
        logger.info(f"✅ Crawl+reindex complete in {elapsed:.1f}s — {total_chunks} chunks total — reloading RAG...")

        # Step 3: hot-reload the running RAG system
        _rag_system = await asyncio.to_thread(_initialize_rag_sync)
        _startup_complete = True

        _crawl_status["last_result"] = {
            "success": True,
            "elapsed_seconds": round(elapsed, 1),
            "total_chunks": total_chunks,
            "seed_chunks": seed_count,
            "crawled_chunks": chunks_from_crawl,
        }
        logger.info("✅ RAG system reloaded after crawl+reindex.")

    except Exception as e:
        elapsed = (datetime.utcnow() - start).total_seconds()
        logger.error(f"❌ Crawl+reindex background task error: {e}", exc_info=True)
        _crawl_status["last_result"] = {
            "success": False,
            "elapsed_seconds": round(elapsed, 1),
            "error": str(e),
        }
    finally:
        _crawl_status["running"] = False
        _crawl_status["last_run"] = datetime.utcnow().isoformat() + "Z"


async def _run_reindex_only_background():
    """Seed-only re-index (no live crawling) — faster, used by /admin/reindex."""
    global _rag_system, _startup_complete, _crawl_status
    import subprocess
    _crawl_status["running"] = True
    _crawl_status["last_result"] = None
    start = datetime.utcnow()
    logger.info("🔄 Admin seed-only re-index starting...")
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["python", "scripts/reindex.py"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = (datetime.utcnow() - start).total_seconds()
        if proc.returncode == 0:
            logger.info(f"✅ Re-index complete in {elapsed:.1f}s — reloading RAG system...")
            _rag_system = await asyncio.to_thread(_initialize_rag_sync)
            _startup_complete = True
            _crawl_status["last_result"] = {
                "success": True,
                "elapsed_seconds": round(elapsed, 1),
                "stdout_tail": proc.stdout.strip()[-2000:],
            }
            logger.info("✅ RAG system reloaded after seed re-index.")
        else:
            logger.error(f"❌ Re-index script failed (exit {proc.returncode}): {proc.stderr[:500]}")
            _crawl_status["last_result"] = {
                "success": False,
                "exit_code": proc.returncode,
                "stderr_tail": proc.stderr.strip()[-2000:],
            }
    except Exception as e:
        logger.error(f"❌ Re-index background task error: {e}", exc_info=True)
        _crawl_status["last_result"] = {"success": False, "error": str(e)}
    finally:
        _crawl_status["running"] = False
        _crawl_status["last_run"] = datetime.utcnow().isoformat() + "Z"


@app.post("/admin/crawl")
async def admin_crawl(authorization: str | None = Header(default=None)):
    """
    Trigger a full background re-crawl + re-index of Maya's knowledge base.

    Pipeline:
      1. Crawl trusted UK autism sources (NHS, NAS, Gov.UK, Hounslow Council, etc.)
         defined in rag/sources.py
      2. Load seed JSONL data (data/maya_hounslow_knowledge_seed.jsonl)
      3. Reset ChromaDB and add all chunks (crawled + seed)
      4. Hot-reload the running RAG system

    If live crawling fails (network issues, site changes), the pipeline falls back
    to seed-only data so the knowledge base is never left empty.

    Requires Bearer token matching ADMIN_CRAWL_TOKEN environment variable.

    Example:
        curl -X POST https://<host>/admin/crawl \\
             -H "Authorization: Bearer <your-token>"
    """
    _verify_admin_token(authorization)

    global _crawl_task
    if _crawl_status["running"]:
        return {
            "status": "already_running",
            "message": "A crawl+reindex is already in progress. Check /admin/crawl/status for updates.",
        }

    _crawl_task = asyncio.create_task(_run_crawl_and_reindex_background())
    logger.info("🚀 Admin crawl+reindex task spawned.")
    return {
        "status": "started",
        "message": "Full crawl+reindex started in the background. Check /admin/crawl/status for progress.",
    }


@app.get("/admin/crawl/status")
async def admin_crawl_status(authorization: str | None = Header(default=None)):
    """Return the status of the last (or current) crawl+reindex run."""
    _verify_admin_token(authorization)
    return {
        "running": _crawl_status["running"],
        "last_run": _crawl_status["last_run"],
        "last_result": _crawl_status["last_result"],
    }


@app.post("/admin/reindex")
async def admin_reindex(authorization: str | None = Header(default=None)):
    """
    Trigger a fast seed-only re-index (no web crawling).

    Rebuilds ChromaDB from data/maya_hounslow_knowledge_seed.jsonl only.
    Use /admin/crawl for a full crawl+reindex.

    Requires Bearer token matching ADMIN_CRAWL_TOKEN environment variable.

    Example:
        curl -X POST https://<host>/admin/reindex \\
             -H "Authorization: Bearer <your-token>"
    """
    _verify_admin_token(authorization)

    global _crawl_task
    if _crawl_status["running"]:
        return {
            "status": "already_running",
            "message": "A re-index is already in progress. Check /admin/crawl/status for updates.",
        }

    _crawl_task = asyncio.create_task(_run_reindex_only_background())
    logger.info("🚀 Admin seed-only re-index task spawned.")
    return {
        "status": "started",
        "message": "Seed-only re-index started in the background. Check /admin/crawl/status for progress.",
    }
