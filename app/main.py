from fastapi import FastAPI, HTTPException, Header, Query as QueryParam, Cookie, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from enum import Enum
import pytz
from datetime import datetime, timezone, timedelta
import logging
import logging.handlers
import json
import os
import re
import asyncio
import secrets
from pathlib import Path
from collections import Counter

from app import notifications

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Ensure log directory exists
Path("logs").mkdir(exist_ok=True)

# ── Re-index history logger (logs/reindex.log) ──────────────────────────────
# Dedicated logger so every re-index (scheduled or manual) is appended to a
# persistent file with its result (success/failure + chunk count).
_reindex_logger = logging.getLogger("maya.reindex_history")
_reindex_logger.setLevel(logging.INFO)
_reindex_logger.propagate = False
if not _reindex_logger.handlers:
    _reindex_fh = logging.handlers.RotatingFileHandler(
        "logs/reindex.log", maxBytes=512 * 1024, backupCount=3
    )
    _reindex_fh.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")
    )
    _reindex_logger.addHandler(_reindex_fh)

# ── Scheduled re-index configuration ────────────────────────────────────────
# A lightweight async scheduler keeps Maya's knowledge base fresh without anyone
# having to remember to hit /admin/crawl. All settings are env-configurable.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

# Enabled by default; set SCHEDULED_REINDEX_ENABLED=false to turn off.
_SCHEDULED_REINDEX_ENABLED: bool = _env_bool("SCHEDULED_REINDEX_ENABLED", True)
# Hour of day (UK time) to run the nightly re-index. Default 2 = 2am.
try:
    _REINDEX_SCHEDULE_HOUR: int = int(os.environ.get("REINDEX_SCHEDULE_HOUR", "2"))
except ValueError:
    _REINDEX_SCHEDULE_HOUR = 2
_REINDEX_SCHEDULE_HOUR = max(0, min(23, _REINDEX_SCHEDULE_HOUR))
# "crawl" = full live crawl + reindex (keeps Hounslow info fresh); "seed" = seed-only.
_SCHEDULED_REINDEX_MODE: str = os.environ.get("SCHEDULED_REINDEX_MODE", "crawl").strip().lower()
_UK_TZ = pytz.timezone("Europe/London")

# ── Admin token ────────────────────────────────────────────────────────────
# Use ADMIN_TOKEN env var; if not set, generate a random one and log it once.
# Staff should set ADMIN_TOKEN in the Replit Secrets panel so the password
# stays the same across restarts and deployments (see replit.md).
_ADMIN_TOKEN: str = os.environ.get("ADMIN_TOKEN", "").strip() or secrets.token_urlsafe(32)
_ADMIN_TOKEN_IS_PERSISTENT: bool = bool(os.environ.get("ADMIN_TOKEN", "").strip())
if not _ADMIN_TOKEN_IS_PERSISTENT:
    logger.warning(
        "⚠️  ADMIN_TOKEN is not set, so a temporary admin password was generated "
        "for THIS session only and will change on the next restart:\n"
        f"    {_ADMIN_TOKEN}\n"
        "    To keep a permanent admin password, open the Replit Secrets panel "
        "(Tools → Secrets), add a secret named ADMIN_TOKEN with a value of your "
        "choosing, then restart. Staff can then sign in at /admin/login."
    )

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
    if _SCHEDULED_REINDEX_ENABLED:
        asyncio.create_task(_scheduled_reindex_loop())
    else:
        logger.info("⏰ Scheduled re-index disabled (SCHEDULED_REINDEX_ENABLED=false).")
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
# Admin dashboard  GET /admin
# Protected by ADMIN_TOKEN (query param ?token= or header X-Admin-Token)
# ──────────────────────────────────────────────────────────────────────

# Name of the cookie used to keep staff signed in after /admin/login.
_ADMIN_COOKIE_NAME = "maya_admin"


def _token_is_valid(token: str | None) -> bool:
    """Constant-time check of a candidate token against ADMIN_TOKEN."""
    if not token:
        return False
    return secrets.compare_digest(token.strip(), _ADMIN_TOKEN)


