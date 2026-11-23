import socket
from contextlib import closing

import pytest

from rag.llm_client import UKAutismLLMClient


def test_unmarked_provider_request_fails_before_opening_a_connection(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/custom/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-only")

    def unexpected_connection(*args, **kwargs):
        pytest.fail("The LLM transport guard let a connection attempt through.", pytrace=False)

    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    llm = UKAutismLLMClient()
    # Enhancement normally catches SDK errors and returns the original query.
    # An accidental live call must instead fail the test.
    with closing(llm.client), pytest.raises(pytest.fail.Exception, match="Live LLM request blocked"):
        llm.enhance_query("What is autism?")
