"""Local cross-process backup alert claims, independent of re-index alerts.

Claims are durable BEFORE delivery. A failed/ambiguous problem send is retried
only after 24 hours; recovery is attempted once. This deliberately favors
duplicate prevention over guaranteed email delivery across process crashes.
Every sender/monitor must share the receipt and claim filesystem.
"""
import asyncio
import fcntl
import logging
from datetime import datetime, timezone
from pathlib import Path

from app import notifications
from app.github_backup_status import (
    STATUS_PATH, STALE_SECONDS, THROTTLE_SECONDS, atomic_write_json,
    backup_health, _timestamp, read_alert_state, record_notification_history,
)

logger = logging.getLogger(__name__)
POLL_SECONDS = 60


_read_state = read_alert_state


def _record_history(path, attempt):
    try:
        # A stuck history writer must not delay a reserved email delivery.
        record_notification_history(path, attempt, wait_for_lock=False)
    except Exception:
        # Never leak exception text or let diagnostic storage affect claims.
        logger.warning("GitHub backup email history unavailable; check history storage privately.")


def check_backup_alerts(path=None, *, now=None, missing_ready=True):
    """Evaluate latest receipt under an exclusive, non-blocking delivery lock.

    Missing receipts get a server-startup grace period. Unreadable claim storage
    fails closed: never silently discard throttle history and duplicate mail.
    Returns a small outcome code; backup operations never depend on delivery.
    """
    if not notifications.is_configured():
        return "disabled"
    path = Path(path) if path is not None else STATUS_PATH
    state_path = path.with_name(path.name + ".alerts.json")
    lock_path = path.with_name(path.name + ".alerts.lock")
    now = now or datetime.now(timezone.utc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persistent inode: never unlink a lock file used by another process.
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "busy"
            state = _read_state(state_path)
            health = backup_health(path, now)
            if health == "missing" and not missing_ready and not state["incident"]:
                return "grace"
            if health == "healthy":
                if not state["incident"]:
                    return "healthy"
                state.update(incident=False, last_attempt=None)
            else:
                last = state["last_attempt"]
                if state["incident"] and last is not None:
                    elapsed = (now - _timestamp(last)).total_seconds()
                    if elapsed < THROTTLE_SECONDS:
                        return "throttled"
                state.update(incident=True, last_attempt=now.isoformat())
            state["last_notification"] = {
                "at": now.isoformat(), "kind": "recovery" if health == "healthy" else "problem",
                "outcome": "reserved",
            }
            atomic_write_json(state_path, state)
            _record_history(path, state["last_notification"])
            # The reserved attempt survives SMTP timeout, process death, restart
            # and concurrent checks. Exceptions must not undo the reservation.
            try:
                outcome = "sent" if notifications.send_github_backup_alert(health) else "delivery_uncertain"
            except Exception:
                outcome = "delivery_uncertain"
            state["last_notification"]["outcome"] = outcome
            _record_history(path, state["last_notification"])
            # If this write fails, the durable reserved/unknown outcome remains.
            # Never undo or recreate a pre-send claim to record a result.
            atomic_write_json(state_path, state)
            return outcome
    except Exception:
        logger.warning("GitHub backup alert check unavailable; check alert tracking storage and delivery privately.")
        return "unavailable"


async def monitor_backup_alerts():
    """Independent server watchdog: detects stopped sync without admin visits."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        await asyncio.to_thread(
            check_backup_alerts,
            missing_ready=loop.time() - started >= STALE_SECONDS,
        )
        await asyncio.sleep(POLL_SECONDS)