def _check_admin_token(
    token_param: str | None,
    token_header: str | None,
    token_cookie: str | None = None,
):
    """Raise 401/403 unless the query param, header, or session cookie matches ADMIN_TOKEN."""
    for candidate in (token_param, token_header, token_cookie):
        if _token_is_valid(candidate):
            return
    if not any((token_param, token_header, token_cookie)):
        raise HTTPException(status_code=401, detail="Admin token required (sign in at /admin/login).")
    raise HTTPException(status_code=403, detail="Invalid admin token.")


def _read_feedback_log(limit: int = 50) -> list[dict]:
    """Return the last `limit` entries from logs/feedback.log, newest first."""
    path = Path("logs/feedback.log")
    if not path.exists():
        return []
    entries = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    return list(reversed(entries))[:limit]


def _read_questions_stats() -> dict:
    """
    Parse logs/questions.log and return:
      - top_sources: list of (url, count) — top 10 most-retrieved source URLs
      - questions_7d: count of questions logged in the last 7 days
      - total_questions: total question entries in the log
    """
    path = Path("logs/questions.log")
    if not path.exists():
        return {"top_sources": [], "questions_7d": 0, "total_questions": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    url_counter: Counter = Counter()
    questions_7d = 0
    total = 0

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    questions_7d += 1
            except (ValueError, TypeError):
                pass
            for url in entry.get("source_ids", []):
                if url:
                    url_counter[url] += 1
    except Exception:
        pass

    top_sources = url_counter.most_common(10)
    return {
        "top_sources": top_sources,
        "questions_7d": questions_7d,
        "total_questions": total,
    }


def _read_reindex_history(limit: int = 15) -> list[dict]:
    """
    Parse logs/reindex.log and return the most recent `limit` runs, newest first.

    Each line is written by `_record_reindex_result` via `_reindex_logger` in the
    format: "<asctime>  <LEVEL>  [<source>] SUCCESS|FAILURE — <detail>".
    Returns a list of dicts: {"ts", "level", "source", "outcome", "detail"}.
    """
    path = Path("logs/reindex.log")
    if not path.exists():
        return []

    line_re = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s+"
        r"(?P<level>\w+)\s+(?P<msg>.*)$"
    )
    msg_re = re.compile(r"^\[(?P<source>[^\]]*)\]\s+(?P<outcome>\S+)\s+(?:—|-)?\s*(?P<detail>.*)$")

    entries: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = line_re.match(line)
            if not m:
                continue
            ts = m.group("ts")
            level = m.group("level")
            msg = m.group("msg").strip()
            source = "—"
            outcome = "—"
            detail = msg
            mm = msg_re.match(msg)
            if mm:
                source = mm.group("source") or "—"
                outcome = mm.group("outcome") or "—"
                detail = (mm.group("detail") or "").strip()
            entries.append(
                {"ts": ts, "level": level, "source": source, "outcome": outcome, "detail": detail}
            )
    except Exception:
        pass

    return list(reversed(entries))[:limit]


def _read_kb_health() -> dict:
    """
    Collect knowledge-base health for the admin dashboard:
      - total_chunks: current chunk count from the live vector store (or None)
      - running / last_run / last_result / alert / schedule: from _crawl_status
      - history: recent re-index runs parsed from logs/reindex.log
    """
    total_chunks = None
    try:
        if _rag_system is not None and getattr(_rag_system, "vector_store", None):
            cs = _rag_system.vector_store.get_collection_stats()
            if isinstance(cs, dict):
                total_chunks = cs.get("total_chunks")
    except Exception:
        total_chunks = None

    next_run = (
        _next_scheduled_run().isoformat() if _SCHEDULED_REINDEX_ENABLED else None
    )

    return {
        "total_chunks": total_chunks,
        "running": _crawl_status["running"],
        "last_run": _crawl_status["last_run"],
        "last_result": _crawl_status["last_result"],
        "alert": _crawl_status["alert"],
        "schedule": {
            "enabled": _SCHEDULED_REINDEX_ENABLED,
            "hour_uk": _REINDEX_SCHEDULE_HOUR,
            "mode": _SCHEDULED_REINDEX_MODE,
            "next_run": next_run,
        },
        "history": _read_reindex_history(),
    }


