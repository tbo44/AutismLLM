"""Retry protection tests; delivery is always mocked, never real email."""
import asyncio
import threading
from unittest.mock import Mock

import httpx
import pytest

from app import main


@pytest.fixture
def alert_attempts(monkeypatch):
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", "isolated-alert-token")
    monkeypatch.setattr(main, "_test_alert_requests", {})
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
    monkeypatch.setattr(main, "_TEST_ALERT_LIMIT", 1)

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
            key = "isolated-attempt-1"
            created, task = main._test_alert_requests[key]
            main._test_alert_requests[key] = (created - main._TEST_ALERT_TTL, task)
            response = await client.post(
                "/admin/alerts/test", headers=headers("isolated-attempt-2"),
            )
            assert response.status_code == 200
            assert alert_attempts.call_count == 2

    asyncio.run(scenario())
