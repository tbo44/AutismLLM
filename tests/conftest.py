from contextvars import ContextVar
from functools import wraps

import httpx
import pytest
from openai import OpenAI

from tests.llm_test_helpers import StubbedChat


_in_llm_request = ContextVar("in_llm_request", default=False)
_allow_live_llm = False


def pytest_configure(config):
    # Install before collection and fixture setup, independent of marker selection.
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    sdk_request = OpenAI.request
    network_request = httpx.HTTPTransport.handle_request

    @wraps(sdk_request)
    def track_llm_request(client, *args, **kwargs):
        token = _in_llm_request.set(True)
        try:
            return sdk_request(client, *args, **kwargs)
        finally:
            _in_llm_request.reset(token)

    @wraps(network_request)
    def guard_network_request(transport, request):
        # Stubs and MockTransport never enter HTTPTransport. Ordinary HTTP
        # traffic is unaffected, even if it uses the same host as a provider.
        if _in_llm_request.get() and not _allow_live_llm:
            pytest.fail(
                "Live LLM request blocked. Use a provider stub or httpx.MockTransport "
                "in routine tests, or mark an intentional live check with pytest.mark.llm_live.",
                pytrace=False,
            )
        return network_request(transport, request)

    patch.setattr(OpenAI, "request", track_llm_request)
    patch.setattr(httpx.HTTPTransport, "handle_request", guard_network_request)


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
