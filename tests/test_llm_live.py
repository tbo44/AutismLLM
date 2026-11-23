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
    """Return a safe diagnostic and whether the rejection blocks the live run."""
    if isinstance(error, openai.APIConnectionError):
        return f"Live LLM provider unreachable for model {model!r}; check the endpoint and network.", False
    status = error.status_code
    if error.code in ("model_not_found", "model_decommissioned", "model_not_available"):
        return (
            f"Live LLM model unavailable: {model!r} (HTTP {status}). "
            "Check LLM_MODEL and whether the provider account can use it.",
            False,
        )
    quota_codes = ("insufficient_quota", "billing_hard_limit_reached")
    if error.code in quota_codes or error.type in quota_codes:
        return (
            f"Live LLM provider quota exhausted for {model!r} (HTTP {status}); check provider billing and quota.",
            True,
        )
    if status in {401, 403}:
        return (
            f"Live LLM provider denied access to {model!r} (HTTP {status}); check credentials and permissions.",
            True,
        )
    if status == 429:
        return (
            f"Live LLM provider rate-limited {model!r} (HTTP 429); check provider quota and try again later.",
            False,
        )
    if status == 404:
        return f"Live LLM model or endpoint not found (HTTP 404); check configuration for {model!r}.", False
    return f"Live LLM provider request failed for {model!r} (HTTP {status}).", False


def _configured_client():
    try:
        return UKAutismLLMClient()
    except (ValueError, openai.OpenAIError):
        pass
    pytest.fail("Live LLM client could not initialize; check provider configuration and credentials.", pytrace=False)


class _LiveProviderRun:
    def __init__(self):
        self.rejected = False

    def complete(self, create, model, **kwargs):
        try:
            return create(model=model, **kwargs)
        except (openai.APIConnectionError, openai.APIStatusError) as error:
            failure, account_wide = _provider_failure(model, error)
            if account_wide:
                self.rejected = True
        # Fail outside the exception handler so pytest doesn't display the provider body.
        pytest.fail(failure, pytrace=False)


@pytest.fixture(scope="session")
def live_provider_run():
    return _LiveProviderRun()


@pytest.fixture(autouse=True)
def skip_after_provider_rejection(live_provider_run):
    if live_provider_run.rejected:
        pytest.skip("An earlier live check received an account-wide provider rejection; see its failure.")


@pytest.fixture
def live_answer(monkeypatch, live_provider_run):
    llm_client = _configured_client()
    with llm_client.client as provider:
        live_create = provider.chat.completions.create

        def checked_create(*, model, **kwargs):
            # Fail at the SDK boundary before the app can return a fallback.
            return live_provider_run.complete(live_create, model, **kwargs)

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


def test_live_model_access(live_provider_run):
    """Ask the configured model for one small completion without using /chat."""
    llm_client = _configured_client()
    with llm_client.client as provider:
        response = live_provider_run.complete(
            provider.with_options(max_retries=0).chat.completions.create,
            llm_client.model,
            messages=[{"role": "user", "content": "Reply with one word: OK."}],
            max_tokens=16,
        )

    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        pytest.fail(f"Live LLM model {llm_client.model!r} returned no completion text.", pytrace=False)


def test_live_model_access_and_structured_answer(live_answer):
    live_answer(ANNUAL_REVIEW.question, ANNUAL_REVIEW.hit)


@pytest.mark.parametrize("case", READING_CASES, ids=lambda case: case.id)
def test_live_clear_answers_are_simpler(live_answer, case):
    """Check both replies for missing or contradictory advice before comparing readability."""
    clear = live_answer(case.question, case.hit, "clear")
    complex_answer = live_answer(case.question, case.hit, "complex")

    check_reading_pair(case, clear=clear, complex_answer=complex_answer)