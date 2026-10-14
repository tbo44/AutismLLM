"""Backup diagnostics only: isolated storage and mocked email delivery."""
import fcntl
import json
import multiprocessing
from datetime import datetime, timedelta, timezone

import pytest

from app import github_backup_alerts as alerts, github_backup_status as status, notifications
from app import github_backup_history_health as health

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_completeness_memory(monkeypatch):
    monkeypatch.setattr(health, "_marker_write_failed", False)


def attempt(index=0, kind="problem", outcome="reserved"):
    return {"at": (NOW + timedelta(minutes=index)).isoformat(), "kind": kind, "outcome": outcome}


def test_rotation_outcome_updates_and_restart(tmp_path):
    path = tmp_path / "receipt.json"
    for index in range(status.NOTIFICATION_HISTORY_LIMIT + 5):
        status.record_notification_history(path, attempt(index))
        status.record_notification_history(path, attempt(index, outcome="sent"))
    rows = status.read_notification_history(path)
    assert len(rows) == status.NOTIFICATION_HISTORY_LIMIT
    assert rows[0] == attempt(5, outcome="sent")
    assert rows[-1] == attempt(104, outcome="sent")
    assert status._history_path(path).stat().st_mode & 0o777 == 0o600
    html = status.render_notification_history(path)
    assert html.index(rows[-1]["at"]) < html.index(rows[0]["at"])
    assert "inbox delivery is not confirmed" in html


def _write_rows(path, worker):
    for index in range(worker * 10, worker * 10 + 10):
        status.record_notification_history(path, attempt(index))
        status.record_notification_history(path, attempt(index, outcome="delivery_uncertain"))


def test_concurrent_process_writes_keep_every_attempt(tmp_path):
    path = tmp_path / "receipt.json"
    context = multiprocessing.get_context("fork")
    processes = [context.Process(target=_write_rows, args=(path, worker)) for worker in range(5)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    rows = status.read_notification_history(path)
    assert len(rows) == 50
    assert {row["at"] for row in rows} == {attempt(index)["at"] for index in range(50)}
    assert all(row["outcome"] == "delivery_uncertain" for row in rows)


def test_projection_privacy_on_write_read_and_render(tmp_path):
    path = tmp_path / "receipt.json"
    unsafe = dict(attempt(), recipient="private-secret", error="private-secret", token="private-secret")
    status.record_notification_history(path, unsafe)
    history_path = status._history_path(path)
    assert "private-secret" not in history_path.read_text()
    history_path.write_text(json.dumps({
        "version": 1, "secret": "private-secret", "attempts": [unsafe],
    }))
    assert status.read_notification_history(path) == [attempt()]
    assert "private-secret" not in status.render_notification_history(path)
    status.record_notification_history(path, attempt(outcome="sent"))
    assert "private-secret" not in history_path.read_text()


@pytest.mark.parametrize("raw", [
    "{private-secret", "[]", "{}",
    json.dumps({"version": 2, "attempts": []}),
    json.dumps({"version": 1, "attempts": [attempt(kind="<script>private-secret</script>")]}),
    json.dumps({"version": 1, "attempts": [attempt(outcome="private-secret")]}),
    json.dumps({"version": 1, "attempts": [{**attempt(), "at": "private-secret"}]}),
    json.dumps({"version": 1, "attempts": [attempt()] * 101}),
    " " * (status.NOTIFICATION_HISTORY_BYTES + 1),
])
def test_corrupt_history_is_explicit_read_only_and_not_recreated(tmp_path, raw):
    path = tmp_path / "receipt.json"
    history_path = status._history_path(path)
    history_path.write_text(raw)
    assert "history unavailable" in status.render_notification_history(path)
    assert "private-secret" not in status.render_notification_history(path)
    with pytest.raises((ValueError, KeyError, TypeError)):
        status.record_notification_history(path, attempt())
    assert history_path.read_text() == raw


def test_empty_history_read_never_creates_files(tmp_path):
    path = tmp_path / "receipt.json"
    assert status.read_notification_history(path) == []
    assert "No backup email history" in status.render_notification_history(path)
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def sender(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(notifications, "is_configured", lambda: True)
    status.write_result(path, failure="sync", now=NOW)
    return path


def test_failed_problem_reminder_and_recovery_remain_visible(sender, monkeypatch):
    path = sender
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: False)
    assert alerts.check_backup_alerts(path, now=NOW) == "delivery_uncertain"
    later = NOW + timedelta(hours=24)
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: True)
    assert alerts.check_backup_alerts(path, now=later) == "sent"
    status.write_result(path, workspace_commit="a" * 40, remote_tip="b" * 40, now=later)
    assert alerts.check_backup_alerts(path, now=later) == "sent"
    rows = status.read_notification_history(path)
    assert [(row["kind"], row["outcome"]) for row in rows] == [
        ("problem", "delivery_uncertain"), ("problem", "sent"), ("recovery", "sent"),
    ]
    assert status.notification_summary(path, later)["last_notification"]["kind"] == "recovery"
    before = {file.name: file.read_bytes() for file in path.parent.iterdir()}
    assert alerts.check_backup_alerts(path, now=later) == "healthy"
    status.render_notification_history(path)
    assert before == {file.name: file.read_bytes() for file in path.parent.iterdir()}


