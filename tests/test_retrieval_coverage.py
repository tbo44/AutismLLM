"""
Retrieval coverage tests for Maya's knowledge base.

WHY THIS EXISTS
---------------
The retriever drops any chunk whose ChromaDB cosine distance is >= the
MIN_RELEVANCE_THRESHOLD (see rag/retriever.py) and, if nothing survives, returns
the "I don't have information about that" fallback. That cut-off silently hides a
real gap: common, natural phrasings of questions whose answers ARE in the seed
data can score above the threshold and get no answer at all, while only a more
precise phrasing retrieves correctly.

These tests turn that silent gap into a visible, monitored one. They build a
fresh vector store directly from the seed file (so the test is deterministic and
does not depend on whatever happens to be in ./chroma_db or on the LLM), run
representative natural-language questions through the same embedding + threshold +
query-expansion path the retriever uses, and assert coverage.

  • CORE_TOPICS — natural questions that MUST retrieve a relevant seed chunk
    below the threshold. A failure here is a regression in retrieval quality.
  • KNOWN_GAPS  — natural questions that currently fall through (short / "what
    is X" phrasings, or topics only partially present in the seed). These are
    marked xfail(strict=True): if a future change (better embeddings, threshold
    tuning, richer expansion, or new seed content) fixes one, pytest reports an
    unexpected pass (XPASS) and fails, forcing it to be promoted into
    CORE_TOPICS so the gain is locked in.

The first run cold-loads the SentenceTransformer model and embeds the seed
(~tens of seconds); subsequent runs are fast.

Run with:
    pytest tests/test_retrieval_coverage.py -v
"""

import pytest

from rag.vector_store import UKAutismVectorStore
from rag.structured_importer import import_structured_knowledge
from rag.retriever import (
    MIN_RELEVANCE_THRESHOLD,
    SECOND_TIER_THRESHOLD,
    apply_relevance_gate,
    expand_query_with_synonyms,
)


SEED_FILE = "data/maya_hounslow_knowledge_seed.jsonl"


# ── fixture: a self-contained store built from the seed file ──────────────────

@pytest.fixture(scope="module")
def seed_store(tmp_path_factory):
    """Build a fresh ChromaDB collection from the seed file only.

    Seed-only (not the live ./chroma_db) keeps the test deterministic: we are
    asserting coverage for topics we know are in the seed, so crawled pages that
    may or may not be present must not influence the result.
    """
    persist_dir = tmp_path_factory.mktemp("chroma_coverage")
    store = UKAutismVectorStore(persist_directory=str(persist_dir))
    store.initialize()

    chunks = import_structured_knowledge(SEED_FILE)
    assert chunks, f"Seed import from {SEED_FILE} produced no chunks"
    store.add_documents(chunks)
    assert store.collection.count() == len(chunks)

    return store


def _relevant_match_distance(store, question, keyword):
    """Best distance of a gate-surviving chunk whose title/text mentions `keyword`.

    Mirrors the retriever: expand acronyms, search, apply the two-tier
    relevance gate (strict MIN_RELEVANCE_THRESHOLD, then the relaxed
    single-best-hit second tier), and require the expected topic to actually be
    among the survivors (so we catch "retrieved the wrong thing", not just
    "retrieved something"). Returns None when nothing survives the gate.
    """
    expanded = expand_query_with_synonyms(question)
    results = store.search(expanded, n_results=8, authority_boost=True)
    gated = apply_relevance_gate(results, question)

    matches = [
        r["distance"]
        for r in gated
        if keyword.lower() in (r["metadata"]["title"] + " " + r["text"]).lower()
    ]
    return min(matches) if matches else None


def _strict_result_titles(store, question):
    """Titles retrieved through the strict gate, with no relaxed fallback."""
    return {
        r["metadata"]["title"]
        for r in _strict_results(store, question)
    }


def _strict_results(store, question):
    """Results below the strict threshold, before the second-tier fallback."""
    expanded = expand_query_with_synonyms(question)
    results = store.search(expanded, n_results=8, authority_boost=True)
    return [r for r in results if r["distance"] < MIN_RELEVANCE_THRESHOLD]


# ── CORE: natural questions that must retrieve in-seed topics ─────────────────

CORE_TOPICS = [
    ("How do I apply for DLA for my autistic child?", "disability living allowance"),
    ("How do I claim Carer's Allowance?", "carer's allowance"),
    ("How do I get an autism diagnosis as an adult?", "autism assessment"),
    ("How do I request a Care Act assessment?", "care act"),
    ("What is Universal Credit LCWRA?", "universal credit"),
    ("How do I get free school transport for my SEND child?", "school transport"),
    ("How do I appeal to the SEND tribunal?", "tribunal"),
    ("How do I appeal an EHCP decision?", "ehcp"),
    ("What is an EHCP and how do I apply?", "education, health and care"),
    ("How do I claim PIP?", "personal independence payment"),
    # Promoted from KNOWN_GAPS: the second-tier relevance gate in
    # rag/retriever.py (apply_relevance_gate) now recovers these short,
    # casual phrasings via a lexically-anchored single-best-hit fallback.
    ("How do I renew my Blue Badge?", "blue badge"),
    ("What is Access to Work?", "access to work"),
    ("What is the Motability scheme?", "motability"),
    ("What respite care is available for carers?", "respite"),
    ("What benefits can I claim as a carer?", "carer"),
]


