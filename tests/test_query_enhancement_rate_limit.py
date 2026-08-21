from types import SimpleNamespace

import httpx
import openai

from rag.llm_client import UKAutismLLMClient
from rag.rag_system import UKAutismRAGSystem
from rag.retriever import UKAutismRetriever


def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "too many requests",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body=None,
    )


def test_enhance_query_reports_a_rate_limit():
    class RateLimitedCompletions:
        def create(self, **kwargs):
            raise _rate_limit_error()

    llm_client = UKAutismLLMClient.__new__(UKAutismLLMClient)
    llm_client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=RateLimitedCompletions())
    )
    llm_client.model = "test-model"

    result = llm_client.enhance_query("How do I get PIP?")

    assert result == {"query": "How do I get PIP?", "rate_limited": True}


def test_retriever_skips_vector_search_when_enhancement_is_rate_limited():
    class RateLimitedEnhancer:
        def enhance_query(self, question):
            return {"query": question, "rate_limited": True}

    class VectorStore:
        def search(self, *args, **kwargs):
            raise AssertionError("Vector search must not run after a rate limit")

    retriever = UKAutismRetriever(VectorStore(), RateLimitedEnhancer())

    result = retriever.retrieve("How do I get PIP?")

    assert result["rate_limited"] is True
    assert result["results"] == []
    assert result["enhanced_query"] == "How do I get PIP?"


def test_answer_question_skips_synthesis_when_query_enhancement_is_rate_limited():
    class LLMClient:
        def check_content_appropriateness(self, question):
            return {"appropriate": True}

        def synthesize_response(self, *args, **kwargs):
            raise AssertionError("Synthesis must not run after a rate limit")

    class RateLimitedRetriever:
        def retrieve(self, question):
            return {"results": [], "rate_limited": True}

    rag_system = UKAutismRAGSystem()
    rag_system.initialized = True
    rag_system.llm_client = LLMClient()
    rag_system.retriever = RateLimitedRetriever()

    result = rag_system.answer_question("How do I get PIP?")

    assert result["rate_limited"] is True
    assert result["sources"] == []
    assert "temporarily unavailable due to a rate limit" in result["answer"]