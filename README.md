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
14. [Syncing to GitHub](#14-syncing-to-github)
15. [Contributing](#15-contributing)
16. [Roadmap](#16-roadmap)
17. [Privacy](#17-privacy)

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
| `LLM_RATE_LIMIT_COOLDOWN_SECONDS` | `30` | Minimum seconds to pause provider calls after a rate limit; finite non-negative number, `0` disables |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformers model |
| `HF_TOKEN` | — | Hugging Face token (private embedding models) |

**Provider examples**

The rate-limit cooldown is shared by answer requests using the application's LLM
client. Moderation, query enhancement, and answer generation all activate it.
An HTTP 429 is not retried automatically. A valid `Retry-After` (whole seconds or
an HTTP date) can extend the configured cooldown, but the provider's requested
wait is capped at 300 seconds. Missing, malformed, or past values leave the
configured wait unchanged. Setting the cooldown to `0` ignores `Retry-After`
and disables the pause.
During the cooldown, answers return the existing temporary-unavailable message
without provider calls; local safety guardrails still run. Calls resume on the
next request after expiry. The cooldown uses a monotonic clock and is in-memory
per server process, not shared across replicas or preserved after a restart.
Already-running provider calls are not cancelled. Change the setting before
starting the server.

In the server logs, `llm_provider_429` marks a fresh provider response. It shows
the stage, effective wait in seconds (`0` if disabled), and whether this response's
hint lengthened the pause. `llm_cooldown_suppressed` marks the first locally
skipped call per uninterrupted pause and shows the remaining wait.

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
| `ADMIN_TOKEN` | private random fallback per server process | Set this to sign in to `/admin` |
| `SESSION_SECRET` | private random signing key per server process | Set a strong random secret distinct from `ADMIN_TOKEN`, shared by all app instances that serve staff. |
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

To use the admin dashboard locally, choose a strong password and set it before
starting the server. In Bash, this prompts without echoing the password:

```bash
read -r -s -p 'Local admin password: ' ADMIN_TOKEN
printf '\n'
export ADMIN_TOKEN
uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
```

Open `http://localhost:5000/admin/login` and enter the same password. If Maya
is already running, stop it and start it again from the shell where you set
`ADMIN_TOKEN`.

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

`rag/retriever.py` defines `MIN_RELEVANCE_THRESHOLD = 0.8`. Lower = stricter. If natural phrasings of an in-seed topic fall through to the "no information" fallback, either add a broader entry or run the [offline retrieval checks](#offline-retrieval-coverage).

---

## 8. Scheduled refresh & crawling

A background loop in `app/main.py` re-indexes nightly at `REINDEX_SCHEDULE_HOUR` (UK time, default 02:00). Each run is logged to `logs/reindex.log`; failures raise an alert surfaced on `/admin` and `GET /admin/crawl/status`. Manual trigger: the "Re-index now" button on `/admin`, or:

```bash
curl -X POST https://<your-app>/admin/crawl -H "Authorization: Bearer $ADMIN_CRAWL_TOKEN"
```

---

## 9. Admin dashboard

`GET /admin` shows: question volume (7-day + total), feedback submissions, top-10 retrieved sources, knowledge-base health (chunk count, last re-index, schedule/next run, failure alerts), and re-index history.

**Staff sign-in**: open `/admin/login` and enter `ADMIN_TOKEN` in the password
form. The password is submitted only in the request body. The browser receives
a separate signed session cookie that expires after 12 hours. Existing cookies
that contained the password are no longer accepted; sign in again to replace
them. Signing out clears the cookie from that browser. Set `ADMIN_TOKEN` in
[your local shell](#6-running-locally) or in Replit **Secrets**, then restart.
If it is unset or blank, Maya generates a private random fallback and does not
print it.

Set `SESSION_SECRET` to a strong random secret shared by all serving instances
so browser sessions work across autoscale instances and restarts. Without it,
each process generates its own private signing key and users may need to sign
in again after a restart or when routed to another instance. Rotating either
`ADMIN_TOKEN` or `SESSION_SECRET` invalidates previously issued sessions.

**Scripts**: use a clean URL and send `ADMIN_TOKEN` in the `X-Admin-Token`
header. This also works for authenticated glossary edits.

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-app>/admin
```

**Moving off old links**: URLs containing `?token=…` no longer sign you in.
Replace old bookmarks with `/admin/login` and update scripts to use the header
before switching to this version. Old browser links to the dashboard redirect
to sign-in (or to a clean dashboard URL when already authenticated). If a real
password was ever used in a URL, rotate `ADMIN_TOKEN`: the change cannot remove
it from browser history, saved links, or request logs that already recorded it.

---

## 10. Running tests

Set up the [retrieval model cache](#offline-retrieval-coverage) once before running the full suite.

```bash
pytest tests/ -v
```

| Suite | Covers |
|---|---|
| `test_answers.py` | Provider-independent chat integration: topic answers and sources, structured prompts, reading-level instructions, safety and off-topic redirects |
| `test_llm_live.py` | Opt-in live checks: configured model access, structured answers, reading levels, and essential advice in both levels |
| `test_llm_live_budget.py` | Offline checks that the model access test uses one attempt and that the session cap includes retries and applies across cases |
| `test_llm_answer_essentials.py` | Offline checks that complete paraphrases pass and fluent replies missing essential advice fail |
| `test_llm_reading_comparison.py` | Offline checks of the live comparison's scoring, request limit, and provider failure reporting |
| `test_retrieval_coverage.py` | Real, cached embeddings: core and compound questions, strict and second-tier thresholds, and off-topic rejection |
| `test_policy.py` | Safety guardrail patterns |
| `test_admin.py` | Admin auth (401/403/cookie/header) and log parsing |
| `test_smoke.py`, `test_static_assets.py` | Basic endpoint and asset checks |

### Offline retrieval coverage

These checks embed the seed into a fresh ChromaDB collection using the real
`sentence-transformers/all-MiniLM-L6-v2` model at the commit pinned in
`scripts/cache_retrieval_model.py`. They don't use the live database, an LLM,
or the app's `EMBEDDING_MODEL` setting.

After installing the project's Python dependencies, run setup once from the
project root on a machine that can reach Hugging Face:

```bash
python -m scripts.cache_retrieval_model
```

Setup downloads the pinned weights and model files to the Hugging Face Hub
cache. Use the same cache for pytest. For an offline runner, set `HF_HUB_CACHE`
to a shared or copied Hub cache directory before running pytest. If copying
the cache, include both `snapshots/` and `blobs/` for the model, since the
snapshot can contain links to the blobs. Don't commit model weights to Git.

```bash
python -m pytest tests/test_retrieval_coverage.py -v

# Optional verification on Linux with unprivileged user namespaces enabled:
unshare --user --map-root-user --net \
  python -m pytest tests/test_retrieval_coverage.py -v
```

Pytest always loads the snapshot locally and never tries to fill the cache.
`RETRIEVAL MODEL SETUP` means the pinned snapshot is missing or incomplete,
not that Maya returned a wrong result. If setup cannot download the files,
stage them from a connected machine. Retrieval, threshold, and precision
assertions run only after the real model has loaded.

### Live LLM checks

Routine chat tests use controlled completions, so they don't need a provider
key or an available model. To check only whether the configured model responds:

```bash
pytest -m llm_live tests/test_llm_live.py::test_live_model_access -v --llm-request-limit=1
```

This sends one small completion request, with no retries, moderation, query
rewriting, or answer-quality scoring. To also run the structured-answer and
reading-level checks:

```bash
pytest -m llm_live tests/test_llm_live.py -v --llm-request-limit=28
```

Both commands send real requests to the configured provider and may use quota.
`--llm-request-limit` is required and caps HTTP attempts across the whole run,
including SDK retries and failed requests. The full suite normally needs 28
attempts without retries: one for model access, plus moderation, query rewriting,
and generation for each of nine answers. If the cap is reached, the next request
is blocked and the test fails with a budget message, not an answer-quality
failure. Run these checks in a single pytest process so tests share one budget.

An unavailable model, denied access, or a rate limit fails with a
provider-specific diagnostic, separate from answer-quality failures. The
reading-level checks ask the same questions at both levels using fixed source
text. Before comparing readability, they check that **both** replies keep
essential advice about school plan reviews, child DLA claims, autism assessment
referrals, and the first step and usual deadline for challenging a PIP decision.
They also flag selected contradictions, such
as telling a parent to submit a completed child DLA form online after saying
to post it. Failures name the issue and reading level. The checks make no
extra provider requests. Readability
checks look for unexplained acronyms in Clear replies and compare sentence
length and use of long words. These checks are regression signals, not a
formal reading-age score or a full factual review; an unfamiliar paraphrase
may need manual review against the fixed source.

### Comparing candidate models across several answers

To compare models available on the **same configured provider**, replace the
example IDs with the models you want to assess:

```bash
python -m scripts.compare_llm_reading --live \
  --model candidate-a --model candidate-b --samples-per-topic 3 --request-limit 48
```

This is a separate, manual run. It sends real requests only with `--live` and
an explicit `--request-limit`. Each sample is a Clear/Complex pair. The run
needs up to `2 × models × topics × samples-per-topic` HTTP requests (four
topics, three pairs per model and topic by default, minimum two pairs).
If the limit is too small, the tool refuses before contacting the provider.
It does not retry requests, follow redirects, or continue after an HTTP 429.

The comparison uses Maya's answer-generation prompt and the same fixed sources
and quality checks as the live suite. It skips moderation, query rewriting, and
retrieval so the scores reflect answer generation. All candidates use the same
configured `TEMPERATURE` and `TOP_P`; repeated replies may be similar with a
low temperature. The report shows passes out of **complete pairs** by model
and topic. Quality misses, provider failures, and unfinished pairs are shown
separately, so a model and topic with no scored pairs has no pass rate. Reading-level
misses are results to review, not an exit failure; provider failures or an
interrupted comparison return a nonzero exit status.

The report flags **limited evidence** for any model and topic with fewer than
five complete pairs, even when all planned requests finish. A perfect `2/2`
score is based on just two pairs. When rates are close, check how many pairs
were scored and how many were missing: a single pair can change the percentage,
and provider errors can leave candidates with different coverage. The
five-pair cutoff is a reporting rule, not a confidence threshold. A small gap
alone does not establish a winner or a regression.

Add `--report-json reading-run.json` to save a machine-readable copy alongside
the terminal summary. Use a new filename for each run; an existing
file is never overwritten. The JSON includes the schema version, rubric ID,
candidate and topic IDs, sampling settings, request counts, and counts for
passed, quality-failed, provider-failed, and unfinished pairs. The pass rate is
a fraction of scored pairs, or `null` when none were scored. Each result has a
`limited_evidence` flag; the top-level `evidence` section records the cutoff
and guidance. Reports include partial results if the comparison stops early,
but no credentials or generated answers. Saving a report makes no extra provider
requests. Compare runs with the same rubric and sampling settings when assessing
a model or prompt change.

---

## 11. Deploying on Replit (Autoscale)

1. Open the Repl → **Deploy** tab → choose **Autoscale**.
2. Run command: `uvicorn app.main:app --host 0.0.0.0 --port 5000`
3. Health check path: `/health`.
4. Add all secrets (see §5) — especially `ADMIN_TOKEN`, `SESSION_SECRET`, and your LLM key.
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

## 14. Syncing to GitHub

The committed Replit workspace `main` branch is the source for the GitHub repo at
[github.com/tbo44/AutismLLM](https://github.com/tbo44/AutismLLM).
The **GitHub Sync** workflow checks for new commits every 60 seconds while the
workspace is running, and re-verifies the remote every five minutes. The Run
button starts it alongside Maya Server. It catches up when restarted.
Uncommitted edits are not uploaded. Publishing the website is separate.

### One-step sync

If the watcher is stopped, run this from inside the Replit shell:

```bash
python scripts/sync_to_github.py
```

The script:
1. Fetches a short-lived OAuth token from the **Replit GitHub integration** (no
   personal token or password needed — the integration must be connected to this
   Repl via **Integrations → GitHub**).
2. Clones the Replit workspace into a temporary directory.
3. Fetches GitHub `main` and merges it with committed workspace `main` inside the
   temporary clone, preserving both histories. A new remote branch is created if
   GitHub does not have `main` yet.
4. Pushes the combined history to `github.com/tbo44/AutismLLM` without force.
5. Verifies that GitHub `main` matches the pushed commit, then deletes the temporary clone.
Credentials are passed through an ephemeral credential helper, never embedded in
remote URLs or command arguments.

> **Prerequisite:** The GitHub integration must be connected. If it isn't, open
> the Replit sidebar → **Integrations** → **GitHub** → **Connect** (one-time
> OAuth flow, then it persists).

### Safety and failures

The watcher never commits unfinished edits, force-pushes, or changes workspace
files or local Git history. Remote-only changes stay on GitHub; automatic merges
are made only in the temporary clone. Later backups preserve those merge commits.
If GitHub advances during a push, the script fetches and merges again, with at
most three push attempts per sync.

Conflicting edits or unrelated histories stop the sync without pushing. The
console names conflicting files when available. Resolve the histories in a
separate working checkout, commit the resolution, and incorporate it into
workspace `main` through your normal Git workflow before retrying. Save any
unfinished workspace work before doing that; the backup script does not import
remote changes into the workspace or resolve conflicts for you.

Failures appear in the **GitHub Sync** workflow console and are retried every
60 seconds. A manual run exits unsuccessfully on failure. Authentication failures
require repairing the GitHub connection. A lock prevents simultaneous manual and
automatic runs.

Isolated regression checks use local repositories and fake authorization, with
no network calls or real GitHub pushes:

```bash
python -m pytest tests/test_github_sync.py -q
```

---

## 15. Contributing

1. **Adding knowledge**: edit the seed JSONL (include all required fields, verify against official sources, set `reliability_score` honestly), then `python scripts/reindex.py` and run the [offline retrieval checks](#offline-retrieval-coverage).
2. **Changing answers/prompts**: the structured format and reading-level rules live in `rag/llm_client.py` (`synthesize_response`). Run `pytest tests/test_answers.py` after changes.
3. **Changing guardrails**: patterns are in `app/main.py`; every change must keep `tests/test_policy.py` green. Crisis safety is non-negotiable — never weaken the crisis path.
4. **Frontend**: keep 18px body text, 44px touch targets, reduced-motion gating, and the disclaimer banner. No frameworks.
5. **Before opening a PR**: run the routine suite `pytest tests/ -v` and keep `README.md` in step with behaviour changes — especially the privacy section.

---

## 16. Roadmap

The current app is live and stable; the following improvements are planned or in progress:

### Knowledge base
- ~~Add "how to apply" guides for EHCP and PIP~~ ✅ done (July 2026)
- Close remaining answer gaps: Blue Badge, Access to Work, Motability, respite care, and carer benefits (currently tracked as expected-fail tests in `tests/test_retrieval_coverage.py`)
- Handle short, casual questions (e.g. "pip?", "help with school") that currently miss the retrieval threshold
- Show plain-English explanations of acronyms directly in the chat window

### Operations & reliability
- Email staff automatically when the nightly re-index fails
- Verify crawl/re-index runs actually store fresh pages (freshness check)
- Skip re-crawling unchanged pages to speed up nightly refreshes
- Rotate `logs/reindex.log` so it does not grow forever
- Keep the GitHub repository automatically in sync with the live app

### Admin & security
- Lock out repeated wrong admin password attempts (rate limiting)
- Ensure staff at `/admin/login` cannot be silently locked out
- Auto-refresh the Knowledge Base status panel while a re-index is running

### Known issues
- One static-asset test still checks the old homepage title (fix queued)

This list evolves — check the project task board (or ask the maintainer) for current status before starting work on any of these.

---

## 17. Privacy

### Test-email retry tracking

Keyed requests to `/admin/alerts/test` save delivery claims and results in an
explicitly selected durable backend:

- `TEST_ALERT_ATTEMPTS_BACKEND=sqlite` (default): uses `TEST_ALERT_ATTEMPTS_PATH`
  (default `logs/test_alert_attempts.sqlite3`). All workers must share this file
  on storage that survives replacement/restarts. **Separate or ephemeral disks
  are not coordinated by SQLite.**
- `TEST_ALERT_ATTEMPTS_BACKEND=postgres`: uses the dedicated secret
  `TEST_ALERT_ATTEMPTS_DATABASE_URL`. All replicas/replacement instances must
  connect to the **same durable PostgreSQL database and table**. Independent
  disks are supported. This does not automatically use `DATABASE_URL`, copy
  SQLite claims, move ChromaDB, or migrate any other application data.

Both backends commit an exclusive claim before delivery. PostgreSQL serializes
the capacity check and insert in one transaction, releasing its database lock
before calling the mail service. Unavailable/full storage or missing tables
prevent keyed sends (HTTP 503); there is no local fallback. Invalid backend names
or a missing PostgreSQL URL fail configuration rather than selecting SQLite.

Completed results (including recipient and error information) are retained for
24 hours from completion and cleaned up on the next keyed request. Interrupted
attempts remain protected indefinitely: if delivery started but its result was
not saved, retries return HTTP 409 and never automatically resend. A slow
in-flight retry can also return 409 after a short wait; retrying the same key
later will recover the completed result if it was saved. Check the inbox before
deliberately starting a new attempt using **Start a new attempt**.
Unresolved attempts count towards the 1,024-attempt limit and must not be deleted
just to allow retries. This is duplicate-retry protection, not exactly-once SMTP
delivery. Requests without an `Idempotency-Key` keep the original behavior.

#### Shared PostgreSQL setup and safe cutover

1. Choose a durable, operator-managed PostgreSQL database accessible by every
   server. Use TLS (`sslmode=require`, or `verify-full` with your provider's CA)
   for remote connections. Store its connection string as the dedicated secret
   `TEST_ALERT_ATTEMPTS_DATABASE_URL`; never put it in Git or command arguments.
   Keep development/tests separate from production. The runtime needs SELECT,
   INSERT, UPDATE and DELETE on `public.maya_test_alert_attempts` (no DDL).
2. Have the database administrator explicitly apply
   `scripts/test_alert_attempts_schema.sql` to that database using their normal
   database tools. The application never creates PostgreSQL tables at startup.
   The table must use durable commits (`synchronous_commit=on`, the PostgreSQL
   default); do not use unlogged tables or disable durability. If you instead
   choose Replit-managed PostgreSQL, apply schema in development through the
   database tools and use the supported Publish schema flow for production,
   not a startup/production schema script.
3. **Stop all old test-email senders, drain any in-flight sends, and disable test
   email access during cutover.** Back up each old SQLite store using SQLite's
   backup tools (including WAL data); preserve claims from every old disk.
   A fresh shared database alone cannot protect retries of old keys.
4. From the project root, with the dedicated target secret configured, import
   **each** stopped source file explicitly:

   ```bash
   python -m scripts.import_test_alert_attempts \
     --source /path/to/old/test_alert_attempts.sqlite3 \
     --confirm-all-senders-stopped
   ```

   This command does not send email or change the source. It imports pending,
   uncertain, archived and completed records in one transaction per source.
   Unresolved claims never become eligible for sending; archived keys remain
   permanently protected. Completed results retain their original completion
   time. Identical records can be imported again; any conflicting key aborts
   the entire source import without overwriting existing protection. Investigate
   conflicts offline and preserve the most conservative protection; do not
   delete keys to force the import through. Verify counts/states in the target
   and keep the source backups.
5. Only after every source is reconciled, set
   `TEST_ALERT_ATTEMPTS_BACKEND=postgres` on **all** servers and restart them.
   Review unresolved attempts in `/admin` before re-enabling test-email access.
   Never run a mixed SQLite/PostgreSQL fleet. Do not switch back to stale SQLite
   files: claims created since cutover would be missing.

If an ephemeral source disk has already been lost, its ambiguous claims cannot
be reconstructed safely. Keep test sends disabled until recovered backups or a
deliberate operator review establishes a safe path; a new database is not proof
that old email was never delivered. Restoring an old PostgreSQL backup can also
lose newer protection records; stop sends and reconcile before reopening.
Manual archive frees active capacity without deleting a protected key; it does
not cancel an in-flight send.

Run the isolated PostgreSQL integration tests with:

```bash
python -m pytest tests/test_postgres_test_alert_attempts.py
```

They create a temporary local PostgreSQL cluster using `initdb` and `pg_ctl`,
never use application database credentials, run servers with separate working
directories, and mock every delivery. Without those server binaries they skip;
install PostgreSQL server tools in CI to run them.

Maya logs question text and retrieved source IDs to `logs/questions.log` to help
improve the service. No user identity (IP address, name, session ID) is ever stored.
Feedback submissions record only the issue type — not free-text comments or identity.
See the **Privacy notice** link inside the chat interface for full details.
