"""Acknowledgements are diagnostic audits, never send claims or repairs."""
import fcntl
import json
import multiprocessing
import re
import subprocess
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import github_backup_alerts as alerts
from app import github_backup_history_health as health
from app import github_backup_status as status
from app import notifications


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
ATTEMPT = {"at": NOW.isoformat(), "kind": "problem", "outcome": "sent"}


@pytest.fixture
def warning(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_marker_write_failed", False)
    path = tmp_path / "receipt.json"
    health.record_failed_update(path, ATTEMPT)
    return path, health._read_marker(path)["warning_id"]


def test_ack_is_durable_idempotent_private_and_not_repair(warning):
    path, warning_id = warning
    marker_bytes = health._marker_path(path).read_bytes()
    row = health.acknowledge_warning(path, warning_id, now=NOW)
    assert row["actor"] == "authenticated_admin"
    assert row["missing_history_recovered"] is False
    assert row["last_failed_update"] == ATTEMPT
    assert health._marker_path(path).read_bytes() == marker_bytes
    assert health._ack_path(path).stat().st_mode & 0o777 == 0o600
    audit_bytes = health._ack_path(path).read_bytes()
    assert health.acknowledge_warning(path, warning_id) == row
    assert health._ack_path(path).read_bytes() == audit_bytes
    html = health.render_completeness(path, [])
    assert "Historical warning acknowledged" in html
    assert "Missing rows or outcomes were not recovered" in html
    assert "githubBackupHistoryAck'" not in html
    result = subprocess.run([
        "python", "-c",
        "from app.github_backup_history_health import render_completeness; "
        "import sys; print(render_completeness(sys.argv[1], []))", str(path),
    ], text=True, capture_output=True, check=True)
    assert "Historical warning acknowledged" in result.stdout


def test_legacy_warning_acknowledged_without_rewriting(warning):
    path, _ = warning
    health._marker_path(path).write_text(json.dumps({
        "version": 1, "last_failed_update": ATTEMPT,
    }))
    before = health._marker_path(path).read_bytes()
    marker = health._read_marker(path)
    health.acknowledge_warning(path, marker["warning_id"], now=NOW)
    assert health._marker_path(path).read_bytes() == before
    health.record_failed_update(path, ATTEMPT)
    assert health._read_marker(path)["warning_id"] != marker["warning_id"]
    assert "Unacknowledged history recording warning" in health.render_completeness(path, [])


def test_new_identical_failure_is_not_acknowledged_and_stale_ack_rejected(warning):
    path, old_id = warning
    health.acknowledge_warning(path, old_id, now=NOW)
    health.record_failed_update(path, ATTEMPT)
    new_id = health._read_marker(path)["warning_id"]
    assert new_id != old_id
    audit_bytes = health._ack_path(path).read_bytes()
    with pytest.raises(health.WarningChanged):
        health.acknowledge_warning(path, old_id)
    assert health._ack_path(path).read_bytes() == audit_bytes
    html = health.render_completeness(path, [])
    assert "Unacknowledged history recording warning" in html
    assert "Historical warning acknowledged</strong>" not in html
    assert old_id in html  # Prior audit is still visible.
    for index in range(status.NOTIFICATION_HISTORY_LIMIT + 1):
        status.record_notification_history(path, {**ATTEMPT, "kind": "recovery"})
    assert health._read_marker(path)["warning_id"] == new_id
    assert "Unacknowledged history recording warning" in status.render_notification_history(path)


@pytest.mark.parametrize("target", ["marker", "audit", "history"])
def test_corrupt_diagnostics_are_never_overwritten(warning, target):
    path, warning_id = warning
    damaged = {"marker": health._marker_path(path), "audit": health._ack_path(path),
               "history": status._history_path(path)}[target]
    damaged.write_text("private-secret")
    if target == "history":
        health.acknowledge_warning(path, warning_id)
    else:
        with pytest.raises((ValueError, KeyError, TypeError)):
            health.acknowledge_warning(path, warning_id)
    html = status.render_notification_history(path)
    assert "private-secret" not in html
    assert damaged.read_text() == "private-secret"
    if target == "history":
        assert "history unavailable" in html
        assert "Historical warning acknowledged" in html
    else:
        assert "githubBackupHistoryAck'" not in html


def test_ack_write_failure_does_not_claim_success_or_hide_process_warning(warning, monkeypatch):
    path, warning_id = warning
    def fail(*_):
        raise OSError("private-secret")
    monkeypatch.setattr(status, "atomic_write_json", fail)
    with pytest.raises(OSError):
        health.acknowledge_warning(path, warning_id)
    assert not health._ack_path(path).exists()
    monkeypatch.setattr(health, "_marker_write_failed", True)
    html = health.render_completeness(path, [])
    assert "Unacknowledged history recording warning" in html
    assert "warning could not be saved" in html


def _race_worker(path, warning_id, start, queue, action):
    start.wait(5)
    if action == "fail":
        with path.with_name(path.name + ".alerts.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            health.record_failed_update(path, ATTEMPT)
        queue.put("failed")
    else:
        try:
            health.acknowledge_warning(path, warning_id, now=NOW)
            queue.put("acknowledged")
        except (health.WarningChanged, BlockingIOError):
            queue.put("conflict")


def test_concurrent_process_failure_acknowledgement_never_hides_new_warning(warning):
    path, old_id = warning
    ctx = multiprocessing.get_context("fork")
    start, queue = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_race_worker, args=(path, old_id, start, queue, action))
                 for action in ("ack", "ack", "fail")]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert "failed" in results
    assert health._read_marker(path)["warning_id"] != old_id
    assert "Unacknowledged history recording warning" in health.render_completeness(path, [])
    assert len(health._read_acknowledgements(path)) <= 1


