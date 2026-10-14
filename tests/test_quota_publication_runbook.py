"""Exercise the copyable external-job helper without installing any job."""
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import github_backup_capacity as capacity


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
GIB = 1024 ** 3


@pytest.fixture
def publisher():
    document = (
        Path(__file__).resolve().parents[1] / "docs/BACKUP_STORAGE_QUOTA.md"
    ).read_text()
    code = document.split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    exec(compile(code, "BACKUP_STORAGE_QUOTA.md", "exec"), namespace)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    namespace["datetime"] = Clock
    return namespace["publish_quota"]


def publish(publisher, path, **overrides):
    values = dict(
        limit_bytes=100 * GIB, used_bytes=20 * GIB,
        measured_at=STAMP, admin_gid=os.getgid(),
    )
    values.update(overrides)
    publisher(path, **values)


@pytest.mark.parametrize("used", [0, 20 * GIB, 110 * GIB, "000123"])
@pytest.mark.parametrize("stamp", [
    STAMP, "2026-10-14T12:00:00Z", "2026-10-14T13:00:00+01:00",
    (NOW - timedelta(hours=24)).isoformat(),
])
def test_publication_matches_reader(publisher, tmp_path, monkeypatch, used, stamp):
    path = tmp_path / "snapshot.json"
    publish(publisher, path, used_bytes=used, measured_at=stamp)
    data = json.loads(path.read_text())
    assert data == {
        "limit_bytes": 100 * GIB, "used_bytes": used, "measured_at": stamp,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.stat().st_gid == os.getgid()
    assert list(tmp_path.iterdir()) == [path]
    monkeypatch.setenv("BACKUP_STORAGE_QUOTA_SNAPSHOT_PATH", str(path))
    assert "quota unavailable" not in capacity.render_quota(now=NOW)


@pytest.mark.parametrize("field,value", [
    ("limit_bytes", 0), ("limit_bytes", None), ("limit_bytes", False),
    ("limit_bytes", -1), ("limit_bytes", "1e6"),
    ("used_bytes", None), ("used_bytes", True), ("used_bytes", -1),
    ("used_bytes", 1.5), ("used_bytes", "1.0"), ("used_bytes", " 2"),
    ("used_bytes", "９"), ("used_bytes", 10 ** 20),
    ("measured_at", None), ("measured_at", ""), ("measured_at", "x" * 65),
    ("measured_at", "2026-10-14T12:00:00"),
    ("measured_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("measured_at", (NOW - timedelta(days=1, seconds=1)).isoformat()),
    ("admin_gid", None), ("admin_gid", -1), ("admin_gid", True),
])
def test_invalid_values_preserve_previous(publisher, tmp_path, field, value):
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"previous snapshot")
    with pytest.raises(ValueError):
        publish(publisher, path, **{field: value})
    assert path.read_bytes() == b"previous snapshot"
    assert list(tmp_path.iterdir()) == [path]


def test_requires_confirmed_absolute_path(publisher):
    with pytest.raises(ValueError, match="absolute"):
        publish(publisher, Path("snapshot.json"))


@pytest.mark.parametrize("operation", ["fchown", "fchmod", "fsync", "replace"])
def test_pre_replace_failure_is_visible_and_preserves_old_file(
    publisher, tmp_path, monkeypatch, operation,
):
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"previous snapshot")

    def fail(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError, match="simulated"):
        publish(publisher, path)
    assert path.read_bytes() == b"previous snapshot"
    assert list(tmp_path.iterdir()) == [path]


def test_post_replace_durability_failure_is_not_reported_as_success(
    publisher, tmp_path, monkeypatch,
):
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"previous snapshot")
    original = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory durability unconfirmed")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError, match="durability"):
        publish(publisher, path)
    assert json.loads(path.read_text())["measured_at"] == STAMP
    assert list(tmp_path.iterdir()) == [path]


def test_republication_keeps_measurement_time(publisher, tmp_path):
    path = tmp_path / "snapshot.json"
    stamp = (NOW - timedelta(hours=12)).isoformat()
    publish(publisher, path, measured_at=stamp)
    publish(publisher, path, measured_at=stamp)
    assert json.loads(path.read_text())["measured_at"] == stamp
