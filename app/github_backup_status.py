"""Local, credential-free backup receipts. Reading never contacts GitHub."""
import fcntl
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parents[1] / "logs" / "github_backup_status.json"
STALE_SECONDS = 15 * 60
THROTTLE_SECONDS = 24 * 60 * 60
NOTIFICATION_HISTORY_LIMIT = 100
NOTIFICATION_HISTORY_BYTES = 32768
NOTIFICATION_OUTCOMES = {
    "reserved": "Attempt reserved; delivery is unknown (it may still be in progress or was interrupted).",
    "sent": "Dispatched to the mail service; inbox delivery is not confirmed.",
    "delivery_uncertain": "Delivery failed or is uncertain; the message may have been accepted.",
}
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


def read_alert_state(path):
    """Read only safe claim fields, including legacy claims without outcomes.

    Missing state is distinct from corrupt state; callers must never reset an
    unreadable claim. Outcome metadata cannot change the reservation.
    """
    try:
        with Path(path).open() as handle:
            raw = json.loads(handle.read(4097))
    except FileNotFoundError:
        return {"version": 1, "incident": False, "last_attempt": None}
    if raw["version"] != 1 or type(raw["incident"]) is not bool:
        raise ValueError("Invalid backup alert state")
    stamp = raw["last_attempt"]
    if stamp is not None:
        stamp = _timestamp(stamp).isoformat()
    if raw["incident"] and stamp is None:
        raise ValueError("Missing backup alert claim")
    state = {"version": 1, "incident": raw["incident"], "last_attempt": stamp}
    attempt = raw.get("last_notification")
    if attempt is not None:
        if attempt["kind"] not in ("problem", "recovery") or attempt["outcome"] not in NOTIFICATION_OUTCOMES:
            raise ValueError("Invalid backup notification outcome")
        state["last_notification"] = {
            "at": _timestamp(attempt["at"]).isoformat(),
            "kind": attempt["kind"], "outcome": attempt["outcome"],
        }
    return state


def notification_summary(path=STATUS_PATH, now=None):
    """Credential-free, read-only snapshot; no mail, locking or writes."""
    from app import notifications

    now = now or datetime.now(timezone.utc)
    summary = {
        "configured": notifications.is_configured(), "tracking": "available",
        "last_notification": None, "throttled_until": None,
    }
    try:
        state = read_alert_state(Path(path).with_name(Path(path).name + ".alerts.json"))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        summary["tracking"] = "unavailable"
        return summary
    summary["last_notification"] = state.get("last_notification")
    if summary["last_notification"] is None and state["last_attempt"] is not None:
        summary["last_notification"] = {
            "at": state["last_attempt"], "kind": "problem", "outcome": "reserved",
        }
    if state["incident"] and state["last_attempt"]:
        try:
            until = _timestamp(state["last_attempt"]) + timedelta(seconds=THROTTLE_SECONDS)
        except OverflowError:
            summary["tracking"] = "unavailable"
            return summary
        # Recovery is exempt from the problem-alert throttle.
        if now < until and backup_health(path, now) != "healthy":
            summary["throttled_until"] = until.isoformat()
    return summary


def _notification_attempt(raw):
    """Project fixed codes and a normalized timestamp, never arbitrary text."""
    if raw["kind"] not in ("problem", "recovery") or raw["outcome"] not in NOTIFICATION_OUTCOMES:
        raise ValueError("Invalid backup notification history")
    return {
        "at": _timestamp(raw["at"]).isoformat(),
        "kind": raw["kind"], "outcome": raw["outcome"],
    }


def _history_path(path):
    path = Path(path)
    return path.with_name(path.name + ".alerts.history.json")


def read_notification_history(path=STATUS_PATH):
    """Read a bounded allowlist without locks, writes, claims or delivery."""
    try:
        with _history_path(path).open() as handle:
            content = handle.read(NOTIFICATION_HISTORY_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(content) > NOTIFICATION_HISTORY_BYTES:
        raise ValueError("Backup notification history too large")
    raw = json.loads(content)
    if raw["version"] != 1 or not isinstance(raw["attempts"], list):
        raise ValueError("Invalid backup notification history")
    if len(raw["attempts"]) > NOTIFICATION_HISTORY_LIMIT:
        raise ValueError("Backup notification history too long")
    return [_notification_attempt(attempt) for attempt in raw["attempts"]]


def record_notification_history(path, attempt, *, wait_for_lock=True):
    """Rotate diagnostic history independently of permanent send reservations.

    Writers share a separate persistent lock; readers see atomic snapshots.
    A terminal outcome replaces its reserved row, so crashes remain unknown.
    Corrupt/unwritable history is never silently replaced.
    """
    attempt = _notification_attempt(attempt)
    history_path = _history_path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.with_suffix(history_path.suffix + ".lock").open("a") as lock:
        flags = fcntl.LOCK_EX if wait_for_lock else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock, flags)
        attempts = read_notification_history(path)
        for index in range(len(attempts) - 1, -1, -1):
            previous = attempts[index]
            if (attempt["outcome"] != "reserved" and previous["outcome"] == "reserved"
                    and previous["at"] == attempt["at"] and previous["kind"] == attempt["kind"]):
                attempts[index] = attempt
                break
        else:
            attempts.append(attempt)
        atomic_write_json(history_path, {
            "version": 1, "attempts": attempts[-NOTIFICATION_HISTORY_LIMIT:],
        })


