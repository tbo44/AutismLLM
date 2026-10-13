"""Local, credential-free backup receipts. Reading never contacts GitHub."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parents[1] / "logs" / "github_backup_status.json"
STALE_SECONDS = 15 * 60
FAILURES = {
    "authorization": "GitHub authorization is unavailable. Ask the workspace owner to check the connection.",
    "conflict": (
        "The histories could not be safely merged. Preserve both histories and unfinished work. "
        "Ask a maintainer to fetch both histories in a separate working checkout, resolve and "
        "commit the merge, then retry the backup. Do not force-push or reset the running workspace. "
        "Nothing was pushed by this failed merge."
    ),
    "remote_changed": "GitHub main changed during backup. Retry when remote updates settle; do not force-push.",
    "verification": "The remote tip could not be verified. A push may have happened; ask a maintainer to check GitHub before retrying.",
    "sync": "Backup did not complete. Ask a maintainer to check the GitHub Sync workflow privately.",
}


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Missing timezone")
    return stamp.astimezone(timezone.utc)


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("Invalid commit")
    return value


def read_record(path=STATUS_PATH):
    """Validate and project an allowlist; never display arbitrary stored text."""
    with Path(path).open() as handle:
        raw = json.loads(handle.read(8193))
    if raw["version"] != 1 or raw["outcome"] not in ("success", "failure"):
        raise ValueError("Invalid receipt")
    record = {"version": 1, "outcome": raw["outcome"], "last_success": None, "last_failure": None}
    success = raw.get("last_success")
    failure = raw.get("last_failure")
    if success is not None:
        record["last_success"] = {
            "at": _timestamp(success["at"]).isoformat(),
            "workspace_commit": _commit(success["workspace_commit"]),
            "remote_tip": _commit(success["remote_tip"]),
        }
    if failure is not None:
        if failure["category"] not in FAILURES:
            raise ValueError("Invalid failure category")
        record["last_failure"] = {
            "at": _timestamp(failure["at"]).isoformat(), "category": failure["category"],
        }
    if record["last_" + raw["outcome"]] is None:
        raise ValueError("Missing outcome receipt")
    return record


def write_result(path, *, workspace_commit=None, remote_tip=None, failure=None, now=None):
    """Atomically retain last success and last failure; no exception text accepted."""
    try:
        record = read_record(path)
    except (OSError, ValueError, KeyError, TypeError):
        record = {"version": 1, "last_success": None, "last_failure": None}
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    if failure is not None:
        if failure not in FAILURES:
            raise ValueError("Invalid failure category")
        record.update(outcome="failure", last_failure={"at": stamp, "category": failure})
    else:
        record.update(outcome="success", last_success={
            "at": stamp, "workspace_commit": _commit(workspace_commit),
            "remote_tip": _commit(remote_tip),
        })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".github-backup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_status(path=STATUS_PATH, now=None):
    """Server-rendered, read-only staff panel; freshness is checked on page load."""
    intro = (
        "<section id='githubBackup'><h2>GitHub backup</h2>"
        "<p>Committed workspace main only; unfinished edits are not backed up. "
        "This is not a deployment. Reload this page to refresh status; "
        "viewing it never pushes, changes the workspace, or renews authorization.</p>"
    )
    try:
        record = read_record(path)
    except FileNotFoundError:
        return intro + "<p><strong>Unknown — no backup receipt yet.</strong> No verified backup is recorded here.</p></section>"
    except (OSError, ValueError, KeyError, TypeError):
        return intro + "<p><strong>Status unavailable.</strong> The receipt cannot be read safely. Ask a maintainer to check status storage.</p></section>"
    now = now or datetime.now(timezone.utc)
    success = record["last_success"]
    age = (now - _timestamp(success["at"])).total_seconds() if success else None
    stale = age is None or age < 0 or age > STALE_SECONDS
    label = "Needs attention — latest backup failed" if record["outcome"] == "failure" else "Verified"
    if stale:
        label += " — stale / no recent verification (15-minute limit)"
    result = intro + f"<p><strong>{label}</strong></p>"
    if success:
        result += (
            f"<p>Last verified success (UTC): {escape(success['at'])}</p>"
            f"<p>Backed-up workspace commit: <code>{success['workspace_commit']}</code><br>"
            f"Verified combined remote tip: <code>{success['remote_tip']}</code></p>"
            "<p>The remote tip can differ because it also preserves GitHub-only commits.</p>"
        )
    else:
        result += "<p>Last verified success: none recorded.</p>"
    if record["last_failure"]:
        failure = record["last_failure"]
        historical = " (recovered by a later verified backup)" if record["outcome"] == "success" else ""
        result += (
            f"<p>Last failure (UTC): {escape(failure['at'])}{historical}</p>"
            f"<p>{FAILURES[failure['category']]}</p>"
        )
    if stale:
        result += "<p>Check that the GitHub Sync workflow is running. Old receipts do not prove a current backup.</p>"
    return result + "</section>"