def _fmt_uk_time(iso_str: str | None) -> str:
    """Format a UTC ISO timestamp (e.g. '2026-06-08T16:11:00Z') in UK local time."""
    if not iso_str:
        return "Never"
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_UK_TZ).strftime("%d %b %Y %H:%M %Z")
    except (ValueError, TypeError):
        return str(iso_str)


def _render_admin_html(feedback: list[dict], stats: dict, kb: dict) -> str:
    """Build and return the admin dashboard as a self-contained HTML string."""

    def _esc(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    feedback_rows = ""
    for entry in feedback:
        ts = _esc(entry.get("ts", "—"))
        issue = _esc(entry.get("issue_type", "—") or "—")
        has_comment = "Yes" if entry.get("has_comment") else "No"
        q_len = _esc(str(entry.get("q_len", "—")))
        feedback_rows += (
            f"<tr><td>{ts}</td><td>{issue}</td>"
            f"<td>{q_len}</td><td>{has_comment}</td></tr>\n"
        )
    if not feedback_rows:
        feedback_rows = '<tr><td colspan="4" class="empty">No feedback entries yet.</td></tr>'

    source_rows = ""
    for url, count in stats["top_sources"]:
        url_esc = _esc(url)
        source_rows += (
            f'<tr><td><a href="{url_esc}" target="_blank" rel="noopener">{url_esc}</a></td>'
            f"<td>{count}</td></tr>\n"
        )
    if not source_rows:
        source_rows = '<tr><td colspan="2" class="empty">No source data yet.</td></tr>'

    # ── Knowledge Base health ──────────────────────────────────────────────
    last_result = kb.get("last_result") or {}
    last_run_uk = _fmt_uk_time(kb.get("last_run"))

    total_chunks = kb.get("total_chunks")
    chunks_display = "—" if total_chunks is None else str(total_chunks)

    if kb.get("running"):
        kb_status_html = '<span class="badge badge-run">Re-indexing now…</span>'
        last_outcome = "In progress"
        last_detail = ""
    elif last_result.get("success"):
        kb_status_html = '<span class="badge badge-ok">Success</span>'
        last_outcome = "Success"
        parts = []
        if last_result.get("total_chunks") is not None:
            parts.append(f"{last_result['total_chunks']} chunks")
        if last_result.get("seed_chunks") is not None:
            parts.append(f"seed {last_result['seed_chunks']}")
        if last_result.get("crawled_chunks") is not None:
            parts.append(f"crawled {last_result['crawled_chunks']}")
        last_detail = _esc(", ".join(parts))
    elif last_result.get("success") is False:
        kb_status_html = '<span class="badge badge-err">Failed</span>'
        last_outcome = "Failed"
        err = (
            last_result.get("error")
            or last_result.get("stderr_tail")
            or (f"exit code {last_result['exit_code']}" if last_result.get("exit_code") is not None else "Unknown error")
        )
        last_detail = _esc(str(err))
    else:
        kb_status_html = '<span class="badge badge-none">No runs yet</span>'
        last_outcome = "—"
        last_detail = ""

    elapsed = last_result.get("elapsed_seconds")
    duration_display = f"{elapsed}s" if elapsed is not None else "—"

    schedule = kb.get("schedule") or {}
    if schedule.get("enabled"):
        sched_text = (
            f"Daily at {schedule.get('hour_uk', '?'):02d}:00 UK "
            f"(mode: {_esc(str(schedule.get('mode', '?')))}) · "
            f"next: {_fmt_uk_time(schedule.get('next_run'))}"
        )
    else:
        sched_text = "Disabled"

    alert = kb.get("alert") or {}
    kb_alert_html = ""
    if alert.get("message"):
        kb_alert_html = (
            f'<div class="alert">⚠️ {_esc(alert["message"])} '
            f'<span class="alert-at">({_fmt_uk_time(alert.get("at"))})</span></div>'
        )

    history_rows = ""
    for h in kb.get("history", []):
        outcome = h.get("outcome", "—")
        oc_cls = "ok" if outcome.upper() == "SUCCESS" else ("err" if outcome.upper() == "FAILURE" else "")
        history_rows += (
            f"<tr><td>{_esc(h.get('ts', '—'))}</td>"
            f"<td>{_esc(h.get('source', '—'))}</td>"
            f'<td class="{oc_cls}">{_esc(outcome)}</td>'
            f"<td>{_esc(h.get('detail', ''))}</td></tr>\n"
        )
    if not history_rows:
        history_rows = '<tr><td colspan="4" class="empty">No re-index history yet.</td></tr>'

    generated_at = datetime.now(pytz.timezone("Europe/London")).strftime("%d %b %Y %H:%M %Z")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maya Admin Dashboard</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f7fa; color: #1a1a2e; margin: 0; padding: 1.5rem;
  }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 2rem; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }}
  .card {{
    background: #fff; border-radius: 8px; padding: 1.25rem 1.75rem;
    box-shadow: 0 1px 4px rgba(0,0,0,.08); min-width: 160px;
  }}
  .card .num {{ font-size: 2.5rem; font-weight: 700; color: #5b3fa6; line-height: 1; }}
  .card .label {{ font-size: 0.8rem; color: #666; margin-top: 0.3rem; }}
  h2 {{ font-size: 1.1rem; margin: 0 0 0.75rem; border-bottom: 2px solid #ede9f9; padding-bottom: 0.4rem; }}
  section {{ background: #fff; border-radius: 8px; padding: 1.25rem 1.5rem;
             box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; padding: 0.4rem 0.6rem; background: #f0ecfc;
        font-weight: 600; color: #4a3580; }}
  td {{ padding: 0.4rem 0.6rem; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  td.empty {{ color: #999; font-style: italic; }}
  td.ok {{ color: #1b7a3d; font-weight: 600; }}
  td.err {{ color: #b00020; font-weight: 600; }}
  a {{ color: #5b3fa6; word-break: break-all; }}
  .footer {{ font-size: 0.75rem; color: #aaa; text-align: center; margin-top: 2rem; }}
  .kb-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap: 0.75rem 1.5rem; margin-bottom: 1rem; }}
  .kb-item .kb-label {{ font-size: 0.75rem; color: #666; text-transform: uppercase;
                        letter-spacing: 0.03em; }}
  .kb-item .kb-val {{ font-size: 1.05rem; font-weight: 600; margin-top: 0.15rem; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
            font-size: 0.8rem; font-weight: 600; }}
  .badge-ok {{ background: #e3f5ea; color: #1b7a3d; }}
  .badge-err {{ background: #fde3e6; color: #b00020; }}
  .badge-run {{ background: #e7eefc; color: #2a52b5; }}
  .badge-none {{ background: #eee; color: #777; }}
  .alert {{ background: #fff6e6; border: 1px solid #f3d28a; color: #8a5a00;
            border-radius: 6px; padding: 0.6rem 0.8rem; font-size: 0.85rem;
            margin-bottom: 1rem; }}
  .alert .alert-at {{ color: #b08a3a; font-size: 0.8rem; }}
  .kb-actions {{ margin: 0.5rem 0 1rem; }}
  button.reindex-btn {{ background: #5b3fa6; color: #fff; border: none; cursor: pointer;
                        border-radius: 6px; padding: 0.55rem 1.1rem; font-size: 0.9rem;
                        font-weight: 600; }}
  button.reindex-btn:hover {{ background: #4a3185; }}
  button.reindex-btn:disabled {{ background: #b9a9dd; cursor: not-allowed; }}
  .reindex-msg {{ font-size: 0.85rem; margin-left: 0.75rem; color: #444; }}
</style>
</head>
<body>
<h1>Maya Admin Dashboard</h1>
<p class="meta">Generated: {generated_at} &nbsp;·&nbsp; Data is anonymised — no personal information is stored. &nbsp;·&nbsp; <a href="/admin/logout">Sign out</a></p>

<div class="cards">
  <div class="card">
    <div class="num">{stats['questions_7d']}</div>
    <div class="label">Questions (last 7 days)</div>
  </div>
  <div class="card">
    <div class="num">{stats['total_questions']}</div>
    <div class="label">Total questions logged</div>
  </div>
  <div class="card">
    <div class="num">{len(feedback)}</div>
    <div class="label">Feedback entries shown</div>
  </div>
</div>

<section>
  <h2>Knowledge Base</h2>
  {kb_alert_html}
  <div class="kb-grid">
    <div class="kb-item">
      <div class="kb-label">Total chunks stored</div>
      <div class="kb-val">{chunks_display}</div>
    </div>
    <div class="kb-item">
      <div class="kb-label">Last re-index</div>
      <div class="kb-val">{last_run_uk}</div>
    </div>
    <div class="kb-item">
      <div class="kb-label">Duration</div>
      <div class="kb-val">{duration_display}</div>
    </div>
    <div class="kb-item">
      <div class="kb-label">Last result</div>
      <div class="kb-val">{kb_status_html}</div>
    </div>
    <div class="kb-item">
      <div class="kb-label">Schedule</div>
      <div class="kb-val" style="font-size:0.9rem;font-weight:500;">{sched_text}</div>
    </div>
  </div>
  {f'<p style="font-size:0.85rem;color:#555;margin:0 0 1rem;">Details: {last_detail}</p>' if last_detail else ''}
  <div class="kb-actions">
    <button id="reindexBtn" class="reindex-btn" onclick="triggerReindex()">Re-index now</button>
    <span id="reindexMsg" class="reindex-msg"></span>
  </div>
  <h2 style="font-size:1rem;margin-top:1.25rem;">Re-index history</h2>
  <table>
    <thead><tr><th>Timestamp (UTC)</th><th>Source</th><th>Outcome</th><th>Details</th></tr></thead>
    <tbody>{history_rows}</tbody>
  </table>
</section>

<section>
  <h2>Top 10 Most-Retrieved Sources</h2>
  <table>
    <thead><tr><th>Source URL</th><th>Retrievals</th></tr></thead>
    <tbody>{source_rows}</tbody>
  </table>
</section>

<section>
  <h2>Last 50 Feedback Submissions</h2>
  <table>
    <thead><tr><th>Timestamp (UTC)</th><th>Issue Type</th><th>Q Length</th><th>Has Comment</th></tr></thead>
    <tbody>{feedback_rows}</tbody>
  </table>
</section>

<p class="footer">Maya Admin &mdash; Autism Hounslow &mdash; For internal use only</p>
<script>
function triggerReindex() {{
  var btn = document.getElementById('reindexBtn');
  var msg = document.getElementById('reindexMsg');
  var token = window.prompt('Enter the crawl admin token (ADMIN_CRAWL_TOKEN) to start a full re-index:');
  if (!token) {{ return; }}
  btn.disabled = true;
  msg.style.color = '#444';
  msg.textContent = 'Starting re-index…';
  fetch('/admin/crawl', {{
    method: 'POST',
    headers: {{ 'Authorization': 'Bearer ' + token.trim() }}
  }})
  .then(function (r) {{ return r.json().then(function (d) {{ return {{ ok: r.ok, body: d }}; }}); }})
  .then(function (res) {{
    if (res.ok) {{
      msg.style.color = '#1b7a3d';
      msg.textContent = (res.body && res.body.message) || 'Re-index started. Refresh in a minute to see progress.';
    }} else {{
      btn.disabled = false;
      msg.style.color = '#b00020';
      msg.textContent = (res.body && res.body.detail) || 'Failed to start re-index.';
    }}
  }})
  .catch(function (e) {{
    btn.disabled = false;
    msg.style.color = '#b00020';
    msg.textContent = 'Error: ' + e;
  }});
}}
</script>
</body>
</html>"""


def _render_login_html(error: str | None = None) -> str:
    """Simple admin sign-in form. Posts the token to /admin/login."""
    error_html = (
        f'<p class="error" role="alert">{error}</p>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maya Admin – Sign in</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #f4f7fa;
         color: #1c2430; display: flex; min-height: 100vh; align-items: center;
         justify-content: center; margin: 0; }}
  .card {{ background: #fff; padding: 2rem; border-radius: 12px; max-width: 360px;
          width: 100%; box-shadow: 0 6px 24px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .25rem; }}
  p.sub {{ margin: 0 0 1.25rem; color: #5a6675; font-size: .9rem; }}
  label {{ display: block; font-weight: 600; margin-bottom: .4rem; font-size: .9rem; }}
  input[type=password] {{ width: 100%; padding: .7rem; font-size: 1rem;
         border: 1px solid #c8d2dd; border-radius: 8px; box-sizing: border-box; }}
  button {{ margin-top: 1rem; width: 100%; padding: .75rem; font-size: 1rem;
           font-weight: 600; color: #fff; background: #2b6cb0; border: none;
           border-radius: 8px; cursor: pointer; min-height: 44px; }}
  button:hover {{ background: #245a94; }}
  .error {{ color: #b42318; background: #fef3f2; border: 1px solid #fda29b;
           padding: .6rem .75rem; border-radius: 8px; font-size: .9rem; }}
</style>
</head>
<body>
  <form class="card" method="post" action="/admin/login">
    <h1>Maya Admin</h1>
    <p class="sub">Enter the admin password to continue.</p>
    {error_html}
    <input type="text" name="username" value="admin" autocomplete="username" hidden aria-hidden="true">
    <label for="token">Admin password</label>
    <input id="token" name="token" type="password" autocomplete="current-password" autofocus required>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>"""


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_form(
    token_cookie: str | None = Cookie(default=None, alias=_ADMIN_COOKIE_NAME),
):
    """Show the admin sign-in form (or go straight to the dashboard if already signed in)."""
    if _token_is_valid(token_cookie):
        return RedirectResponse(url="/admin", status_code=303)
    return HTMLResponse(content=_render_login_html())


@app.post("/admin/login")
async def admin_login_submit(token: str = Form(default="")):
    """Validate the submitted token and, on success, store it in a session cookie."""
    if not _token_is_valid(token):
        return HTMLResponse(
            content=_render_login_html(error="Incorrect password. Please try again."),
            status_code=401,
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=_ADMIN_COOKIE_NAME,
        value=token.strip(),
        max_age=60 * 60 * 12,  # 12 hours
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return response


@app.get("/admin/logout")
async def admin_logout():
    """Clear the admin session cookie and return to the sign-in form."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(key=_ADMIN_COOKIE_NAME)
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    token: str | None = QueryParam(default=None),
    x_admin_token: str | None = Header(default=None),
    token_cookie: str | None = Cookie(default=None, alias=_ADMIN_COOKIE_NAME),
    accept: str | None = Header(default=None),
):
    """
    Admin dashboard — shows feedback reports and question trends.

    Staff can sign in once at /admin/login (stores a session cookie), or pass the
    ADMIN_TOKEN directly:
      GET /admin?token=<ADMIN_TOKEN>
      GET /admin  (with header X-Admin-Token: <ADMIN_TOKEN>)
    """
    if not any(_token_is_valid(c) for c in (token, x_admin_token, token_cookie)):
        # Browsers with no credentials get the friendly login form instead of
        # a bare error page; API clients get proper 401/403 status codes.
        if not any((token, x_admin_token, token_cookie)) and "text/html" in (accept or ""):
            return RedirectResponse(url="/admin/login", status_code=303)
        _check_admin_token(token, x_admin_token, token_cookie)
    feedback = _read_feedback_log(limit=50)
    stats = _read_questions_stats()
    kb = _read_kb_health()
    html = _render_admin_html(feedback, stats, kb)
    return HTMLResponse(content=html)


# ──────────────────────────────────────────────────────────────────────
# Admin: /admin/crawl — web crawl trusted UK sources + re-index
#        /admin/reindex — re-index from seed JSONL files only (faster)
# Both protected by ADMIN_CRAWL_TOKEN env var (Bearer token).
# ──────────────────────────────────────────────────────────────────────

_crawl_task: asyncio.Task | None = None
_crawl_status: dict = {
    "running": False,
    "last_run": None,
    "last_result": None,
    # Set when a re-index fails so the failure surfaces prominently in
    # /admin/crawl/status until the next successful run clears it.
    "alert": None,
}


def _record_reindex_result(source: str, result: dict) -> None:
    """
    Append a re-index outcome to logs/reindex.log and, on failure, raise a
    prominent alert that surfaces via /admin/crawl/status.

    `source` is a short label such as "scheduled", "manual-crawl", or
    "manual-reindex".
    """
    when = datetime.utcnow().isoformat() + "Z"
    if result.get("success"):
        total = result.get("total_chunks", "?")
        seed = result.get("seed_chunks", "?")
        crawled = result.get("crawled_chunks", "?")
        elapsed = result.get("elapsed_seconds", "?")
        _reindex_logger.info(
            f"[{source}] SUCCESS — {total} chunks "
            f"(seed={seed}, crawled={crawled}) in {elapsed}s"
        )
        # A successful run clears any standing failure alert and resets the
        # email-alert throttle so the *next* failure notifies immediately.
        _crawl_status["alert"] = None
        notifications.reset_throttle()
    else:
        detail = (
            result.get("error")
            or result.get("stderr_tail")
            or (f"exit code {result['exit_code']}" if result.get("exit_code") is not None else "unknown error")
        )
        _reindex_logger.warning(f"[{source}] FAILURE — {detail}")
        logger.warning(
            f"🚨 Re-index FAILED ({source}): {detail} — "
            "knowledge base may be stale. See /admin/crawl/status and logs/reindex.log."
        )
        _crawl_status["alert"] = {
            "level": "warning",
            "source": source,
            "message": f"Re-index failed ({source}): {detail}",
            "at": when,
        }
        # Actively notify staff by email (fire-and-forget, throttled, no-op if
        # REINDEX_ALERT_EMAIL_TO is not configured — see app/notifications.py).
        notifications.notify_reindex_failure_async(source, str(detail), when)


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


async def _run_crawl_and_reindex_background(source: str = "manual-crawl"):
    """
    Full pipeline: crawl trusted UK autism sources → add to ChromaDB alongside
    seed JSONL data → hot-reload RAG system.

    `source` labels who triggered the run (e.g. "manual-crawl" or "scheduled")
    and is recorded in logs/reindex.log.
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
        from rag.crawler import (
            crawl_and_chunk_all,
            save_crawled_chunks,
            dedupe_crawled_chunks,
        )
        from rag.vector_store import UKAutismVectorStore
        from rag.structured_importer import StructuredKnowledgeImporter

        try:
            crawled_chunks = await crawl_and_chunk_all()
            docs_crawled = len(crawled_chunks)
            logger.info(f"✅ Crawled {docs_crawled} chunks from live sources")
            # Persist the raw crawl output to data/raw/ alongside ChromaDB.
            if crawled_chunks:
                await asyncio.to_thread(save_crawled_chunks, crawled_chunks)
        except Exception as crawl_err:
            logger.warning(f"⚠️ Web crawl failed or partially failed: {crawl_err} — continuing with seed data only")
            crawled_chunks = []

        # Step 2: load seed JSONL data, then add crawled chunks (skipping any URL
        # already present in the curated seed data so it is never stored twice).
        def _rebuild_index():
            vs = UKAutismVectorStore()
            vs.initialize()
            vs.reset_collection()

            importer = StructuredKnowledgeImporter()
            seed_chunks = importer.import_file("data/maya_hounslow_knowledge_seed.jsonl")
            seed_urls = {
                c["metadata"].get("url")
                for c in seed_chunks
                if c.get("metadata", {}).get("url")
            }
            unique_crawled = dedupe_crawled_chunks(crawled_chunks, seed_urls)
            all_chunks = seed_chunks + unique_crawled
            vs.add_documents(all_chunks)
            stats = vs.get_collection_stats()
            return stats.get("total_chunks", len(all_chunks)), len(seed_chunks), len(unique_crawled)

        total_chunks, seed_count, chunks_from_crawl = await asyncio.to_thread(_rebuild_index)
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
        _record_reindex_result(source, _crawl_status["last_result"])
        logger.info("✅ RAG system reloaded after crawl+reindex.")

    except Exception as e:
        elapsed = (datetime.utcnow() - start).total_seconds()
        logger.error(f"❌ Crawl+reindex background task error: {e}", exc_info=True)
        _crawl_status["last_result"] = {
            "success": False,
            "elapsed_seconds": round(elapsed, 1),
            "error": str(e),
        }
        _record_reindex_result(source, _crawl_status["last_result"])
    finally:
        _crawl_status["running"] = False
        _crawl_status["last_run"] = datetime.utcnow().isoformat() + "Z"


async def _run_reindex_only_background(source: str = "manual-reindex"):
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
            _record_reindex_result(source, _crawl_status["last_result"])
            logger.info("✅ RAG system reloaded after seed re-index.")
        else:
            logger.error(f"❌ Re-index script failed (exit {proc.returncode}): {proc.stderr[:500]}")
            _crawl_status["last_result"] = {
                "success": False,
                "exit_code": proc.returncode,
                "stderr_tail": proc.stderr.strip()[-2000:],
            }
            _record_reindex_result(source, _crawl_status["last_result"])
    except Exception as e:
        logger.error(f"❌ Re-index background task error: {e}", exc_info=True)
        _crawl_status["last_result"] = {"success": False, "error": str(e)}
        _record_reindex_result(source, _crawl_status["last_result"])
    finally:
        _crawl_status["running"] = False
        _crawl_status["last_run"] = datetime.utcnow().isoformat() + "Z"


# ──────────────────────────────────────────────────────────────────────
# Scheduled (nightly) re-index — keeps the knowledge base fresh with no
# manual intervention. Configured via SCHEDULED_REINDEX_* env vars.
# ──────────────────────────────────────────────────────────────────────

def _next_scheduled_run(now: datetime | None = None) -> datetime:
    """Return the next datetime (UK tz) at which the nightly re-index should run."""
    now = now or datetime.now(_UK_TZ)
    nxt = now.replace(hour=_REINDEX_SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt = nxt + timedelta(days=1)
    return nxt


async def _scheduled_reindex_loop():
    """
    Background loop that triggers a re-index every day at _REINDEX_SCHEDULE_HOUR
    (UK time). Uses the full crawl pipeline by default so live Hounslow/UK info
    stays fresh; set SCHEDULED_REINDEX_MODE=seed for a faster seed-only rebuild.
    """
    logger.info(
        f"⏰ Scheduled re-index enabled — daily at {_REINDEX_SCHEDULE_HOUR:02d}:00 UK time "
        f"(mode={_SCHEDULED_REINDEX_MODE})."
    )
    while True:
        try:
            now = datetime.now(_UK_TZ)
            nxt = _next_scheduled_run(now)
            delay = (nxt - now).total_seconds()
            logger.info(
                f"⏰ Next scheduled re-index at {nxt.isoformat()} "
                f"(in {delay / 3600:.1f}h)."
            )
            await asyncio.sleep(delay)

            if _crawl_status["running"]:
                logger.info("⏰ Scheduled re-index skipped — a run is already in progress.")
            else:
                logger.info("⏰ Starting scheduled re-index...")
                if _SCHEDULED_REINDEX_MODE == "seed":
                    await _run_reindex_only_background(source="scheduled")
                else:
                    await _run_crawl_and_reindex_background(source="scheduled")

            # Guard against re-triggering within the same scheduled minute.
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("⏰ Scheduled re-index loop cancelled.")
            raise
        except Exception as e:
            logger.error(f"❌ Scheduled re-index loop error: {e}", exc_info=True)
            # Back off briefly before recomputing the next run.
            await asyncio.sleep(300)


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
    next_run = (
        _next_scheduled_run().isoformat() if _SCHEDULED_REINDEX_ENABLED else None
    )
    return {
        "running": _crawl_status["running"],
        "last_run": _crawl_status["last_run"],
        "last_result": _crawl_status["last_result"],
        "alert": _crawl_status["alert"],
        "schedule": {
            "enabled": _SCHEDULED_REINDEX_ENABLED,
            "hour_uk": _REINDEX_SCHEDULE_HOUR,
            "mode": _SCHEDULED_REINDEX_MODE,
            "next_run": next_run,
        },
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
