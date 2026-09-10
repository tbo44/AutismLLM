"""
Tests for annotateAcronyms() and renderAnswer() in static/script.js.

The Node.js runner (acronym_runner.cjs) loads the real production script
in a vm sandbox with a minimal mock DOM, so these tests exercise the same
glossary, regex, and annotation logic that runs in the browser.
"""

import json
import subprocess
from pathlib import Path

import pytest

RUNNER = str(Path(__file__).parent / "acronym_runner.cjs")
GLOSSARY_PATH = Path(__file__).parent.parent / "data" / "acronyms.json"
ACRONYM_GLOSSARY = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))

# Spot-check subset of expected definitions from ACRONYM_GLOSSARY.
# The runner also returns the full glossary for any additional assertions.
GLOSSARY_SAMPLE = {
    "EHCP":  "Education, Health and Care Plan",
    "PIP":   "Personal Independence Payment",
    "SEND":  "Special Educational Needs and Disabilities",
    "DLA":   "Disability Living Allowance",
    "SENCO": "Special Educational Needs Co-ordinator",
    "ASD":   "Autism Spectrum Disorder",
    "GP":    "General Practitioner (your family doctor)",
}


# ── Runner helpers ────────────────────────────────────────────────────────────

def run_annotate(html: str, already_seen: list[str] | None = None) -> dict:
    """Call annotateAcronyms via the runner and return {result, seen, glossary}."""
    payload = json.dumps({"op": "annotate", "html": html, "seen": already_seen or []})
    proc = subprocess.run(
        ["node", RUNNER],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"Node.js runner failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    return json.loads(proc.stdout)


def run_render(raw_text: str) -> dict:
    """Call renderAnswer (full pipeline) via the runner and return {result, glossary}."""
    payload = json.dumps({"op": "render", "raw": raw_text})
    proc = subprocess.run(
        ["node", RUNNER],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"Node.js runner failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    return json.loads(proc.stdout)


def abbr_for(acronym: str, definition: str) -> str:
    safe_def = definition.replace('"', "&quot;")
    return (
        f'<abbr class="maya-abbr" tabindex="0" role="term" '
        f'data-tooltip="{safe_def}" '
        f'title="{safe_def}" '
        f'aria-label="{acronym}: {safe_def}">{acronym}</abbr>'
    )


# ── Glossary coverage ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("acronym,definition", sorted(ACRONYM_GLOSSARY.items()))
def test_every_glossary_entry_is_exercised_by_runner(acronym, definition):
    """Every canonical glossary entry must be loaded and annotated by the runner."""
    out = run_annotate(f"<p>{acronym}</p>")

    assert out["glossary"] == ACRONYM_GLOSSARY, (
        "The test runner glossary has drifted from data/acronyms.json"
    )
    assert abbr_for(acronym, definition) in out["result"], (
        f"Canonical glossary entry {acronym!r} was not annotated:\n{out['result']}"
    )


# ── First-occurrence wrapping ─────────────────────────────────────────────────

@pytest.mark.parametrize("acronym", list(GLOSSARY_SAMPLE.keys()))
def test_first_occurrence_is_wrapped(acronym):
    """First occurrence of each key acronym must be wrapped in .maya-abbr."""
    html = f"<p>You should apply for {acronym} today.</p>"
    out = run_annotate(html)
    expected = abbr_for(acronym, GLOSSARY_SAMPLE[acronym])
    assert expected in out["result"], (
        f"Expected <abbr> for {acronym} not found in:\n{out['result']}"
    )


def test_acronym_at_start_of_sentence():
    """Acronym at the very start of the text is wrapped."""
    out = run_annotate("EHCP is important.")
    assert 'class="maya-abbr"' in out["result"]
    assert ">EHCP<" in out["result"]


def test_acronym_at_end_of_sentence():
    """Acronym at the very end of the text (before full stop) is wrapped."""
    out = run_annotate("You need a GP.")
    assert 'class="maya-abbr"' in out["result"]
    assert ">GP<" in out["result"]


def test_acronym_inside_bold_text():
    """Acronym inside <strong> tags is still wrapped."""
    out = run_annotate("<strong>EHCP</strong> is what you need.")
    assert 'class="maya-abbr"' in out["result"]
    assert ">EHCP<" in out["result"]


# ── Subsequent occurrences not re-wrapped ─────────────────────────────────────

def test_subsequent_occurrence_not_wrapped():
    """Second occurrence of the same acronym in one message must not be wrapped."""
    html = "<p>Apply for EHCP. The EHCP process takes time.</p>"
    out = run_annotate(html)
    result = out["result"]

    # Exactly one <abbr> wrapping EHCP
    assert result.count('class="maya-abbr"') == 1, (
        f"Expected exactly 1 <abbr> but got:\n{result}"
    )

    # The first occurrence is wrapped
    assert abbr_for("EHCP", GLOSSARY_SAMPLE["EHCP"]) in result, (
        "First EHCP not wrapped"
    )

    # The second occurrence is present as plain text (not inside an abbr tag)
    # Strip the one abbr block and confirm a bare "EHCP" still remains.
    result_without_abbr = result.replace(abbr_for("EHCP", GLOSSARY_SAMPLE["EHCP"]), "__FIRST__")
    assert "EHCP" in result_without_abbr, (
        "Second (unwrapped) EHCP not found as plain text"
    )


def test_multiple_different_acronyms_each_wrapped_once():
    """Each distinct acronym is wrapped exactly once in a message with several."""
    html = "<p>PIP and DLA and ASD and GP — PIP again and DLA again.</p>"
    out = run_annotate(html)
    result = out["result"]
    for acronym in ["PIP", "DLA", "ASD", "GP"]:
        assert result.count(f'aria-label="{acronym}:') == 1, (
            f"Expected exactly 1 <abbr> for {acronym} but got:\n{result}"
        )


def test_already_seen_acronym_not_rewrapped():
    """Acronym passed in the 'seen' pre-seed is not wrapped at all."""
    out = run_annotate("<p>Apply for EHCP now.</p>", already_seen=["EHCP"])
    assert 'class="maya-abbr"' not in out["result"], (
        f"EHCP should not be wrapped (pre-seen):\n{out['result']}"
    )


# ── HTML attribute values untouched ──────────────────────────────────────────

def test_acronym_in_href_not_wrapped():
    """Acronym inside an href attribute value must not be annotated."""
    html = '<a href="/info/EHCP-guide">EHCP guide</a>'
    out = run_annotate(html)
    assert 'href="/info/EHCP-guide"' in out["result"], (
        f"href was modified:\n{out['result']}"
    )


def test_acronym_in_data_attribute_not_wrapped():
    """Acronym inside a data- attribute must not be annotated."""
    html = '<span data-topic="SEND overview">SEND</span>'
    out = run_annotate(html)
    assert 'data-topic="SEND overview"' in out["result"], (
        f"data attribute was modified:\n{out['result']}"
    )


def test_acronym_in_title_attribute_not_wrapped():
    """Acronym inside a title= attribute must not be annotated."""
    html = '<abbr title="PIP meaning">PIP</abbr>'
    out = run_annotate(html)
    assert 'title="PIP meaning"' in out["result"], (
        f"title attribute was modified:\n{out['result']}"
    )


def test_acronym_in_class_attribute_not_wrapped():
    """Acronym-like string inside a class attribute is not wrapped."""
    html = '<div class="GP-section">See a GP.</div>'
    out = run_annotate(html)
    assert 'class="GP-section"' in out["result"], (
        f"class attribute was modified:\n{out['result']}"
    )


# ── Tooltip text matches glossary ─────────────────────────────────────────────

@pytest.mark.parametrize("acronym,definition", GLOSSARY_SAMPLE.items())
def test_tooltip_text_matches_glossary(acronym, definition):
    """data-tooltip, title, and aria-label must exactly match the glossary definition."""
    html = f"<p>{acronym} information.</p>"
    out = run_annotate(html)
    safe_def = definition.replace('"', "&quot;")
    assert f'data-tooltip="{safe_def}"' in out["result"], (
        f"data-tooltip mismatch for {acronym}:\n{out['result']}"
    )
    assert f'title="{safe_def}"' in out["result"], (
        f"title mismatch for {acronym}:\n{out['result']}"
    )
    assert f'aria-label="{acronym}: {safe_def}"' in out["result"], (
        f"aria-label mismatch for {acronym}:\n{out['result']}"
    )


# ── Plain text unaffected ─────────────────────────────────────────────────────

def test_non_acronym_text_unchanged():
    """Regular words that happen to be uppercase are not annotated."""
    html = "<p>This is just TEXT without known acronyms.</p>"
    out = run_annotate(html)
    assert 'class="maya-abbr"' not in out["result"]
    assert out["result"] == html


def test_empty_input():
    """Empty string returns empty string without error."""
    out = run_annotate("")
    assert out["result"] == ""


# ── renderAnswer integration: plain markdown AI response ─────────────────────

def test_render_plain_markdown_wraps_first_acronym():
    """renderAnswer wraps acronyms when the AI returns plain markdown (no ## sections)."""
    raw = "PIP is a benefit. You can also claim DLA. Contact your GP for a letter. PIP requires evidence."
    out = run_render(raw)
    result = out["result"]

    # Each of PIP, DLA, GP wrapped exactly once
    assert result.count('aria-label="PIP:') == 1
    assert result.count('aria-label="DLA:') == 1
    assert result.count('aria-label="GP:') == 1

    # Second 'PIP' must remain as plain text
    pip_abbr = abbr_for("PIP", "Personal Independence Payment")
    result_without_abbr = result.replace(pip_abbr, "__FIRST_PIP__")
    assert "PIP" in result_without_abbr, "Second (unwrapped) PIP not found as plain text"


def test_render_structured_sections_wraps_across_sections():
    """renderAnswer with ## sections wraps acronyms and does not double-wrap across sections."""
    raw = (
        "## Short Answer\n"
        "Apply for EHCP support. EHCP takes time.\n"
        "## Steps\n"
        "1. Contact your LA\n"
        "2. Speak to a SENCO"
    )
    out = run_render(raw)
    result = out["result"]

    # EHCP wrapped once (first occurrence in Short Answer section)
    assert result.count('aria-label="EHCP:') == 1, (
        f"EHCP should appear as <abbr> exactly once:\n{result}"
    )
    # Second raw EHCP in the same section stays unwrapped
    ehcp_abbr = abbr_for("EHCP", "Education, Health and Care Plan")
    stripped = result.replace(ehcp_abbr, "__FIRST__")
    assert "EHCP" in stripped, "Second EHCP not found as plain text in structured output"

    # LA and SENCO each wrapped once (first occurrences in Steps section)
    assert result.count('aria-label="LA:') == 1
    assert result.count('aria-label="SENCO:') == 1


def test_render_structured_sections_tooltip_correct():
    """Tooltip definitions in renderAnswer output match the glossary."""
    raw = "## Short Answer\nYou may qualify for ASD support.\n## Steps\n1. See your GP"
    out = run_render(raw)
    result = out["result"]

    asd_def  = "Autism Spectrum Disorder"
    gp_def   = "General Practitioner (your family doctor)"
    assert f'data-tooltip="{asd_def}"' in result
    assert f'data-tooltip="{gp_def}"' in result


def test_render_structured_sections_html_attrs_untouched():
    """renderAnswer does not corrupt HTML attribute values containing acronym-like text."""
    # The markdown renderer produces links; ensure the href isn't mangled
    raw = "## Useful Links\n[PIP guidance](https://gov.uk/pip)"
    out = run_render(raw)
    result = out["result"]
    assert 'href="https://gov.uk/pip"' in result, (
        f"href was corrupted:\n{result}"
    )
