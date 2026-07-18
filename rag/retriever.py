"""
Intelligent Retrieval System for UK Autism Assistant
Handles query enhancement, retrieval, and result ranking
"""

from typing import List, Dict, Any, Optional
import logging
import re

from .vector_store import UKAutismVectorStore
from .llm_client import UKAutismLLMClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Relevance threshold
# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB returns a cosine *distance* for every hit (0.0 = identical, larger =
# less similar; in practice values run from ~0.3 for a strong match up to ~1.4
# for an unrelated chunk). Any result whose distance is >= this cut-off is
# dropped, and if nothing survives the retriever returns an empty list so the RAG
# layer emits the explicit "not in my knowledge base" message instead of
# hallucinating from irrelevant chunks.
#
# Why 0.8 (and how to tune it):
#   • It was chosen empirically. Precise, keyword-rich phrasings of in-seed
#     topics score well below it (e.g. "What is the EHCP annual review process
#     for autism?" ≈ 0.51), while clearly off-topic chunks score ~1.0+.
#   • It is a genuine trade-off. SHORT, natural questions for topics that DO
#     exist in the seed data can still score 0.8–1.4 even when the correct chunk
#     is the top hit — e.g. "What is an EHCP and how do I apply?" ≈ 1.07 and
#     "How do I renew my Blue Badge?" ≈ 0.90. Those silently fall through, which
#     is the retrieval gap that `tests/test_retrieval_coverage.py` guards.
#   • Lower it  → fewer false answers, but more "no information" misses.
#     Raise it  → better recall, but rising risk of answering from a weak,
#                 only-loosely-related chunk. Off-topic questions are already
#                 filtered upstream by the LLM appropriateness check, so the main
#                 risk of raising it is answering in-domain-but-wrong.
#   • Before changing the value, run the coverage test and watch which questions
#     move between CORE_TOPICS (must retrieve) and KNOWN_GAPS (tracked misses).
#
# `expand_query_with_synonyms` below mitigates part of the gap for abbreviated
# queries without touching this number: expanding "EHCP" to its full form pulls
# the EHCP example from ~1.07 down to ~0.61, back under the threshold.
MIN_RELEVANCE_THRESHOLD = 0.8

# ─────────────────────────────────────────────────────────────────────────────
# Second-tier (relaxed) relevance gate
# ─────────────────────────────────────────────────────────────────────────────
# Short, casual questions ("How do I renew my Blue Badge?", "What is Access to
# Work?") score 0.9–1.4 with all-MiniLM-L6-v2 even when the correct chunk is the
# clear top hit, so they fall through the 0.8 bar and users get the "no
# information" fallback for answerable questions.
#
# When NOTHING clears the strict 0.8 threshold, the second tier may return the
# SINGLE best chunk — but only when it is clearly the right topic:
#   • its distance is below SECOND_TIER_THRESHOLD (measured: genuinely off-topic
#     questions like weather/pizza/football score >= ~1.48 against this seed),
#     AND
#   • it has a strong lexical anchor — distinctive words from the user's own
#     question appear in the chunk TITLE. Two distinctive title matches are
#     required at higher distances; one suffices only under
#     SECOND_TIER_SINGLE_MATCH_MAX (e.g. "What benefits can I claim as a
#     carer?" → "How to claim Carer's Allowance…" at 0.94).
# This keeps precision: in-domain-but-wrong questions ("How do I apply for
# housing benefit in Scotland?" best hit ≈ 1.09 with at most one generic title
# word) are still rejected, and only ONE chunk is ever passed to the LLM from
# this tier, keeping the hallucination surface minimal.
SECOND_TIER_THRESHOLD = 1.45
SECOND_TIER_SINGLE_MATCH_MAX = 1.0

# Words too generic to anchor a topic match on their own — common across most
# seed titles or questions, so they carry no topical signal.
_GENERIC_TERMS = frozenset({
    "what", "when", "where", "which", "how", "does", "do", "can", "could",
    "should", "will", "the", "and", "for", "with", "about", "from", "this",
    "that", "there", "have", "has", "get", "getting", "apply", "applying",
    "claim", "claiming", "available", "help", "support", "service", "services",
    "autism", "autistic", "child", "children", "adult", "adults", "need",
    "needs", "want", "would", "your", "you", "may", "might", "more", "some",
    "any", "who", "whom", "are", "is", "was",
})


