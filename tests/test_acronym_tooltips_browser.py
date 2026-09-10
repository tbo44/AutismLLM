"""Browser-level regression test for acronym tooltips in the live chat UI."""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).parent.parent
EHCP_DEFINITION = "Education, Health and Care Plan"


@pytest.fixture(scope="module")
def live_server():
    """Run the real FastAPI application on an unused local port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
    env["SCHEDULED_REINDEX_ENABLED"] = "false"
    process = subprocess.Popen(
        [
            "python",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("The test server exited before becoming ready")
            try:
                with urlopen(f"{base_url}/api/acronyms", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("The test server did not become ready within 30 seconds")

        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.browser
@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touchscreen"])
def test_acronym_tooltip_is_visible_after_chat_response(live_server, mobile):
    """Rendered chat acronyms support mouse/keyboard and touchscreen dismissal."""
    with sync_playwright() as playwright:
        system_chromium = shutil.which("chromium")
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=system_chromium,
        )
        context = browser.new_context(**(playwright.devices["Pixel 5"] if mobile else {}))
        page = context.new_page()

        chat_requests = []

        def mock_chat(route):
            chat_requests.append(json.loads(route.request.post_data))
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "answer": "An EHCP explains the support a child needs.",
                        "sources": [],
                        "timestamp": "2026-09-10T00:00:00Z",
                    }
                ),
            )

        page.route("**/chat", mock_chat)
        with page.expect_response(
            lambda response: response.url.endswith("/api/acronyms")
        ):
            page.goto(live_server)

        page.get_by_label("Type your question").fill("What is an EHCP?")
        page.get_by_role("button", name="Send message").click()

        acronym = page.locator(".message.assistant .message-bubble abbr.maya-abbr").last
        acronym.wait_for(state="visible")

        assert chat_requests == [
            {
                "question": "What is an EHCP?",
                "comprehension_level": "standard",
            }
        ]
        assert acronym.text_content() == "EHCP"
        assert acronym.get_attribute("data-tooltip") == EHCP_DEFINITION

        if mobile:
            assert page.evaluate("navigator.maxTouchPoints") > 0
            assert page.evaluate("matchMedia('(hover: none)').matches")

            def wait_for_tooltip(opened):
                page.wait_for_function(
                    """({element, opened}) => {
                        const tooltip = getComputedStyle(element, '::after');
                        return element.classList.contains('open') === opened &&
                            tooltip.visibility === (opened ? 'visible' : 'hidden') &&
                            tooltip.opacity === (opened ? '1' : '0');
                    }""",
                    arg={"element": acronym.element_handle(), "opened": opened},
                    timeout=5000,
                )

            acronym.tap()
            wait_for_tooltip(True)
            page.get_by_label("Type your question").tap()
            wait_for_tooltip(False)

            acronym.tap()
            wait_for_tooltip(True)
            page.keyboard.press("Escape")
            wait_for_tooltip(False)
            # Dismissal must not prevent a later tap from reopening the explanation.
            acronym.tap()
            wait_for_tooltip(True)
            context.close()
            browser.close()
            return

        acronym.hover()
        page.wait_for_function(
            """element => {
                const tooltip = getComputedStyle(element, '::after');
                return tooltip.visibility === 'visible' && tooltip.opacity === '1';
            }""",
            arg=acronym.element_handle(),
        )

        page.mouse.move(0, 0)
        page.keyboard.press("Tab")
        acronym.focus()
        page.wait_for_function(
            """element => {
                const tooltip = getComputedStyle(element, '::after');
                return tooltip.visibility === 'visible' && tooltip.opacity === '1';
            }""",
            arg=acronym.element_handle(),
        )

        browser.close()