def test_crash_keeps_reserved_history_and_claim(sender, monkeypatch):
    def crash(*_):
        raise SystemExit()
    monkeypatch.setattr(notifications, "send_github_backup_alert", crash)
    with pytest.raises(SystemExit):
        alerts.check_backup_alerts(sender, now=NOW)
    assert status.read_notification_history(sender) == [attempt()]
    assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"
    html = status.render_notification_history(sender)
    assert "was interrupted; a history gap is not established" in html
    assert "missing row" not in html


@pytest.mark.parametrize("failure", ["corrupt", "write", "read"])
def test_history_failure_does_not_block_send_or_release_claim(sender, monkeypatch, caplog, failure):
    calls = []
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: calls.append(1) or True)
    if failure == "corrupt":
        status._history_path(sender).write_text("private-secret")
    elif failure == "write":
        original = status.atomic_write_json
        def fail(path, data):
            if path == status._history_path(sender):
                raise OSError("private-secret")
            return original(path, data)
        monkeypatch.setattr(status, "atomic_write_json", fail)
    else:
        def fail(*_):
            raise PermissionError("private-secret")
        monkeypatch.setattr(status, "read_notification_history", fail)
    assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
    assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"
    assert calls == [1]
    assert "private-secret" not in caplog.text
    assert "history unavailable" in status.render_notification_history(sender) or failure == "write"


def test_rotation_never_prunes_duplicate_protection(sender, monkeypatch):
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: True)
    assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
    claim_path = sender.with_name(sender.name + ".alerts.json")
    claim = claim_path.read_bytes()
    for index in range(101):
        status.record_notification_history(sender, attempt(index + 1))
    assert claim_path.read_bytes() == claim
    assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"


def test_busy_history_lock_does_not_delay_delivery(sender, monkeypatch, caplog):
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: True)
    history_path = status._history_path(sender)
    with history_path.with_suffix(history_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
        assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"
    assert "history unavailable" in caplog.text
    assert status.notification_summary(sender, NOW)["last_notification"]["outcome"] == "sent"
    assert "missing row" in status.render_notification_history(sender)
    # Another successful send must not hide the older missed row after lock recovery.
    assert alerts.check_backup_alerts(sender, now=NOW + timedelta(hours=24)) == "sent"
    assert len(status.read_notification_history(sender)) == 1
    html = status.render_notification_history(sender)
    assert "History recording has failed" in html
    assert "missing row for the latest" not in html
    assert health._marker_path(sender).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("stage", ["reserved", "sent", "both"])
def test_write_failure_recovers_without_hiding_gaps(sender, monkeypatch, stage):
    original = status.atomic_write_json
    calls = []

    def fail(path, data):
        if path == status._history_path(sender):
            outcome = data["attempts"][-1]["outcome"]
            if stage == "both" or stage == outcome:
                raise OSError("private-secret")
        return original(path, data)

    monkeypatch.setattr(status, "atomic_write_json", fail)
    monkeypatch.setattr(notifications, "send_github_backup_alert",
                        lambda *_: calls.append(1) or True)
    assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
    assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"
    html = status.render_notification_history(sender)
    assert "History recording has failed" in html
    if stage == "sent":
        assert "History is missing the latest terminal outcome" in html
    elif stage == "both":
        assert "History has a missing row" in html
    else:
        assert status.read_notification_history(sender) == [attempt(outcome="sent")]
    monkeypatch.setattr(status, "atomic_write_json", original)
    assert alerts.check_backup_alerts(sender, now=NOW + timedelta(hours=24)) == "sent"
    assert calls == [1, 1]
    # Reads remain warning-bearing even after successful writes and a fresh process.
    import subprocess
    import sys
    result = subprocess.run([
        sys.executable, "-c",
        "from app.github_backup_status import render_notification_history; "
        "import sys; print(render_notification_history(sys.argv[1]))", str(sender),
    ], capture_output=True, text=True, check=True)
    assert "History recording has failed" in result.stdout
    assert "private-secret" not in result.stdout
    assert health._marker_path(sender).stat().st_size < health.MARKER_BYTES


def test_marker_failure_is_explicit_and_does_not_block_delivery(sender, monkeypatch, caplog):
    original = status.atomic_write_json
    def fail(path, data):
        if path in (status._history_path(sender), health._marker_path(sender)):
            raise PermissionError("private-secret")
        return original(path, data)
    monkeypatch.setattr(status, "atomic_write_json", fail)
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: True)
    assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
    assert alerts.check_backup_alerts(sender, now=NOW) == "throttled"
    assert "warning could not be saved" in status.render_notification_history(sender)
    monkeypatch.setattr(status, "atomic_write_json", original)
    assert alerts.check_backup_alerts(sender, now=NOW + timedelta(hours=24)) == "sent"
    html = status.render_notification_history(sender)
    assert "Completeness cannot be confirmed" in html
    assert "private-secret" not in html + caplog.text