def test_busy_delivery_lock_ack_fails_fast_without_changing_claims(warning):
    path, warning_id = warning
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text("diagnostic-test-claim")
    with path.with_name(path.name + ".alerts.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            health.acknowledge_warning(path, warning_id)
    assert claim.read_text() == "diagnostic-test-claim"
    assert not health._ack_path(path).exists()


def test_ack_does_not_release_reservation_or_send_email(warning, monkeypatch):
    path, warning_id = warning
    monkeypatch.setattr(notifications, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *_: calls.append(1) or True)
    status.write_result(path, failure="sync", now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"
    claim = path.with_name(path.name + ".alerts.json")
    before = claim.read_bytes()
    health.acknowledge_warning(path, warning_id, now=NOW)
    assert claim.read_bytes() == before
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"
    assert calls == [1]


def test_audit_is_bounded_and_projects_only_safe_fields(warning):
    path, warning_id = warning
    health.acknowledge_warning(path, warning_id, now=NOW)
    raw = json.loads(health._ack_path(path).read_text())
    raw["acknowledgements"][0]["secret"] = "private-secret"
    health._ack_path(path).write_text(json.dumps(raw))
    assert "private-secret" not in health.render_completeness(path, [])
    for _ in range(health.ACK_LIMIT + 1):
        health.record_failed_update(path, ATTEMPT)
        health.acknowledge_warning(path, health._read_marker(path)["warning_id"], now=NOW)
    assert len(health._read_acknowledgements(path)) == health.ACK_LIMIT
    assert health._ack_path(path).stat().st_size < health.ACK_BYTES
    assert "private-secret" not in health._ack_path(path).read_text()


def test_endpoint_auth_confirmation_conflicts_and_storage_failure(warning, monkeypatch):
    from app import main
    path, warning_id = warning
    monkeypatch.setattr(main, "_ADMIN_TOKEN", "synthetic-admin")
    monkeypatch.setattr(main, "BACKUP_STATUS_PATH", path)
    client = TestClient(main.app)
    url = "/admin/backup/history/acknowledge"
    payload = {"warning_id": warning_id, "investigated": True}
    assert client.post(url, json=payload).status_code == 401
    assert client.post(url + "?token=synthetic-admin", json=payload).status_code == 401
    assert client.post(url, json=payload, cookies={"admin_session": "synthetic-admin"}).status_code == 401
    assert client.post(url, json=payload, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert client.get(url).status_code == 405
    assert not health._ack_path(path).exists()
    headers = {"X-Admin-Token": "synthetic-admin"}
    for bad in ([], {}, {"warning_id": warning_id}, {**payload, "investigated": "true"},
                {**payload, "warning_id": "<script>private-secret</script>"}):
        response = client.post(url, json=bad, headers=headers)
        assert response.status_code == 400
        assert "private-secret" not in response.text
    assert client.post(url, content="{bad", headers=headers).status_code == 400
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["missing_history_recovered"] is False
    with path.with_name(path.name + ".alerts.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert client.post(url, json=payload, headers=headers).status_code == 409
    health.record_failed_update(path, ATTEMPT)
    assert client.post(url, json=payload, headers=headers).status_code == 409
    payload["warning_id"] = health._read_marker(path)["warning_id"]
    health._ack_path(path).write_text("private-secret")
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 503
    assert "private-secret" not in response.text
    assert health._ack_path(path).read_text() == "private-secret"


def test_rendered_dashboard_ack_javascript(warning, monkeypatch, tmp_path):
    from app import main
    path, warning_id = warning
    monkeypatch.setattr(main, "_ADMIN_TOKEN", "synthetic-admin")
    monkeypatch.setattr(main, "render_backup_notification_history",
                        lambda: status.render_notification_history(path))
    client = TestClient(main.app)
    response = client.get("/admin", headers={"X-Admin-Token": "synthetic-admin"})
    assert response.status_code == 200
    assert f"data-warning-id='{warning_id}'" in response.text
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", response.text, re.DOTALL)
    script = tmp_path / "admin.js"
    script.write_text(scripts[0])
    runner = str(__import__("pathlib").Path(__file__).with_name("backup_history_ack_runner.cjs"))
    result = subprocess.run(["node", runner, str(script), warning_id],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
