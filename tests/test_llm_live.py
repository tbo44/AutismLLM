"""Small, explicit health check for the live LLM provider."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.llm_test_helpers import _RATE_LIMIT_SENTINEL


pytestmark = pytest.mark.llm_live

client = TestClient(app)


def test_live_provider_does_not_emit_rate_limit_response():
    """A healthy provider must not be mistaken for a rate-limited response."""
    response = client.post(
        "/chat",
        json={"question": "What is the EHCP annual review process for autism?"},
    )

    assert response.status_code == 200, (
        f"Expected 200 from /chat, got {response.status_code}: {response.text}"
    )
    data = response.json()
    answer = data.get("answer", "")

    assert not data.get("rate_limited"), (
        "The live LLM smoke test received a rate-limited response; "
        "run this check only when the provider is healthy."
    )
    assert _RATE_LIMIT_SENTINEL not in answer, (
        "The rate-limit sentinel appeared in a live answer. This must fail "
        "instead of being silently skipped."
    )
    assert answer.strip(), "The live provider returned an empty answer."
    assert "## Short Answer" in answer, (
        "The live provider did not return a structured generated answer. "
        "This check must exercise synthesis rather than a fallback response."
    )