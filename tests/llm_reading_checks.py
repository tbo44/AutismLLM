"""Shared structure and reading-level checks for live model evaluation."""

import re

from tests.llm_answer_essentials import assert_essential_advice


# Bump when scoring rules or the fixed evaluation cases change, independently
# of the report schema. Changing the production prompt alone does not change it.
READING_RUBRIC_ID = "maya-reading-level-v1"

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


def check_answer_structure(answer, level):
    lines = [line.strip() for line in answer.splitlines()]
    if "## Short Answer" not in lines:
        raise AssertionError(f"Live answer regression ({level}): missing the '## Short Answer' heading.")
    if not WORD.search(_answer_prose(answer)):
        raise AssertionError(f"Live answer regression ({level}): no answer text under the headings.")


def check_reading_pair(case, *, clear, complex_answer):
    assert_essential_advice(case, clear=clear, complex_answer=complex_answer)

    unexplained = _unexplained_acronyms(clear)
    if unexplained:
        raise AssertionError(
            "Live answer quality regression: Clear used these acronyms without explaining them at first use: "
            + ", ".join(unexplained)
        )

    clear_sentence, clear_long = _reading_profile(clear)
    complex_sentence, complex_long = _reading_profile(complex_answer)
    sentence_gain = complex_sentence - clear_sentence
    long_word_gain = complex_long - clear_long
    # Clear should improve at least one signal without materially worsening the other.
    if not ((sentence_gain >= 1 and long_word_gain >= -0.05) or
            (long_word_gain >= 0.05 and sentence_gain >= -1)):
        raise AssertionError(
            "Live answer quality regression: Clear was not noticeably simpler than Complex. "
            f"Clear: {clear_sentence:.1f} words/sentence, {clear_long:.1%} long words; "
            f"Complex: {complex_sentence:.1f} words/sentence, {complex_long:.1%} long words. "
            "Expected at least 1 fewer word per sentence or 5 percentage points fewer words "
            "of 8+ letters, without worsening the other measure beyond its threshold."
        )
