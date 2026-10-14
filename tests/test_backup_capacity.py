"""Capacity reads must warn safely without modifying audits or sending email."""
import json
import os
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app import github_backup_ack_archive as archive
from app import github_backup_capacity as capacity

GIB = 1024 ** 3
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_operator_quota(monkeypatch):
    for key in ("LIMIT_BYTES", "USED_BYTES", "MEASURED_AT", "SNAPSHOT_PATH"):
        monkeypatch.delenv("BACKUP_STORAGE_QUOTA_" + key, raising=False)


def configure_quota(monkeypatch, used=20 * GIB, limit=100 * GIB, measured=NOW):
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_LIMIT_BYTES", str(limit))
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_USED_BYTES", str(used))
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_MEASURED_AT", measured.isoformat())


def snapshot_data(used=20 * GIB, limit=100 * GIB, measured=NOW):
    return {"limit_bytes": limit, "used_bytes": used, "measured_at": measured.isoformat()}


def publish_snapshot(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


@pytest.mark.parametrize("used,warning", [
    (0, None), (20 * GIB, None), (90 * GIB, "nearly full"),
    (GIB, "nearly full"), (101 * GIB, "reached or exceeded"),
])
def test_file_quota_overrides_environment(tmp_path, monkeypatch, used, warning):
    path = tmp_path / "quota.json"
    limit = 2 * GIB if used == GIB else 100 * GIB
    publish_snapshot(path, snapshot_data(used=used, limit=limit))
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    configure_quota(monkeypatch, used=100 * GIB)
    before = path.read_bytes()
    html = capacity.render_quota(now=NOW)
    assert "quota unavailable" not in html
    assert ("role='alert'" in html) is bool(warning)
    if warning:
        assert f"quota is {warning}" in html
    assert path.read_bytes() == before


@pytest.mark.parametrize("content,known_limit", [
    (b"", False), (b"private-secret <script>", False),
    (b"\xff", False), (b"[]", False), (b"null", False),
    (b'{"limit_bytes": 1, "limit_bytes": 2}', False),
    (b"[" * 2000, False),
    (b"x" * (capacity.QUOTA_SNAPSHOT_MAX_BYTES + 1), False),
    (json.dumps({"limit_bytes": 100 * GIB}).encode(), True),
    (json.dumps({**snapshot_data(), "used_bytes": True}).encode(), True),
    (json.dumps({**snapshot_data(), "used_bytes": -1}).encode(), True),
    (json.dumps({**snapshot_data(), "used_bytes": 1.5}).encode(), True),
    (json.dumps({**snapshot_data(), "used_bytes": "9" * 21}).encode(), True),
    (json.dumps({**snapshot_data(), "limit_bytes": False}).encode(), False),
    (json.dumps({**snapshot_data(), "limit_bytes": 0}).encode(), False),
    (json.dumps({**snapshot_data(), "measured_at": None}).encode(), True),
    (json.dumps({**snapshot_data(), "measured_at": "private-secret <script>"}).encode(), True),
    (json.dumps({**snapshot_data(), "measured_at": "2026-10-14T12:00:00"}).encode(), True),
    (json.dumps({**snapshot_data(), "measured_at": "x" * 65}).encode(), True),
])
def test_corrupt_file_is_sanitized_without_environment_fallback(
        tmp_path, monkeypatch, content, known_limit):
    path = tmp_path / "private-path.json"
    path.write_bytes(content)
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    configure_quota(monkeypatch)
    html = capacity.render_quota(now=NOW)
    assert "quota unavailable" in html
    assert ("Known operator-configured limit: 100.0 GiB" in html) is known_limit
    assert "private-path" not in html and "private-secret" not in html
    assert "<script>" not in html and str(tmp_path) not in html
    assert path.read_bytes() == content


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "unreadable"])
def test_unreadable_file_is_unavailable_and_does_not_create_storage(
        tmp_path, monkeypatch, kind):
    path = tmp_path / "private-path.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "unreadable":
        def denied(*_, **__):
            raise PermissionError("private-secret")
        monkeypatch.setattr(capacity.os, "open", denied)
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    configure_quota(monkeypatch)
    before = list(tmp_path.iterdir())
    html = capacity.render_quota(now=NOW)
    assert "quota unavailable" in html
    assert "Known operator-configured limit" not in html
    assert "private" not in html and str(tmp_path) not in html
    assert list(tmp_path.iterdir()) == before


