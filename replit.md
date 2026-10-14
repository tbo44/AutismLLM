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
- **GitHub backup status**: The read-only staff panel reads `logs/github_backup_status.json`, atomically written by the sync script after verified success or failure. It retains both last-success and last-failure timestamps in UTC, the backed-up workspace main commit, and the verified combined remote tip (which may include GitHub-only history). No tokens, raw errors, command environments, or conflict filenames are stored. Verification older than 15 minutes is stale; the watcher normally verifies at least every 5 minutes. Reload the dashboard to refresh; viewing never pushes, changes the checkout, or renews OAuth. Missing/unreadable receipts are explicitly unknown/unavailable. Receipts are local runtime data excluded from Git, not a backup of unfinished edits or evidence of deployment. A separate/ephemeral server disk cannot see the workspace watcher's status; preserve/share the receipt filesystem if status must survive disk replacement. Resolve conflicts in a separate working checkout preserving both histories; never force-push or reset the running workspace.

## Failure Email Alerts
- **Backup-warning review retention**: The panel shows the latest 100 acknowledgement reviews. Before rotation, older reviews are retained without automatic expiry in the owner-only receipt-adjacent `.alerts.history.json.gaps.json.acknowledgements.json.archive` directory, one immutable JSON file per warning. Archives contain only warning identifiers, normalized failed-update timestamps/kinds/outcomes, acknowledgement timestamps, the `authenticated_admin` actor (not a named individual), and `missing_history_recovered: false`. The panel links to the read-only JSONL export at `/admin/backup/history/acknowledgements/export`, authenticated by dashboard session or `X-Admin-Token` (no query credentials). Export includes active and archived reviews, deduplicates interrupted rotation overlap, validates all records before downloading and fails safely on corrupt storage without overwriting it. There is no automatic deletion; preserve both active audit and archive on persistent shared storage. Previously discarded reviews cannot be recovered. Export does not imply that missing history was restored or email delivered, and never changes send claims.
- **Backup history completeness**: The authenticated history panel retains a credential-free warning when history recording fails, including after later successful updates or row rotation. A separate owner-only `.alerts.history.json.gaps.json` marker stores just the latest failed update's normalized timestamp, notice kind and outcome; send eligibility never reads it. The panel also compares the latest send record with retained rows to distinguish a missing row/terminal outcome from a still-reserved, possibly interrupted send. Corrupt/unreadable diagnostics remain explicit and are never silently recreated. If the marker itself cannot be saved, a bounded process-local warning says completeness cannot be confirmed and may not survive restart. No local diagnostic mechanism can recover older gaps after all evidence is lost; absence of warnings is not proof of complete history. Viewing performs no writes, deliveries or reconstruction.
- **Backup email history**: The authenticated `/admin` dashboard separately lists the newest 100 backup notice attempts, newest first, using only normalized UTC timestamps, `problem`/`recovery` kinds and `reserved`/`sent`/`delivery_uncertain` outcomes. Owner-only `logs/github_backup_status.json.alerts.history.json` is rotated atomically under its own cross-process lock; no recipients, transport errors or credentials are retained. Reserved rows are updated after delivery; interrupted attempts stay unknown. History is best-effort: corruption, write failure or lock contention never resets claims or blocks delivery, and may leave missing/unknown rows. Missing history is explicitly empty and corrupt/unreadable history is explicitly unavailable. Old attempts are not reconstructed. This is separate from manual test-email reviews; viewing it sends nothing and offers no resend/reset/delete action. Keep sharing the receipt/claim filesystem across senders; rotating history never rotates duplicate protection.
- **Backup email visibility**: `/admin` shows a separate, read-only backup-warning email summary: disabled/enabled configuration, last reserved problem or recovery attempt, problem-reminder throttle expiry, and safe delivery outcomes. A dispatched message is not proof of inbox delivery; reserved/unknown and failed/uncertain attempts retain duplicate protection. Old claims without outcomes remain unknown, never treated as successful. Reload to refresh. Viewing never sends mail, exposes destinations or raw errors, or offers a resend/reset action. Outcome-write failures leave the pre-send reservation intact.
- **GitHub backup emails**: The same `REINDEX_ALERT_EMAIL_TO` destination and SMTP/Replit mail transport also receive backup problem and recovery notices. Backup tracking is independent of re-index throttling: one problem attempt immediately, then at most once every 24 hours across failure-category changes and restarts. Only a fresh verified successful receipt (at most 15 minutes old, not future-dated) clears the incident, attempts one recovery notice, and allows the next problem to alert immediately. A push without verification never triggers recovery. Messages use fixed safe descriptions, never raw errors, command output, credentials, or environment details; authorization alerts do not renew OAuth.
- **Independent backup watchdog**: The server checks local receipts every 60 seconds without requiring `/admin` visits or a running sync watcher. Existing failed/stale/unreadable receipts alert immediately; a missing receipt has a 15-minute server-startup grace period unless an incident is already open. Set `GITHUB_BACKUP_MONITOR_ENABLED=false` on servers that do not share the workspace receipt disk to avoid misleading local-missing alerts. This switch only disables the server watchdog, not the sync script's result-triggered alerts. Detection requires the server to remain running; if both server and sync stop, no local process can notify staff.
- **Backup duplicate protection**: The receipt-adjacent `.alerts.json` and `.alerts.lock` files are local runtime data excluded from Git. All senders must share these files on a persistent filesystem; preserve them with receipts across restarts/disk changes. Cross-process locking and durable pre-send reservations suppress duplicate attempts, including after a delivery timeout or process crash. Problem delivery failures remain throttled for 24 hours; recovery is attempted only once, even if delivery is uncertain. Delivery is not guaranteed, so check inboxes and `/admin` when diagnosing trouble. Corrupt/unwritable alert tracking fails closed (no email), with a generic warning in logs; restore intact tracking rather than deleting claims to force a resend. The watchdog does not sync, push, or renew authorization.
- When a re-index (nightly or manual) fails, Maya emails staff with the error detail so a stale knowledge base doesn't go unnoticed.
- **REINDEX_ALERT_EMAIL_TO**: comma-separated staff addresses (needs `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`), or the special value `replit` to use Replit's built-in mailer (sends to the Repl owner, no SMTP setup). Unset = alerts off (no-op).
- **REINDEX_ALERT_THROTTLE_HOURS** (default 24): repeated failures send at most one email per window; a successful refresh resets the throttle. Implemented in `app/notifications.py`.
- **Test-email retries**: `TEST_ALERT_ATTEMPTS_BACKEND=sqlite` (default) persists claims/results at `TEST_ALERT_ATTEMPTS_PATH` (default `logs/test_alert_attempts.sqlite3`), requiring a shared persistent filesystem. For separate/ephemeral disks explicitly select `postgres` and set the dedicated secret `TEST_ALERT_ATTEMPTS_DATABASE_URL` to the same durable PostgreSQL database on every instance. Unrelated `DATABASE_URL` never switches backends. No automatic SQLite/application-data migration, PostgreSQL startup DDL, or local fallback occurs. Completed results are retained for 24 hours from completion, then pruned on the next keyed request. Pending/uncertain attempts never expire or resend automatically, even after a crash between delivery and recording success. Retrying these returns 409 with an inbox-check warning. The 1,024-active-attempt limit is atomic across instances; full/unavailable/missing-schema storage fails closed. Invalid configuration fails startup. Keyless callers retain their original non-idempotent behavior.
- **Manual test-email review**: In `/admin`, open **Review unresolved test emails** and refresh using `ADMIN_CRAWL_TOKEN`. Check the recipient's inbox, choose **Email found**, **Email not found**, or **Still uncertain**, then explicitly confirm **Resolve / archive**. Archiving never sends or cancels email; a pending send may still finish. The key and review outcome are retained permanently, including after restart and result expiry, but archived attempts no longer count against active capacity. **Send test email** retries the same key after an uncertain response; **Start a new attempt** deliberately uses a fresh key only after a duplicate-delivery warning. Every fresh attempt asks for confirmation. Review and resolution use the same Bearer authentication as test delivery: `GET /admin/alerts/test/attempts` and `POST /admin/alerts/test/attempts/{key}/resolve` with JSON `{"inbox_checked": true, "resolution": "delivered" | "not_found" | "unknown"}`. Resolution repeats return the original outcome rather than overwriting it. Archived keys always return 409 on send retries. Permanent protection records accumulate on disk and must not be pruned to reclaim capacity.

### Shared test-email backend setup / migration
- **Archived history**: Open **Review archived test-email outcomes** in `/admin` and refresh with `ADMIN_CRAWL_TOKEN`. The read-only table shows original keys, inbox outcomes, and resolution timestamps in UTC, ordered by key, with 25 rows per page. `GET /admin/alerts/test/attempts/history?limit=25&offset=0` uses the same Bearer protection (limit 1–100, non-negative offset) and returns `attempts`, `total`, `limit`, and `offset` for either storage backend. Reviewing history never prunes claims or triggers delivery; no delete or reclaim action is provided. Each page reads current history, so newly archived keys can shift later pages; refresh to return to the start.

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