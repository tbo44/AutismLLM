"""Backup receipts and staff display use isolated files and synthetic credentials."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import github_backup_status as status
from scripts import sync_to_github as sync

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
WORKSPACE_COMMIT = "a" * 40
REMOTE_TIP = "b" * 40


@pytest.fixture(autouse=True)
def no_live_email(monkeypatch):
    monkeypatch.delenv("REINDEX_ALERT_EMAIL_TO", raising=False)


def successful(path, now=NOW):
    status.write_result(path, workspace_commit=WORKSPACE_COMMIT, remote_tip=REMOTE_TIP, now=now)


def test_atomic_receipts_retain_history_and_recover(tmp_path):
    path = tmp_path / "logs" / "receipt.json"
    successful(path)
    status.write_result(path, failure="conflict", now=NOW + timedelta(minutes=1))
    receipt = status.read_record(path)
    assert receipt["outcome"] == "failure"
    assert receipt["last_success"]["workspace_commit"] == WORKSPACE_COMMIT
    assert receipt["last_success"]["remote_tip"] == REMOTE_TIP
    assert receipt["last_failure"]["category"] == "conflict"
    successful(path, NOW + timedelta(minutes=2))
    receipt = status.read_record(path)
    assert receipt["outcome"] == "success"
    assert receipt["last_failure"]["category"] == "conflict"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]
    html = status.render_status(path, now=NOW + timedelta(minutes=3))
    assert "recovered by a later verified backup" in html
    assert "Needs attention" not in html


def test_failed_atomic_replace_preserves_previous_receipt(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    successful(path)
    previous = path.read_bytes()
    def fail(*args):
        raise OSError("disk error")
    monkeypatch.setattr(status.os, "replace", fail)
    with pytest.raises(OSError):
        status.write_result(path, failure="sync", now=NOW)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("contents", [None, "{invalid", "{}", '[]'])
def test_missing_or_corrupt_status_is_explicit_and_read_only(tmp_path, contents):
    path = tmp_path / "receipt.json"
    if contents is not None:
        path.write_text(contents)
    before = path.read_bytes() if path.exists() else None
    html = status.render_status(path, now=NOW)
    assert ("Unknown" if contents is None else "Status unavailable") in html
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("age,stale", [(0, False), (899, False), (901, True), (-1, True)])
def test_status_freshness(tmp_path, age, stale):
    path = tmp_path / "receipt.json"
    successful(path)
    html = status.render_status(path, now=NOW + timedelta(seconds=age))
    assert ("stale / no recent verification" in html) == stale
    assert WORKSPACE_COMMIT in html and REMOTE_TIP in html
    assert "unfinished edits are not backed up" in html


def test_receipt_never_includes_raw_failures_or_extra_fields(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(sync, "STATUS_PATH", path)
    def fail():
        raise sync.GitCommandError(["git", "secret-command"], "token=private-test-secret ENV=secret")
    monkeypatch.setattr(sync, "_sync_once", fail)
    with pytest.raises(sync.GitCommandError):
        sync.sync_once()
    assert "private-test-secret" not in path.read_text()
    assert "secret-command" not in path.read_text()
    raw = json.loads(path.read_text())
    raw["environment"] = "private-test-secret"
    raw["last_failure"]["message"] = "<script>private-test-secret</script>"
    path.write_text(json.dumps(raw))
    assert "private-test-secret" not in status.render_status(path, now=datetime.now(timezone.utc))
    assert "environment" not in status.read_record(path)


def test_authorization_failure_is_recorded_without_renewal(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(sync, "STATUS_PATH", path)
    def unavailable():
        raise SystemExit("connector diagnostic with private-test-secret")
    monkeypatch.setattr(sync, "_get_github_token", unavailable)
    with pytest.raises(SystemExit):
        sync.sync_once()
    assert status.read_record(path)["last_failure"]["category"] == "authorization"
    assert "private-test-secret" not in path.read_text()


@pytest.mark.parametrize("auth", ["missing", "invalid", "browser", "header", "query", "cookie"])
def test_dashboard_status_requires_admin_and_has_no_sync_side_effects(tmp_path, monkeypatch, auth):
    path = tmp_path / "receipt.json"
    successful(path)
    monkeypatch.setattr(main, "_ADMIN_TOKEN", "test-backup-admin")
    reads = []
    def render():
        reads.append(True)
        return status.render_status(path, now=NOW)
    monkeypatch.setattr(main, "render_github_backup_status", render)
    notification_reads = []
    def render_notifications():
        notification_reads.append(True)
        return status.render_notification_status(path, now=NOW)
    monkeypatch.setattr(main, "render_backup_notification_status", render_notifications)
    def forbidden(*args, **kwargs):
        pytest.fail("Dashboard must not run git, push, write status, or obtain authorization")
    monkeypatch.setattr(sync, "sync_once", forbidden)
    monkeypatch.setattr(sync, "_get_github_token", forbidden)
    monkeypatch.setattr(sync, "_run", forbidden)
    monkeypatch.setattr(status, "write_result", forbidden)
    monkeypatch.setattr(status, "atomic_write_json", forbidden)
    from app import notifications
    monkeypatch.setattr(notifications, "send_github_backup_alert", forbidden)
    before = path.read_bytes()
    # No lifespan startup: these checks must not launch RAG/crawl background work.
    client = TestClient(main.app)
    try:
        kwargs = {"follow_redirects": False}
        url = "/admin"
        if auth == "invalid":
            kwargs["headers"] = {"X-Admin-Token": "wrong"}
        elif auth == "browser":
            kwargs["headers"] = {"Accept": "text/html"}
        elif auth == "header":
            kwargs["headers"] = {"X-Admin-Token": "test-backup-admin"}
        elif auth == "query":
            url += "?token=test-backup-admin"
        elif auth == "cookie":
            client.cookies.set(main._ADMIN_COOKIE_NAME, "test-backup-admin")
        response = client.get(url, **kwargs)
    finally:
        client.close()
    if auth in ("missing", "invalid", "browser"):
        assert response.status_code == {"missing": 401, "invalid": 403, "browser": 303}[auth]
        assert not reads
        assert not notification_reads
        assert WORKSPACE_COMMIT not in response.text
    else:
        assert response.status_code == 200
        assert reads == [True]
        assert notification_reads == [True]
        assert "Backup warning emails" in response.text
        assert "Disabled" in response.text
        assert "GitHub backup" in response.text
        assert WORKSPACE_COMMIT in response.text and REMOTE_TIP in response.text
        assert "test-backup-admin" not in response.text
    assert path.read_bytes() == before


def test_notification_summary_disabled_preserves_history_and_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text(json.dumps({
        "version": 1, "incident": True, "last_attempt": NOW.isoformat(),
        "last_notification": {"at": NOW.isoformat(), "kind": "problem", "outcome": "delivery_uncertain"},
        "recipient": "private-secret", "error": "<script>private-secret</script>",
    }))
    before = claim.read_bytes()
    html = status.render_notification_status(path, NOW)
    assert "Disabled" in html and "Last reserved attempt" in html
    assert "Delivery failed or is uncertain" in html and "throttled" in html
    assert "private-secret" not in html and "<button" not in html and "<form" not in html
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "private-secret")
    html = status.render_notification_status(path, NOW + timedelta(hours=24))
    assert "Enabled" in html and "private-secret" not in html
    assert "Problem reminders throttled" not in html
    assert claim.read_bytes() == before and list(tmp_path.iterdir()) == [claim]


def test_legacy_claim_is_unknown_and_upgrades_safely(tmp_path, monkeypatch):
    from app import github_backup_alerts as alerts, notifications
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "synthetic-destination")
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *args: True)
    path = tmp_path / "receipt.json"
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text(json.dumps({"version": 1, "incident": True, "last_attempt": NOW.isoformat()}))
    html = status.render_notification_status(path, NOW)
    assert "delivery is unknown" in html
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24)) == "sent"
    assert status.notification_summary(path, NOW + timedelta(hours=24))["last_notification"]["outcome"] == "sent"


@pytest.mark.parametrize("contents", [
    "private-secret", "[]", "{}",
    json.dumps({"version": 1, "incident": True, "last_attempt": NOW.isoformat(),
                "last_notification": {"at": NOW.isoformat(), "kind": "problem", "outcome": "private-secret"}}),
])
def test_unreadable_notification_tracking_fails_closed(tmp_path, monkeypatch, contents):
    from app import github_backup_alerts as alerts, notifications
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "synthetic-destination")
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *args: pytest.fail("must not send"))
    path = tmp_path / "receipt.json"
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text(contents)
    html = status.render_notification_status(path, NOW)
    assert "Notification tracking unavailable" in html
    assert "private-secret" not in html
    assert alerts.check_backup_alerts(path, now=NOW) == "unavailable"
    assert claim.read_text() == contents


def test_no_attempt_and_dispatched_labels(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    assert "No reserved backup email attempt" in status.render_notification_status(path, NOW)
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text(json.dumps({
        "version": 1, "incident": False, "last_attempt": None,
        "last_notification": {"at": NOW.isoformat(), "kind": "recovery", "outcome": "sent"},
    }))
    html = status.render_notification_status(path, NOW)
    assert "Dispatched to the mail service; inbox delivery is not confirmed" in html
    assert "recovery notice" in html
