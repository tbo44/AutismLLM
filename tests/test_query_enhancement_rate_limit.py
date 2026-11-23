from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest

import rag.llm_client as llm_module
from rag.llm_client import UKAutismLLMClient
from rag.rag_system import UKAutismRAGSystem
from rag.retriever import UKAutismRetriever


def _rate_limit_error(headers=None) -> openai.RateLimitError:
    return openai.RateLimitError(
        "too many requests",
        response=httpx.Response(
            429,
            headers=headers,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
        ),
        body=None,
    )


@pytest.fixture
def client_factory(monkeypatch):
    def make(create, seconds="30"):
        monkeypatch.setenv("LLM_RATE_LIMIT_COOLDOWN_SECONDS", seconds)
        monkeypatch.setattr(
            llm_module, "_build_openai_client",
            lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        )
        return UKAutismLLMClient()
    return make


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm_module.time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def wall_clock(monkeypatch):
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(llm_module.time, "time", lambda: now)


@pytest.fixture
def transport_client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_RATE_LIMIT_COOLDOWN_SECONDS", raising=False)
    build_client = llm_module._build_openai_client

    def make(http_client, seconds=None):
        if seconds is not None:
            monkeypatch.setenv("LLM_RATE_LIMIT_COOLDOWN_SECONDS", seconds)
        monkeypatch.setattr(
            llm_module, "_build_openai_client", partial(build_client, http_client=http_client),
        )
        return UKAutismLLMClient()

    return make


def _successful_completion_response():
    return httpx.Response(200, json={
        "id": "test-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "PIP benefits"},
        }],
    })


