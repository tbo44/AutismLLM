"""Immutable acknowledgement archives, independent of email send claims."""
import json
import os
import tempfile

from app import github_backup_history_health as health

RECORD_BYTES = 1024


def archive_path(path):
    active = health._ack_path(path)
    return active.with_name(active.name + ".archive")


def _read_record(file):
    with file.open(encoding="utf-8") as handle:
        content = handle.read(RECORD_BYTES + 1)
    if len(content) > RECORD_BYTES:
        raise ValueError("Archived acknowledgement too large")
    raw = json.loads(content)
    if raw["version"] != 1:
        raise ValueError("Invalid acknowledgement archive")
    record = health._ack_record(raw["acknowledgement"])
    if file.name != record["warning_id"] + ".json":
        raise ValueError("Archive identifier mismatch")
    return record


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_record(path, record):
    """Called under the existing acknowledgement lock; never replace a file."""
    record = health._ack_record(record)
    directory = archive_path(path)
    directory.mkdir(mode=0o700, exist_ok=True)
    _sync_directory(directory.parent)
    target = directory / (record["warning_id"] + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "acknowledgement": record}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Atomic no-replace publication: even damaged existing files remain.
            os.link(temporary, target)
        except FileExistsError:
            if _read_record(target) != record:
                raise ValueError("Conflicting archived acknowledgement")
        _sync_directory(directory)
    finally:
        os.unlink(temporary)


def archived_records(path):
    """Missing archive means no retained older rows; corruption fails explicitly."""
    directory = archive_path(path)
    try:
        files = sorted(directory.iterdir())
    except FileNotFoundError:
        return
    for file in files:
        # Temporary files are unpublished; crashes cannot make them audit rows.
        if file.name.startswith(".pending-"):
            continue
        yield _read_record(file)


def export_snapshot(path):
    """Validated JSONL snapshot, spooled to disk rather than held in memory.

    Read active first: a concurrent rotation publishes archives before replacing
    active rows. Overlap is deduplicated without holding the sender's lock.
    Validate the entire download before returning it, never a partial success.
    """
    snapshot = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        active = health._read_acknowledgements(path)
        active_ids = {row["warning_id"] for row in active}
        for row in archived_records(path):
            if row["warning_id"] not in active_ids:
                snapshot.write((json.dumps(row) + "\n").encode("utf-8"))
        for row in active:
            snapshot.write((json.dumps(row) + "\n").encode("utf-8"))
        snapshot.seek(0)
        return snapshot
    except BaseException:
        snapshot.close()
        raise


def download_chunks(snapshot):
    try:
        while chunk := snapshot.read(65536):
            yield chunk
    finally:
        snapshot.close()


def render_retention(path):
    explanation = (
        f"<p>Acknowledgement retention: latest {health.ACK_LIMIT} reviews in this panel. "
        "Older reviews are archived locally with no automatic expiry from the time "
        "archiving was introduced; previously discarded reviews cannot be recovered. "
        "Preserve the active audit and archive directory on persistent shared storage. "
        "The actor means an authenticated admin, not an identified individual.</p>"
    )
    try:
        count = sum(1 for _ in archived_records(path))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return explanation + (
            "<p>Acknowledgement archive unavailable or corrupt. Export is unavailable; "
            "ask a maintainer to check diagnostic storage privately. No archive was replaced.</p>"
        )
    return explanation + (
        f"<p>{count} older reviews archived. "
        "<a href='/admin/backup/history/acknowledgements/export'>"
        "Download all retained acknowledgement reviews (JSONL)</a> "
        "(requires admin sign-in; read-only, no email sent).</p>"
    )
