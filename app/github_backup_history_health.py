"""Bounded, best-effort diagnostics; never consulted for send eligibility."""
import fcntl
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from app import github_backup_status as status

# If even the diagnostic marker cannot be saved, be conservative on this process.
# A single sticky bit is bounded and contains no paths, errors or credentials.
_marker_write_failed = False
MARKER_BYTES = 1024
ACK_LIMIT = 100
ACK_BYTES = 32768


class WarningChanged(Exception):
    """The warning shown to staff is no longer the current warning."""


def _ack_path(path):
    marker = _marker_path(path)
    return marker.with_name(marker.name + ".acknowledgements.json")


def _warning_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid warning identifier")
    return value


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
    if raw["version"] not in (1, 2):
        raise ValueError("Invalid backup history completeness marker")
    attempt = status._notification_attempt(raw["last_failed_update"])
    # Legacy warnings can be acknowledged without rewriting their evidence.
    warning_id = (hashlib.sha256(json.dumps(attempt, sort_keys=True).encode()).hexdigest()
                  if raw["version"] == 1 else _warning_id(raw["warning_id"]))
    return {"warning_id": warning_id, "last_failed_update": attempt}


def _read_acknowledgements(path):
    try:
        with _ack_path(path).open() as handle:
            content = handle.read(ACK_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(content) > ACK_BYTES:
        raise ValueError("Acknowledgement history too large")
    raw = json.loads(content)
    if (raw["version"] != 1 or not isinstance(raw["acknowledgements"], list)
            or len(raw["acknowledgements"]) > ACK_LIMIT):
        raise ValueError("Invalid acknowledgement history")
    return [_ack_record(row) for row in raw["acknowledgements"]]


def _ack_record(row):
    """Project only safe fields, including when reading older archives."""
    if row["actor"] != "authenticated_admin" or row["missing_history_recovered"] is not False:
        raise ValueError("Invalid acknowledgement")
    return {
        "warning_id": _warning_id(row["warning_id"]),
        "last_failed_update": status._notification_attempt(row["last_failed_update"]),
        "at": status._timestamp(row["at"]).isoformat(),
        "actor": "authenticated_admin", "missing_history_recovered": False,
    }


def acknowledge_warning(path, warning_id, *, now=None):
    """Audit exactly the reviewed warning; never modify evidence or send state.

    Use the sender's persistent delivery lock for compare-and-write, but never
    wait for it. The sender already holds this lock when recording failures.
    """
    warning_id = _warning_id(warning_id)
    path = Path(path)
    with path.with_name(path.name + ".alerts.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = _read_marker(path)
        if not marker or marker["warning_id"] != warning_id:
            raise WarningChanged()
        records = _read_acknowledgements(path)  # Never recreate corrupt audit.
        existing = next((row for row in records if row["warning_id"] == warning_id), None)
        if existing:
            return existing
        record = {
            **marker, "at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            "actor": "authenticated_admin", "missing_history_recovered": False,
        }
        # Archive first. A failed active replacement leaves overlap, not lost
        # audit rows; the immutable archive and export are retry-safe.
        from app.github_backup_ack_archive import archive_record
        for older in (records + [record])[:-ACK_LIMIT]:
            archive_record(path, older)
        status.atomic_write_json(_ack_path(path), {
            "version": 1, "acknowledgements": (records + [record])[-ACK_LIMIT:],
        })
        return record


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
            "version": 2, "last_failed_update": attempt,
            "warning_id": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        })
    except Exception:
        _marker_write_failed = True


def render_completeness(path, attempts):
    """Read-only comparison of safe diagnostics with the latest durable claim."""
    from app.github_backup_ack_archive import render_retention
    messages = [render_retention(path)]
    try:
        marker = _read_marker(path)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        messages.append(
            "History completeness tracking unavailable or corrupt. "
            "Ask a maintainer to check diagnostic storage privately."
        )
    else:
        if marker:
            attempt = marker["last_failed_update"]
            stage = "reservation row" if attempt["outcome"] == "reserved" else "terminal outcome"
            messages.append(
                f"History recording has failed: the latest failed update was a {stage} "
                f"at {escape(attempt['at'])}. Earlier rows or outcomes may be missing, "
                "even if later updates succeeded. This warning is retained across history rotation."
            )
        try:
            acknowledgements = _read_acknowledgements(path)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            messages.append(
                "Acknowledgement tracking unavailable or corrupt. No warning can be safely "
                "acknowledged here; ask a maintainer to check diagnostic storage privately."
            )
        else:
            current_ack = next((row for row in acknowledgements
                                if marker and row["warning_id"] == marker["warning_id"]), None)
            if current_ack:
                messages.append(
                    f"<strong>Historical warning acknowledged</strong> by an authenticated admin "
                    f"at {escape(current_ack['at'])}. Missing rows or outcomes were not recovered. "
                    "This does not confirm complete history or email delivery."
                )
            elif marker:
                messages.append(
                    "<strong>Unacknowledged history recording warning.</strong> "
                    "Investigate the diagnostic storage before acknowledging."
                    f"<button type='button' id='githubBackupHistoryAck' "
                    f"data-warning-id='{marker['warning_id']}'>Acknowledge investigated warning</button>"
                    "<p id='githubBackupHistoryAckResult' role='status' aria-live='polite'></p>"
                )
            if acknowledgements:
                messages.append(
                    f"<details><summary>Acknowledgement audit (last {ACK_LIMIT})</summary>"
                    + "".join(
                        f"<p>Authenticated admin acknowledged warning <code>{row['warning_id']}</code> "
                        f"at {escape(row['at'])}; failed {row['last_failed_update']['outcome']} update "
                        f"at {escape(row['last_failed_update']['at'])}. Missing history not recovered.</p>"
                        for row in reversed(acknowledgements)
                    ) + "</details>"
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
        f"<div>{message}</div>" for message in messages
    ) + "</div>"