def render_notification_history(path=STATUS_PATH):
    from app.github_backup_history_health import render_completeness

    result = (
        "<section id='githubBackupNotificationHistory'><h2>Backup warning email history</h2>"
        f"<p>Last {NOTIFICATION_HISTORY_LIMIT} reserved backup email attempts, newest first. "
        "Older rows are removed automatically; send reservations are not removed. "
        "History is local and separate from test-email reviews. Reload to refresh. "
        "Viewing history never sends email. History failures do not disable duplicate protection.</p>"
    )
    try:
        attempts = read_notification_history(path)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return result + (
            "<p><strong>Backup email history unavailable.</strong> Ask a maintainer to check "
            "history storage privately. Do not delete send tracking to force a resend.</p>"
        ) + render_completeness(path, []) + "</section>"
    result += render_completeness(path, attempts)
    if not attempts:
        return result + "<p>No backup email history recorded yet. Earlier attempts are not reconstructed.</p></section>"
    result += "<table><thead><tr><th>Attempt (UTC)</th><th>Notice</th><th>Outcome</th></tr></thead><tbody>"
    for attempt in reversed(attempts):
        result += (
            f"<tr><td>{escape(attempt['at'])}</td><td>{attempt['kind']}</td>"
            f"<td>{NOTIFICATION_OUTCOMES[attempt['outcome']]}</td></tr>"
        )
    return result + "</tbody></table></section>"


def render_notification_status(path=STATUS_PATH, now=None):
    summary = notification_summary(path, now)
    result = "<section id='githubBackupNotifications'><h2>Backup warning emails</h2>"
    if not summary["configured"]:
        result += "<p><strong>Disabled — no staff email destination is configured.</strong> No new backup emails will be attempted.</p>"
    else:
        result += "<p>Enabled — uses the staff email configuration. This does not confirm that the mail service is reachable.</p>"
    if summary["tracking"] == "unavailable":
        result += "<p><strong>Notification tracking unavailable.</strong> Attempts and delivery cannot be read safely. Sending fails closed when tracking cannot be read or written; ask a maintainer to check storage privately.</p>"
    attempt = summary["last_notification"]
    if attempt:
        result += (
            f"<p>Last reserved attempt (UTC): {escape(attempt['at'])} "
            f"— {attempt['kind']} notice.</p>"
            f"<p><strong>{NOTIFICATION_OUTCOMES[attempt['outcome']]}</strong></p>"
        )
    elif summary["tracking"] == "available":
        result += "<p>No reserved backup email attempt is recorded here.</p>"
    if summary["throttled_until"]:
        result += (
            "<p><strong>Problem reminders throttled</strong> until (UTC): "
            f"{escape(summary['throttled_until'])}. Recovery can still be attempted once.</p>"
        )
    result += (
        "<p>Problem attempts are limited to once per 24 hours; recovery is attempted once per incident. "
        "Failed, interrupted or uncertain sends keep their reservations. Do not delete tracking to force a resend.</p>"
        "<p>Local tracking only, separate from re-index email status. Reload to refresh; viewing this page never sends email.</p></section>"
    )
    return result


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
    atomic_write_json(path, record)


def atomic_write_json(path, record):
    """Durably replace local runtime state with owner-only permissions."""
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


def backup_health(path=STATUS_PATH, now=None):
    """Return only fixed health codes; never return arbitrary receipt contents."""
    try:
        record = read_record(path)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, KeyError, TypeError):
        return "unavailable"
    if record["outcome"] == "failure":
        return record["last_failure"]["category"]
    success = record["last_success"]
    age = ((now or datetime.now(timezone.utc)) - _timestamp(success["at"])).total_seconds()
    return "healthy" if 0 <= age <= STALE_SECONDS else "stale"


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
