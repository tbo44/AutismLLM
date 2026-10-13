"""Real isolated PostgreSQL; separate disks/processes; EVERY delivery mocked."""
import asyncio
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import httpx
import psycopg
import pytest

from app import main
from app.postgres_test_alert_attempts import (
    PostgresTestAlertAttemptStore, create_test_alert_attempt_store,
)
from app.test_alert_attempts import AttemptCapacityError, TestAlertAttemptStore
from scripts.import_test_alert_attempts import import_attempts


SCHEMA = Path(__file__).resolve().parents[1] / "scripts/test_alert_attempts_schema.sql"
RESULT = {"sent": True, "configured": True, "error": None}


@pytest.fixture(scope="module")
def postgres_url(tmp_path_factory):
    """Never use an application database or a configured connection secret."""
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl")):
        pytest.skip("PostgreSQL server binaries required for isolated integration tests")
    root = tmp_path_factory.mktemp("alert-pg")
    data, socket = root / "data", root / "socket"
    socket.mkdir()
    subprocess.run(
        ["initdb", "-D", str(data), "-A", "trust", "-U", "alert_test"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["pg_ctl", "-D", str(data), "-l", str(root / "server.log"),
         "-o", f"-k {socket} -h '' -F", "-w", "start"],
        check=True, capture_output=True,
    )
    try:
        # Local socket only; generated test database, no credentials or network.
        yield f"host={socket} dbname=postgres user=alert_test"
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=True, capture_output=True,
        )


@pytest.fixture
def store(postgres_url):
    with psycopg.connect(postgres_url) as connection:
        connection.execute("DROP TABLE IF EXISTS public.maya_test_alert_attempts")
        connection.execute(SCHEMA.read_text())
    return PostgresTestAlertAttemptStore(postgres_url)


def test_read_only_history_paginates_original_archives(store, monkeypatch):
    delivery = Mock()
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
    expected = []
    for key, outcome in [("c-history", "unknown"), ("a-history", "delivered"), ("b-history", "not_found")]:
        store.claim(key)
        result = store.resolve(key, outcome)
        assert store.resolve(key, "unknown") == result
        expected.append({"key": key, **result})
    expected.sort(key=lambda row: row["key"])
    store.claim("pending-history")
    store.claim("completed-history")
    store.finish("completed-history", RESULT)
    before = store.review()
    restarted = PostgresTestAlertAttemptStore(store._database_url, ttl=-1)
    for offset, rows in [(0, expected[:2]), (2, expected[2:]), (4, [])]:
        assert restarted.history(2, offset) == {
            "attempts": rows, "total": 3, "limit": 2, "offset": offset,
        }
    assert store.review() == before
    assert store.read("completed-history") == ("completed", RESULT)
    for row in expected:
        assert not store.claim(row["key"])
        assert store.read(row["key"]) == ("archived", {k: v for k, v in row.items() if k != "key"})
    delivery.assert_not_called()


def _worker(url, disk, mode, pipe, start=None, key="independent-attempt", limit=1024):
    """Fresh interpreter and private filesystem, but the same durable database."""
    os.chdir(disk)
    os.environ["ADMIN_CRAWL_TOKEN"] = "mock-test-token"
    os.environ["TEST_ALERT_ATTEMPTS_BACKEND"] = "postgres"
    os.environ["TEST_ALERT_ATTEMPTS_DATABASE_URL"] = url
    os.environ["TEST_ALERT_ATTEMPTS_PATH"] = str(Path(disk) / "unused.sqlite3")
    main._test_alert_store = create_test_alert_attempt_store()
    main._test_alert_store.limit = limit
    main._TEST_ALERT_RETRY_WAIT = 3 if mode == "send" else 0

    def delivery():
        pipe.send("delivery")
        time.sleep(0.15)
        return {**RESULT, "sent": mode != "not_sent"}

    mock = Mock(side_effect=delivery)
    main.notifications.send_test_alert = mock
    if mode == "crash":
        main._test_alert_store.finish = lambda *args: os._exit(17)
    if mode in ("record_failure", "record_and_uncertainty"):
        main._test_alert_store.finish = Mock(side_effect=OSError("mock result write failed"))
    if mode == "record_and_uncertainty":
        main._test_alert_store.mark_uncertain = Mock(side_effect=OSError("mock write failed"))
    if start is not None:
        assert start.wait(10)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            response = await client.post("/admin/alerts/test", headers={
                "Authorization": "Bearer mock-test-token", "Idempotency-Key": key,
            })
            if mode == "replay":
                mock.assert_not_called()
            pipe.send((response.status_code, response.json()))

    asyncio.run(scenario())
    assert not Path(os.environ["TEST_ALERT_ATTEMPTS_PATH"]).exists()