@pytest.mark.parametrize("age,available", [
    (0, True), (24 * 3600, True), (24 * 3600 + 1, False), (-1, False),
])
def test_file_measurement_freshness(tmp_path, monkeypatch, age, available):
    path = tmp_path / "quota.json"
    publish_snapshot(path, snapshot_data(measured=NOW - timedelta(seconds=age)))
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    html = capacity.render_quota(now=NOW)
    assert ("quota unavailable" not in html) is available
    if not available:
        assert "Known operator-configured limit: 100.0 GiB" in html


def test_concurrent_atomic_replacement_reads_one_complete_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    publish_snapshot(path, snapshot_data())
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    original = capacity.os.fdopen
    def replace_after_open(fd, *args, **kwargs):
        publish_snapshot(path, snapshot_data(used=199 * GIB, limit=200 * GIB))
        return original(fd, *args, **kwargs)
    monkeypatch.setattr(capacity.os, "fdopen", replace_after_open)
    html = capacity.render_quota(now=NOW)
    assert "20.0 GiB used of 100.0 GiB" in html
    monkeypatch.setattr(capacity.os, "fdopen", original)
    html = capacity.render_quota(now=NOW)
    assert "199.0 GiB used of 200.0 GiB" in html
    assert "quota is nearly full" in html
    assert sorted(p.name for p in tmp_path.iterdir()) == ["quota.json"]


