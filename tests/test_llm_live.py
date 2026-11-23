"""Opt-in checks of real model access and generated answers, without a local index."""

import re
from contextlib import closing

import openai
import pytest

from rag.llm_client import UKAutismLLMClient
from tests.llm_answer_essentials import ANNUAL_REVIEW, READING_CASES, assert_essential_advice
from tests.llm_test_helpers import chat_client


pytestmark = pytest.mark.llm_live

# Common shorthand for these topics, including a few acceptable plain-English explanations.
ACRONYM_EXPANSIONS = {
    "EHCP": r"education,? health(?:,? and| &) care plan|school(?: support)? plan",
    "EHC": r"education,? health(?:,? and| &) care(?: plan)?|school(?: support)? plan",
    "DLA": r"disability living allowance|benefit for disabled children",
    "DWP": r"department for work and pensions",
    "GP": r"general practitioner|(?:family )?doctor",
    "ASD": r"autism spectrum disorder|autism",
}
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


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
            lines = [line.strip() for line in data["answer"].splitlines()]
            assert "## Short Answer" in lines, (
                f"Live answer regression ({level}): missing the '## Short Answer' heading."
            )
            assert WORD.search(_answer_prose(data["answer"])), (
                f"Live answer regression ({level}): no answer text under the headings."
            )
            return data["answer"]

        yield ask


def _visible_text(answer):
    """Drop URLs and Markdown emphasis while keeping visible link labels."""
    text = re.sub(r"\[([^\]]+)\]\(https?://[^\s)]+\)", r"\1", answer)
    return re.sub(r"<?https?://[^\s>]+>?", "", text).replace("*", "").replace("_", "")


def _answer_prose(answer):
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+.*$", "", _visible_text(answer))
    return re.sub(r"(?m)^[ \t]*(?:[-+•]|\d+[.)])[ \t]+", "", text)


def _unexplained_acronyms(answer):
    # Accept either "full name (ACR)" or "ACR (full name)" at first use.
    text = _visible_text(answer)
    unexplained = []
    for acronym, expansion in ACRONYM_EXPANSIONS.items():
        first = re.search(rf"\b{acronym}s?\b", text, re.IGNORECASE)
        if not first:
            continue
        name = rf"\b(?:{expansion})s?\b"
        before = re.search(
            rf"{name}\s*[,:(–—-]?\s*(?:(?:or|a|an)\s+)?$", text[:first.start()], re.IGNORECASE,
        )
        after = re.match(
            rf"(?:\s*[,:(–—-]\s*(?:or\s+)?|\s+(?:means|stands for|is)\s+)"
            rf"(?:(?:a|an|the|your)\s+)?{name}",
            text[first.end():],
            re.IGNORECASE,
        )
        if not (before or after):
            unexplained.append(acronym)
    return unexplained


def _reading_profile(answer):
    # Count list items and other nonempty lines as separate units, even if
    # they don't end with punctuation. Ignore headings, URLs and list numbers.
    groups = [
        WORD.findall(part) for part in re.split(r"[.!?]+(?=\s|$)|\n+", _answer_prose(answer))
    ]
    words = [word for group in groups for word in group]
    return len(words) / sum(bool(group) for group in groups), (
        sum(sum(letter.isalpha() for letter in word) >= 8 for word in words) / len(words)
    )


def test_live_model_access_and_structured_answer(live_answer):
    live_answer(ANNUAL_REVIEW.question, ANNUAL_REVIEW.hit)


@pytest.mark.parametrize("case", READING_CASES, ids=lambda case: case.id)
def test_live_clear_answers_are_simpler(live_answer, case):
    clear = live_answer(case.question, case.hit, "clear")
    complex_answer = live_answer(case.question, case.hit, "complex")

    assert_essential_advice(case, clear=clear, complex_answer=complex_answer)

    unexplained = _unexplained_acronyms(clear)
    assert not unexplained, (
        "Live answer quality regression: Clear used these acronyms without explaining them at first use: "
        + ", ".join(unexplained)
    )

    clear_sentence, clear_long = _reading_profile(clear)
    complex_sentence, complex_long = _reading_profile(complex_answer)
    sentence_gain = complex_sentence - clear_sentence
    long_word_gain = complex_long - clear_long
    # Clear should improve at least one signal without materially worsening the other.
    assert (sentence_gain >= 1 and long_word_gain >= -0.05) or (
        long_word_gain >= 0.05 and sentence_gain >= -1
    ), (
        "Live answer quality regression: Clear was not noticeably simpler than Complex. "
        f"Clear: {clear_sentence:.1f} words/sentence, {clear_long:.1%} long words; "
        f"Complex: {complex_sentence:.1f} words/sentence, {complex_long:.1%} long words. "
        "Expected at least 1 fewer word per sentence or 5 percentage points fewer words "
        "of 8+ letters, without worsening the other measure beyond its threshold."
    )