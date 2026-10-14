"""Long-term review retention must not repair evidence or change send claims."""
import json
import subprocess
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import github_backup_ack_archive as archive
from app import github_backup_history_health as health
from app import github_backup_status as status

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
ATTEMPT = {"at": NOW.isoformat(), "kind": "problem", "outcome": "sent"}


@pytest.fixture
def rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "ACK_LIMIT", 3)
    monkeypatch.setattr(health, "_marker_write_failed", False)
    path = tmp_path / "receipt.json"
    rows = []
    for _ in range(5):
        health.record_failed_update(path, ATTEMPT)
        rows.append(health.acknowledge_warning(path, health._read_marker(path)["warning_id"], now=NOW))
    return path, rows


def exported(path):
    with archive.export_snapshot(path) as snapshot:
        return [json.loads(line) for line in snapshot]


def test_rotation_export_permissions_restart_and_retention(rotated):
    path, rows = rotated
    assert health._read_acknowledgements(path) == rows[-3:]
    assert sorted(archive.archived_records(path), key=lambda row: row["warning_id"]) == sorted(
        rows[:2], key=lambda row: row["warning_id"])
    assert sorted(exported(path), key=lambda row: row["warning_id"]) == sorted(
        rows, key=lambda row: row["warning_id"])
    assert archive.archive_path(path).stat().st_mode & 0o777 == 0o700
    for file in archive.archive_path(path).iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
    result = subprocess.run(
        ["python", "-c", "from app.github_backup_ack_archive import archived_records; "
         "import sys; print(len(list(archived_records(sys.argv[1]))))", str(path)],
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.strip() == "2"
    html = health.render_completeness(path, [])
    assert "latest 3 reviews" in html
    assert "no automatic expiry" in html
    assert "previously discarded reviews cannot be recovered" in html
    assert "2 older reviews archived" in html
    assert "acknowledgements/export" in html


def test_full_active_limit_retains_first_review(tmp_path):
    path = tmp_path / "receipt.json"
    ids = []
    for _ in range(health.ACK_LIMIT + 2):
        health.record_failed_update(path, ATTEMPT)
        warning_id = health._read_marker(path)["warning_id"]
        ids.append(warning_id)
        health.acknowledge_warning(path, warning_id, now=NOW)
    assert len(health._read_acknowledgements(path)) == health.ACK_LIMIT
    assert {row["warning_id"] for row in archive.archived_records(path)} == set(ids[:2])
    assert {row["warning_id"] for row in exported(path)} == set(ids)


@pytest.mark.parametrize("damage", ["private-secret", '{"version":2}',
                                   '{"version":1,"acknowledgement":[]}', "\xff", "x" * 1025])
def test_archive_corruption_fails_export_without_overwrite(rotated, damage):
    path, _ = rotated
    damaged = next(archive.archive_path(path).iterdir())
    damaged.write_bytes(damage.encode("latin-1"))
    before = damaged.read_bytes()
    active = health._ack_path(path).read_bytes()
    with pytest.raises((ValueError, KeyError, TypeError)):
        exported(path)
    html = health.render_completeness(path, [])
    assert "archive unavailable or corrupt" in html
    assert "private-secret" not in html
    assert "acknowledgements/export" not in html
    assert damaged.read_bytes() == before
    assert health._ack_path(path).read_bytes() == active


def test_sanitized_fields_in_archive_and_export(rotated):
    path, _ = rotated
    file = next(archive.archive_path(path).iterdir())
    raw = json.loads(file.read_text())
    raw["secret"] = "private-secret"
    raw["acknowledgement"]["error"] = "private-secret"
    raw["acknowledgement"]["last_failed_update"]["token"] = "private-secret"
    file.write_text(json.dumps(raw))
    records = exported(path)
    assert "private-secret" not in json.dumps(records)
    assert "private-secret" not in health.render_completeness(path, [])
    assert all(set(row) == {"warning_id", "last_failed_update", "at", "actor",
                            "missing_history_recovered"} for row in records)
    assert "private-secret" in file.read_text()  # Read-only, not a repair.


def test_archive_first_failed_active_write_retry_deduplicates(rotated, monkeypatch):
    path, rows = rotated
    claim = path.with_name(path.name + ".alerts.json")
    claim.write_text("unchanged-send-claims")
    health.record_failed_update(path, ATTEMPT)
    warning_id = health._read_marker(path)["warning_id"]
    before = health._ack_path(path).read_bytes()
    real_write = status.atomic_write_json
    def fail(*_):
        raise OSError("private-secret")
    monkeypatch.setattr(status, "atomic_write_json", fail)
    with pytest.raises(OSError):
        health.acknowledge_warning(path, warning_id, now=NOW)
    assert health._ack_path(path).read_bytes() == before
    assert len(list(archive.archived_records(path))) == 3
    assert len(exported(path)) == len(rows)  # Overlap is not counted twice.
    monkeypatch.setattr(status, "atomic_write_json", real_write)
    health.acknowledge_warning(path, warning_id, now=NOW)
    assert len(exported(path)) == len(rows) + 1
    assert claim.read_text() == "unchanged-send-claims"
    assert health.acknowledge_warning(path, warning_id)["warning_id"] == warning_id


def test_corrupt_rotation_target_blocks_ack_and_preserves_both_audits(rotated):
    path, rows = rotated
    target = archive.archive_path(path) / (rows[2]["warning_id"] + ".json")
    target.write_text("private-secret")
    health.record_failed_update(path, ATTEMPT)
    before = health._ack_path(path).read_bytes()
    with pytest.raises(ValueError):
        health.acknowledge_warning(path, health._read_marker(path)["warning_id"])
    assert target.read_text() == "private-secret"
    assert health._ack_path(path).read_bytes() == before


def test_archive_write_failure_does_not_rotate_active(rotated, monkeypatch):
    path, _ = rotated
    health.record_failed_update(path, ATTEMPT)
    before = health._ack_path(path).read_bytes()
    def fail(*_):
        raise OSError("private-secret")
    monkeypatch.setattr(archive.os, "link", fail)
    with pytest.raises(OSError):
        health.acknowledge_warning(path, health._read_marker(path)["warning_id"])
    assert health._ack_path(path).read_bytes() == before
    assert len(list(archive.archived_records(path))) == 2


def test_export_authenticated_readonly_no_cache_and_safe_errors(rotated, monkeypatch):
    from app import main
    path, rows = rotated
    monkeypatch.setattr(main, "_ADMIN_TOKEN", "synthetic-admin")
    monkeypatch.setattr(main, "BACKUP_STATUS_PATH", path)
    client = TestClient(main.app)
    url = "/admin/backup/history/acknowledgements/export"
    assert client.get(url).status_code == 401
    assert client.get(url + "?token=synthetic-admin").status_code == 401
    assert client.get(url, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert client.get(url, cookies={"admin_session": "wrong"}).status_code == 403
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    for auth in ({"headers": {"X-Admin-Token": "synthetic-admin"}},
                 {"cookies": {"admin_session": "synthetic-admin"}}):
        response = client.get(url, **auth)
        assert response.status_code == 200
        assert len(response.text.splitlines()) == len(rows)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in response.headers["content-disposition"]
        assert "synthetic-admin" not in response.text
    assert before == {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    damaged = next(archive.archive_path(path).iterdir())
    damaged.write_text("private-secret")
    response = client.get(url, headers={"X-Admin-Token": "synthetic-admin"})
    assert response.status_code == 503
    assert "private-secret" not in response.text
    assert damaged.read_text() == "private-secret"


def test_empty_and_active_only_exports_do_not_create_archive(tmp_path):
    path = tmp_path / "receipt.json"
    assert exported(path) == []
    health.record_failed_update(path, ATTEMPT)
    row = health.acknowledge_warning(path, health._read_marker(path)["warning_id"], now=NOW)
    assert exported(path) == [row]
    assert not archive.archive_path(path).exists()
