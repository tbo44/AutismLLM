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

An authentication rejection on a retry is not evidence that the original
delivery completed. Preserve the pending attempt while asking for a new token.

**Why:** The original send may have reached the mail provider before the
connection was lost; rotating a token does not resolve that uncertainty.

**How to apply:** Treat credential cleanup separately from attempt cleanup.
Confirmed results, deliberate new-attempt confirmation, and sign-out are the
boundaries for discarding a pending browser attempt.

Manual inbox review may remove an attempt from active capacity, but it must not
make the original key reusable. A staff resolution is not proof that delivery
stopped: an in-flight send can finish after the review.

**Why:** Bounding active work is a different concern from duplicate-delivery
protection. Expiring an archived key would turn a delayed retry into another send.

**How to apply:** Keep permanent protection records outside the active-attempt
limit, including when moving coordination to a shared backend. Warn staff that
archiving is neither cancellation nor a resend.