def test_file_growth_after_stat_is_still_bounded(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    publish_snapshot(path, snapshot_data())
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    original = capacity.os.fdopen
    reads = []
    def grow_after_stat(fd, *args, **kwargs):
        path.write_bytes(b" " * (capacity.QUOTA_SNAPSHOT_MAX_BYTES + 100))
        stream = original(fd, *args, **kwargs)
        original_read = stream.read
        def bounded_read(size):
            reads.append(size)
            return original_read(size)
        stream.read = bounded_read
        return stream
    monkeypatch.setattr(capacity.os, "fdopen", grow_after_stat)
    assert "quota unavailable" in capacity.render_quota(now=NOW)
    assert reads == [capacity.QUOTA_SNAPSHOT_MAX_BYTES + 1]


@pytest.mark.parametrize("used,limit,low,reached", [
    (0, 100 * GIB, False, False),
    (89 * GIB, 100 * GIB, False, False),
    (90 * GIB, 100 * GIB, True, False),
    (91 * GIB, 100 * GIB, True, False),
    (GIB - 1, 2 * GIB, False, False),
    (GIB, 2 * GIB, True, False),
    (GIB + 1, 2 * GIB, True, False),
    (100 * GIB, 100 * GIB, True, True),
    (101 * GIB, 100 * GIB, True, True),
])
def test_quota_thresholds(monkeypatch, used, limit, low, reached):
    configure_quota(monkeypatch, used, limit)
    html = capacity.render_quota(now=NOW)
    assert "operator-configured" in html
    assert "quota unavailable" not in html
    assert ("role='alert'" in html) is low
    assert ("reached or exceeded" in html) is reached
    assert ("quota is nearly full" in html) is (low and not reached)
    assert "separate from local filesystem capacity" in html
    if low:
        assert "outside this quota" in html
        assert "Do not delete audit history or email send-protection files" in html
        assert "this check sends no email" in html


@pytest.mark.parametrize("key,value,known_limit", [
    ("LIMIT_BYTES", None, False), ("LIMIT_BYTES", "0", False),
    ("LIMIT_BYTES", "-1", False), ("LIMIT_BYTES", "1.5", False),
    ("LIMIT_BYTES", "9" * 21, False),
    ("LIMIT_BYTES", "<script>private-secret</script>", False),
    ("USED_BYTES", None, True), ("USED_BYTES", "-1", True),
    ("USED_BYTES", "NaN", True), ("USED_BYTES", "inf", True),
    ("USED_BYTES", "private-path private-secret", True),
    ("MEASURED_AT", None, True),
    ("MEASURED_AT", "2026-10-14T12:00:00", True),
    ("MEASURED_AT", "<script>private-secret</script>", True),
    ("MEASURED_AT", "x" * 65, True),
])
def test_unavailable_quota_sanitizes_input(monkeypatch, key, value, known_limit):
    configure_quota(monkeypatch)
    name = "BACKUP_STORAGE_QUOTA_" + key
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    html = capacity.render_quota(now=NOW)
    assert "quota unavailable" in html
    assert "does not mean storage is healthy" in html
    assert ("Known operator-configured limit: 100.0 GiB" in html) is known_limit
    assert "private-path" not in html
    assert "private-secret" not in html
    assert "<script>" not in html


@pytest.mark.parametrize("age,available", [
    (0, True), (24 * 3600, True), (24 * 3600 + 1, False), (-1, False),
])
def test_quota_measurement_freshness(monkeypatch, age, available):
    configure_quota(monkeypatch, measured=NOW - timedelta(seconds=age))
    assert ("quota unavailable" not in capacity.render_quota(now=NOW)) is available


def test_quota_timestamp_timezone_is_normalized(monkeypatch):
    configure_quota(monkeypatch)
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_MEASURED_AT", "2026-10-14T14:00:00+02:00")
    assert "2026-10-14T12:00:00+00:00" in capacity.render_quota(now=NOW)
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_MEASURED_AT", "2026-10-14T12:00:00Z")
    assert "quota unavailable" not in capacity.render_quota(now=NOW)


def test_missing_quota_is_not_inferred_from_healthy_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity.os, "statvfs", lambda _: disk())
    html = capacity.render_capacity(tmp_path)
    assert "available of" in html
    assert "quota unavailable" in html
    assert "Known operator-configured limit" not in html


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
@pytest.mark.parametrize("quota_state", ["healthy", "low", "exceeded", "missing", "stale", "corrupt"])
@pytest.mark.parametrize("source", ["environment", "file"])
def test_authenticated_dashboard_and_export_preserve_files_without_mail(
        tmp_path, monkeypatch, state, quota_state, source):
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
    if quota_state != "missing":
        measured = datetime.now(timezone.utc)
        if quota_state == "stale":
            measured -= timedelta(days=2)
        used = {"low": 95 * GIB, "exceeded": 101 * GIB}.get(quota_state, 20 * GIB)
        configure_quota(monkeypatch, used=used, measured=measured)
        if quota_state == "corrupt":
            monkeypatch.setenv("BACKUP_STORAGE_QUOTA_USED_BYTES", "private-secret")
    quota_path = tmp_path / "private-quota.json"
    if source == "file":
        monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(quota_path))
        if quota_state != "missing":
            publish_snapshot(quota_path, snapshot_data(used=used, measured=measured))
            if quota_state == "corrupt":
                quota_path.write_text("private-secret")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    # No lifespan startup: the independent mail watchdog must not run in this test.
    client = TestClient(main.app)
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/backup/history/acknowledgements/export").status_code == 401
    auth = {"X-Admin-Token": "synthetic-admin"}
    response = client.get("/admin", headers=auth)
    assert response.status_code == 200
    assert ("storage is nearly full" in response.text) is (state == "low")
    assert ("capacity unavailable" in response.text) is (state == "unavailable")
    assert ("quota unavailable" in response.text) is (quota_state in ("missing", "stale", "corrupt"))
    assert ("quota is nearly full" in response.text) is (quota_state == "low")
    assert ("quota is reached or exceeded" in response.text) is (quota_state == "exceeded")
    assert "private-secret" not in response.text
    assert str(tmp_path) not in response.text
    assert "acknowledgements/export" in response.text
    download = client.get("/admin/backup/history/acknowledgements/export", headers=auth)
    assert download.status_code == 200
    assert len(download.text.splitlines()) == 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if source == "file":
        # The same server sees new operator data, without changing export bytes.
        publish_snapshot(quota_path, snapshot_data(
            used=101 * GIB, measured=datetime.now(timezone.utc)))
        before_reload = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        refreshed = client.get("/admin", headers=auth)
        assert refreshed.status_code == 200
        assert "quota unavailable" not in refreshed.text
        assert "quota is reached or exceeded" in refreshed.text
        assert ("storage is nearly full" in refreshed.text) is (state == "low")
        assert ("capacity unavailable" in refreshed.text) is (state == "unavailable")
        again = client.get("/admin/backup/history/acknowledgements/export", headers=auth)
        assert again.status_code == 200
        assert again.content == download.content
        assert again.headers["content-disposition"] == download.headers["content-disposition"]
        assert before_reload == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
