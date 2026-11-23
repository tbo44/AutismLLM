from contextvars import ContextVar
from functools import wraps
from threading import Lock

import httpx
import pytest
from openai import OpenAI

from tests.llm_test_helpers import StubbedChat


_in_llm_request = ContextVar("in_llm_request", default=False)
_allow_live_llm = False
_REQUEST_LIMIT_OPTION = "--llm-request-limit"


def _positive_request_limit(value):
    limit = int(value)
    if limit < 1:
        raise ValueError("request limit must be positive")
    return limit


def pytest_addoption(parser):
    parser.addoption(
        _REQUEST_LIMIT_OPTION, type=_positive_request_limit,
        help="Required for llm_live tests: maximum total provider HTTP attempts, including retries.",
    )


def pytest_collection_finish(session):
    config = session.config
    if config.option.collectonly or not any(item.get_closest_marker("llm_live") for item in session.items):
        return
    if config.getoption(_REQUEST_LIMIT_OPTION) is None:
        raise pytest.UsageError(f"Live LLM tests require {_REQUEST_LIMIT_OPTION}=N (a positive integer).")
    if hasattr(config, "workerinput"):
        raise pytest.UsageError("Run live LLM tests in one pytest process (disable pytest-xdist).")


def pytest_configure(config):
    # Install before collection and fixture setup, independent of marker selection.
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    sdk_request = OpenAI.request
    network_request = httpx.HTTPTransport.handle_request
    mock_request = httpx.MockTransport.handle_request
    limit = config.getoption(_REQUEST_LIMIT_OPTION)
    used = 0
    budget_lock = Lock()

    def reserve_live_attempt():
        nonlocal used
        # Reserve immediately before dispatch, across clients, tests and worker
        # threads. Fail outside Exception so the SDK and /chat cannot retry or
        # turn local budget exhaustion into an answer fallback.
        with budget_lock:
            if limit is None:
                pytest.fail(f"Live LLM requests require {_REQUEST_LIMIT_OPTION}=N.", pytrace=False)
            if used >= limit:
                pytest.fail(
                    f"Live LLM request budget exhausted ({used}/{limit} HTTP attempts used); "
                    "the next attempt was blocked before sending.",
                    pytrace=False,
                )
            used += 1

    @wraps(sdk_request)
    def track_llm_request(client, *args, **kwargs):
        token = _in_llm_request.set(True)
        try:
            return sdk_request(client, *args, **kwargs)
        finally:
            _in_llm_request.reset(token)

    @wraps(network_request)
    def guard_network_request(transport, request):
        if _in_llm_request.get():
            if not _allow_live_llm:
                pytest.fail(
                    "Live LLM request blocked. Use a provider stub or httpx.MockTransport "
                    "in routine tests, or mark an intentional live check with pytest.mark.llm_live.",
                    pytrace=False,
                )
            reserve_live_attempt()
        return network_request(transport, request)

    @wraps(mock_request)
    def guard_mock_request(transport, request):
        # Exercise the same per-attempt accounting without sending real requests.
        # Mock traffic in ordinary tests is unaffected.
        if _in_llm_request.get() and _allow_live_llm:
            reserve_live_attempt()
        return mock_request(transport, request)

    patch.setattr(OpenAI, "request", track_llm_request)
    patch.setattr(httpx.HTTPTransport, "handle_request", guard_network_request)
    patch.setattr(httpx.MockTransport, "handle_request", guard_mock_request)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _allow_live_llm
    # Cover setup and teardown as well as the test body. A process-wide
    # exemption also applies to provider calls made by TestClient's app thread.
    _allow_live_llm = item.get_closest_marker("llm_live") is not None
    try:
        yield
    finally:
        _allow_live_llm = False


@pytest.fixture
def chat_stub(monkeypatch):
    """Use an isolated provider stub only in tests that request it."""
    return StubbedChat(monkeypatch)