def run_workers(url, tmp_path, modes, keys=None, limit=1024):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers, readers = [], []
    for index, mode in enumerate(modes):
        disk = tmp_path / f"disk-{index}-{time.monotonic_ns()}"
        disk.mkdir()
        reader, writer = context.Pipe(duplex=False)
        worker = context.Process(
            target=_worker,
            args=(url, str(disk), mode, writer, start,
                  keys[index] if keys else "independent-attempt", limit),
        )
        workers.append(worker)
        readers.append(reader)
        worker.start()
        writer.close()
    start.set()
    try:
        for worker, mode in zip(workers, modes):
            worker.join(20)
            assert worker.exitcode == (17 if mode == "crash" else 0)
        messages = []
        for reader in readers:
            while reader.poll():
                try:
                    messages.append(reader.recv())
                except EOFError:
                    break
        return messages
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        for reader in readers:
            reader.close()


def test_independent_disks_atomically_deliver_once(store, postgres_url, tmp_path):
    messages = run_workers(postgres_url, tmp_path, ["send", "send"])
    assert messages.count("delivery") == 1
    assert messages.count((200, RESULT)) == 2
    assert store.read("independent-attempt") == ("completed", RESULT)


@pytest.mark.parametrize("mode", ["send", "not_sent"])
def test_completed_result_survives_replacement_instance(store, postgres_url, tmp_path, mode):
    result = {**RESULT, "sent": mode != "not_sent"}
    assert run_workers(postgres_url, tmp_path, [mode]) == ["delivery", (200, result)]
    assert run_workers(postgres_url, tmp_path, ["replay"]) == [(200, result)]


@pytest.mark.parametrize("mode,state", [
    ("crash", "pending"), ("record_failure", "uncertain"),
    ("record_and_uncertainty", "pending"),
])
def test_ambiguous_delivery_never_reclaimed(store, postgres_url, tmp_path, mode, state):
    messages = run_workers(postgres_url, tmp_path, [mode])
    assert messages.count("delivery") == 1
    assert store.read("independent-attempt") == (state, None)
    # Even beyond TTL and in a new instance, the original key cannot resend.
    store.ttl = -1
    assert not store.claim("independent-attempt")
    messages = run_workers(postgres_url, tmp_path, ["replay"])
    assert len(messages) == 1
    assert messages[0][0] == 409
    assert "will not be resent" in messages[0][1]["detail"]


def test_capacity_is_atomic_across_different_keys(store, postgres_url, tmp_path):
    messages = run_workers(
        postgres_url, tmp_path, ["send", "send"],
        keys=["capacity-key-one", "capacity-key-two"], limit=1,
    )
    assert messages.count("delivery") == 1
    assert sorted(message[0] for message in messages if isinstance(message, tuple)) == [200, 503]
    assert store.review()["retained"] == 1


def test_archived_keys_stay_protected_and_late_finish_cannot_overwrite(store, postgres_url, tmp_path):
    store.limit = 1
    assert store.claim("independent-attempt")
    with pytest.raises(AttemptCapacityError):
        store.claim("different-key")
    review = store.review()
    assert review["attempts"] == [{"key": "independent-attempt", "state": "pending"}]
    result = store.resolve("independent-attempt", "unknown")
    assert store.resolve("independent-attempt", "delivered") == result
    with pytest.raises(RuntimeError):
        store.finish("independent-attempt", RESULT)
    store.mark_uncertain("independent-attempt")
    store.ttl = -1
    assert store.claim("different-key")
    assert not store.claim("independent-attempt")
    assert store.read("independent-attempt") == ("archived", result)
    assert store.review()["archived"] == 1
    assert run_workers(postgres_url, tmp_path, ["replay"])[0][0] == 409


