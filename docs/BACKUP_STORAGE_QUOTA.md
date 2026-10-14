# Updating backup-storage quota measurements

The admin backup-history panel reports filesystem capacity and storage quota
independently. Filesystem free space and archive size do **not** establish an
account or hosting-plan quota. Neither warning changes download authentication,
audit records, email send claims, or email eligibility; these checks send no email.

## Optional reloadable file

Configure `BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH` once on the admin server, then
restart it to pick up that configuration. Prefer an absolute path to an
operator-maintained file readable by the admin process. Relative paths resolve
from the server's working directory. The app never creates or writes this file.
Its location does not prove that any workspace or other server shares storage.

The file is a UTF-8 JSON object, at most 4,096 bytes, for example:

```json
{
  "limit_bytes": 107374182400,
  "used_bytes": 21474836480,
  "measured_at": "2026-10-14T12:00:00+00:00"
}
```

- `limit_bytes`: the positive applicable quota containing the retained reviews.
- `used_bytes`: **all storage charged against that quota**, not just these
  reviews or a scan of the archive. Zero and usage above the limit are valid.
- Both byte fields accept JSON integers or digit-only decimal strings, at most
  20 digits. Booleans, negative values, fractions, and exponent notation are
  not valid byte counts.
- `measured_at`: the time the figures were measured, as a timezone-aware
  ISO-8601 string (at most 64 characters; `Z` is accepted). The actual measurement
  must be no more than 24 hours old and not in the future. Republishing an old
  measurement must **not** give it a new measurement time.

Every admin reload opens and reads a new snapshot. There is no cached measurement
or daily server restart requirement. The configured file is authoritative:
missing, unreadable, corrupt, oversized, non-regular, stale, or future data shows
quota unavailable, never an environment fallback or a healthy result. A valid
limit parsed before a usage or timestamp failure remains visible as a known
limit. Errors and file paths are not displayed to staff. Duplicate JSON keys are
rejected. A failure in either filesystem or quota checks does not suppress the
other warning.

If no path is configured (including an empty path), the existing environment
values remain supported: `BACKUP_STORAGE_QUOTA_LIMIT_BYTES`,
`BACKUP_STORAGE_QUOTA_USED_BYTES`, and `BACKUP_STORAGE_QUOTA_MEASURED_AT`.
Changes to those environment values still require restarting the admin process.

## Publish atomically from an external operator job

**This is a publication runbook, not an installed job.** No measurement source
or schedule is approved by this document. Until an operator approves and deploys
an external collector, measurements will not refresh automatically. This app
does not query provider APIs or infer charged usage.

### Operator approval before activation

Record these decisions in the operator job's configuration/change record:

| Decision | Required confirmation |
| --- | --- |
| Quota scope | Provider/account/plan and the exact quota containing the retained reviews; confirm which storage categories are charged to it. |
| Supported measurement source | Explicitly approve a documented provider report, export, or supported API and its reference. Confirm that it supplies the applicable limit, **all** charged usage, and the actual measurement time for the same scope. |
| Units and completeness | Document any exact conversion to bytes, aggregation across charged categories, and reporting delay. Reject incomplete reports; an absent category or value is not zero. |
| Job ownership | Name the external operator responsible for the collector, access, retries, and incident response. Keep credentials in the job's approved secret store, never the snapshot. |
| Publication target and access | Confirm the admin's configured absolute path, that this job can reach the same persistent filesystem, and the admin-readable group and directory permissions. Separate hosts need an explicitly arranged shared mount or publication on the admin host. |
| Schedule and monitoring | Approve frequency, source latency, timeout, bounded retries, overlap prevention, and the existing operator monitoring destination. Confirm who receives failed/missed-run and stale-measurement alerts. |

Do not use archive scans, filesystem free space, or undocumented provider APIs
as substitutes. A report without a trustworthy measurement timestamp or complete
charged usage is not an approved source. If no supported complete source exists,
leave quota unavailable and ask the operator/provider to resolve the reporting
gap; do not fabricate values.

### Collection and scheduling contract

The external job must collect one coherent measurement from the approved source,
validate and publish it, then report its result through **its existing monitoring**.
The limit and usage must describe the same quota and measurement, not unrelated
cached responses. Keep the original measurement timestamp, including when
republishing or retrying; neither collection nor publication time refreshes it.

Choose an operator-approved cadence with enough margin for source reporting delay,
collection time, retries, and outages before the measurement's 24-hour expiry.
A frequent schedule cannot make a provider's old report fresh. Prevent overlapping
runs (including retries) so an older run cannot replace a newer measurement.
No cron entry, workflow, background task, provider adapter, or monitoring channel
is installed here.

Run collection and publication outside admin request handling and outside the
backup/email watcher. Do not couple them to email delivery, review exports, audit
writes, send claims, or cleanup. The admin remains a read-only consumer.

### Validated publication helper

Publish **all three fields together**, by writing a temporary file in the
destination directory and replacing the destination with `os.replace`.
Never truncate or overwrite the published file in place. Keep the directory
trusted and arrange permissions so the operator can replace the file and the
admin process can read it. Do not put credentials in the snapshot.

