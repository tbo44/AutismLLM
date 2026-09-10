"""
Email notifications for re-index failures.

When the nightly (or manual) re-index fails, staff should hear about it
without having to watch /admin/crawl/status. This module sends an email
with the failure detail, throttled so repeated nightly failures don't spam.

Configuration (all via environment variables):

  REINDEX_ALERT_EMAIL_TO       Who to notify. Either:
                                 - a comma-separated list of email addresses
                                   (requires the SMTP_* settings below), or
                                 - the special value "replit" to send via
                                   Replit's built-in mail service to the
                                   Repl owner's verified email (no SMTP
                                   settings needed; only works on Replit).
                               If unset/empty, notifications are a no-op.

  SMTP_HOST                    SMTP server hostname (required for SMTP mode).
  SMTP_PORT                    SMTP port (default 587).
  SMTP_USERNAME                SMTP username (optional if server allows).
  SMTP_PASSWORD                SMTP password (optional if server allows).
  SMTP_STARTTLS                Use STARTTLS (default true).
  REINDEX_ALERT_EMAIL_FROM     From address (default: SMTP_USERNAME, or
                               "maya-alerts@localhost" as last resort).

  REINDEX_ALERT_THROTTLE_HOURS Minimum hours between failure emails
                               (default 24). A successful re-index resets
                               the throttle so the *next* failure alerts
                               immediately.

The throttle timestamp is persisted to logs/.reindex_alert_last_sent so it
survives restarts.
"""

import json
import logging
import os
import smtplib
import subprocess
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)

_THROTTLE_FILE = Path("logs/.reindex_alert_last_sent")
_lock = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _recipients() -> list[str]:
    raw = _env("REINDEX_ALERT_EMAIL_TO")
    if not raw:
        return []
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def is_configured() -> bool:
    """True if failure notifications are configured at all."""
    return bool(_recipients())


def get_status() -> dict:
    """
    Return a safe summary of alert configuration for display in the admin
    dashboard.  Never exposes SMTP credentials — only the recipient
    address(es) and the last-sent timestamp.

    Keys:
      configured       (bool)        — True if REINDEX_ALERT_EMAIL_TO is set.
      recipient_display (str | None) — Human-readable destination, e.g.
                                       "staff@example.org" or
                                       "Repl owner (Replit mail)".
      last_sent_at     (str | None)  — ISO-8601 UTC timestamp of the most
                                       recent alert, or None if never sent.
      throttled         (bool)        — True while failure alerts are suppressed.
      next_allowed_at   (str | None)  — ISO-8601 UTC timestamp when suppression
                                        ends, or None if not throttled.
    """
    recipients = _recipients()
    configured = bool(recipients)
    if not configured:
        recipient_display = None
    elif recipients == ["replit"]:
        recipient_display = "Repl owner (Replit mail)"
    else:
        recipient_display = ", ".join(recipients)

    last = _last_sent_at()
    next_allowed = None
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        else:
            last = last.astimezone(timezone.utc)
        candidate = last + timedelta(hours=_throttle_hours())
        if datetime.now(timezone.utc) < candidate:
            next_allowed = candidate

    return {
        "configured": configured,
        "recipient_display": recipient_display,
        "last_sent_at": last.isoformat() if last else None,
        "throttled": next_allowed is not None,
        "next_allowed_at": next_allowed.isoformat() if next_allowed else None,
    }


def _throttle_hours() -> float:
    try:
        return max(0.0, float(_env("REINDEX_ALERT_THROTTLE_HOURS", "24")))
    except ValueError:
        return 24.0


def _last_sent_at() -> datetime | None:
    try:
        raw = _THROTTLE_FILE.read_text().strip()
        return datetime.fromisoformat(raw) if raw else None
    except (OSError, ValueError):
        return None


def _record_sent() -> None:
    try:
        _THROTTLE_FILE.parent.mkdir(exist_ok=True)
        _THROTTLE_FILE.write_text(datetime.now(timezone.utc).isoformat())
    except OSError as e:
        logger.warning(f"Could not persist alert throttle timestamp: {e}")


def _is_throttled() -> bool:
    last = _last_sent_at()
    if last is None:
        return False
    elapsed_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return elapsed_hours < _throttle_hours()


def reset_throttle() -> None:
    """Called after a successful re-index so the next failure alerts immediately."""
    try:
        if _THROTTLE_FILE.exists():
            _THROTTLE_FILE.unlink()
    except OSError:
        pass


def _send_smtp(recipients: list[str], subject: str, body: str) -> None:
    host = _env("SMTP_HOST")
    if not host:
        raise RuntimeError(
            "REINDEX_ALERT_EMAIL_TO is set but SMTP_HOST is not — cannot send alert email. "
            "Set SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD, or use "
            "REINDEX_ALERT_EMAIL_TO=replit for Replit's built-in mailer."
        )
    port = int(_env("SMTP_PORT", "587") or "587")
    username = _env("SMTP_USERNAME")
    password = _env("SMTP_PASSWORD")
    use_starttls = _env("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes", "on")
    sender = _env("REINDEX_ALERT_EMAIL_FROM") or username or "maya-alerts@localhost"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_starttls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)


