"""Read-only filesystem and operator quota hints; never email eligibility."""
import os
import re
from datetime import datetime, timezone

LOW_FREE_BYTES = 1024 ** 3
LOW_FREE_FILES = 1000
LOW_FREE_PERCENT = 10
QUOTA_MAX_AGE_SECONDS = 24 * 60 * 60


def _space(value):
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.1f} GiB"
    return f"{value / 1024 ** 2:.1f} MiB"


def _quota_bytes(name):
    # Bound parsing and formatting; never echo operator input or parse errors.
    raw = os.environ.get(name, "")
    if not re.fullmatch(r"[0-9]{1,20}", raw):
        raise ValueError("Quota bytes unavailable")
    return int(raw)


def render_quota(*, now=None):
    """Operator attestation for the quota covering this archive, not a disk scan."""
    explanation = (
        "This is separate from local filesystem capacity. A hosting-plan or account "
        "quota can block writes even when the filesystem has free space. "
        "Operator figures must cover the quota containing these reviews and all "
        "storage charged against it, not just the archive. "
        "Measurements expire after 24 hours; this check does not verify provider figures."
    )
    limit_text = ""
    try:
        limit = _quota_bytes("BACKUP_STORAGE_QUOTA_LIMIT_BYTES")
        if limit <= 0:
            raise ValueError("Invalid quota limit")
        limit_text = f" Known operator-configured limit: {_space(limit)}."
        used = _quota_bytes("BACKUP_STORAGE_QUOTA_USED_BYTES")
        raw_time = os.environ.get("BACKUP_STORAGE_QUOTA_MEASURED_AT", "")
        if len(raw_time) > 64:
            raise ValueError("Invalid quota timestamp")
        measured = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if measured.tzinfo is None:
            raise ValueError("Quota timestamp needs timezone")
        measured = measured.astimezone(timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - measured).total_seconds()
        if not 0 <= age <= QUOTA_MAX_AGE_SECONDS:
            raise ValueError("Quota measurement not current")
    except (ValueError, OverflowError):
        return (
            "<p role='alert'><strong>Backup review storage quota unavailable.</strong>"
            f"{limit_text} Quota headroom cannot be established; this does not mean "
            "storage is healthy. Ask a maintainer to configure a known quota limit, "
            "total charged usage and a current timezone-aware measurement time "
            f"from the hosting provider. {explanation}</p>"
        )

    remaining = max(0, limit - used)
    summary = (
        "<p>Backup review storage quota (operator-configured): "
        f"{_space(used)} used of {_space(limit)}; {_space(remaining)} remaining "
        f"({remaining / limit * 100:.1f}% headroom). "
        f"Measured at {measured.isoformat()} (UTC). {explanation} "
        "Warning thresholds: 10% or less headroom or 1 GiB or less remaining.</p>"
    )
    if remaining <= LOW_FREE_BYTES or remaining * 100 <= limit * LOW_FREE_PERCENT:
        state = "reached or exceeded" if used >= limit else "nearly full"
        summary += (
            f"<p role='alert'><strong>Backup review storage quota is {state}.</strong> "
            "The quota may limit saving new reviews and backup email tracking "
            "regardless of filesystem free space. Use the authenticated download "
            "below, when available, to preserve all retained reviews; verify the "
            "download and keep a secure copy outside this quota. Downloading does "
            "not free space. Ask a maintainer to expand the applicable quota or "
            "safely free unrelated storage charged against it. Do not delete audit "
            "history or email send-protection files to reclaim space. No records "
            "are automatically deleted and this check sends no email.</p>"
        )
    return summary


def render_capacity(directory):
    """Show independent filesystem and quota snapshots, even if either fails."""
    return _render_filesystem(directory) + render_quota()


def _render_filesystem(directory):
    """Probe the archive's filesystem, or its parent before the archive exists.

    Do not create storage, scan records, acquire sender locks or climb to an
    unrelated ancestor when the configured storage parent is missing.
    """
    try:
        try:
            capacity = os.statvfs(directory)
        except FileNotFoundError:
            capacity = os.statvfs(directory.parent)
        total = capacity.f_blocks * capacity.f_frsize
        free = capacity.f_bavail * capacity.f_frsize
        if total <= 0 or free < 0 or free > total or capacity.f_frsize <= 0:
            raise ValueError("Invalid capacity")
        percent = free / total * 100
        low = free <= LOW_FREE_BYTES or free * 100 <= total * LOW_FREE_PERCENT
        details = f"{_space(free)} available of {_space(total)} ({percent:.1f}% free)"
        # Some filesystems do not report a finite inode/file limit.
        if capacity.f_files < 0 or capacity.f_favail < 0:
            raise ValueError("Invalid file capacity")
        if capacity.f_files:
            if capacity.f_favail > capacity.f_files:
                raise ValueError("Invalid file capacity")
            low = low or capacity.f_favail <= LOW_FREE_FILES or (
                capacity.f_favail * 100 <= capacity.f_files * LOW_FREE_PERCENT
            )
            details += f"; {capacity.f_favail:,} of {capacity.f_files:,} file slots available"
    except (OSError, ValueError, OverflowError, AttributeError):
        return (
            "<p role='alert'><strong>Backup review storage capacity unavailable.</strong> "
            "Free space could not be checked; this does not mean storage is healthy. "
            "Ask a maintainer to check shared diagnostic storage privately.</p>"
        )

    summary = (
        f"<p>Backup review storage capacity: {details}. "
        "This is a read-only snapshot of the local filesystem containing the retained "
        "reviews, not the archive size, an account or hosting-plan quota, or a "
        "guarantee of shared-storage health. "
        "Warning thresholds: 10% or less free space, 1 GiB or less available, "
        "or 10% or less / 1,000 or fewer available file slots when reported.</p>"
    )
    if low:
        summary += (
            "<p role='alert'><strong>Backup review storage is nearly full.</strong> "
            "New reviews and backup email tracking may fail to save. "
            "Use the authenticated download below, when available, to preserve all "
            "retained reviews; verify the downloaded records and keep a secure copy "
            "outside this filesystem. Downloading does not free space. Ask a maintainer "
            "to expand persistent shared storage or safely free unrelated files. "
            "Do not delete audit history or email send-protection files to reclaim space. "
            "No records are automatically deleted and this check sends no email.</p>"
        )
    return summary
