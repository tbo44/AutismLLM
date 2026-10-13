# Overview

Maya is a UK-focused autism facts assistant built with FastAPI and vanilla JavaScript. It provides a web-based chat interface for autistic people and carers to access information about UK autism support, services, benefits, and education. The application incorporates robust safety guardrails against inappropriate advice and advanced accessibility features, including reading comprehension level options for users with learning disabilities. The project aims to offer a reliable and accessible resource, leveraging a dual knowledge base system for comprehensive and accurate information.

# User Preferences

Preferred communication style: Simple, everyday language
Target audience: UK autistic people and carers
Jurisdiction: United Kingdom (emphasis on England and Hounslow)

# System Architecture

## Knowledge Base System
Maya utilizes dual knowledge sources:
- **Web Crawling System**: Extracts general autism information from trusted UK websites (NHS, NAS, Gov.UK).
- **Structured Knowledge Import**: Curated JSONL/CSV files containing detailed bureaucratic guides, prioritized in search with a relevance boost.

## Backend Architecture
- **Framework**: FastAPI with Python 3.11 for a robust API.
- **Core Endpoints**: `/` (main chat interface), `/health` (ultra-fast health check), `/status` (RAG readiness), `/warmup` (component health check), and `/chat` (main conversation with safety guardrails).
- **Startup Optimization**: Background async task using `asyncio.to_thread()` initializes RAG non-blockingly, allowing the server to start rapidly and respond to health checks immediately.
- **Deployment Ready**: Configured for autoscale deployment using the `/health` endpoint.
- **Safety System**: Regex-based guardrail patterns detect and refuse clinical, legal, or crisis content.
- **Configuration**: Environment variable-based with UK timezone and locale settings.

## Frontend Architecture
- **Technology Stack**: Vanilla HTML, CSS, and JavaScript.
- **UI Design**: Light theme with a calm, autism-friendly color palette.
- **Chat Interface**: Managed by the MayaApp class for real-time conversations.
- **Responsive Design**: Mobile-first with optional width expansion.
  - **Layout Modes**: Default 420px "Focus Mode" for reduced overwhelm, and an optional 960px "Expanded Mode" for carers/desktop users. User preference is saved in localStorage.
- **Accessibility Features**:
  - Reading comprehension levels (Clear/Standard/Complex) selector.
  - Low-stimulation mode toggle.
  - Keyboard accessible controls and 44px minimum touch targets.
  - Respects `prefers-reduced-motion` and includes iOS safe-area support. All preferences are persisted in localStorage.

## Safety & Compliance System
- **Guardrails**: Automatic detection and refusal of inappropriate content in clinical, legal, and crisis categories.
- **Refusal Templates**: Professional redirects to appropriate UK services.
- **Disclaimer**: Persistent banner emphasizing the information-only nature of the advice.

## Admin Access
- **Admin Dashboard**: `/admin` shows feedback reports, question trends, and knowledge-base health.
- **ADMIN_TOKEN**: Set this in the Replit **Secrets** panel (Tools → Secrets) to create a permanent admin password. If it is not set, a temporary password is generated on each restart and printed once to the server log — staff would otherwise lose access after every restart or deployment.
- **Sign in**: Staff visit `/admin/login`, enter the `ADMIN_TOKEN`, and stay signed in via an HttpOnly session cookie (12-hour expiry) so they don't need to paste the token in the URL each visit. `/admin/logout` clears the session. The token may also still be passed via `?token=` query param or the `X-Admin-Token` header.
- **ADMIN_CRAWL_TOKEN**: A separate Bearer token (set in Secrets) protects the crawl/re-index endpoints (`/admin/crawl`, `/admin/reindex`).

