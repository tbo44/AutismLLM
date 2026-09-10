"""Tests for re-index failure email notifications (app/notifications.py)."""

import os
import smtplib
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import notifications
from app import main as app_main
from app.main import app

_client = TestClient(app)
_CRAWL_TOKEN = "test-crawl-token-abc"


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


def test_get_status_reports_active_throttle(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.org")
    monkeypatch.setenv("REINDEX_ALERT_THROTTLE_HOURS", "24")
    sent_at = datetime.now(timezone.utc) - timedelta(hours=1)
    notifications._THROTTLE_FILE.write_text(sent_at.isoformat())

    status = notifications.get_status()

    assert status["throttled"] is True
    assert datetime.fromisoformat(status["next_allowed_at"]) == sent_at + timedelta(hours=24)


def test_get_status_reports_expired_throttle(monkeypatch):
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.org")
    monkeypatch.setenv("REINDEX_ALERT_THROTTLE_HOURS", "24")
    sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
    notifications._THROTTLE_FILE.write_text(sent_at.isoformat())

    status = notifications.get_status()

    assert status["throttled"] is False
    assert status["next_allowed_at"] is None


def test_admin_dashboard_shows_throttle_expiry():
    kb = {
        "email_alerts": {
            "configured": True,
            "recipient_display": "staff@example.org",
            "last_sent_at": "2026-09-10T00:00:00+00:00",
            "throttled": True,
            "next_allowed_at": "2026-09-11T00:00:00+00:00",
        },
        "schedule": {"enabled": False},
        "history": [],
    }

    stats = {"top_sources": [], "questions_7d": 0, "total_questions": 0}
    html = app_main._render_admin_html([], stats, kb)

    assert "throttled until 11 Sep 2026 01:00 BST" in html


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


# ── send_test_alert() unit tests ──────────────────────────────────────────────


def test_send_test_alert_not_configured(monkeypatch):
    """Returns not-sent + helpful error when REINDEX_ALERT_EMAIL_TO is absent."""
    monkeypatch.delenv("REINDEX_ALERT_EMAIL_TO", raising=False)
    with patch.object(notifications, "_send_smtp") as smtp, \
         patch.object(notifications, "_send_replit_mail") as rmail:
        result = notifications.send_test_alert()
    assert result["sent"] is False
    assert result["configured"] is False
    assert result["recipient"] is None
    assert "REINDEX_ALERT_EMAIL_TO" in result["error"]
    smtp.assert_not_called()
    rmail.assert_not_called()


def test_send_test_alert_smtp_success(monkeypatch):
    """Sends via SMTP and reports the recipients without raising."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    with patch.object(notifications, "_send_smtp") as smtp:
        result = notifications.send_test_alert()
    assert result["sent"] is True
    assert result["configured"] is True
    assert "staff@example.com" in result["recipient"]
    assert result["error"] is None
    smtp.assert_called_once()
    _, subject, body = smtp.call_args[0]
    assert "Test alert" in subject
    assert "test" in body.lower()


def test_send_test_alert_replit_mail(monkeypatch):
    """Routes through Replit mailer when REINDEX_ALERT_EMAIL_TO=replit."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "replit")
    with patch.object(notifications, "_send_replit_mail") as rmail, \
         patch.object(notifications, "_send_smtp") as smtp:
        result = notifications.send_test_alert()
    assert result["sent"] is True
    rmail.assert_called_once()
    smtp.assert_not_called()


def test_send_test_alert_missing_smtp_host(monkeypatch):
    """Missing SMTP_HOST gives a direct configuration fix."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    result = notifications.send_test_alert()
    assert result["sent"] is False
    assert result["configured"] is True
    assert "SMTP_HOST" in result["error"]
    assert "Traceback" not in result["error"]


def test_send_test_alert_auth_failure(monkeypatch):
    """Authentication failures identify the credential env vars safely."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    raw_error = "535 5.7.8 Authentication credentials invalid"
    failure = smtplib.SMTPAuthenticationError(535, raw_error.encode())
    with patch.object(notifications, "_send_smtp", side_effect=failure):
        result = notifications.send_test_alert()
    assert result["sent"] is False
    assert "SMTP_USERNAME" in result["error"]
    assert "SMTP_PASSWORD" in result["error"]
    assert raw_error not in result["error"]
    assert "Traceback" not in result["error"]


def test_send_test_alert_does_not_touch_throttle(monkeypatch, tmp_path):
    """Test sends must never update the throttle timestamp."""
    monkeypatch.setattr(notifications, "_THROTTLE_FILE", tmp_path / "last_sent")
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    with patch.object(notifications, "_send_smtp"):
        notifications.send_test_alert()
    # Throttle file must still not exist after a test send.
    assert not (tmp_path / "last_sent").exists()


def test_send_test_alert_does_not_suppress_real_failure(monkeypatch, tmp_path):
    """A test send must not prevent the next real failure alert from going out."""
    monkeypatch.setattr(notifications, "_THROTTLE_FILE", tmp_path / "last_sent")
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    monkeypatch.setenv("REINDEX_ALERT_THROTTLE_HOURS", "24")
    with patch.object(notifications, "_send_smtp"):
        notifications.send_test_alert()
    # A real failure alert right after should still go out (throttle untouched).
    with patch.object(notifications, "_send_smtp") as smtp:
        sent = notifications.send_reindex_failure_alert("scheduled", "error", "now")
    assert sent is True
    smtp.assert_called_once()


# ── POST /admin/alerts/test endpoint tests ────────────────────────────────────


@pytest.fixture
def crawl_token(monkeypatch):
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", _CRAWL_TOKEN)
    return _CRAWL_TOKEN


def test_test_alert_endpoint_no_token(crawl_token):
    """Missing Authorization header → 401."""
    resp = _client.post("/admin/alerts/test")
    assert resp.status_code == 401


def test_test_alert_endpoint_wrong_token(crawl_token):
    """Wrong token → 403."""
    resp = _client.post(
        "/admin/alerts/test",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_test_alert_endpoint_not_configured(crawl_token, monkeypatch):
    """Authorised call when email not configured → 200 with configured=False."""
    monkeypatch.delenv("REINDEX_ALERT_EMAIL_TO", raising=False)
    resp = _client.post(
        "/admin/alerts/test",
        headers={"Authorization": f"Bearer {_CRAWL_TOKEN}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False
    assert body["configured"] is False


def test_test_alert_endpoint_sends_and_reports(crawl_token, monkeypatch):
    """Authorised call with valid config → 200, sent=True, throttle unchanged."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    with patch.object(notifications, "_send_smtp") as smtp:
        resp = _client.post(
            "/admin/alerts/test",
            headers={"Authorization": f"Bearer {_CRAWL_TOKEN}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is True
    assert "staff@example.com" in body["recipient"]
    assert body["error"] is None
    smtp.assert_called_once()


def test_test_alert_endpoint_transport_failure(crawl_token, monkeypatch):
    """Transport error → 200 with safe, actionable configuration guidance."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.com")
    raw_error = "Traceback: connection details from internal host"
    with patch.object(notifications, "_send_smtp", side_effect=RuntimeError(raw_error)):
        resp = _client.post(
            "/admin/alerts/test",
            headers={"Authorization": f"Bearer {_CRAWL_TOKEN}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False
    assert "SMTP_HOST" in body["error"]
    assert raw_error not in body["error"]
    assert "Traceback" not in body["error"]


@pytest.mark.parametrize(
    "failure",
    [
        "missing_hostname",
        "identity_nonzero",
        "identity_empty_token",
        "identity_timeout",
        "identity_command_missing",
        "mailer_http_error",
        "mailer_error_status",
    ],
)
def test_test_alert_endpoint_replit_failures_are_safe(
    failure, crawl_token, monkeypatch,
):
    """Exercise the real mail path without running a command or sending mail."""
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "replit")
    hostname = "private-connector.example.invalid"
    monkeypatch.setenv("REPLIT_CONNECTORS_HOSTNAME", hostname)
    token = "fake-identity-token-do-not-expose"
    subprocess_detail = "private-subprocess-stderr-do-not-expose"
    http_detail = "private-http-response-do-not-expose"
    command = [
        "replit", "identity", "create", "--audience", f"https://{hostname}",
    ]
    mailer_url = f"https://{hostname}/api/v2/mailer/send"

    with patch.object(notifications.subprocess, "run") as run, \
         patch.object(notifications.urllib.request, "urlopen") as urlopen, \
         patch.object(notifications, "_send_smtp") as smtp:
        run.return_value = subprocess.CompletedProcess(
            command, returncode=0, stdout=token, stderr=subprocess_detail,
        )
        if failure == "missing_hostname":
            monkeypatch.delenv("REPLIT_CONNECTORS_HOSTNAME")
        elif failure == "identity_nonzero":
            run.return_value.returncode = 1
        elif failure == "identity_empty_token":
            run.return_value.stdout = " \n"
        elif failure == "identity_timeout":
            run.side_effect = subprocess.TimeoutExpired(
                command, 30, output=token, stderr=subprocess_detail,
            )
        elif failure == "identity_command_missing":
            run.side_effect = FileNotFoundError(subprocess_detail)
        elif failure == "mailer_http_error":
            urlopen.side_effect = urllib.error.HTTPError(
                mailer_url, 503, f"{http_detail}: {token}", {}, None,
            )
        elif failure == "mailer_error_status":
            urlopen.return_value.__enter__.return_value.status = 503

        resp = _client.post(
            "/admin/alerts/test",
            headers={"Authorization": f"Bearer {crawl_token}"},
        )

        smtp.assert_not_called()
        if failure == "missing_hostname":
            run.assert_not_called()
        else:
            run.assert_called_once_with(
                command, capture_output=True, text=True, timeout=30,
            )
        if failure.startswith("mailer_"):
            urlopen.assert_called_once()
            request = urlopen.call_args.args[0]
            assert request.full_url == mailer_url
            assert request.get_header("Replit-authentication") == f"Bearer {token}"
        else:
            urlopen.assert_not_called()

    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False
    assert body["configured"] is True
    assert body["recipient"] == "replit"
    assert "Check" in body["error"]
    assert "REINDEX_ALERT_EMAIL_TO=replit" in body["error"]
    assert "REPLIT_CONNECTORS_HOSTNAME" in body["error"]
    for internal_detail in (
        token, subprocess_detail, http_detail, hostname, mailer_url,
        "Traceback", "TimeoutExpired", "FileNotFoundError", "HTTPError",
        "Replit mailer returned HTTP 503", "Could not obtain Replit identity token",
    ):
        assert internal_detail not in resp.text
    assert notifications._last_sent_at() is None
