"""Capacity reads must warn safely without modifying audits or sending email."""
from types import SimpleNamespace

import pytest

from app import github_backup_ack_archive as archive
from app import github_backup_capacity as capacity

GIB = 1024 ** 3


def disk(free=20 * GIB, total=100 * GIB, files=100000, available=20000):
    return SimpleNamespace(f_frsize=1, f_blocks=total, f_bavail=free,
                           f_files=files, f_favail=available)


@pytest.mark.parametrize("free,total,low", [
    (11 * GIB, 100 * GIB, False),
    (10 * GIB, 100 * GIB, True),
    (9 * GIB, 100 * GIB, True),
    (GIB + 1, 2 * GIB, False),
    (GIB, 2 * GIB, True),
    (GIB - 1, 2 * GIB, True),
    (0, 100 * GIB, True),
])
def test_byte_thresholds(tmp_path, monkeypatch, free, total, low):
    monkeypatch.setattr(capacity.os, "statvfs", lambda _: disk(free, total))
    html = capacity.render_capacity(tmp_path)
    assert ("storage is nearly full" in html) is low
    assert "available of" in html
    assert "capacity unavailable" not in html
    if low:
        assert "Downloading does not free space" in html
        assert "Do not delete audit history or email send-protection files" in html
        assert "outside this filesystem" in html


@pytest.mark.parametrize("files,available,low", [
    (100000, 10001, False), (100000, 10000, True),
    (100000, 9999, True), (5000, 1001, False),
    (5000, 1000, True), (5000, 999, True), (5000, 0, True),
    (0, 0, False),
])
def test_file_thresholds(tmp_path, monkeypatch, files, available, low):
    monkeypatch.setattr(capacity.os, "statvfs",
                        lambda _: disk(files=files, available=available))
    html = capacity.render_capacity(tmp_path)
    assert ("storage is nearly full" in html) is low
    assert ("file slots available" in html) is bool(files)


@pytest.mark.parametrize("error", [
    PermissionError("private-path private-secret"),
    OSError("private-path private-secret"),
    FileNotFoundError("private-path private-secret"),
])
def test_unavailable_is_sanitized_and_never_creates_storage(tmp_path, monkeypatch, error):
    def fail(_):
        raise error
    monkeypatch.setattr(capacity.os, "statvfs", fail)
    html = archive.render_retention(tmp_path / "missing" / "private-receipt.json")
    assert "capacity unavailable" in html
    assert "does not mean storage is healthy" in html
    assert "private" not in html.replace("privately", "")
    assert str(tmp_path) not in html
    assert list(tmp_path.iterdir()) == []
    assert "acknowledgements/export" in html


@pytest.mark.parametrize("data", [
    disk(total=0), disk(free=-1), disk(free=101 * GIB),
    disk(files=-1), disk(available=-1), disk(files=10, available=11),
])
def test_invalid_capacity_is_unavailable(tmp_path, monkeypatch, data):
    monkeypatch.setattr(capacity.os, "statvfs", lambda _: data)
    assert "capacity unavailable" in capacity.render_capacity(tmp_path)


def test_missing_archive_checks_only_parent(tmp_path, monkeypatch):
    calls = []
    directory = archive.archive_path(tmp_path / "receipt.json")
    def probe(path):
        calls.append(path)
        if path == directory:
            raise FileNotFoundError
        return disk()
    monkeypatch.setattr(capacity.os, "statvfs", probe)
    assert "available of" in archive.render_retention(tmp_path / "receipt.json")
    assert calls == [directory, directory.parent]
    assert not directory.exists()


def test_existing_archive_uses_own_filesystem_even_when_corrupt(tmp_path, monkeypatch):
    directory = archive.archive_path(tmp_path / "receipt.json")
    directory.mkdir()
    (directory / "damaged.json").write_text("private-secret")
    calls = []
    def probe(path):
        calls.append(path)
        return disk(free=0)
    monkeypatch.setattr(capacity.os, "statvfs", probe)
    html = archive.render_retention(tmp_path / "receipt.json")
    assert calls == [directory]
    assert "storage is nearly full" in html
    assert "archive unavailable or corrupt" in html
    assert "acknowledgements/export" not in html
    assert "private-secret" not in html
    assert (directory / "damaged.json").read_text() == "private-secret"


@pytest.mark.parametrize("state", ["healthy", "low", "unavailable"])
def test_authenticated_dashboard_and_export_preserve_files_without_mail(tmp_path, monkeypatch, state):
    from fastapi.testclient import TestClient
    from app import main, notifications, github_backup_status as status
    from app import github_backup_history_health as health

    path = tmp_path / "receipt.json"
    health.record_failed_update(path, {
        "at": "2026-10-14T00:00:00+00:00", "kind": "problem", "outcome": "sent",
    })
    health.acknowledge_warning(path, health._read_marker(path)["warning_id"])
    path.with_name(path.name + ".alerts.json").write_text("untouched-send-protection")
    directory = archive.archive_path(path)
    directory.mkdir()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(main, "_ADMIN_TOKEN", "synthetic-admin")
    monkeypatch.setattr(main, "BACKUP_STATUS_PATH", path)
    monkeypatch.setattr(main, "render_backup_notification_history",
                        lambda: status.render_notification_history(path))
    def no_mail(*_, **__):
        pytest.fail("Capacity viewing and export must not send mail")
    monkeypatch.setattr(notifications, "send_github_backup_alert", no_mail)
    monkeypatch.setattr(notifications, "_send_smtp", no_mail)
    monkeypatch.setattr(notifications, "_send_replit_mail", no_mail)
    def probe(target):
        assert target == directory
        if state == "unavailable":
            raise PermissionError("private-secret")
        return disk(free=0 if state == "low" else 20 * GIB)
    monkeypatch.setattr(capacity.os, "statvfs", probe)
    # No lifespan startup: the independent mail watchdog must not run in this test.
    client = TestClient(main.app)
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/backup/history/acknowledgements/export").status_code == 401
    auth = {"X-Admin-Token": "synthetic-admin"}
    response = client.get("/admin", headers=auth)
    assert response.status_code == 200
    assert ("storage is nearly full" in response.text) is (state == "low")
    assert ("capacity unavailable" in response.text) is (state == "unavailable")
    assert "private-secret" not in response.text
    assert str(tmp_path) not in response.text
    assert "acknowledgements/export" in response.text
    download = client.get("/admin/backup/history/acknowledgements/export", headers=auth)
    assert download.status_code == 200
    assert len(download.text.splitlines()) == 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
