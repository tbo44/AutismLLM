"""Retry protection tests; delivery is always mocked, never real email."""
import asyncio
import multiprocessing
import os
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app import main
from app.test_alert_attempts import TestAlertAttemptStore


@pytest.fixture
def alert_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", "isolated-alert-token")
    monkeypatch.setattr(main, "_test_alert_store", TestAlertAttemptStore(tmp_path / "attempts.sqlite3"))
    delivery = Mock(return_value={"sent": True, "configured": True, "error": None})
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
    return delivery


def headers(key="isolated-attempt-1"):
    return {"Authorization": "Bearer isolated-alert-token", "Idempotency-Key": key}


@pytest.mark.parametrize("sent", [True, False])
def test_replay_result_and_allow_new_attempt(alert_attempts, sent):
    alert_attempts.return_value["sent"] = sent

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            first = await client.post("/admin/alerts/test", headers=headers())
            retry = await client.post("/admin/alerts/test", headers=headers())
            assert first.status_code == retry.status_code == 200
            assert first.json() == retry.json() == alert_attempts.return_value
            alert_attempts.assert_called_once_with()
            # Replaying a result must still require valid authentication.
            unauthorized = await client.post(
                "/admin/alerts/test", headers={"Idempotency-Key": "isolated-attempt-1"},
            )
            assert unauthorized.status_code == 401
            alert_attempts.assert_called_once_with()
            await client.post("/admin/alerts/test", headers=headers("isolated-attempt-2"))
            assert alert_attempts.call_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("authorization, status", [(None, 401), ("Bearer wrong", 403)])
def test_review_and_resolution_require_admin(alert_attempts, authorization, status):
    store = main._test_alert_store
    store.claim("protected-attempt")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            auth = {"Authorization": authorization} if authorization else {}
            review = await client.get("/admin/alerts/test/attempts", headers=auth)
            resolve = await client.post(
                "/admin/alerts/test/attempts/protected-attempt/resolve",
                headers=auth, json={"inbox_checked": True, "resolution": "delivered"},
            )
            assert review.status_code == resolve.status_code == status
            assert store.read("protected-attempt") == ("pending", None)
            alert_attempts.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["pending", "uncertain"])
@pytest.mark.parametrize("resolution", ["delivered", "not_found", "unknown"])
def test_archive_frees_capacity_without_unprotecting_original_key(
    alert_attempts, monkeypatch, state, resolution,
):
    store = main._test_alert_store
    store.limit = 1
    store.ttl = -1  # Elapsed retention must not evict unresolved or archived claims.
    store.claim("protected-attempt")
    if state == "uncertain":
        store.mark_uncertain("protected-attempt")
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            review = await client.get("/admin/alerts/test/attempts", headers=headers())
            assert review.json() == {
                "attempts": [{"key": "protected-attempt", "state": state}],
                "retained": 1, "archived": 0, "limit": 1,
            }
            assert (await client.post("/admin/alerts/test", headers=headers("new-attempt-key"))).status_code == 503
            url = "/admin/alerts/test/attempts/protected-attempt/resolve"
            payload = {"inbox_checked": True, "resolution": resolution}
            resolved = await client.post(url, headers=headers(), json=payload)
            assert resolved.status_code == 200
            assert resolved.json()["resolution"] == resolution
            # Repeated manual requests replay the original resolution, not overwrite it.
            repeated = await client.post(url, headers=headers(), json={
                "inbox_checked": True, "resolution": "unknown",
            })
            assert repeated.json() == resolved.json()
            alert_attempts.assert_not_called()
            assert (await client.get("/admin/alerts/test/attempts", headers=headers())).json() == {
                "attempts": [], "retained": 0, "archived": 1, "limit": 1,
            }
            # Fresh store object represents restart; archive survives TTL and pruning.
            monkeypatch.setattr(main, "_test_alert_store", TestAlertAttemptStore(store.path, ttl=-1, limit=1))
            for _ in range(2):
                replay = await client.post("/admin/alerts/test", headers=headers("protected-attempt"))
                assert replay.status_code == 409
                assert "permanently" in replay.json()["detail"] or "protected" in replay.json()["detail"]
            alert_attempts.assert_not_called()
            new = await client.post("/admin/alerts/test", headers=headers("new-attempt-key"))
            assert new.status_code == 200
            alert_attempts.assert_called_once_with()
            assert not main._test_alert_store.claim("protected-attempt")
            assert main._test_alert_store.read("protected-attempt")[0] == "archived"

    asyncio.run(scenario())


