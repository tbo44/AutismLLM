---
name: Test-email retry scope
description: Why test-email retry protection is scoped to a running server and ambiguous browser responses.
---

Test-email retry protection is intentionally lightweight and process-local rather than introducing a new persistent delivery store.

**Why:** The requested failure is a browser response lost after delivery, and the current service runs in one server process. Durable recovery and coordination across multiple workers are separate work, not an exactly-once guarantee that ordinary SMTP can provide.

**How to apply:** Preserve the same attempt when browser delivery confirmation is ambiguous. If adding restarts or multiple workers to the guarantee, use shared durable attempt tracking and explicitly handle uncertain sends rather than assuming it is safe to resend.
