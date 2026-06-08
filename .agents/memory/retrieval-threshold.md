---
name: Retrieval relevance threshold & coverage gap
description: Why short natural questions silently get "no answer", the 0.8 distance cut-off trade-off, and how the coverage test + acronym expansion guard it
---

# Retrieval relevance threshold (MIN_RELEVANCE_THRESHOLD = 0.8)

The retriever drops every ChromaDB hit whose cosine **distance** is >= 0.8 and,
if nothing survives, returns the "not in my knowledge base" fallback.

**The non-obvious gap:** with the `all-MiniLM-L6-v2` embeddings, SHORT / natural
phrasings of topics that ARE in the seed still score 0.8–1.4 even when the
correct chunk is the top hit — e.g. "What is an EHCP and how do I apply?" ≈ 1.07,
"How do I renew my Blue Badge?" ≈ 0.90. Only a precise, keyword-rich phrasing
("What is the EHCP annual review process for autism?" ≈ 0.51) clears the bar.
So the same question can silently get no answer or a good answer depending on
wording. This is a distance scale quirk, not missing data — though some "gap"
questions are also genuinely absent from the seed (e.g. there is NO general
"how to claim PIP" or "how to apply for an EHCP" entry; only appeal/review ones).

**Why:** lowering the threshold means more "no information" misses; raising it
risks answering from a weak, only-loosely-related chunk. Off-topic questions are
already filtered upstream by the LLM appropriateness check, so the real risk of
raising it is in-domain-but-wrong answers.

**How to apply:**
- Mitigation in place: `expand_query_with_synonyms` appends full forms for UK
  acronyms (EHCP→Education, Health and Care Plan, etc.). The retriever runs it as
  a *fallback* only when the first pass returns nothing below threshold.
  Expansion pulls the EHCP example from ~1.07 to ~0.61.
- Guard: `tests/test_retrieval_coverage.py` builds a fresh seed-only store and
  checks CORE_TOPICS retrieve below threshold; KNOWN_GAPS are `xfail(strict=True)`
  tripwires — if one starts passing, promote it into CORE_TOPICS.
- The coverage test is deterministic (no LLM): embeddings are deterministic, so
  borderline distances near 0.8 are stable, not flaky.