def test_resolution_validation_and_storage_fail_closed(alert_attempts, monkeypatch):
    store = main._test_alert_store
    store.claim("protected-attempt")
    store.claim("completed-attempt")
    store.finish("completed-attempt", alert_attempts.return_value)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            url = "/admin/alerts/test/attempts/protected-attempt/resolve"
            for payload, status in [
                ({"inbox_checked": False, "resolution": "delivered"}, 400),
                ({"inbox_checked": "yes", "resolution": "delivered"}, 422),
                ({"resolution": "delivered"}, 422),
                ({"inbox_checked": True, "resolution": "delete"}, 422),
            ]:
                response = await client.post(url, headers=headers(), json=payload)
                assert response.status_code == status
                assert store.read("protected-attempt") == ("pending", None)
            payload = {"inbox_checked": True, "resolution": "unknown"}
            for key, status in [("missing-attempt", 404), ("completed-attempt", 409)]:
                response = await client.post(
                    f"/admin/alerts/test/attempts/{key}/resolve", headers=headers(), json=payload,
                )
                assert response.status_code == status
            monkeypatch.setattr(store, "_connect", Mock(side_effect=OSError("Mock storage failure")))
            assert (await client.get("/admin/alerts/test/attempts", headers=headers())).status_code == 503
            assert (await client.post(url, headers=headers(), json=payload)).status_code == 503
            monkeypatch.delenv("ADMIN_CRAWL_TOKEN")
            assert (await client.get("/admin/alerts/test/attempts", headers=headers())).status_code == 503
            assert (await client.post(url, headers=headers(), json=payload)).status_code == 503
            alert_attempts.assert_not_called()

    asyncio.run(scenario())


