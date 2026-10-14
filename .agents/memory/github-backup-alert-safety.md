---
name: GitHub backup alert safety
description: Delivery uncertainty and shared-disk constraints for backup notifications
---
Backup notifications favor duplicate prevention over guaranteed delivery:
reserve an attempt durably before mail transport, including recovery.

**Why:** A transport timeout or crash can occur after email acceptance. Retrying
immediately can duplicate messages, and concurrent server/watcher checks must
not each notify staff about the same incident.

**How to apply:** Preserve claims on uncertain delivery and across restarts.
Do not silently recreate corrupt tracking or reset claims to force a resend.
The server monitor and sync sender must share persistent receipt/claim storage;
an unrelated server disk cannot establish workspace backup health. Independent
monitoring is needed to detect a stopped watcher; neither local process can
report an outage if both are stopped.

Diagnostic history is best-effort, not a source of send eligibility. Keep its
failures and lock contention independent of pre-send protection, and never
reconstruct or clear reservations from rotated or missing history.

**Why:** Losing diagnostics must not suppress warnings or cause duplicate mail;
a history lock held by another process must not stall a reserved delivery.