@pytest.mark.parametrize("question,keyword", CORE_TOPICS)
def test_core_seed_topics_are_retrievable(seed_store, question, keyword):
    """A natural-language question must retrieve its in-seed topic below the bar."""
    distance = _relevant_match_distance(seed_store, question, keyword)

    assert distance is not None, (
        f"RETRIEVAL GAP: the question below returned no '{keyword}' chunk under the "
        f"{MIN_RELEVANCE_THRESHOLD} distance threshold, even though that topic exists "
        f"in {SEED_FILE}.\n"
        f"  Question: {question!r}\n"
        f"This is exactly the silent gap the threshold can hide — users get the "
        f"'no information' fallback for an answerable question. Tune "
        f"MIN_RELEVANCE_THRESHOLD or extend the query expansion in rag/retriever.py."
    )


def test_compound_carer_question_retrieves_every_core_benefit_strictly(seed_store):
    """A broad carer question must reach all benefits, not a fallback single hit."""
    strict_results = _strict_results(seed_store, "What benefits can I claim as a carer?")
    strict_text = "\n".join(result["text"].lower() for result in strict_results)
    expected_benefits = {
        "carer's allowance",
        "carer's credit",
        "carer's element",
    }
    missing = {
        benefit for benefit in expected_benefits if benefit not in strict_text
    }

    assert not missing, (
        "COMPOUND RETRIEVAL GAP: a carer benefits question must retrieve Carer's "
        "Allowance, Carer's Credit, and the Universal Credit Carer's Element below "
        f"the strict {MIN_RELEVANCE_THRESHOLD} threshold. Missing: {sorted(missing)!r}. "
        "Do not rely on the second-tier gate, which intentionally returns only one hit."
    )


def test_motability_and_respite_overviews_clear_the_strict_gate(seed_store):
    """Broad Motability and respite questions must not depend on a single-hit fallback."""
    motability_titles = _strict_result_titles(seed_store, "What is the Motability scheme?")
    respite_titles = _strict_result_titles(
        seed_store, "What respite care is available for carers?"
    )

    assert "What is the Motability Scheme?" in motability_titles
    assert (
        "Respite care: short breaks, sitting services and overnight support"
        in respite_titles
    )


# ── KNOWN GAPS: tracked misses (strict xfail tripwire) ────────────────────────
# All previous entries were fixed by the second-tier relevance gate and promoted
# into CORE_TOPICS. Add new tracked misses here as they are discovered.

KNOWN_GAPS = []


@pytest.mark.parametrize("question,keyword", KNOWN_GAPS)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Documented retrieval gap: short / abbreviated phrasing scores >= the "
        "threshold (or the topic is only partially in the seed). If this starts "
        "passing, move it into CORE_TOPICS. See MIN_RELEVANCE_THRESHOLD notes in "
        "rag/retriever.py."
    ),
)
def test_known_retrieval_gaps_are_tracked(seed_store, question, keyword):
    """These currently fall through the threshold; xfail keeps the gap visible."""
    distance = _relevant_match_distance(seed_store, question, keyword)
    assert distance is not None


# ── PRECISION: off-topic / out-of-scope questions must return nothing ─────────
# The second-tier gate relaxes recall, so this guards against the corresponding
# precision regression: none of these may survive the gate. The seed_store
# fixture imports every record in the current seed, so additions automatically
# rerun this calibration guard against the expanded knowledge base. Genuinely
# off-topic questions currently score >= ~1.48; the in-domain-but-wrong one
# ("housing benefit in Scotland" ≈ 1.09) is rejected by the lexical anchor.

# These have no topical overlap with the seed, so their best distance is the
# calibration floor for SECOND_TIER_THRESHOLD. Scotland is intentionally not in
# this group: it is in-domain but wrong, and the lexical anchor is what rejects
# it despite its much closer best match.
GENUINELY_OFF_TOPIC = [
    "What is the weather today?",
    "How do I fix my car engine?",
    "Best pizza recipe",
    "Tell me about football scores",
    "How do I get a mortgage?",
    "What is ADHD medication dosage?",
    "How do I apply for a US green card?",
]

OFF_TOPIC = [
    *GENUINELY_OFF_TOPIC,
    "How do I apply for housing benefit in Scotland?",
]


@pytest.mark.parametrize("question", GENUINELY_OFF_TOPIC)
def test_second_tier_threshold_stays_below_off_topic_distance_floor(
    seed_store, question
):
    """Seed additions must not move a truly off-topic hit into tier-two range."""
    expanded = expand_query_with_synonyms(question)
    results = seed_store.search(expanded, n_results=8, authority_boost=True)
    best = min(results, key=lambda result: result["distance"])

    assert best["distance"] >= SECOND_TIER_THRESHOLD, (
        f"CALIBRATION DRIFT: the genuinely off-topic question {question!r} now "
        f"has a best seed match at {best['distance']:.4f} to "
        f"{best['metadata']['title']!r}, below the second-tier threshold of "
        f"{SECOND_TIER_THRESHOLD}. Retune SECOND_TIER_THRESHOLD before this "
        f"seed expansion can admit a weak match."
    )


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_off_topic_questions_return_nothing(seed_store, question):
    """Each off-topic question must fall through both tiers after seed changes."""
    expanded = expand_query_with_synonyms(question)
    results = seed_store.search(expanded, n_results=8, authority_boost=True)
    gated = apply_relevance_gate(results, question)
    best = min(results, key=lambda result: result["distance"])

    assert gated == [], (
        f"PRECISION REGRESSION: the off-topic question {question!r} retrieved "
        f"{[r['metadata']['title'] for r in gated]!r} through the relevance gate. "
        f"Best seed match: {best['distance']:.4f} to "
        f"{best['metadata']['title']!r}; second-tier threshold: "
        f"{SECOND_TIER_THRESHOLD}. Tighten SECOND_TIER_THRESHOLD or the lexical "
        f"anchor in rag/retriever.py."
    )