@pytest.mark.parametrize(
    ("headers", "configured", "wait"),
    [
        pytest.param({}, None, 30, id="missing"),
        pytest.param({"Retry-After": "0"}, None, 30, id="zero-hint"),
        pytest.param({"Retry-After": "1"}, None, 30, id="shorter"),
        pytest.param({"Retry-After": "45", "x-should-retry": "true"}, None, 45, id="longer"),
        pytest.param({"Retry-After": " 60\t"}, None, 60, id="surrounding-whitespace"),
        pytest.param({"Retry-After": "Mon, 23 Nov 2026 12:01:00 GMT"}, None, 60, id="future-date"),
        pytest.param({"Retry-After": "Monday, 23-Nov-26 12:01:00 GMT"}, None, 60, id="rfc850-date"),
        pytest.param({"Retry-After": "Mon Nov 23 12:01:00 2026"}, None, 60, id="asctime-date"),
        pytest.param({"Retry-After": "Mon, 23 Nov 2026 11:59:00 GMT"}, None, 30, id="past-date"),
        pytest.param({"Retry-After": "Mon, 23 Nov 2026 13:00:00 GMT"}, None, 300, id="date-capped"),
        pytest.param({"Retry-After": "99999"}, None, 300, id="seconds-capped"),
        pytest.param({"Retry-After": "9" * 4500}, None, 300, id="huge-integer-capped"),
        pytest.param({"Retry-After": "99999"}, "400", 400, id="configured-above-cap"),
        pytest.param({"Retry-After": "bad value"}, "12.5", 12.5, id="malformed-with-custom-default"),
        pytest.param({"Retry-After": ""}, None, 30, id="blank"),
        pytest.param({"Retry-After": "-60"}, None, 30, id="negative"),
        pytest.param({"Retry-After": "90.5"}, None, 30, id="fractional"),
        pytest.param({"Retry-After": "inf"}, None, 30, id="non-finite"),
        pytest.param({"Retry-After": "Mon, 23 Nov 2026 12:01:00"}, None, 30, id="no-timezone"),
        pytest.param({"Retry-After": "Mon, 32 Nov 2026 12:01:00 GMT"}, None, 30, id="invalid-date"),
        pytest.param([("Retry-After", "60"), ("Retry-After", "90")], None, 30, id="duplicate"),
    ],
)
def test_first_http_429_starts_shared_cooldown_without_retry(
    transport_client, clock, wall_clock, monkeypatch, headers, configured, wait,
):
    def unexpected_sleep(*args):
        pytest.fail("The SDK must not sleep or retry after a 429")

    monkeypatch.setattr(llm_module.time, "sleep", unexpected_sleep)
    attempts = []

    def respond(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            return httpx.Response(429, headers=headers, json={"error": {"message": "Too many requests"}})
        return _successful_completion_response()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = transport_client(http_client, seconds=configured)
        result = client.enhance_query("PIP support")
        assert attempts == ["/v1/chat/completions"]
        assert result == {"query": "PIP support", "rate_limited": True}
        assert client.is_rate_limited()

        expires_at = clock[0] + wait
        clock[0] = expires_at - 0.01
        assert client.check_content_appropriateness("PIP support")["rate_limited"] is True
        assert client.synthesize_response("PIP support", [])["rate_limited"] is True
        assert client.enhance_query("PIP support")["rate_limited"] is True
        assert len(attempts) == 1

        clock[0] = expires_at
        assert client.enhance_query("PIP support") == "PIP benefits"
        assert len(attempts) == 2
        assert not client.is_rate_limited()


@pytest.mark.parametrize("failure", ["connection", "http-503"])
def test_transient_provider_errors_still_retry(transport_client, failure):
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            if failure == "connection":
                raise httpx.ConnectError("Temporary connection failure", request=request)
            return httpx.Response(503, json={"error": {"message": "Try again shortly"}})
        return _successful_completion_response()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = transport_client(http_client)
        assert client.enhance_query("PIP support") == "PIP benefits"
        assert len(attempts) == 2
        assert not client.is_rate_limited()


def test_enhance_query_reports_a_rate_limit(client_factory):
    class RateLimitedCompletions:
        def create(self, **kwargs):
            raise _rate_limit_error()

    llm_client = client_factory(RateLimitedCompletions().create)

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


def _completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.mark.parametrize("stage", ["moderation", "enhancement", "synthesis"])
def test_all_provider_stages_activate_shared_cooldown(client_factory, clock, stage):
    create = Mock(side_effect=_rate_limit_error({"Retry-After": "45"}))
    client = client_factory(create, seconds="12.5")
    calls = {
        "moderation": lambda: client.check_content_appropriateness("PIP support"),
        "enhancement": lambda: client.enhance_query("PIP support"),
        "synthesis": lambda: client.synthesize_response("PIP support", []),
    }
    assert calls[stage]()["rate_limited"] is True
    assert client.is_rate_limited()
    clock[0] = 144.99
    for call in calls.values():
        assert call()["rate_limited"] is True
    assert create.call_count == 1

    # Suppressed requests do not extend the deadline.
    clock[0] = 145.0
    create.side_effect = None
    create.return_value = _completion("PIP benefits")
    assert client.enhance_query("PIP support") == "PIP benefits"
    assert create.call_count == 2
    assert not client.is_rate_limited()


@pytest.mark.parametrize("failed_call", [0, 1, 2])
def test_answer_requests_short_circuit_and_recover(client_factory, clock, failed_call):
    successful = [
        _completion('{"appropriate": true}'),
        _completion("PIP benefits"),
        _completion("Here is how to apply for PIP."),
    ]
    create = Mock(side_effect=successful[:failed_call] + [_rate_limit_error()])
    client = client_factory(create)
    vector_store = Mock()
    vector_store.search.return_value = [{
        "text": "Apply for PIP.",
        "distance": 0.05,
        "metadata": {"source_name": "Gov.UK", "url": "https://www.gov.uk/pip", "title": "PIP", "authority": 1},
    }]
    system = UKAutismRAGSystem()
    system.initialized = True
    system.llm_client = client
    system.retriever = UKAutismRetriever(vector_store, client)

    first = system.answer_question("How do I get PIP?")
    assert first["rate_limited"] is True
    assert first["sources"] == []
    assert "temporarily unavailable due to a rate limit" in first["answer"]
    assert create.call_count == failed_call + 1
    vector_store.reset_mock()
    for question in ["How do I get PIP?", "What is an EHCP?"]:
        assert system.answer_question(question) == first
    assert create.call_count == failed_call + 1
    vector_store.search.assert_not_called()

    clock[0] = 130.0
    create.side_effect = successful
    recovered = system.answer_question("How do I get PIP?")
    assert recovered["answer"] == "Here is how to apply for PIP."
    assert not recovered.get("rate_limited")
    assert create.call_count == failed_call + 4


def test_repeated_rate_limits_restart_cooldown(client_factory, clock):
    create = Mock(side_effect=_rate_limit_error())
    client = client_factory(create)
    client.enhance_query("PIP support")
    clock[0] = 130.0
    client.enhance_query("PIP support")
    clock[0] = 159.9
    assert client.is_rate_limited()
    client.enhance_query("PIP support")
    assert create.call_count == 2


def test_zero_disables_cooldown_even_with_retry_after(transport_client, clock):
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "90"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = transport_client(http_client, seconds="0")
        for expected_attempts in (1, 2):
            assert client.enhance_query("PIP support") == {"query": "PIP support", "rate_limited": True}
            assert len(attempts) == expected_attempts
            assert not client.is_rate_limited()


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "invalid"])
def test_invalid_cooldown_configuration_fails_explicitly(client_factory, value):
    with pytest.raises(ValueError):
        client_factory(Mock(), seconds=value)


def test_other_errors_do_not_start_cooldown(client_factory, clock):
    create = Mock(side_effect=RuntimeError("connection failed"))
    client = client_factory(create)
    assert client.enhance_query("PIP support") == "PIP support"
    assert not client.is_rate_limited()
    client.enhance_query("PIP support")
    assert create.call_count == 2


def test_local_safety_guardrails_still_work_during_cooldown(client_factory, clock, monkeypatch):
    create = Mock(side_effect=_rate_limit_error())
    client = client_factory(create)
    client.enhance_query("PIP support")
    system = UKAutismRAGSystem()
    system.initialized = True
    system.llm_client = client
    monkeypatch.setattr("rag.answerer.apply_guardrails", lambda question: "Local safety advice")
    assert system.answer_question("Safety question") == {"answer": "Local safety advice", "sources": []}
    assert create.call_count == 1