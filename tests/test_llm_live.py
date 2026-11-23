"""Opt-in check of real model access and generated answers, without a local index."""

import openai
import pytest

from rag.llm_client import UKAutismLLMClient
from tests.llm_test_helpers import chat_client, search_hit


pytestmark = pytest.mark.llm_live


def _provider_failure(model, error):
    """Label SDK errors without printing credentials or raw provider responses."""
    if isinstance(error, openai.APIConnectionError):
        return f"Live LLM provider unreachable for model {model!r}; check the endpoint and network."
    status = error.status_code
    if error.code in ("model_not_found", "model_decommissioned", "model_not_available"):
        return (
            f"Live LLM model unavailable: {model!r} (HTTP {status}). "
            "Check LLM_MODEL and whether the provider account can use it."
        )
    if status in {401, 403}:
        return f"Live LLM provider denied access to {model!r} (HTTP {status}); check credentials and permissions."
    if status == 429:
        return f"Live LLM provider rate-limited {model!r} (HTTP 429); check provider quota and try again later."
    if status == 404:
        return f"Live LLM model or endpoint not found (HTTP 404); check configuration for {model!r}."
    return f"Live LLM provider request failed for {model!r} (HTTP {status})."


def _configured_client():
    try:
        return UKAutismLLMClient()
    except (ValueError, openai.OpenAIError):
        pass
    pytest.fail("Live LLM client could not initialize; check provider configuration and credentials.", pytrace=False)


def test_live_model_access_and_structured_answer(monkeypatch):
    llm_client = _configured_client()
    hit = search_hit(
        "EHCP annual review",
        "https://www.gov.uk/children-with-special-educational-needs",
        text="An Education, Health and Care plan (EHCP) is reviewed at least once a year.",
    )
    with llm_client.client as provider:
        live_create = provider.chat.completions.create

        def checked_create(**kwargs):
            try:
                return live_create(**kwargs)
            except (openai.APIConnectionError, openai.APIStatusError) as error:
                # Fail at the SDK boundary: the app would otherwise turn this
                # error into a fallback answer that hides the provider status.
                failure = _provider_failure(llm_client.model, error)
            pytest.fail(failure, pytrace=False)

        monkeypatch.setattr(provider.chat.completions, "create", checked_create)
        client = chat_client(monkeypatch, llm_client, lambda *_args, **_kwargs: [hit])
        response = client.post(
            "/chat", json={"question": "What is the EHCP annual review process for autism?"},
        )

    assert response.status_code == 200, f"Live /chat returned HTTP {response.status_code}."
    data = response.json()
    assert data["sources"] == [{
        "title": hit["metadata"]["title"],
        "url": hit["metadata"]["url"],
        "publisher": hit["metadata"]["source_name"],
    }], "Live chat did not return the expected source; it may have returned a fallback instead of a generated answer."
    lines = [line.strip() for line in data["answer"].splitlines() if line.strip()]
    assert "## Short Answer" in lines, (
        "Live answer regression: the provider responded, but the answer omitted the '## Short Answer' heading."
    )
    assert any(not line.startswith("#") for line in lines), (
        "Live answer regression: the provider returned headings without answer text."
    )