def test_only_completed_results_expire_using_database_clock(store):
    store.claim("completed-key")
    store.finish("completed-key", RESULT)
    store.claim("uncertain-key")
    store.mark_uncertain("uncertain-key")
    store.claim("pending-key")
    store.claim("archived-key")
    store.resolve("archived-key", "not_found")
    store.ttl = -1
    assert store.claim("completed-key")
    for key in ("pending-key", "uncertain-key", "archived-key"):
        assert not store.claim(key)


def test_missing_schema_and_unreachable_shared_storage_fail_closed(
    store, postgres_url, tmp_path, monkeypatch,
):
    # API must never call the mocked mailer, nor create a SQLite fallback.
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", "mock-test-token")
    delivery = Mock(return_value=RESULT)
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            response = await client.post("/admin/alerts/test", headers={
                "Authorization": "Bearer mock-test-token", "Idempotency-Key": "independent-attempt",
            })
            assert response.status_code == 503

    with psycopg.connect(postgres_url) as connection:
        connection.execute("DROP TABLE public.maya_test_alert_attempts")
    monkeypatch.setattr(main, "_test_alert_store", store)
    asyncio.run(request())
    monkeypatch.setattr(main, "_test_alert_store", PostgresTestAlertAttemptStore(
        f"host={tmp_path / 'nonexistent-socket'} dbname=postgres user=alert_test"
    ))
    asyncio.run(request())
    delivery.assert_not_called()


def test_configuration_is_explicit_and_never_uses_unrelated_database(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_ALERT_ATTEMPTS_BACKEND", raising=False)
    monkeypatch.setenv("DATABASE_URL", "not-an-application-database")
    monkeypatch.setenv("TEST_ALERT_ATTEMPTS_DATABASE_URL", "also-not-selected")
    monkeypatch.setenv("TEST_ALERT_ATTEMPTS_PATH", str(tmp_path / "local.sqlite3"))
    assert isinstance(create_test_alert_attempt_store(), TestAlertAttemptStore)
    monkeypatch.setenv("TEST_ALERT_ATTEMPTS_BACKEND", "postgres")
    monkeypatch.delenv("TEST_ALERT_ATTEMPTS_DATABASE_URL")
    with pytest.raises(ValueError):
        create_test_alert_attempt_store()
    monkeypatch.setenv("TEST_ALERT_ATTEMPTS_BACKEND", "typo")
    with pytest.raises(ValueError):
        create_test_alert_attempt_store()


def test_import_preserves_all_claims_and_source_without_sending(store, tmp_path, monkeypatch):
    source = TestAlertAttemptStore(tmp_path / "old.sqlite3")
    for key in ("pending-key", "uncertain-key", "completed-key", "archived-key"):
        source.claim(key)
    source.mark_uncertain("uncertain-key")
    source.finish("completed-key", RESULT)
    archive = source.resolve("archived-key", "unknown")
    delivery = Mock(side_effect=AssertionError("Migration must never send"))
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
    assert import_attempts(source.path, store) == 4
    assert import_attempts(source.path, store) == 4  # Safe repeat while offline.
    for key in ("pending-key", "uncertain-key", "completed-key", "archived-key"):
        assert source.read(key) == store.read(key)
        assert not store.claim(key)
    store.ttl = -1
    assert not store.claim("pending-key")
    assert not store.claim("uncertain-key")
    assert not store.claim("archived-key")
    assert store.read("archived-key") == ("archived", archive)
    delivery.assert_not_called()


def test_import_conflict_rolls_back_entire_batch_and_missing_file_is_not_created(store, tmp_path):
    source = TestAlertAttemptStore(tmp_path / "old.sqlite3")
    source.claim("first-key")
    source.claim("conflicting-key")
    store.claim("conflicting-key")
    store.mark_uncertain("conflicting-key")
    with pytest.raises(ValueError, match="Conflicting"):
        import_attempts(source.path, store)
    with pytest.raises(RuntimeError, match="disappeared"):
        store.read("first-key")
    assert store.read("conflicting-key") == ("uncertain", None)
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        import_attempts(missing, store)
    assert not missing.exists()