def _distinctive_terms(question: str) -> List[str]:
    """Content words from the user's question that can anchor a topic match."""
    words = re.findall(r"[a-z']+", question.lower())
    return [w for w in words if len(w) >= 4 and w not in _GENERIC_TERMS]


def apply_relevance_gate(results: List[Dict[str, Any]],
                         user_question: str) -> List[Dict[str, Any]]:
    """Two-tier relevance gate shared by the retriever and the coverage tests.

    Tier 1: keep everything under MIN_RELEVANCE_THRESHOLD (unchanged behaviour).
    Tier 2: if tier 1 is empty, return the single best lexically-anchored chunk
    under SECOND_TIER_THRESHOLD (see rationale above). Returns [] when neither
    tier matches, which triggers the explicit "no information" fallback.
    """
    if not results:
        return []

    strict = [r for r in results if r["distance"] < MIN_RELEVANCE_THRESHOLD]
    if strict:
        return strict

    terms = _distinctive_terms(user_question)
    if not terms:
        return []

    candidates = []
    for r in results:
        if r["distance"] >= SECOND_TIER_THRESHOLD:
            continue
        title = r["metadata"].get("title", "").lower()
        matches = sum(1 for t in terms if t in title)
        if matches >= 2 or (matches >= 1 and r["distance"] < SECOND_TIER_SINGLE_MATCH_MAX):
            candidates.append(r)

    if not candidates:
        return []

    best = min(candidates, key=lambda r: r["distance"])
    logger.info(
        "Second-tier relevance gate matched a single chunk "
        f"(distance={best['distance']:.3f}, title={best['metadata'].get('title', '')!r})"
    )
    return [best]


# Common UK autism / SEND / benefits acronyms mapped to their full forms.
# Abbreviations embed far from the spelled-out wording used in the seed text, so
# appending the full form to the search query is a cheap, deterministic way to
# recover otherwise-missed matches.
UK_TERM_EXPANSIONS = {
    "ehcp": "Education, Health and Care Plan",
    "pip": "Personal Independence Payment",
    "dla": "Disability Living Allowance",
    "send": "Special Educational Needs and Disabilities",
    "uc": "Universal Credit",
    "lcwra": "Limited Capability for Work and Work-Related Activity",
    "asd": "autism spectrum disorder",
    "camhs": "Child and Adolescent Mental Health Services",
    "dfg": "Disabled Facilities Grant",
    "sendiass": "SEND Information Advice and Support Service",
    "sendias": "SEND Information Advice and Support Service",
    "tfl": "Transport for London",
    "dwp": "Department for Work and Pensions",
}


def expand_query_with_synonyms(query: str) -> str:
    """Append full forms for any UK acronym found in the query.

    Returns the query unchanged when it contains no known acronyms (or already
    spells the full form out), so callers can cheaply detect whether expansion
    actually did anything by comparing the result to the input.
    """
    lower = query.lower()
    additions = []
    for acronym, full_form in UK_TERM_EXPANSIONS.items():
        if re.search(rf"\b{re.escape(acronym)}\b", lower) and full_form.lower() not in lower:
            additions.append(full_form)
    if not additions:
        return query
    return f"{query} {' '.join(additions)}"