def _send_replit_mail(subject: str, body: str) -> None:
    """Send via Replit's built-in mail service to the Repl owner's verified email."""
    hostname = _env("REPLIT_CONNECTORS_HOSTNAME")
    if not hostname:
        raise RuntimeError(
            "REINDEX_ALERT_EMAIL_TO=replit but REPLIT_CONNECTORS_HOSTNAME is not set — "
            "Replit mail is only available when running on Replit."
        )
    proc = subprocess.run(
        ["replit", "identity", "create", "--audience", f"https://{hostname}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        raise RuntimeError(f"Could not obtain Replit identity token: {proc.stderr.strip()[:300]}")

    payload = json.dumps({"subject": subject, "text": body}).encode()
    req = urllib.request.Request(
        f"https://{hostname}/api/v2/mailer/send",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Replit-Authentication": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Replit mailer returned HTTP {resp.status}")


def _test_alert_error(exc: Exception, recipients: list[str]) -> str:
    """Map transport failures to safe, actionable configuration guidance."""
    if recipients == ["replit"]:
        return (
            "The Replit mail test failed. Check REINDEX_ALERT_EMAIL_TO=replit and "
            "that REPLIT_CONNECTORS_HOSTNAME is available in this Replit environment."
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "SMTP authentication failed. Check SMTP_USERNAME and SMTP_PASSWORD "
            "in Secrets, then try again."
        )
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return (
            "Could not connect to the SMTP server. Check SMTP_HOST, SMTP_PORT, "
            "and SMTP_STARTTLS, then try again."
        )
    if isinstance(exc, (smtplib.SMTPException, ValueError)):
        return (
            "The SMTP server rejected the test email. Check SMTP_HOST, SMTP_PORT, "
            "SMTP_USERNAME, SMTP_PASSWORD, SMTP_STARTTLS, and "
            "REINDEX_ALERT_EMAIL_FROM."
        )
    return (
        "The test email could not be sent. Check SMTP_HOST, SMTP_PORT, "
        "SMTP_USERNAME, SMTP_PASSWORD, SMTP_STARTTLS, and "
        "REINDEX_ALERT_EMAIL_FROM, then try again."
    )


def send_reindex_failure_alert(source: str, detail: str, when: str) -> bool:
    """
    Send a failure notification email (synchronous). Returns True if an email
    was actually sent, False if skipped (not configured or throttled).
    Raises nothing — all errors are logged.
    """
    recipients = _recipients()
    if not recipients:
        logger.info(
            "📭 Re-index failure alert skipped — REINDEX_ALERT_EMAIL_TO not set. "
            "Set it in Secrets to receive email alerts when the automatic refresh fails."
        )
        return False

    with _lock:
        if _is_throttled():
            logger.info(
                f"📭 Re-index failure alert throttled — last alert sent within "
                f"{_throttle_hours():g}h (REINDEX_ALERT_THROTTLE_HOURS)."
            )
            return False

        subject = f"[Maya] Knowledge base refresh FAILED ({source})"
        body = (
            "Maya's knowledge base refresh failed.\n\n"
            f"Source:   {source}\n"
            f"Time:     {when}\n"
            f"Detail:   {detail}\n\n"
            "The knowledge base may be stale until a re-index succeeds.\n"
            "Check /admin/crawl/status and logs/reindex.log for more detail.\n"
            "You can trigger a manual re-run via POST /admin/crawl.\n\n"
            f"Repeated failures are throttled to one email every "
            f"{_throttle_hours():g} hours; a successful refresh resets the throttle."
        )

        try:
            if recipients == ["replit"]:
                _send_replit_mail(subject, body)
                sent_to = "Repl owner (Replit mail)"
            else:
                _send_smtp(recipients, subject, body)
                sent_to = ", ".join(recipients)
            _record_sent()
            logger.info(f"📧 Re-index failure alert emailed to {sent_to}.")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send re-index failure alert email: {e}")
            return False


def notify_reindex_failure_async(source: str, detail: str, when: str) -> None:
    """Fire-and-forget: send the alert in a background thread so the caller never blocks."""
    threading.Thread(
        target=send_reindex_failure_alert,
        args=(source, detail, when),
        daemon=True,
        name="reindex-alert-email",
    ).start()


def send_test_alert() -> dict:
    """
    Send a test alert email through the same code path as a real failure alert,
    but WITHOUT touching the throttle timestamp so no real alert is suppressed.

    Returns a dict with keys:
      - sent (bool): True if the email was dispatched without error.
      - recipient (str | None): Who it was sent to, or None if not configured.
      - error (str | None): Error message if the send failed.
      - configured (bool): Whether REINDEX_ALERT_EMAIL_TO is set at all.
    """
    recipients = _recipients()
    if not recipients:
        return {
            "sent": False,
            "configured": False,
            "recipient": None,
            "error": (
                "REINDEX_ALERT_EMAIL_TO is not set. "
                "Add it in Secrets to enable alert emails."
            ),
        }

    when = datetime.now(timezone.utc).isoformat()
    subject = "[Maya] Test alert — notification setup check"
    body = (
        "This is a test alert from Maya.\n\n"
        "If you received this, Maya's re-index failure notifications are configured\n"
        "correctly and will reach you when an automatic refresh fails.\n\n"
        f"Sent at: {when}\n\n"
        "No action is required. This test does not affect the failure alert throttle."
    )

    try:
        if recipients == ["replit"]:
            _send_replit_mail(subject, body)
            sent_to = "Repl owner (Replit mail)"
        else:
            _send_smtp(recipients, subject, body)
            sent_to = ", ".join(recipients)
        logger.info(f"📧 Test alert email sent to {sent_to}.")
        return {
            "sent": True,
            "configured": True,
            "recipient": sent_to,
            "error": None,
        }
    except Exception as e:
        logger.error(f"❌ Test alert email failed: {e}")
        return {
            "sent": False,
            "configured": True,
            "recipient": ", ".join(recipients),
            "error": _test_alert_error(e, recipients),
        }
