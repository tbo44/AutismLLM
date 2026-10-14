"""Bounded, best-effort diagnostics; never consulted for send eligibility."""
import json
from html import escape
from pathlib import Path

from app import github_backup_status as status

# If even the diagnostic marker cannot be saved, be conservative on this process.
# A single sticky bit is bounded and contains no paths, errors or credentials.
_marker_write_failed = False
MARKER_BYTES = 1024


def _marker_path(path):
    history = status._history_path(path)
    return history.with_name(history.name + ".gaps.json")


def _read_marker(path):
    try:
        with _marker_path(path).open() as handle:
            content = handle.read(MARKER_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(content) > MARKER_BYTES:
        raise ValueError("Backup history completeness marker too large")
    raw = json.loads(content)
    if raw["version"] != 1:
        raise ValueError("Invalid backup history completeness marker")
    return status._notification_attempt(raw["last_failed_update"])


def record_failed_update(path, attempt):
    """Called only under the sender's existing exclusive delivery lock.

    No history lock or new waiting lock: a busy history writer cannot stall mail.
    Keep only the latest failed update, and never erase the warning on success.
    Even marker failure must not escape to delivery or reset a reservation.
    """
    global _marker_write_failed
    try:
        attempt = status._notification_attempt(attempt)
        _read_marker(path)  # Preserve corrupt diagnostics for private repair.
        status.atomic_write_json(_marker_path(path), {
            "version": 1, "last_failed_update": attempt,
        })
    except Exception:
        _marker_write_failed = True


def render_completeness(path, attempts):
    """Read-only comparison of safe diagnostics with the latest durable claim."""
    messages = []
    try:
        marker = _read_marker(path)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        messages.append(
            "History completeness tracking unavailable or corrupt. "
            "Ask a maintainer to check diagnostic storage privately."
        )
    else:
        if marker:
            stage = "reservation row" if marker["outcome"] == "reserved" else "terminal outcome"
            messages.append(
                f"History recording has failed: the latest failed update was a {stage} "
                f"at {escape(marker['at'])}. Earlier rows or outcomes may be missing, "
                "even if later updates succeeded. This warning is retained across history rotation."
            )
    if _marker_write_failed:
        messages.append(
            "A history completeness warning could not be saved in this process. "
            "Completeness cannot be confirmed; this warning may not survive a restart."
        )
    try:
        path = Path(path)
        state = status.read_alert_state(path.with_name(path.name + ".alerts.json"))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        messages.append(
            "Latest send tracking unavailable or corrupt; history completeness cannot be checked. "
            "Do not delete send tracking to force a resend."
        )
    else:
        latest = state.get("last_notification")
        if latest:
            matches = [row for row in attempts
                       if row["at"] == latest["at"] and row["kind"] == latest["kind"]]
            # A claim older than the retained window is not evidence of a gap.
            rotated = (len(attempts) == status.NOTIFICATION_HISTORY_LIMIT
                       and latest["at"] < min(row["at"] for row in attempts))
            if not matches and not rotated:
                messages.append(
                    "History has a missing row for the latest reserved backup email attempt. "
                    "Its absence does not mean no email was sent."
                )
            elif matches and latest["outcome"] != "reserved" and not any(
                row["outcome"] == latest["outcome"] for row in matches
            ):
                messages.append(
                    "History is missing the latest terminal outcome recorded in send tracking. "
                    "The history's unknown outcome is a recording gap, not evidence of an interrupted send."
                )
            elif matches and latest["outcome"] == "reserved" and all(
                row["outcome"] == "reserved" for row in matches
            ):
                messages.append(
                    "The latest attempt has no recorded terminal outcome. "
                    "It may still be in progress or was interrupted; a history gap is not established."
                )
    if not messages:
        messages.append(
            "No history gaps detected in available diagnostics. This is not a guarantee of complete "
            "history: earlier or legacy attempts are not reconstructed."
        )
    return "<div id='githubBackupHistoryCompleteness'>" + "".join(
        f"<p>{message}</p>" for message in messages
    ) + "</div>"
