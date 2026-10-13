"""Dashboard-to-crawler regression, isolated from real sources and vector data."""
import asyncio
import logging
import shutil
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

import app.main as main
import rag.crawler as crawler
from rag.sources import Source, SourceAuthority
from rag.structured_importer import StructuredKnowledgeImporter
from rag.vector_store import UKAutismVectorStore


@pytest.fixture
def targeted_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    token = "isolated-browser-admin"
    monkeypatch.setattr(main, "_ADMIN_TOKEN", token)
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", token)
    monkeypatch.setenv("CRAWL_CACHE_MAX_AGE_DAYS", "30")
    monkeypatch.setattr(main, "_login_attempts", {})
    monkeypatch.setattr(main, "_crawl_status", {
        "running": False, "last_run": None, "last_result": None, "alert": None,
    })
    monkeypatch.setattr(main, "_crawl_task", None)
    monkeypatch.setattr(main, "_startup_complete", True)
    monkeypatch.setattr(main.notifications, "reset_throttle", lambda: None)
    monkeypatch.setattr(main.notifications, "notify_reindex_failure_async", lambda *args: None)
    monkeypatch.setattr(main.notifications, "get_status", lambda: {"configured": False})
    source = Source("Test source", "https://source.example", SourceAuthority.NHS,
                    ["/due", "/fresh"], "Isolated crawl source")
    monkeypatch.setattr(crawler, "UK_SOURCES", [source])
    due, fresh = [source.base_url + path for path in source.crawl_paths]
    cache = crawler.CrawlCache()
    for url, age in [(due, 31), (fresh, 1)]:
        cache.update(url, etag=None, last_modified=None, content_hash="old",
                     chunks=[{"text": "Cached page", "metadata": {"url": url}}])
        cache._data[url]["cached_at"] = (
            datetime.now(timezone.utc) - timedelta(days=age)
        ).isoformat()
    cache._save()

    requested = []
    release = threading.Event()
    fail_refresh = threading.Event()

    async def transport(request):
        requested.append(str(request.url))
        while not release.is_set():
            await asyncio.sleep(0.01)
        if fail_refresh.is_set():
            return httpx.Response(503, text="Source temporarily unavailable")
        return httpx.Response(200, text=(
            "<html><title>Support guidance</title><body><article><h1>Support</h1><p>"
            + "Families can ask their local authority for information about education, "
              "health and care assessments and support available to children. " * 5
            + "</p></article></body></html>"
        ))

    async def enter(instance):
        instance.session = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        instance.delay = 0
        return instance

    monkeypatch.setattr(crawler.UKAutismCrawler, "__aenter__", enter)
    # Exercise real selection, extraction, caching and orchestration, but never
    # open/reset the project's real vector store or call an embedding provider.
    store = Mock(spec=UKAutismVectorStore)
    store.get_collection_stats.return_value = {"total_chunks": 2}
    monkeypatch.setattr("rag.vector_store.UKAutismVectorStore", lambda: store)
    monkeypatch.setattr(StructuredKnowledgeImporter, "import_file", lambda *args: [])
    rag = SimpleNamespace(vector_store=store)
    monkeypatch.setattr(main, "_rag_system", rag)
    monkeypatch.setattr(main, "_initialize_rag_sync", lambda: rag)
    (tmp_path / "logs").mkdir()
    history_logger = logging.Logger("isolated-targeted-browser")
    handler = logging.FileHandler(tmp_path / "logs/reindex.log")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    history_logger.addHandler(handler)
    monkeypatch.setattr(main, "_reindex_logger", history_logger)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{port}", token=token, due=due, fresh=fresh,
            requested=requested, release=release, store=store, fail_refresh=fail_refresh,
        )
    finally:
        release.set()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        handler.close()
        assert not thread.is_alive()


@pytest.mark.browser
@pytest.mark.parametrize("failed", [False, True], ids=["success", "failed-page-refresh"])
def test_dashboard_targeted_crawl_through_completion(targeted_server, failed):
    server = targeted_server
    if failed:
        server.fail_refresh.set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which("chromium"),
        )
        try:
            page = browser.new_page()
            page.goto(server.url + "/admin")
            page.get_by_label("Admin password").fill(server.token)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(server.url + "/admin")
            expect(page.locator("#cacheExpiring")).to_have_text("1")
            button = page.get_by_role("button", name="Re-crawl expiring pages")
            expect(button).to_be_enabled()
            page.on("dialog", lambda dialog: dialog.accept(server.token))
            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["count"] == 1
            expect(button).to_be_disabled()
            expect(page.locator("#reindexMsg")).to_have_text("Re-crawling 1 expiring cached page(s).")
            expect(page.locator("#kbStatusBadge")).to_contain_text("Re-indexing now")
            assert server.requested == [server.due]

            server.release.set()
            if failed:
                expect(page.locator("#reindexMsg")).to_contain_text(
                    "Page refresh incomplete:", timeout=15000,
                )
                expect(page.locator("#kbStatusBadge")).to_contain_text("Refresh incomplete")
                expect(page.locator("#kbDetails")).to_contain_text(server.due)
                expect(page.locator("#kbDetails")).to_contain_text("Index rebuilt")
                expect(page.locator("#cacheExpiring")).to_have_text("1")
                expect(button).to_be_enabled()  # Still stale; an operator can retry.
                expect(page.locator("#historyTbody tr").first).to_contain_text("FAILURE")
                result = main._crawl_status["last_result"]
                assert result["success"] is False
                assert result["index_success"] is True
                assert result["failed_refresh_urls"] == [server.due]
                assert result["failed_refresh_count"] == 1
                assert main._crawl_status["alert"]["source"] == "manual-expiring-crawl"
                cached = crawler.CrawlCache()
                assert cached._data[server.due]["chunks"][0]["text"] == "Cached page"
                assert cached.get(server.due) is None  # A failed refresh did not renew its age.
                # Reload also renders the same warning and failed URL server-side.
                page.reload()
                expect(page.locator("#kbStatusBadge")).to_contain_text("Refresh incomplete")
                expect(page.locator("#kbDetails")).to_contain_text(server.due)
            else:
                expect(page.locator("#reindexMsg")).to_have_text(
                    "Re-index finished successfully.", timeout=15000,
                )
                expect(page.locator("#kbStatusBadge")).to_contain_text("Success")
                expect(page.locator("#cacheExpiring")).to_have_text("0")
                expect(button).to_be_disabled()  # Nothing remains due for refresh.
                expect(page.locator("#historyTbody tr").first).to_contain_text("SUCCESS")
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#historyTbody tr").first).to_contain_text("manual-expiring-crawl")
            assert server.requested == [server.due]
            assert server.fresh not in server.requested
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == int(failed)
            if failed:
                assert next(c for c in indexed if c["metadata"]["url"] == server.due)["text"] == "Cached page"
        finally:
            browser.close()
