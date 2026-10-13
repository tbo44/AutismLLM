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


@pytest.mark.browser
def test_dashboard_targeted_crawl_resumes_polling_after_token_rotation(targeted_server, monkeypatch):
    server = targeted_server
    replacement_token = "rotated-browser-admin"
    token_key = "maya_admin_crawl_token"
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
            expect(page.locator("#historyTbody")).to_contain_text("No re-index history yet.")
            button = page.get_by_role("button", name="Re-crawl expiring pages")

            navigations, crawl_posts, prompts = [], [], []
            page.on("framenavigated", lambda frame: navigations.append(frame.url))
            page.on("request", lambda request: crawl_posts.append(request.url)
                    if request.method == "POST" and request.url in (
                        server.url + "/admin/crawl", server.url + "/admin/crawl/expiring",
                    ) else None)
            tokens = iter([server.token, replacement_token])

            def enter_token(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                dialog.accept(next(tokens))

            page.on("dialog", enter_token)
            # Record state before the native prompt blocks JavaScript, so we can
            # verify polling stopped and the rejected token was already cleared.
            page.evaluate("""(key) => {
                const originalPrompt = window.prompt;
                window.prompt = function(message) {
                    window.tokenPromptState = {
                        saved: sessionStorage.getItem(key),
                        token: _crawlToken,
                        timer: _pollTimer
                    };
                    return originalPrompt.call(window, message);
                };
            }""", token_key)
            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 200
            ) as initial_status, page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["count"] == 1
            assert initial_status.value.json()["running"] is True
            expect(page.locator("#kbStatusBadge")).to_contain_text("Re-indexing now")
            expect(button).to_be_disabled()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert server.requested == [server.due]
            original_task = main._crawl_task
            assert original_task is not None
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()

            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 403
            ) as rejected, page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status")
                and r.request.headers.get("authorization") == "Bearer " + replacement_token
            ) as resumed:
                monkeypatch.setenv("ADMIN_CRAWL_TOKEN", replacement_token)
            assert rejected.value.request.headers["authorization"] == "Bearer " + server.token
            assert resumed.value.status == 200
            assert resumed.value.json()["running"] is True
            assert len(prompts) == 2
            assert "re-crawl expiring pages" in prompts[0]
            assert "token was rejected" in prompts[1]
            assert "may have been rotated" in prompts[1]
            assert page.evaluate("window.tokenPromptState") == {
                "saved": None, "token": "", "timer": None,
            }
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == replacement_token
            assert page.evaluate("_crawlToken") == replacement_token
            assert page.evaluate("_pollTimer !== null") is True
            expect(button).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_disabled()
            assert main._crawl_task is original_task
            assert main._crawl_status["running"] is True
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert server.requested == [server.due]

            server.release.set()
            expect(page.locator("#reindexMsg")).to_have_text(
                "Re-index finished successfully.", timeout=15000,
            )
            expect(page.locator("#kbStatusBadge")).to_contain_text("Success")
            expect(page.locator("#cacheExpiring")).to_have_text("0")
            expect(button).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#historyTbody tr")).to_have_count(1)
            expect(page.locator("#historyTbody tr").first).to_contain_text("SUCCESS")
            expect(page.locator("#historyTbody tr").first).to_contain_text("manual-expiring-crawl")
            assert page.evaluate("_pollTimer") is None
            assert len(prompts) == 2
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert navigations == []
            assert server.requested == [server.due]
            server.store.reset_collection.assert_called_once()
            server.store.add_documents.assert_called_once()
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 0
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize("saved_token", [False, True], ids=["entered-token", "saved-token"])
def test_dashboard_targeted_crawl_retries_rejected_token(targeted_server, saved_token):
    server = targeted_server
    invalid_token = "rejected-browser-token"
    token_key = "maya_admin_crawl_token"
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

            navigations = []
            page.on("framenavigated", lambda frame: navigations.append(frame.url))
            if saved_token:
                page.evaluate(
                    "([key, token]) => { sessionStorage.setItem(key, token); _crawlToken = token; }",
                    [token_key, invalid_token],
                )
            prompts = []
            tokens = iter([server.token] if saved_token else [invalid_token, server.token])

            def enter_token(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                dialog.accept(next(tokens))

            page.on("dialog", enter_token)
            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as rejected:
                button.click()
            assert rejected.value.status == 403
            expect(page.locator("#reindexMsg")).to_have_text(
                "Token rejected — enter the current token and try again.",
            )
            expect(button).to_be_enabled()
            expect(page.locator("#cacheExpiring")).to_have_text("1")
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert len(prompts) == (0 if saved_token else 1)
            assert server.requested == []
            assert main._crawl_status["running"] is False
            assert main._crawl_task is None
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()

            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["count"] == 1
            expect(button).to_be_disabled()
            expect(page.locator("#reindexMsg")).to_have_text("Re-crawling 1 expiring cached page(s).")
            expect(page.locator("#kbStatusBadge")).to_contain_text("Re-indexing now")
            assert len(prompts) == (1 if saved_token else 2)
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert server.requested == [server.due]

            server.release.set()
            expect(page.locator("#reindexMsg")).to_have_text(
                "Re-index finished successfully.", timeout=15000,
            )
            expect(page.locator("#kbStatusBadge")).to_contain_text("Success")
            expect(page.locator("#cacheExpiring")).to_have_text("0")
            expect(button).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#historyTbody tr").first).to_contain_text("SUCCESS")
            expect(page.locator("#historyTbody tr").first).to_contain_text("manual-expiring-crawl")
            assert server.requested == [server.due]
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 0
            assert navigations == []  # Both attempts completed in the original dashboard document.
        finally:
            browser.close()