def test_archive_at_default_capacity_keeps_all_claims(alert_attempts, monkeypatch):
    store = main._test_alert_store
    assert store.limit == 1024
    store.claim("protected-attempt")
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "INSERT INTO test_alert_attempts (key, state) VALUES (?, 'uncertain')",
            [(f"capacity-{i:04d}",) for i in range(1023)],
        )
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            assert (await client.post("/admin/alerts/test", headers=headers())).status_code == 503
            response = await client.post(
                "/admin/alerts/test/attempts/protected-attempt/resolve", headers=headers(),
                json={"inbox_checked": True, "resolution": "unknown"},
            )
            assert response.status_code == 200
            alert_attempts.assert_not_called()
            assert (await client.post("/admin/alerts/test", headers=headers())).status_code == 200
            # Full active capacity must still replay an archived key safely.
            assert (await client.post("/admin/alerts/test", headers=headers("protected-attempt"))).status_code == 409
            assert store.review()["retained"] == 1024
            assert store.review()["archived"] == 1
            with sqlite3.connect(store.path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM test_alert_attempts").fetchone()[0] == 1025
            alert_attempts.assert_called_once_with()

    asyncio.run(scenario())


def test_resolving_during_delivery_preserves_archive(alert_attempts, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def delivery():
        started.set()
        assert release.wait(5)
        return {"sent": True, "configured": True, "error": None}

    alert_attempts.side_effect = delivery
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            send = asyncio.create_task(client.post("/admin/alerts/test", headers=headers()))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                resolved = await client.post(
                    "/admin/alerts/test/attempts/isolated-attempt-1/resolve", headers=headers(),
                    json={"inbox_checked": True, "resolution": "not_found"},
                )
                assert resolved.status_code == 200
                release.set()
                assert (await send).status_code == 409
                assert main._test_alert_store.read("isolated-attempt-1")[0] == "archived"
                assert (await client.post("/admin/alerts/test", headers=headers())).status_code == 409
                alert_attempts.assert_called_once_with()
            finally:
                release.set()

    asyncio.run(scenario())


def test_concurrent_retry_survives_cancelled_request(alert_attempts):
    release = threading.Event()
    started = threading.Event()
    result = alert_attempts.return_value

    def delivery():
        started.set()
        assert release.wait(5)
        return result

    alert_attempts.side_effect = delivery

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            first = asyncio.create_task(client.post("/admin/alerts/test", headers=headers()))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                retry = asyncio.create_task(client.post("/admin/alerts/test", headers=headers()))
                await asyncio.sleep(0.02)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                release.set()
                response = await retry
                assert response.status_code == 200
                assert response.json() == result
                alert_attempts.assert_called_once_with()
            finally:
                release.set()

    asyncio.run(scenario())


def test_invalid_keys_capacity_and_expiry(alert_attempts, monkeypatch):
    main._test_alert_store.limit = 1

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            for key in ["", "short", "a" * 129, "invalid key"]:
                response = await client.post("/admin/alerts/test", headers=headers(key))
                assert response.status_code == 400
            alert_attempts.assert_not_called()
            await client.post("/admin/alerts/test", headers=headers())
            response = await client.post(
                "/admin/alerts/test", headers=headers("isolated-attempt-2"),
            )
            assert response.status_code == 503
            # Capacity must not evict the original result and permit duplicates.
            response = await client.post("/admin/alerts/test", headers=headers())
            assert response.status_code == 200
            alert_attempts.assert_called_once_with()
            with sqlite3.connect(main._test_alert_store.path) as connection:
                connection.execute(
                    "UPDATE test_alert_attempts SET completed_at = completed_at - ?",
                    (main._test_alert_store.ttl,),
                )
            response = await client.post(
                "/admin/alerts/test", headers=headers("isolated-attempt-2"),
            )
            assert response.status_code == 200
            assert alert_attempts.call_count == 2

    asyncio.run(scenario())


def _worker(path, mode, output, start=None):
    """Fresh interpreter/worker. Delivery is mocked in every subprocess."""
    os.environ["ADMIN_CRAWL_TOKEN"] = "isolated-alert-token"
    main._test_alert_store = TestAlertAttemptStore(Path(path))
    main._TEST_ALERT_RETRY_WAIT = 5
    result = {"sent": True, "configured": True, "error": None}

    def delivery():
        output.put("delivery")
        time.sleep(0.15)  # Keep ownership pending while the other worker retries.
        return result

    mock = Mock(side_effect=delivery)
    main.notifications.send_test_alert = mock
    if mode == "crash":
        # Mock delivery succeeded, then the process died before saving success.
        def crash_before_recording(*args):
            os._exit(17)
        main._test_alert_store.finish = crash_before_recording
    if start is not None:
        assert start.wait(10)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            response = await client.post("/admin/alerts/test", headers=headers())
            if mode == "replay":
                mock.assert_not_called()
            output.put((response.status_code, response.json()))

    asyncio.run(scenario())


@pytest.mark.parametrize("sent", [True, False])
def test_result_survives_fresh_process_restart(alert_attempts, sent):
    alert_attempts.return_value["sent"] = sent

    async def initial_send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            response = await client.post("/admin/alerts/test", headers=headers())
            assert response.status_code == 200
            assert response.json() == alert_attempts.return_value

    asyncio.run(initial_send())
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    path = str(main._test_alert_store.path)
    second = context.Process(target=_worker, args=(path, "replay", output))
    second.start()
    second.join(15)
    assert second.exitcode == 0
    assert output.get(timeout=5) == (200, alert_attempts.return_value)
    alert_attempts.assert_called_once_with()


def test_separate_workers_coordinate_one_delivery(alert_attempts):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    start = context.Event()
    workers = [
        context.Process(target=_worker, args=(str(main._test_alert_store.path), "send", output, start))
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        messages = [output.get(timeout=5) for _ in range(3)]
        assert messages.count("delivery") == 1
        assert messages.count((200, alert_attempts.return_value)) == 2
        alert_attempts.assert_not_called()
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()


def test_crash_after_external_delivery_does_not_resend(alert_attempts, monkeypatch):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=_worker, args=(str(main._test_alert_store.path), "crash", output),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 17
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            for _ in range(2):
                response = await client.post("/admin/alerts/test", headers=headers())
                assert response.status_code == 409
                assert "will not be resent" in response.json()["detail"]
            alert_attempts.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["delivery", "record", "record_and_uncertainty"])
def test_unexpected_failure_stays_uncertain(alert_attempts, monkeypatch, failure):
    store = main._test_alert_store
    if failure == "delivery":
        alert_attempts.side_effect = RuntimeError("Mock delivery interrupted")
    else:
        monkeypatch.setattr(store, "finish", Mock(side_effect=OSError("Mock disk failure")))
    if failure == "record_and_uncertainty":
        monkeypatch.setattr(store, "mark_uncertain", Mock(side_effect=OSError("Mock disk failure")))
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            for _ in range(2):
                response = await client.post("/admin/alerts/test", headers=headers())
                assert response.status_code == 409
                assert "will not be resent" in response.json()["detail"]
            alert_attempts.assert_called_once_with()

    asyncio.run(scenario())


def test_pending_claim_never_expires_or_is_evicted(alert_attempts, monkeypatch):
    store = main._test_alert_store
    store.limit = 1
    store.ttl = -1  # Even an elapsed retention window cannot discard a pending claim.
    assert store.claim("isolated-attempt-1")
    monkeypatch.setattr(main, "_TEST_ALERT_RETRY_WAIT", 0)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            retry = await client.post("/admin/alerts/test", headers=headers())
            assert retry.status_code == 409
            new = await client.post("/admin/alerts/test", headers=headers("isolated-attempt-2"))
            assert new.status_code == 503
            alert_attempts.assert_not_called()

    asyncio.run(scenario())


def test_storage_failure_fails_closed_but_keyless_behavior_is_unchanged(alert_attempts, monkeypatch):
    monkeypatch.setattr(
        main._test_alert_store, "claim", Mock(side_effect=OSError("Mock storage unavailable")),
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            response = await client.post("/admin/alerts/test", headers=headers())
            assert response.status_code == 503
            alert_attempts.assert_not_called()
            for _ in range(2):
                response = await client.post(
                    "/admin/alerts/test",
                    headers={"Authorization": "Bearer isolated-alert-token"},
                )
                assert response.status_code == 200
            assert alert_attempts.call_count == 2

    asyncio.run(scenario())
