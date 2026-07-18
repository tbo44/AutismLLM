# Maya — Autism Hounslow Information Assistant

Maya is a free web-based information assistant for autistic people and carers in Hounslow, powered by a Retrieval-Augmented Generation (RAG) system built with FastAPI and ChromaDB.

This README is the developer playbook: full specification, setup, operations, and contribution guide.

---

## Contents

1. [Product specification](#1-product-specification)
2. [Architecture overview](#2-architecture-overview)
3. [Project structure](#3-project-structure)
4. [Prerequisites](#4-prerequisites)
5. [Environment variables](#5-environment-variables)
6. [Running locally](#6-running-locally)
7. [The knowledge base](#7-the-knowledge-base)
8. [Scheduled refresh & crawling](#8-scheduled-refresh--crawling)
9. [Admin dashboard](#9-admin-dashboard)
10. [Running tests](#10-running-tests)
11. [Deploying on Replit (Autoscale)](#11-deploying-on-replit-autoscale)
12. [Custom subdomain setup](#12-custom-subdomain-setup)
13. [Wix integration instructions](#13-wix-integration-instructions)
14. [Contributing](#14-contributing)
15. [Privacy](#15-privacy)

---

## 1. Product specification

### Purpose
Maya answers factual questions about UK autism support — benefits (DLA, PIP, Universal Credit), education (EHCP, SEND), NHS services, and Hounslow-specific local services — for autistic people and carers. Jurisdiction: United Kingdom, with emphasis on England and the London Borough of Hounslow.

### What Maya must do
- Answer only from its knowledge base (no invented facts). When nothing relevant is found, say so explicitly and signpost official sources.
- Produce **structured answers** with these exact sections: `## Short Answer`, `## Steps`, `## Who to Contact`, `## Useful Links`, `## Important Note`.
- Be **deterministic**: `TEMPERATURE=0` by default so identical questions get identical answers.
- Offer three **reading comprehension levels**: Clear (short sentences, no acronyms), Standard (plain English, jargon explained), Complex (professional detail).
- Provide accessibility features: 18px minimum body text, 44px touch targets, low-stimulation mode, `prefers-reduced-motion` respected, focus/expanded layout modes.

### What Maya must never do (safety guardrails)
Regex-based guardrails in `app/main.py` detect and refuse:
- **Clinical**: diagnoses, medication, treatment advice → redirect to GP/NHS.
- **Legal**: individual case advice → redirect to Citizens Advice / IPSEA.
- **Crisis**: self-harm or danger signals → immediate safety template (999, A&E, NHS 111, Samaritans 116 123). This bypasses the RAG pipeline entirely.

Off-topic questions (checked via the LLM appropriateness filter) get a polite redirect listing what Maya can help with.

### UI requirements
- Persistent top disclaimer banner ("general guidance only… 999 / Samaritans 116 123").
- "About Maya" panel explaining what Maya can and cannot do.
- "Report an issue" feedback form (POST `/feedback`).
- Privacy notice modal.
- Suggestion buttons for common questions.
- Calm, autism-friendly colour palette; no unnecessary animation.

---

## 2. Architecture overview

| Component | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| Vector database | ChromaDB (persisted to `./chroma_db/`) |
| Embeddings | SentenceTransformers (`all-MiniLM-L6-v2` by default, env-configurable) |
| LLM | Any OpenAI-compatible endpoint, env-configurable (see §5) |
| Frontend | Vanilla HTML + CSS + JavaScript (no framework) |
| Knowledge base | JSONL seed file + web crawl of trusted UK sources |

### Request flow (`POST /chat`)
1. Guardrail regex check (crisis/clinical/legal) — may short-circuit with a refusal/safety template.
2. Query enhancement via LLM, then vector search in ChromaDB.
3. Relevance filter: results with cosine distance ≥ `MIN_RELEVANCE_THRESHOLD` (0.8, defined in `rag/retriever.py`) are dropped. If nothing passes, a **query-expansion fallback** expands UK acronyms (EHCP → full name, etc.) and retries. If still nothing, the explicit "not in knowledge base" response is returned — never a hallucinated answer.
4. LLM synthesises a structured answer from the retrieved chunks, at the requested reading level.
5. Question text + retrieved source IDs are appended to `logs/questions.log`.

### Key endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Main chat interface |
| `/health` | GET | Health check (deployment probes) |
| `/status` | GET | RAG readiness status |
| `/warmup` | GET | Component health check |
| `/chat` | POST | Answer a question (`{"question": "...", "comprehension_level": "clear\|standard\|complex"}`) |
| `/feedback` | POST | Submit a feedback report |
| `/admin` | GET | Staff dashboard (auth required, see §9) |
| `/admin/login` / `/admin/logout` | GET/POST | Staff session sign-in/out |
| `/admin/crawl` | POST | Trigger crawl + re-index (Bearer `ADMIN_CRAWL_TOKEN`) |
| `/admin/crawl/status` | GET | Re-index status, schedule, alerts |

---

## 3. Project structure

```
app/main.py                  FastAPI app: endpoints, guardrails, logging, admin
                             dashboard, scheduled re-index loop
rag/llm_client.py            LLM provider abstraction + structured answer prompt
rag/vector_store.py          ChromaDB wrapper + embeddings
rag/retriever.py             Search, relevance threshold, query expansion
rag/rag_system.py            Orchestrates retrieve → synthesise, no-results handling
rag/structured_importer.py   JSONL/CSV seed importer + field normalisation
rag/crawler.py               Web crawler for trusted UK sources
scripts/reindex.py           CLI: rebuild knowledge base (--crawl, --no-reset, --seed-file)
scripts/post-merge.sh        Post-merge setup (deps + logs dir)
data/maya_hounslow_knowledge_seed.jsonl   Curated knowledge seed (one JSON per line)
static/                      Frontend (index.html, styles.css, script.js)
tests/                       Test suites (see §10)
docs/STRUCTURED_KNOWLEDGE_GUIDE.md        Seed format, field by field
MAYA_RAG_TECHNICAL_GUIDE.md               RAG pipeline deep dive
replit.md                    Project overview & admin access notes
```

---

## 4. Prerequisites

- Python 3.11+
- pip

```bash
pip install -r requirements.txt
```

---

## 5. Environment variables

Set these in your shell, `.env` file, or (on Replit) via the **Secrets** tab.

### LLM backend

Provider selection priority: explicit `LLM_PROVIDER` → auto-detect from keys/URL → local Ollama with Qwen.

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | auto | `groq`, `openai`, `ollama`, or `custom` — explicitly selects the backend |
| `LLM_BASE_URL` | — | Base URL for any OpenAI-compatible endpoint |
| `LLM_API_KEY` | — | API key for the chosen endpoint |
| `GROQ_API_KEY` | — | Groq key; auto-selects Groq when no other backend configured |
| `OPENAI_API_KEY` | — | Used when `LLM_PROVIDER=openai` |
| `LLM_MODEL` | per provider | Groq→`llama-3.3-70b-versatile`, OpenAI→`gpt-4o-mini`, Ollama→`qwen2.5:72b` |
| `TEMPERATURE` | `0` | Determinism — keep at 0 in production |
| `TOP_P` | `1.0` | Top-p sampling |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformers model |
| `HF_TOKEN` | — | Hugging Face token (private embedding models) |

**Provider examples**

```bash
# Ollama with Qwen (no-keys default)
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:72b

# Groq
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o
```

### Admin & operations

| Variable | Default | Description |
|---|---|---|
| `ADMIN_TOKEN` | random per restart | Permanent staff password for `/admin` — **set this in production** |
| `ADMIN_CRAWL_TOKEN` | — | Separate Bearer token for `/admin/crawl` and re-index endpoints |
| `SCHEDULED_REINDEX_ENABLED` | `true` | Nightly automatic re-index on/off |
| `REINDEX_SCHEDULE_HOUR` | `2` | UK-local hour for the nightly run |
| `SCHEDULED_REINDEX_MODE` | `crawl` | `crawl` = full web crawl + re-index; `seed` = seed-only (faster) |

---

## 6. Running locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
# open http://localhost:5000
```

The server starts immediately; the RAG knowledge base loads in the background (~30–60 s first run, ~10 s once ChromaDB is cached).

---

## 7. The knowledge base

The primary source is `data/maya_hounslow_knowledge_seed.jsonl` — one JSON object per line, curated by hand. See `docs/STRUCTURED_KNOWLEDGE_GUIDE.md` for the full field reference.

Required fields per entry: `id`, `title`, `url`, `source_name`, `source_type`, `category`, `subcategory`, `audience`, `age_range`, `locality`, `description_plain`, `format_type`, `reliability_score`, plus `date_added`, `last_reviewed`, `content`, and `tags`.

### Re-indexing

Run whenever the seed file changes:

```bash
python scripts/reindex.py                 # clear + rebuild from seed
python scripts/reindex.py --crawl         # also crawl trusted UK sources (deduped)
python scripts/reindex.py --no-reset      # add without clearing
python scripts/reindex.py --seed-file data/my_data.jsonl
```

Prints a per-category chunk summary. Raw crawl output is saved to `data/raw/` with deduplication against seed URLs.

### Retrieval tuning

`rag/retriever.py` defines `MIN_RELEVANCE_THRESHOLD = 0.8` (cosine distance). Lower = stricter. If natural phrasings of an in-seed topic fall through to the "no information" fallback, either add a broader entry or check `tests/test_retrieval_coverage.py`, which guards 8 core questions and tracks known gaps as expected-fail tests.

---

## 8. Scheduled refresh & crawling

A background loop in `app/main.py` re-indexes nightly at `REINDEX_SCHEDULE_HOUR` (UK time, default 02:00). Each run is logged to `logs/reindex.log`; failures raise an alert surfaced on `/admin` and `GET /admin/crawl/status`. Manual trigger: the "Re-index now" button on `/admin`, or:

```bash
curl -X POST https://<your-app>/admin/crawl -H "Authorization: Bearer $ADMIN_CRAWL_TOKEN"
```

---

## 9. Admin dashboard

`GET /admin` shows: question volume (7-day + total), feedback submissions, top-10 retrieved sources, knowledge-base health (chunk count, last re-index, schedule/next run, failure alerts), and re-index history.

**Access**: staff sign in at `/admin/login` with `ADMIN_TOKEN` (session cookie, 12 h). The token also works via `?token=` or `X-Admin-Token` header for scripts. **Set `ADMIN_TOKEN` in Secrets** — otherwise a temporary token is generated each restart and printed once to the log.

---

## 10. Running tests

```bash
pytest tests/ -v
```

| Suite | Covers |
|---|---|
| `test_answers.py` | Live integration: 5 core topics, reading levels, off-topic redirects, acronym-free "clear" answers |
| `test_retrieval_coverage.py` | Deterministic (no LLM): core questions must retrieve below threshold; known gaps tracked as xfail |
| `test_policy.py` | Safety guardrail patterns |
| `test_admin.py` | Admin auth (401/403/cookie/header) and log parsing |
| `test_smoke.py`, `test_static_assets.py` | Basic endpoint and asset checks |

Note: `test_answers.py` needs a working LLM backend (e.g. `GROQ_API_KEY`).

---

## 11. Deploying on Replit (Autoscale)

1. Open the Repl → **Deploy** tab → choose **Autoscale**.
2. Run command: `uvicorn app.main:app --host 0.0.0.0 --port 5000`
3. Health check path: `/health`.
4. Add all secrets (see §5) — especially `ADMIN_TOKEN` and your LLM key.
5. Deploy. The app is served at a `.replit.app` URL.

> ChromaDB (`./chroma_db/`) persists within a running instance but is rebuilt from the seed file on cold start via background initialisation.

---

## 12. Custom subdomain setup

To serve Maya at `maya.autismhounslow.org.uk`:

### Option A — Replit custom domain (recommended)
1. Deploy settings → **Custom domains** → add `maya.autismhounslow.org.uk`.
2. Add the CNAME record Replit shows you at your DNS provider.
3. Wait for propagation; Replit provisions HTTPS automatically.

### Option B — Reverse proxy (own server)

```nginx
server {
    listen 443 ssl;
    server_name maya.autismhounslow.org.uk;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_read_timeout 120s;
    }
}
```

---

## 13. Wix integration instructions

### Option 1 — Link button (simplest, recommended)
Add a Wix **Button** → Link → Web address → your Maya URL → "Open in new tab".

### Option 2 — Embedded iframe
Wix editor → **Add** → **Embed** → **Embed HTML**:

```html
<iframe
  src="https://maya.autismhounslow.org.uk"
  width="100%"
  height="700"
  style="border:none; border-radius:12px;"
  title="Maya – Autism Hounslow Information Assistant"
  allow="clipboard-write"
></iframe>
```

Notes: HTTPS required (automatic on Replit); use ≥650px height on desktop, ≥500px mobile; Wix iframes don't support `100vh` — use fixed heights.

### Recommended Wix page structure
```
Page: "Get Information"
├── Heading: "Ask Maya – Autism Hounslow Information Assistant"
├── Paragraph: "Maya provides general information about autism support, benefits,
│              and services in Hounslow and the UK. Maya does not give medical
│              or legal advice."
└── HTML iframe embed → Maya URL
```

---

## 14. Contributing

1. **Adding knowledge**: edit the seed JSONL (include all required fields, verify against official sources, set `reliability_score` honestly), then `python scripts/reindex.py` and run `pytest tests/test_retrieval_coverage.py`.
2. **Changing answers/prompts**: the structured format and reading-level rules live in `rag/llm_client.py` (`synthesize_response`). Run `pytest tests/test_answers.py` after changes.
3. **Changing guardrails**: patterns are in `app/main.py`; every change must keep `tests/test_policy.py` green. Crisis safety is non-negotiable — never weaken the crisis path.
4. **Frontend**: keep 18px body text, 44px touch targets, reduced-motion gating, and the disclaimer banner. No frameworks.
5. **Before opening a PR**: run the full suite `pytest tests/ -v` (LLM-dependent tests need a key) and keep `README.md` in step with behaviour changes — especially the privacy section.

---

## 15. Privacy

Maya logs question text and retrieved source IDs to `logs/questions.log` to help
improve the service. No user identity (IP address, name, session ID) is ever stored.
Feedback submissions record only the issue type — not free-text comments or identity.
See the **Privacy notice** link inside the chat interface for full details.
