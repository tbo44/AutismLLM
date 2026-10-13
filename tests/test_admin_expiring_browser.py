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
@pytest.mark.parametrize(
    "prompt_input", [None, "", "   ", "\t  \t"],
    ids=["dismissed", "empty", "spaces", "tabs-and-spaces"],
)
def test_dashboard_test_alert_ignores_blank_token_then_retries(
    targeted_server, monkeypatch, prompt_input,
):
    server = targeted_server
    delivery = Mock(return_value={
        "sent": True, "configured": True,
        "recipient": "isolated@example.test", "error": None,
    })
    # Exercise the real authenticated endpoint, but never an email transport.
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
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
            button = page.get_by_role("button", name="Send test email")
            message = page.locator("#testAlertMsg")
            expect(button).to_be_enabled()
            initial_message = message.text_content()
            # Record attempted fetches synchronously, so a wrongly issued POST
            # cannot slip past the assertions while its response is in flight.
            page.evaluate("""() => {
                window.alertPosts = [];
                const originalFetch = window.fetch;
                window.fetch = function(url, options) {
                    if (url === '/admin/alerts/test' && options.method === 'POST') {
                        window.alertPosts.push({
                            url: url, authorization: options.headers.Authorization
                        });
                    }
                    return originalFetch.apply(this, arguments);
                };
            }""")
            requests = []
            page.on("request", lambda request: requests.append(request)
                    if request.url.endswith("/admin/alerts/test")
                    and request.method == "POST" else None)
            prompts = []
            tokens = iter([prompt_input, "  " + server.token + "  "])

            def enter_token(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                token = next(tokens)
                if token is None:
                    dialog.dismiss()
                else:
                    dialog.accept(token)

            page.on("dialog", enter_token)
            button.click()
            assert len(prompts) == 1
            assert page.evaluate("window.alertPosts") == []
            assert requests == []
            delivery.assert_not_called()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("(key) => localStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            expect(button).to_be_enabled()
            expect(message).to_have_text(initial_message)

            with page.expect_response(
                lambda r: r.url.endswith("/admin/alerts/test") and r.request.method == "POST"
            ) as sent:
                button.click()
            assert sent.value.status == 200
            assert sent.value.json()["sent"] is True
            expect(message).to_have_text("Email sent to isolated@example.test.")
            expect(button).to_be_enabled()
            assert len(prompts) == 2
            assert page.evaluate("window.alertPosts") == [{
                "url": "/admin/alerts/test", "authorization": "Bearer " + server.token,
            }]
            assert len(requests) == 1
            assert requests[0].headers["authorization"] == "Bearer " + server.token
            delivery.assert_called_once_with()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("(key) => localStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize("rejection_status", [401, 403], ids=["unauthorized", "forbidden"])
def test_dashboard_test_alert_clears_rejected_saved_token_then_retries(
    targeted_server, monkeypatch, rejection_status,
):
    server = targeted_server
    delivery = Mock(return_value={
        "sent": True, "configured": True,
        "recipient": "isolated@example.test", "error": None,
    })
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
    token_key = "maya_admin_crawl_token"
    rejected_token = "expired-isolated-browser-token"
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
            page.evaluate("""({key, token}) => {
                sessionStorage.setItem(key, token);
                _crawlToken = token;
            }""", {"key": token_key, "token": rejected_token})
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == rejected_token
            assert page.evaluate("_crawlToken") == rejected_token
            # A wrong Bearer token gives a real 403. Simulate the alternative
            # 401 response without changing authentication or sending email.
            if rejection_status == 401:
                page.route("**/admin/alerts/test", lambda route: route.fulfill(
                    status=401, content_type="application/json",
                    body='{"detail": "Unauthorized"}',
                ), times=1)
            requests, prompts = [], []
            page.on("request", lambda request: requests.append(request)
                    if request.url.endswith("/admin/alerts/test")
                    and request.method == "POST" else None)

            def enter_token(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                dialog.accept("  " + server.token + "  ")

            page.on("dialog", enter_token)
            button = page.get_by_role("button", name="Send test email")
            message = page.locator("#testAlertMsg")
            with page.expect_response(
                lambda r: r.url.endswith("/admin/alerts/test") and r.request.method == "POST"
            ) as rejected:
                button.click()
            assert rejected.value.status == rejection_status
            expect(message).to_have_text(
                "Token rejected — please click Send test email again and enter the current token."
            )
            expect(button).to_be_enabled()
            assert prompts == []  # A saved token was used without prompting.
            assert len(requests) == 1
            assert requests[0].headers["authorization"] == "Bearer " + rejected_token
            delivery.assert_not_called()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""

            with page.expect_response(
                lambda r: r.url.endswith("/admin/alerts/test") and r.request.method == "POST"
            ) as retried:
                button.click()
            assert retried.value.status == 200
            assert retried.value.json()["sent"] is True
            expect(message).to_have_text("Email sent to isolated@example.test.")
            expect(button).to_be_enabled()
            assert len(prompts) == 1
            assert "ADMIN_CRAWL_TOKEN" in prompts[0]
            assert len(requests) == 2
            assert requests[1].headers["authorization"] == "Bearer " + server.token
            delivery.assert_called_once_with()
            # Test-email prompts must not retain a newly entered credential.
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("(key) => localStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize("failure", ["delivery", "fetch"], ids=["delivery-failed", "fetch-rejected"])
def test_dashboard_test_alert_failure_allows_successful_retry(
    targeted_server, monkeypatch, failure,
):
    server = targeted_server
    success = {
        "sent": True, "configured": True,
        "recipient": "isolated@example.test", "error": None,
    }
    guidance = (
        "Could not connect to the SMTP server. Check SMTP_HOST, SMTP_PORT, "
        "and SMTP_STARTTLS, then try again."
    )
    delivery = Mock(side_effect=[
        {"sent": False, "configured": True, "recipient": "isolated@example.test",
         "error": guidance},
        success,
    ]) if failure == "delivery" else Mock(return_value=success)
    monkeypatch.setattr(main.notifications, "send_test_alert", delivery)
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
            page.evaluate("""({key, token}) => {
                sessionStorage.setItem(key, token);
                _crawlToken = token;
            }""", {"key": token_key, "token": server.token})
            prompts, requests = [], []
            page.on("dialog", lambda dialog: (prompts.append(dialog.message), dialog.dismiss()))
            page.on("request", lambda request: requests.append(request)
                    if request.url.endswith("/admin/alerts/test")
                    and request.method == "POST" else None)
            button = page.get_by_role("button", name="Send test email")
            message = page.locator("#testAlertMsg")
            if failure == "fetch":
                # Abort only this POST; the next click uses the real endpoint.
                page.route("**/admin/alerts/test", lambda route: route.abort("failed"), times=1)
                with page.expect_event(
                    "requestfailed",
                    predicate=lambda r: r.url.endswith("/admin/alerts/test") and r.method == "POST",
                ):
                    button.click()
                expect(message).to_have_text("Error: TypeError: Failed to fetch")
                delivery.assert_not_called()
            else:
                with page.expect_response(
                    lambda r: r.url.endswith("/admin/alerts/test") and r.request.method == "POST"
                ) as failed:
                    button.click()
                assert failed.value.status == 200
                assert failed.value.json()["sent"] is False
                expect(message).to_have_text(guidance)
                delivery.assert_called_once_with()
            expect(button).to_be_enabled()
            assert prompts == []
            assert len(requests) == 1
            assert requests[0].headers["authorization"] == "Bearer " + server.token
            # Transport failures must not discard a valid saved token.
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert page.evaluate("_crawlToken") == server.token

            with page.expect_response(
                lambda r: r.url.endswith("/admin/alerts/test") and r.request.method == "POST"
            ) as retried:
                button.click()
            assert retried.value.status == 200
            assert retried.value.json()["sent"] is True
            expect(message).to_have_text("Email sent to isolated@example.test.")
            expect(button).to_be_enabled()
            assert prompts == []
            assert len(requests) == 2
            assert requests[1].headers["authorization"] == "Bearer " + server.token
            assert delivery.call_count == (2 if failure == "delivery" else 1)
            assert all(call.args == () and call.kwargs == {} for call in delivery.call_args_list)
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert page.evaluate("_crawlToken") == server.token
        finally:
            browser.close()


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
def test_dashboard_targeted_crawl_cancels_initial_prompt_then_retries(targeted_server):
    server = targeted_server
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
            expect(page.locator("#reindexBtn")).to_be_enabled()

            crawl_posts, status_requests, prompts = [], [], []

            def record_request(request):
                if request.method == "POST" and request.url in (
                    server.url + "/admin/crawl", server.url + "/admin/crawl/expiring",
                    server.url + "/admin/reindex",
                ):
                    crawl_posts.append(request.url)
                if request.url == server.url + "/admin/crawl/status":
                    status_requests.append(request.headers.get("authorization"))

            def handle_prompt(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                if len(prompts) == 1:
                    dialog.dismiss()
                else:
                    dialog.accept(server.token)

            page.on("request", record_request)
            page.on("dialog", handle_prompt)
            # Observe more than two polling intervals after cancellation, not
            # just the timer's instantaneous state.
            page.evaluate("_pollInterval = 500")
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert page.evaluate("_pollTimer") is None
            initial_message = page.locator("#reindexMsg").inner_text()
            button.click()
            page.wait_for_timeout(1100)

            assert len(prompts) == 1
            assert "ADMIN_CRAWL_TOKEN" in prompts[0]
            assert "re-crawl expiring pages" in prompts[0]
            assert crawl_posts == []
            assert status_requests == []
            assert server.requested == []
            assert main._crawl_task is None
            assert main._crawl_status == {
                "running": False, "last_run": None, "last_result": None, "alert": None,
            }
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert page.evaluate("_pollTimer") is None
            expect(button).to_be_enabled()
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#cacheExpiring")).to_have_text("1")
            expect(page.locator("#reindexMsg")).to_have_text(initial_message)
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 1

            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 200
            ) as initial_status, page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["count"] == 1
            assert initial_status.value.json()["running"] is True
            assert len(prompts) == 2
            assert prompts[1] == prompts[0]
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert server.requested == [server.due]
            expect(button).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_disabled()
            expect(page.locator("#kbStatusBadge")).to_contain_text("Re-indexing now")
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert page.evaluate("_crawlToken") == server.token
            assert page.evaluate("_pollTimer !== null") is True

            server.release.set()
            expect(page.locator("#reindexMsg")).to_have_text(
                "Re-index finished successfully.", timeout=15000,
            )
            expect(page.locator("#kbStatusBadge")).to_contain_text("Success")
            expect(page.locator("#cacheExpiring")).to_have_text("0")
            expect(button).to_be_disabled()  # No expiring pages remain.
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#historyTbody tr")).to_have_count(1)
            expect(page.locator("#historyTbody tr").first).to_contain_text("SUCCESS")
            expect(page.locator("#historyTbody tr").first).to_contain_text("manual-expiring-crawl")
            assert main._crawl_status["running"] is False
            assert main._crawl_status["last_result"]["success"] is True
            assert main._crawl_task.done()
            assert main._crawl_task.exception() is None
            server.store.reset_collection.assert_called_once()
            server.store.add_documents.assert_called_once()
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 0
            assert page.evaluate("_pollTimer") is None
            completed_requests = list(status_requests)
            assert set(completed_requests) == {"Bearer " + server.token}
            page.wait_for_timeout(1100)
            assert status_requests == completed_requests
            assert len(prompts) == 2
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert server.requested == [server.due]
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize(
    "button_id, endpoint, history_source",
    [
        ("expiringBtn", "/admin/crawl/expiring", "manual-expiring-crawl"),
        ("reindexBtn", "/admin/crawl", "manual-crawl"),
    ],
    ids=["expiring-refresh", "full-reindex"],
)
@pytest.mark.parametrize("blank_token", ["", "   "], ids=["empty", "whitespace"])
def test_dashboard_crawl_ignores_blank_token_then_retries(
    targeted_server, button_id, endpoint, history_source, blank_token,
):
    server = targeted_server
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
            button = page.locator("#" + button_id)
            crawl_posts, status_requests, prompts = [], [], []

            def record_request(request):
                if request.method == "POST" and request.url in (
                    server.url + "/admin/crawl", server.url + "/admin/crawl/expiring",
                    server.url + "/admin/reindex",
                ):
                    crawl_posts.append(request)
                if request.url == server.url + "/admin/crawl/status":
                    status_requests.append(request)

            def handle_prompt(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                # Accept a blank value; do not dismiss the native prompt.
                dialog.accept(blank_token if len(prompts) == 1 else "  " + server.token + "  ")

            page.on("request", record_request)
            page.on("dialog", handle_prompt)
            page.evaluate("_pollInterval = 500")
            initial_message = page.locator("#reindexMsg").inner_text()
            button.click()
            # Observe multiple polling intervals to catch unintended timers.
            page.wait_for_timeout(1100)

            assert len(prompts) == 1
            assert "ADMIN_CRAWL_TOKEN" in prompts[0]
            assert crawl_posts == []
            assert status_requests == []
            assert server.requested == []
            assert main._crawl_task is None
            assert main._crawl_status == {
                "running": False, "last_run": None, "last_result": None, "alert": None,
            }
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert page.evaluate("_pollTimer") is None
            expect(page.locator("#expiringBtn")).to_be_enabled()
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#reindexMsg")).to_have_text(initial_message)
            expect(page.locator("#historyTbody")).to_contain_text("No re-index history yet.")
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 1

            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 200
            ) as initial_status, page.expect_response(
                lambda r: r.url == server.url + endpoint and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["status"] == "started"
            assert initial_status.value.json()["running"] is True
            assert len(prompts) == 2
            assert prompts[1] == prompts[0]
            assert [request.url for request in crawl_posts] == [server.url + endpoint]
            assert crawl_posts[0].headers["authorization"] == "Bearer " + server.token
            assert server.requested == [server.due]
            expect(page.locator("#expiringBtn")).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_disabled()
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert page.evaluate("_crawlToken") == server.token

            server.release.set()
            expect(page.locator("#reindexMsg")).to_have_text(
                "Re-index finished successfully.", timeout=15000,
            )
            expect(page.locator("#kbStatusBadge")).to_contain_text("Success")
            expect(page.locator("#cacheExpiring")).to_have_text("0")
            expect(page.locator("#reindexBtn")).to_be_enabled()
            expect(page.locator("#historyTbody tr")).to_have_count(1)
            expect(page.locator("#historyTbody tr").first).to_contain_text("SUCCESS")
            expect(page.locator("#historyTbody tr").first).to_contain_text(history_source)
            assert main._crawl_status["last_result"]["success"] is True
            server.store.reset_collection.assert_called_once()
            server.store.add_documents.assert_called_once()
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            expected_urls = [server.due] if button_id == "expiringBtn" else [server.due, server.fresh]
            assert server.requested == expected_urls
            assert page.evaluate("_pollTimer") is None
            completed_requests = list(status_requests)
            assert completed_requests
            assert {request.headers["authorization"] for request in completed_requests} == {
                "Bearer " + server.token,
            }
            page.wait_for_timeout(1100)
            assert status_requests == completed_requests
            assert len(crawl_posts) == 1
            assert len(prompts) == 2
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
def test_dashboard_targeted_crawl_keeps_running_after_replacement_prompt_cancel(
    targeted_server, monkeypatch,
):
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
            button = page.get_by_role("button", name="Re-crawl expiring pages")

            navigations, crawl_posts, status_requests, prompts = [], [], [], []
            page.on("framenavigated", lambda frame: navigations.append(frame.url))

            def record_request(request):
                if request.method == "POST" and request.url in (
                    server.url + "/admin/crawl", server.url + "/admin/crawl/expiring",
                    server.url + "/admin/reindex",
                ):
                    crawl_posts.append(request.url)
                if request.url == server.url + "/admin/crawl/status":
                    status_requests.append(request.headers.get("authorization"))

            def handle_prompt(dialog):
                assert dialog.type == "prompt"
                prompts.append(dialog.message)
                if len(prompts) == 1:
                    dialog.accept(server.token)
                else:
                    dialog.dismiss()

            page.on("request", record_request)
            page.on("dialog", handle_prompt)
            # Shorten the real polling interval, then observe multiple missed
            # intervals after dismissal rather than only checking timer state.
            page.evaluate("_pollInterval = 500")
            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 200
            ) as initial_status, page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/expiring") and r.request.method == "POST"
            ) as started:
                button.click()
            assert started.value.status == 200
            assert started.value.json()["count"] == 1
            assert initial_status.value.json()["running"] is True
            expect(button).to_be_disabled()
            expect(page.locator("#kbStatusBadge")).to_contain_text("Re-indexing now")
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) == server.token
            assert page.evaluate("_crawlToken") == server.token
            assert page.evaluate("_pollTimer !== null") is True
            assert server.requested == [server.due]
            original_task = main._crawl_task
            assert original_task is not None
            assert not original_task.done()
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()

            with page.expect_response(
                lambda r: r.url.endswith("/admin/crawl/status") and r.status == 403
            ) as rejected:
                monkeypatch.setenv("ADMIN_CRAWL_TOKEN", replacement_token)
            assert rejected.value.request.headers["authorization"] == "Bearer " + server.token
            # The response event precedes its JavaScript handler; wait for the
            # dismissed prompt's cancellation branch to finish.
            page.wait_for_function(
                "(key) => sessionStorage.getItem(key) === null"
                " && _crawlToken === '' && _pollTimer === null", arg=token_key,
            )
            assert len(prompts) == 2
            assert "re-crawl expiring pages" in prompts[0]
            assert "token was rejected" in prompts[1]
            assert "may have been rotated" in prompts[1]
            stopped_requests = list(status_requests)
            assert stopped_requests
            assert set(stopped_requests) == {"Bearer " + server.token}
            page.wait_for_timeout(1100)
            assert status_requests == stopped_requests
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert page.evaluate("_pollTimer") is None
            expect(button).to_be_disabled()
            expect(page.locator("#reindexBtn")).to_be_disabled()
            assert main._crawl_task is original_task
            assert main._crawl_status["running"] is True
            assert not original_task.done()
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert server.requested == [server.due]
            server.store.reset_collection.assert_not_called()
            server.store.add_documents.assert_not_called()

            server.release.set()
            deadline = time.monotonic() + 15
            while not original_task.done() and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert original_task.done()
            assert not original_task.cancelled()
            assert original_task.exception() is None
            assert main._crawl_task is original_task
            # Check completion independently: the cancelled dashboard must not
            # resume polling just because the background crawl has finished.
            response = httpx.get(
                server.url + "/admin/crawl/status",
                headers={"Authorization": "Bearer " + replacement_token},
            )
            assert response.status_code == 200
            status = response.json()
            assert status["running"] is False
            assert status["last_run"] is not None
            assert status["last_result"]["success"] is True
            assert status["last_result"]["index_success"] is True
            assert status["last_result"]["failed_refresh_count"] == 0
            assert len(status["history"]) == 1
            assert status["history"][0]["source"] == "manual-expiring-crawl"
            assert status["history"][0]["outcome"] == "SUCCESS"
            server.store.reset_collection.assert_called_once()
            server.store.add_documents.assert_called_once()
            indexed = server.store.add_documents.call_args.args[0]
            assert {chunk["metadata"]["url"] for chunk in indexed} == {server.due, server.fresh}
            assert crawler.CrawlCache().expiry_summary()["expiring_entries"] == 0

            page.wait_for_timeout(1100)
            assert status_requests == stopped_requests
            assert len(prompts) == 2
            assert crawl_posts == [server.url + "/admin/crawl/expiring"]
            assert navigations == []
            assert server.requested == [server.due]
            assert page.evaluate("(key) => sessionStorage.getItem(key)", token_key) is None
            assert page.evaluate("_crawlToken") == ""
            assert page.evaluate("_pollTimer") is None
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