@pytest.mark.parametrize("raw", [
    "private-secret", "[]", "{}",
    json.dumps({"version": 2, "last_failed_update": attempt()}),
    json.dumps({"version": 1, "last_failed_update": attempt(outcome="private-secret")}),
    " " * (health.MARKER_BYTES + 1),
])
def test_marker_corruption_remains_explicit_and_is_not_replaced(sender, raw):
    health._marker_path(sender).write_text(raw)
    html = status.render_notification_history(sender)
    assert "completeness tracking unavailable or corrupt" in html
    assert "private-secret" not in html
    health.record_failed_update(sender, attempt())
    assert health._marker_path(sender).read_text() == raw


def test_completeness_projection_rotation_and_read_only(sender, monkeypatch):
    health.record_failed_update(sender, dict(attempt(), token="private-secret"))
    assert "private-secret" not in health._marker_path(sender).read_text()
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: True)
    assert alerts.check_backup_alerts(sender, now=NOW) == "sent"
    for index in range(1, 102):
        status.record_notification_history(sender, attempt(index, outcome="sent"))
    before = {file.name: file.read_bytes() for file in sender.parent.iterdir()}
    html = status.render_notification_history(sender)
    assert "History recording has failed" in html
    assert "missing row for the latest" not in html
    assert before == {file.name: file.read_bytes() for file in sender.parent.iterdir()}


def test_claim_corruption_and_unavailable_marker_read_are_explicit(sender, monkeypatch):
    claim = sender.with_name(sender.name + ".alerts.json")
    claim.write_text("private-secret")
    assert "Latest send tracking unavailable or corrupt" in status.render_notification_history(sender)
    def fail(*_):
        raise PermissionError("private-secret")
    monkeypatch.setattr(health, "_read_marker", fail)
    html = status.render_notification_history(sender)
    assert "completeness tracking unavailable or corrupt" in html
    assert "private-secret" not in html


def test_admin_history_is_authenticated_private_and_read_only(sender, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "_ADMIN_TOKEN", "synthetic-admin-token")
    monkeypatch.setattr(main, "render_backup_notification_history",
                        lambda: status.render_notification_history(sender))
    monkeypatch.setattr(notifications, "send_github_backup_alert",
                        lambda *_: pytest.fail("Reading history must not send email"))
    status.record_notification_history(sender, attempt(outcome="delivery_uncertain"))
    history_path = status._history_path(sender)
    before = history_path.read_bytes()
    client = TestClient(main.app)
    assert client.get("/admin").status_code == 401
    denied = client.get("/admin", headers={"X-Admin-Token": "wrong"})
    assert denied.status_code == 403
    assert "githubBackupNotificationHistory" not in denied.text
    response = client.get("/admin", headers={"X-Admin-Token": "synthetic-admin-token"})
    assert response.status_code == 200
    assert "githubBackupNotificationHistory" in response.text
    assert NOW.isoformat() in response.text
    assert status.NOTIFICATION_OUTCOMES["delivery_uncertain"] in response.text
    health.record_failed_update(sender, attempt(outcome="sent"))
    response = client.get("/admin", headers={"X-Admin-Token": "synthetic-admin-token"})
    assert "githubBackupHistoryCompleteness" in response.text
    assert "History recording has failed" in response.text
    assert history_path.read_bytes() == before
    history_path.write_text("private-secret")
    response = client.get("/admin", headers={"X-Admin-Token": "synthetic-admin-token"})
    assert response.status_code == 200
    assert "history unavailable" in response.text
    assert "private-secret" not in response.text
