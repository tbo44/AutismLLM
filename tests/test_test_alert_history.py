"""Read-only archived review: isolated storage and mocked email delivery."""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app import main
from app.test_alert_attempts import TestAlertAttemptStore


@pytest.fixture
def history_store(monkeypatch, tmp_path):
    store = TestAlertAttemptStore(tmp_path / "history.sqlite3")
    monkeypatch.setattr(main, "_test_alert_store", store)
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", "history-test-token")
    delivery = Mock()
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
    yield store, delivery
    delivery.assert_not_called()


def test_history_authorization_validation_and_storage_failure(history_store, monkeypatch):
    store, _ = history_store
    read = Mock(wraps=store.history)
    monkeypatch.setattr(store, "history", read)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            path = "/admin/alerts/test/attempts/history"
            for auth, status in [
                ({}, 401), ({"Authorization": "Bearer wrong"}, 403),
                ({"Authorization": "Basic history-test-token"}, 403),
            ]:
                assert (await client.get(path, headers=auth)).status_code == status
            read.assert_not_called()
            auth = {"Authorization": "Bearer history-test-token"}
            for query in ["limit=0", "limit=101", "limit=-1", "limit=abc", "offset=-1", "offset=abc"]:
                assert (await client.get(path + "?" + query, headers=auth)).status_code == 422
            read.assert_not_called()
            assert (await client.get(path, headers=auth)).json() == {
                "attempts": [], "total": 0, "limit": 25, "offset": 0,
            }
            for method in ("post", "delete", "patch"):
                assert (await getattr(client, method)(path, headers=auth)).status_code == 405
            read.side_effect = OSError("mock unavailable storage")
            assert (await client.get(path, headers=auth)).status_code == 503
            read.reset_mock()
            monkeypatch.delenv("ADMIN_CRAWL_TOKEN")
            assert (await client.get(path, headers=auth)).status_code == 503
            read.assert_not_called()

    asyncio.run(scenario())


def test_history_pagination_original_outcomes_and_permanent_protection(history_store):
    store, _ = history_store
    originals = []
    for key, outcome in [("c-attempt", "unknown"), ("a-attempt", "delivered"), ("b-attempt", "not_found")]:
        store.claim(key)
        result = store.resolve(key, outcome)
        originals.append({"key": key, **result})
        assert store.resolve(key, "unknown" if outcome != "unknown" else "delivered") == result
    originals.sort(key=lambda row: row["key"])
    store.claim("pending-attempt")
    store.claim("uncertain-attempt")
    store.mark_uncertain("uncertain-attempt")
    store.claim("completed-attempt")
    store.finish("completed-attempt", {"sent": True})
    before = store.review()
    # A restarted store with elapsed TTL must expose the same permanent records.
    main._test_alert_store = TestAlertAttemptStore(store.path, ttl=-1)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            auth = {"Authorization": "Bearer history-test-token"}
            path = "/admin/alerts/test/attempts/history"
            for offset, expected in [(0, originals[:2]), (2, originals[2:]), (4, [])]:
                response = await client.get(path, params={"limit": 2, "offset": offset}, headers=auth)
                assert response.status_code == 200
                assert response.json() == {
                    "attempts": expected, "total": 3, "limit": 2, "offset": offset,
                }
            # History alone must not even prune expired completed results.
            assert store.review() == before
            assert store.read("completed-attempt") == ("completed", {"sent": True})
            assert store.read("pending-attempt") == ("pending", None)
            assert store.read("uncertain-attempt") == ("uncertain", None)
            for row in originals:
                retry = await client.post("/admin/alerts/test", headers={
                    **auth, "Idempotency-Key": row["key"],
                })
                assert retry.status_code == 409
                assert store.read(row["key"]) == ("archived", {k: v for k, v in row.items() if k != "key"})
            # A send retry can prune ordinary completed results; never archives.
            assert store.review()["archived"] == 3
            assert store.read("pending-attempt") == ("pending", None)
            assert store.read("uncertain-attempt") == ("uncertain", None)

    asyncio.run(scenario())


def test_history_dashboard_script():
    subprocess.run(
        ["node", str(Path(__file__).with_name("admin_test_alert_history_runner.cjs"))],
        check=True, capture_output=True, text=True,
    )