class UKAutismRetriever:
    def __init__(self, vector_store: UKAutismVectorStore, llm_client: UKAutismLLMClient):
        self.vector_store = vector_store
        self.llm_client = llm_client
    
    def retrieve(self, user_question: str, max_results: int = 8) -> Dict[str, Any]:
        """
        Main retrieval method with intelligent query enhancement and ranking
        """
        try:
            # Check if question is Hounslow-specific
            hounslow_specific = self._is_hounslow_query(user_question)
            
            # Enhance query for better retrieval
            enhanced_query = self.llm_client.enhance_query(user_question)
            
            # First pass with the (LLM-)enhanced query
            results = self._vector_search(enhanced_query, hounslow_specific, max_results)
            filtered_results = self._filter_and_rank_results(results, user_question)

            # Query-expansion fallback: if nothing cleared the relevance bar, retry
            # with UK acronyms spelled out in full. Abbreviations like "EHCP" embed
            # far from the seed wording ("Education, Health and Care Plan"), so
            # expanding them recovers answers that would otherwise be silently
            # dropped (see MIN_RELEVANCE_THRESHOLD notes above).
            expansion_used = False
            if not filtered_results:
                expanded_query = expand_query_with_synonyms(enhanced_query)
                if expanded_query != enhanced_query:
                    expansion_used = True
                    logger.info(f"No results below threshold; retrying with expanded query: '{expanded_query}'")
                    results = self._vector_search(expanded_query, hounslow_specific, max_results)
                    filtered_results = self._filter_and_rank_results(results, user_question)

            return {
                "results": filtered_results,
                "enhanced_query": enhanced_query,
                "expansion_used": expansion_used,
                "hounslow_specific": hounslow_specific,
                "total_found": len(results),
                "total_returned": len(filtered_results)
            }
            
        except Exception as e:
            logger.error(f"Retrieval error: {str(e)}")
            return {
                "results": [],
                "enhanced_query": user_question,
                "expansion_used": False,
                "hounslow_specific": False,
                "total_found": 0,
                "total_returned": 0,
                "error": str(e)
            }

    def _vector_search(self, query: str, hounslow_specific: bool,
                       max_results: int) -> List[Dict[str, Any]]:
        """Run the vector search, prioritising local sources for Hounslow queries."""
        if hounslow_specific:
            # First try Hounslow-specific sources
            local_results = self.vector_store.search(
                query,
                n_results=max_results // 2,
                hounslow_specific=True
            )

            # Then get general results
            general_results = self.vector_store.search(
                query,
                n_results=max_results - len(local_results),
                hounslow_specific=False
            )

            # Combine with local results prioritized
            return local_results + general_results

        # Standard search with authority ranking
        return self.vector_store.search(
            query,
            n_results=max_results,
            authority_boost=True
        )

    def _is_hounslow_query(self, question: str) -> bool:
        """Check if question specifically asks about Hounslow"""
        hounslow_terms = [
            "hounslow", "local", "near me", "my area", "my council", 
            "local authority", "local services", "tw3", "tw4", "tw5"
        ]
        
        question_lower = question.lower()
        return any(term in question_lower for term in hounslow_terms)
    
    def _filter_and_rank_results(self, results: List[Dict[str, Any]], 
                                query: str) -> List[Dict[str, Any]]:
        """Apply additional filtering and ranking logic"""
        if not results:
            return results
        
        # Two-tier relevance gate: strict MIN_RELEVANCE_THRESHOLD first, then a
        # relaxed single-best-hit fallback for short natural questions (see the
        # rationale next to apply_relevance_gate at the top of this file).
        filtered = apply_relevance_gate(results, query)

        # Do NOT fall back to unfiltered results — an empty list signals "no match"
        # and triggers the explicit out-of-scope message in rag_system.py.
        
        # Apply query-specific ranking adjustments
        for result in filtered:
            result['relevance_score'] = self._calculate_relevance_score(result, query)
        
        # Sort by relevance score (higher is better)
        filtered.sort(key=lambda x: x['relevance_score'], reverse=True)
        
        return filtered
    
    def _calculate_relevance_score(self, result: Dict[str, Any], query: str) -> float:
        """Calculate relevance score combining distance and other factors"""
        base_score = 1.0 - result['distance']  # Higher for closer matches
        
        metadata = result['metadata']
        
        # Authority boost (government and NHS sources get priority)
        authority = int(metadata['authority'])
        authority_boost = (6 - authority) * 0.1  # 0.5 for gov, 0.4 for NHS, etc.
        
        # Structured entry boost (prefer curated bureaucratic guides)
        structured_boost = 0.15 if metadata.get('is_structured') else 0.0
        
        # Recency boost (more recent content gets slight priority)
        # This would require parsing crawled_at timestamp, simplified for now
        recency_boost = 0.0
        
        # Content quality boost (longer, well-structured content)
        content_length = len(result['text'])
        length_boost = min(content_length / 1000, 0.1)  # Up to 0.1 boost
        
        # Query-specific boost for exact term matches
        query_terms = query.lower().split()
        text_lower = result['text'].lower()
        title_lower = metadata['title'].lower()
        
        term_match_boost = 0.0
        for term in query_terms:
            if len(term) > 3:  # Only boost for meaningful terms
                if term in title_lower:
                    term_match_boost += 0.05
                elif term in text_lower:
                    term_match_boost += 0.02
        
        final_score = base_score + authority_boost + structured_boost + recency_boost + length_boost + term_match_boost
        
        return min(final_score, 1.0)  # Cap at 1.0