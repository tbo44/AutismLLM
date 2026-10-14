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

An operator must obtain verified figures for the applicable quota from the
provider's supported reporting process. This app does not query provider APIs
or infer charged usage. Scheduling, source selection, and access to provider
figures belong to an external operator job, not the admin request handler.

Publish **all three fields together**, by writing a temporary file in the
destination directory and replacing the destination with `os.replace`.
Never truncate or overwrite the published file in place. Keep the directory
trusted and arrange permissions so the operator can replace the file and the
admin process can read it. Do not put credentials in the snapshot.

Example publication helper for that external job, after collecting and validating
the complete measurement:

```python
import json
import os
import tempfile
from pathlib import Path

def publish_quota(destination, limit_bytes, used_bytes, measured_at):
    destination = Path(destination)
    payload = json.dumps({
        "limit_bytes": limit_bytes,
        "used_bytes": used_bytes,
        "measured_at": measured_at,
    }).encode("utf-8")
    if len(payload) > 4096:
        raise ValueError("Quota snapshot exceeds 4096 bytes")
    fd, temporary = tempfile.mkstemp(prefix=".quota-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        # mkstemp uses mode 0600; arrange reader ownership/permissions as needed.
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
```

The temporary file and destination must be on the same filesystem. A concurrent
admin read sees one complete old or new inode, not a mixture of measurements.
If publication fails, report that failure through the operator job's monitoring;
do not fabricate measurements. The previous snapshot will eventually expire,
and the panel will show unavailable. Reload the admin panel after publication
to see the new values.

Low headroom (≤10% or ≤1 GiB remaining) warns independently of free disk space.
At or above the limit, the panel explicitly reports quota reached or exceeded.
Authenticated review downloads remain read-only and do not reclaim space.
Verify downloaded records and retain a secure copy outside the applicable quota;
ask a maintainer to expand the quota or safely free unrelated charged storage.
Never delete audit records or email send-protection files to reclaim space.
