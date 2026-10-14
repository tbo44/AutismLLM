"""Read-only filesystem capacity hints; never involved in email eligibility."""
import os

LOW_FREE_BYTES = 1024 ** 3
LOW_FREE_FILES = 1000
LOW_FREE_PERCENT = 10


def _space(value):
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.1f} GiB"
    return f"{value / 1024 ** 2:.1f} MiB"


def render_capacity(directory):
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
        "reviews, not the archive size or a guarantee of shared-storage health. "
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
