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

Successful diagnostic writes do not establish that earlier missing history was
repaired. Retain completeness warnings rather than clearing them on recovery;
an unknown reserved outcome alone is not proof of a recording gap.

**Why:** A later attempt can replace the latest send metadata while an older
missing row or terminal outcome remains invisible. An interrupted send and a
failed history update are different problems and require different staff advice.

**How to apply:** Compare available diagnostic evidence conservatively, never
reconstruct transport outcomes, and disclose when all persistent evidence could
not be saved. Acknowledgement must remain separate from send claims.

Acknowledgement means staff investigated a particular historical warning, not
that missing diagnostics were recovered or delivery was confirmed. Never let it
cover a later failure, even when that failure has the same attempt timestamp
and outcome.

**Why:** A stale dashboard and a repeating failed update can otherwise make a
new recording gap appear reviewed. Shared admin-token authentication also
cannot prove which individual staff member acted.

**How to apply:** Scope acknowledgements to the reviewed failure generation,
reject stale actions, and retain independent corruption and unsaved-marker
warnings. Describe the audit actor as an authenticated admin, not a named user.

Long-term diagnostic review and export must not hold the delivery lock while
scanning retained history.

**Why:** Unlike bounded active diagnostics, permanent review archives grow
indefinitely; reading them under the sender's lock can delay or suppress alert
attempts even though staff are only downloading an audit.

**How to apply:** Keep archival publication short and crash-safe, and use
read-only snapshots for exports. Validate the complete download before returning
success; never offer a partial audit as if it were complete.
