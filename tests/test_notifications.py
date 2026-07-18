"""Tests for re-index failure email notifications (app/notifications.py)."""

from unittest.mock import patch, MagicMock

import pytest

from app import notifications
from app import main as app_main


@pytest.fixture(autouse=True)
def _isolate_throttle(tmp_path, monkeypatch):
    """Point the throttle file at a temp location so tests don't interfere."""
    monkeypatch.setattr(notifications, "_THROTTLE_FILE", tmp_path / "last_sent")
    yield


def test_noop_when_not_configured(monkeypatch):
    monkeypatch.delenv("REINDEX_ALERT_EMAIL_TO", raising=False)
    with patch.object(notifications, "_send_smtp") as smtp, \
         patch.object(notifications, "_send_replit_mail") as rmail:
        assert notifications.send_reindex_failure_alert("scheduled", "boom", "now") is False
        smtp.assert_not_called()
        rmail.assert_not_called()
    assert not notifications.is_configured()


def test_smtp_send_and_throttle(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "a@example.org, b@example.org")
    monkeypatch.setenv("REINDEX_ALERT_THROTTLE_HOURS", "24")
    with patch.object(notifications, "_send_smtp") as smtp:
        assert notifications.send_reindex_failure_alert("scheduled", "boom", "now") is True
        smtp.assert_called_once()
        recipients, subject, body = smtp.call_args[0]
        assert recipients == ["a@example.org", "b@example.org"]
        assert "FAILED" in subject
        assert "boom" in body

        # Second failure within the throttle window is suppressed.
        assert notifications.send_reindex_failure_alert("scheduled", "boom2", "later") is False
        smtp.assert_called_once()

        # A successful run resets the throttle → next failure alerts again.
        notifications.reset_throttle()
        assert notifications.send_reindex_failure_alert("scheduled", "boom3", "later") is True
        assert smtp.call_count == 2


def test_zero_throttle_always_sends(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "a@example.org")
    monkeypatch.setenv("REINDEX_ALERT_THROTTLE_HOURS", "0")
    with patch.object(notifications, "_send_smtp") as smtp:
        assert notifications.send_reindex_failure_alert("scheduled", "x", "t") is True
        assert notifications.send_reindex_failure_alert("scheduled", "y", "t") is True
        assert smtp.call_count == 2


def test_replit_mail_mode(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "replit")
    with patch.object(notifications, "_send_replit_mail") as rmail, \
         patch.object(notifications, "_send_smtp") as smtp:
        assert notifications.send_reindex_failure_alert("scheduled", "boom", "now") is True
        rmail.assert_called_once()
        smtp.assert_not_called()


def test_smtp_missing_host_is_logged_not_raised(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "a@example.org")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    # Should not raise, should not mark as sent (so a later fix can alert).
    assert notifications.send_reindex_failure_alert("scheduled", "boom", "now") is False
    assert notifications._last_sent_at() is None


def test_record_reindex_result_failure_triggers_notification():
    with patch.object(app_main.notifications, "notify_reindex_failure_async") as notify:
        app_main._record_reindex_result("scheduled", {"success": False, "error": "kaput"})
        notify.assert_called_once()
        args = notify.call_args[0]
        assert args[0] == "scheduled"
        assert "kaput" in args[1]
    # Clean up the alert this set.
    app_main._crawl_status["alert"] = None


def test_record_reindex_result_success_resets_throttle():
    with patch.object(app_main.notifications, "reset_throttle") as reset, \
         patch.object(app_main.notifications, "notify_reindex_failure_async") as notify:
        app_main._record_reindex_result(
            "scheduled",
            {"success": True, "total_chunks": 1, "seed_chunks": 1,
             "crawled_chunks": 0, "elapsed_seconds": 1},
        )
        reset.assert_called_once()
        notify.assert_not_called()
