---
name: Test-email retry scope
description: Durable retry protection must preserve ambiguous delivery claims without automatically resending.
---

Persist ownership before external delivery and never automatically reclaim an
unresolved attempt, even after its normal result-retention window expires.

**Why:** A crash can occur after SMTP accepted the email but before success was
recorded. Restart recovery cannot safely infer that a missing result means the
email was not sent. Ordinary SMTP cannot provide an exactly-once guarantee.

**How to apply:** Preserve the browser's key on uncertain responses, show that
delivery is unconfirmed, and require a deliberate new attempt after checking the
inbox. File-backed coordination only covers workers using the same persistent
filesystem; isolated replicas require a shared backend, not independent copies.