## Failure Email Alerts
- When a re-index (nightly or manual) fails, Maya emails staff with the error detail so a stale knowledge base doesn't go unnoticed.
- **REINDEX_ALERT_EMAIL_TO**: comma-separated staff addresses (needs `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`), or the special value `replit` to use Replit's built-in mailer (sends to the Repl owner, no SMTP setup). Unset = alerts off (no-op).
- **REINDEX_ALERT_THROTTLE_HOURS** (default 24): repeated failures send at most one email per window; a successful refresh resets the throttle. Implemented in `app/notifications.py`.
- **Test-email retries**: `TEST_ALERT_ATTEMPTS_BACKEND=sqlite` (default) persists claims/results at `TEST_ALERT_ATTEMPTS_PATH` (default `logs/test_alert_attempts.sqlite3`), requiring a shared persistent filesystem. For separate/ephemeral disks explicitly select `postgres` and set the dedicated secret `TEST_ALERT_ATTEMPTS_DATABASE_URL` to the same durable PostgreSQL database on every instance. Unrelated `DATABASE_URL` never switches backends. No automatic SQLite/application-data migration, PostgreSQL startup DDL, or local fallback occurs. Completed results are retained for 24 hours from completion, then pruned on the next keyed request. Pending/uncertain attempts never expire or resend automatically, even after a crash between delivery and recording success. Retrying these returns 409 with an inbox-check warning. The 1,024-active-attempt limit is atomic across instances; full/unavailable/missing-schema storage fails closed. Invalid configuration fails startup. Keyless callers retain their original non-idempotent behavior.
- **Manual test-email review**: In `/admin`, open **Review unresolved test emails** and refresh using `ADMIN_CRAWL_TOKEN`. Check the recipient's inbox, choose **Email found**, **Email not found**, or **Still uncertain**, then explicitly confirm **Resolve / archive**. Archiving never sends or cancels email; a pending send may still finish. The key and review outcome are retained permanently, including after restart and result expiry, but archived attempts no longer count against active capacity. **Send test email** retries the same key after an uncertain response; **Start a new attempt** deliberately uses a fresh key only after a duplicate-delivery warning. Every fresh attempt asks for confirmation. Review and resolution use the same Bearer authentication as test delivery: `GET /admin/alerts/test/attempts` and `POST /admin/alerts/test/attempts/{key}/resolve` with JSON `{"inbox_checked": true, "resolution": "delivered" | "not_found" | "unknown"}`. Resolution repeats return the original outcome rather than overwriting it. Archived keys always return 409 on send retries. Permanent protection records accumulate on disk and must not be pruned to reclaim capacity.

### Shared test-email backend setup / migration
- Choose operator-managed durable PostgreSQL, TLS for remote connections, durable logged tables and `synchronous_commit=on`. Apply `scripts/test_alert_attempts_schema.sql` explicitly using the database administrator's tools before activation; runtime needs SELECT/INSERT/UPDATE/DELETE only. For Replit-managed PostgreSQL use development schema tools and Publish's production schema flow instead of production/startup DDL.
- Disable test sends, stop **all** old senders and drain in-flight sends. Back up every old SQLite file using SQLite backup tools (including WAL data). Configure the dedicated target URL secret, then run `python -m scripts.import_test_alert_attempts --source /path/to/old.sqlite3 --confirm-all-senders-stopped` from the project root for **each** old disk. This never delivers email or modifies source data. Pending/uncertain claims and permanent archives are preserved; completed results keep their completion time.
- Imports are transactional per source and repeatable for identical records. Conflicting keys roll back the whole source import without overwriting protection; reconcile offline, never delete unresolved/archived keys to force migration. Verify target states/counts before selecting `postgres` and restarting **every** replica. Never use mixed backends or revert to stale SQLite.
- Missing/lost ephemeral sources or restoring stale PostgreSQL backups require recovery/reconciliation while sends remain disabled. A fresh store does not establish that old sends never happened. See README's **Shared PostgreSQL setup and safe cutover** for details.
- `python -m pytest tests/test_postgres_test_alert_attempts.py` starts a disposable local PostgreSQL cluster (requires `initdb`/`pg_ctl`), uses independent processes/private disks, mocks every email, and never connects to the application database.

## Testing Architecture
- **Test Coverage**: Comprehensive tests for endpoints, guardrails, and static assets.
- **Policy Tests**: Parametrized tests for safety guardrail functionality.
- **Smoke Tests**: Basic functionality verification.
- **Static Asset Tests**: Verification of CSS, JavaScript, and HTML loading.

# External Dependencies

## Core Dependencies
- **FastAPI**: Web framework.
- **Uvicorn**: ASGI server.
- **Pydantic**: Data validation and API modeling.
- **pytz**: Timezone handling.

## Content Processing & RAG Implementation
- **ChromaDB**: Vector database for semantic search.
- **SentenceTransformers**: Text embeddings.
- **Groq**: Open-source LLM API client for response generation.
- **Trafilatura**: Web content extraction.
- **BeautifulSoup4**: HTML parsing.
- **lxml**: XML/HTML processing.
- **python-multipart**: Form data handling.