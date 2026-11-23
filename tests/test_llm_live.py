"""Opt-in model and answer checks; the pytest transport guard caps provider attempts."""

from contextlib import closing

import openai
import pytest

from rag.llm_client import UKAutismLLMClient
from tests.llm_answer_essentials import ANNUAL_REVIEW, READING_CASES
from tests.llm_reading_checks import check_answer_structure, check_reading_pair
from tests.llm_test_helpers import chat_client


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


@pytest.fixture
def live_answer(monkeypatch):
    llm_client = _configured_client()
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

        def ask(question, hit, level="standard"):
            # Don't enter the TestClient lifespan: it can start the real index.
            with closing(chat_client(monkeypatch, llm_client, lambda *_args, **_kwargs: [hit])) as client:
                response = client.post("/chat", json={"question": question, "comprehension_level": level})
            assert response.status_code == 200, f"Live /chat returned HTTP {response.status_code} ({level})."
            data = response.json()
            assert data["sources"] == [{
                "title": hit["metadata"]["title"],
                "url": hit["metadata"]["url"],
                "publisher": hit["metadata"]["source_name"],
            }], f"Live chat did not return the expected source ({level}); check for a fallback or redirect."
            check_answer_structure(data["answer"], level)
            return data["answer"]

        yield ask


def test_live_model_access_and_structured_answer(live_answer):
    live_answer(ANNUAL_REVIEW.question, ANNUAL_REVIEW.hit)


@pytest.mark.parametrize("case", READING_CASES, ids=lambda case: case.id)
def test_live_clear_answers_are_simpler(live_answer, case):
    """Check both replies for missing or contradictory advice before comparing readability."""
    clear = live_answer(case.question, case.hit, "clear")
    complex_answer = live_answer(case.question, case.hit, "complex")

    check_reading_pair(case, clear=clear, complex_answer=complex_answer)