The following standalone helper can be copied into the approved external job.
It has no provider or application dependencies. Supply `admin_gid` explicitly
after confirming the admin process belongs to that group. The trusted destination
directory must already exist and be traversable by the admin; its group ownership
does not automatically grant access to a newly created file.

```python
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

def publish_quota(destination, limit_bytes, used_bytes, measured_at, *, admin_gid):
    # Match app/github_backup_capacity.py's byte and freshness contract.
    for value in (limit_bytes, used_bytes):
        text = str(value) if type(value) is int else value
        if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,20}", text):
            raise ValueError("Quota byte counts must be complete decimal values")
    if int(limit_bytes) <= 0:
        raise ValueError("Quota limit must be positive")
    if not isinstance(measured_at, str) or len(measured_at) > 64:
        raise ValueError("Quota measurement timestamp is required")
    measured = datetime.fromisoformat(measured_at.replace("Z", "+00:00"))
    if measured.tzinfo is None:
        raise ValueError("Quota measurement timestamp must include a timezone")
    age = (datetime.now(timezone.utc) - measured.astimezone(timezone.utc)).total_seconds()
    if not 0 <= age <= 24 * 60 * 60:
        raise ValueError("Quota measurement is stale or in the future")
    if type(admin_gid) is not int or admin_gid < 0:
        raise ValueError("An approved admin-readable group ID is required")
    destination = Path(destination)
    if not destination.is_absolute():
        raise ValueError("Use the confirmed absolute snapshot path")
    payload = json.dumps({
        "limit_bytes": limit_bytes,
        "used_bytes": used_bytes,
        # Preserve the source timestamp, not the time this file is published.
        "measured_at": measured_at,
    }, allow_nan=False).encode("utf-8")
    if len(payload) > 4096:
        raise ValueError("Quota snapshot exceeds 4096 bytes")
    fd, temporary = tempfile.mkstemp(prefix=".quota-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            # Fail before replacement if the job cannot grant reader access.
            os.fchown(output.fileno(), -1, admin_gid)
            os.fchmod(output.fileno(), 0o640)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        # Persist the directory entry as well as the file on POSIX filesystems.
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
```

This helper targets a POSIX filesystem supporting file/directory `fsync`, group
ownership, and atomic replacement. Confirm those guarantees on the actual mount.
It intentionally rejects missing/invalid/stale/future values before touching the
published file. Zero usage and usage above the limit are valid. Source approval,
completeness, unit conversions, and quota scope remain the collector's responsibility;
syntactic validation cannot prove them.

The temporary file and destination must be on the same filesystem. A concurrent
admin read sees one complete old or new inode, not a mixture of measurements.
Replacement creates a new inode: do not depend on old file ownership, permissions,
or ACLs surviving. Where additional ACLs or security labels are needed, the operator
must adapt the helper to apply and verify them on the temporary inode **before**
replacement. Never solve reader access by making the file world-readable or the
directory world-writable.

### Failure reporting and activation checks

The job runner must treat collection, parsing, validation, access setup, write,
replacement, and durability errors as failures. Let exceptions reach the runner
and exit unsuccessfully; report through its existing monitoring with a sanitized
stage/reason (no credentials or raw provider responses). Do not catch an error and
report success, write a placeholder, or advance `measured_at`. A failure before
replacement leaves the previous snapshot intact. If directory synchronization
fails after replacement, the new file may already be visible, but durability is
unconfirmed: report publication failure, do not claim success or restore an older
measurement. Clean up this job's temporary files, not audit/claim records.

Record job execution time and source measurement time separately in monitoring.
Monitor failed **and missed** runs and the last successful measurement age; the
admin warning alone cannot detect a stopped job proactively. Route retries and
outage notifications through the operator's existing system, not this app's email
or claim machinery. On unresolved failure, the previous snapshot expires and the
panel shows unavailable instead of healthy.

Before enabling the approved schedule, perform an operator-owned acceptance check:

1. Collect a complete measurement from the approved source; compare its scope,
   units, limit, usage, and timestamp to the source report.
2. Publish to the confirmed path and verify a read **as the actual admin identity**
   (including directory traversal, group membership, ACLs, and security labels).
   A read as the collector does not establish admin access.
3. Reload the authenticated backup-history panel and confirm it shows that
   measurement without restarting the server. Confirm all admin replicas read
   the same intended snapshot.
4. In a separate test destination, reject missing values, malformed counts, stale
   or future timestamps, and simulate collection/write/access/replace failures.
   Confirm unsuccessful job status reaches existing monitoring and that failures
   before replacement preserve the last complete snapshot.
5. Confirm the monitoring system notices a missed scheduled run and an aging
   measurement. Confirm no app email, audit, or send-claim operations occur.
6. Record the approved source, schedule, owner, and acceptance results before
   activation. To pause refresh, disable the external schedule; do not rewrite old
   timestamps. The admin will report unavailable when the last measurement expires.

Low headroom (≤10% or ≤1 GiB remaining) warns independently of free disk space.
At or above the limit, the panel explicitly reports quota reached or exceeded.
Authenticated review downloads remain read-only and do not reclaim space.
Verify downloaded records and retain a secure copy outside the applicable quota;
ask a maintainer to expand the quota or safely free unrelated charged storage.
Never delete audit records or email send-protection files to reclaim space.
