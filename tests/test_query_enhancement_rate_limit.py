import asyncio
from datetime import datetime, timezone
from functools import partial
import json
import logging
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest

import rag.llm_client as llm_module
from rag.llm_client import UKAutismLLMClient
from rag.rag_system import UKAutismRAGSystem
from rag.retriever import UKAutismRetriever
from tests.llm_test_helpers import chat_client, search_hit


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


def _successful_completion_response(content="PIP benefits"):
    return httpx.Response(200, json={
        "id": "test-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content},
        }],
    })


def _cooldown_log_records(caplog):
    return [
        (level, message) for logger, level, message in caplog.record_tuples
        if logger == llm_module.__name__ and message.startswith("event=llm_")
    ]


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
        pytest.param({"Retry-After": "PRIVATE-RETRY-HINT"}, "12.5", 12.5, id="malformed-with-custom-default"),
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
    transport_client, clock, wall_clock, monkeypatch, caplog, headers, configured, wait,
):
    def unexpected_sleep(*args):
        pytest.fail("The SDK must not sleep or retry after a 429")

    monkeypatch.setattr(llm_module.time, "sleep", unexpected_sleep)
    caplog.set_level(logging.INFO)
    attempts = []
    question = "PRIVATE-QUESTION about PIP support"

    def respond(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            response_headers = httpx.Headers(headers)
            response_headers["x-private-trace"] = "PRIVATE-TRACE-HEADER"
            # A provider error can echo sensitive request data. Use fake values
            # across the request, response headers, and SDK error body.
            echoed_auth = request.headers["authorization"]
            return httpx.Response(429, headers=response_headers, json={
                "error": {"message": f"PRIVATE-ERROR-BODY: {question}; {echoed_auth}"},
            })
        return _successful_completion_response()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = transport_client(http_client, seconds=configured)
        result = client.enhance_query(question)
        assert attempts == ["/v1/chat/completions"]
        assert result == {"query": question, "rate_limited": True}
        assert client.is_rate_limited()
        hint_extended = wait > float(configured or "30")
        fresh_429 = (logging.WARNING, (
            f"event=llm_provider_429 stage=enhancement wait_seconds={wait:g} "
            f"provider_hint_extended={hint_extended}"
        ))
        assert _cooldown_log_records(caplog) == [fresh_429]

        expires_at = clock[0] + wait
        clock[0] = expires_at - 0.01
        assert client.check_content_appropriateness(question)["rate_limited"] is True
        assert client.synthesize_response(question, [])["rate_limited"] is True
        assert client.enhance_query(question)["rate_limited"] is True
        assert len(attempts) == 1
        assert _cooldown_log_records(caplog) == [
            fresh_429,
            (logging.INFO, "event=llm_cooldown_suppressed stage=moderation remaining_seconds=0.01"),
        ]
        for private in (question, "PRIVATE-TRACE-HEADER", "PRIVATE-RETRY-HINT", "PRIVATE-ERROR-BODY", "test-key"):
            assert private not in caplog.text

        clock[0] = expires_at
        assert client.enhance_query("PIP support") == "PIP benefits"
        assert len(attempts) == 2
        assert not client.is_rate_limited()


def test_chat_resumes_after_provider_retry_after(transport_client, monkeypatch):
    provider_time = Mock(wraps=llm_module.time)
    provider_time.monotonic.return_value = 100.0
    monkeypatch.setattr(llm_module, "time", provider_time)

    answer = "You can apply for Personal Independence Payment through GOV.UK."
    search_query = "Personal Independence Payment application"
    source_url = "https://www.gov.uk/pip/how-to-claim"
    search = Mock(return_value=[search_hit("How to claim PIP", source_url)])
    moderation = '{"appropriate": true}'
    responses = iter([
        # The first question passes moderation but hits a limit during enhancement.
        _successful_completion_response(moderation),
        httpx.Response(
            429, headers={"Retry-After": "45"}, json={"error": {"message": "Too many requests"}},
        ),
        # The next admitted question runs all three provider stages.
        _successful_completion_response(moderation),
        _successful_completion_response(search_query),
        _successful_completion_response(answer),
    ])
    attempts = []

    def respond(request):
        attempts.append(request.url.path)
        response = next(responses, None)
        if response is None:
            pytest.fail("Unexpected extra provider call")
        return response

    unavailable = (
        "The AI provider is temporarily unavailable due to a rate limit. "
        "Please try again in a few minutes."
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        llm_client = transport_client(http_client, seconds="30")
        api = chat_client(monkeypatch, llm_client, search)
        question = {"question": "How do I apply for PIP?"}

        first = api.post("/chat", json=question)
        assert first.status_code == 200
        assert first.json()["answer"] == unavailable
        assert first.json()["sources"] == []
        assert attempts == ["/v1/chat/completions"] * 2
        search.assert_not_called()

        # The 30-second local minimum has elapsed, but the provider asked for 45.
        provider_time.monotonic.return_value = 144.99
        blocked = api.post("/chat", json={"question": "What is an EHCP?"})
        assert blocked.status_code == 200
        assert blocked.json()["answer"] == unavailable
        assert blocked.json()["sources"] == []
        assert len(attempts) == 2
        search.assert_not_called()

        provider_time.monotonic.return_value = 145.01
        recovered = api.post("/chat", json=question)
        assert recovered.status_code == 200
        assert recovered.json()["answer"] == answer
        assert recovered.json()["sources"] == [{
            "title": "How to claim PIP", "url": source_url, "publisher": "Gov.UK",
        }]
        assert attempts == ["/v1/chat/completions"] * 5
        search.assert_called_once_with(search_query, n_results=8, authority_boost=True)


@pytest.mark.parametrize("limited", [False, True], ids=["answer", "rate-limited"])
def test_health_responds_while_chat_provider_is_stalled(transport_client, monkeypatch, limited):
    from app import main

    question = {"question": "How do I apply for PIP?", "comprehension_level": "clear"}
    answer = "## Short Answer\nApply for PIP through GOV.UK."
    source_url = "https://www.gov.uk/pip/how-to-claim"
    search = Mock(return_value=[search_hit("How to claim PIP", source_url)])
    unavailable = (
        "The AI provider is temporarily unavailable due to a rate limit. "
        "Please try again in a few minutes."
    )

    async def scenario():
        entered_synthesis = asyncio.Event()
        release_synthesis = Event()
        loop = asyncio.get_running_loop()

        def synthesize(request):
            messages = json.loads(request.content)["messages"]
            assert "LANGUAGE LEVEL — CLEAR" in messages[0]["content"]
            loop.call_soon_threadsafe(entered_synthesis.set)
            if not release_synthesis.wait(timeout=5):
                pytest.fail("Synthesis was not released; it may be blocking the request loop")
            if limited:
                return httpx.Response(429, headers={"Retry-After": "45"}, json={
                    "error": {"message": "Too many requests"},
                })
            return _successful_completion_response(answer)

        responses = iter([
            lambda _: _successful_completion_response('{"appropriate": true}'),
            lambda _: _successful_completion_response("Personal Independence Payment application"),
            synthesize,
        ])

        def respond(request):
            reply = next(responses, None)
            if reply is None:
                pytest.fail("Unexpected extra provider call")
            return reply(request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
            llm_client = transport_client(provider, seconds="30")
            chat_client(monkeypatch, llm_client, search)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test",
            ) as api:
                pending = asyncio.create_task(api.post("/chat", json=question))
                chats = [pending]
                try:
                    await asyncio.wait_for(entered_synthesis.wait(), timeout=2)
                    health = await asyncio.wait_for(api.get("/health"), timeout=2)
                    assert health.status_code == 200
                    assert health.json() == {"status": "ok"}
                    assert not pending.done(), "Health responded only after the stalled chat finished"
                    if limited:
                        # A second chat enters the same event loop before the 429.
                        # It must wait rather than start another provider request.
                        queued = asyncio.create_task(api.post("/chat", json={
                            "question": "What is an EHCP?",
                        }))
                        chats.append(queued)
                        await asyncio.wait({queued}, timeout=0.05)
                        assert not queued.done()
                finally:
                    release_synthesis.set()
                    responses_from_api = await asyncio.gather(*chats, return_exceptions=True)

                for response in responses_from_api:
                    if isinstance(response, BaseException):
                        raise response
                    assert response.status_code == 200
                    assert response.json()["answer"] == (unavailable if limited else answer)
                    assert response.json()["sources"] == ([] if limited else [{
                        "title": "How to claim PIP", "url": source_url, "publisher": "Gov.UK",
                    }])
                    assert response.json()["timestamp"].startswith("Last checked: ")
                    assert "NHS 111" in response.json()["disclaimer"]
                search.assert_called_once()
                assert llm_client.is_rate_limited() is limited

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_running", [False, True], ids=["queued", "running"])
def test_cancelled_chat_does_not_start_extra_provider_calls(
    transport_client, monkeypatch, cancel_running,
):
    from app import main

    first_question = "How do I apply for PIP?"
    next_question = "What is an EHCP?"
    answer = "Apply for PIP through GOV.UK."
    search_query = "Personal Independence Payment application"
    search = Mock(return_value=[search_hit("How to claim PIP", "https://www.gov.uk/pip/how-to-claim")])
    # Freeze only the cooldown clock, not asyncio's timeout clock.
    provider_time = Mock(wraps=llm_module.time, monotonic=lambda: 100.0)
    monkeypatch.setattr(llm_module, "time", provider_time)

    async def scenario():
        loop = asyncio.get_running_loop()
        entered_synthesis = asyncio.Event()
        next_submitted = asyncio.Event()
        release_synthesis = Event()
        attempts = []
        submit = main._rag_executor.submit

        def observe_submission(func, *args, **kwargs):
            future = submit(func, *args, **kwargs)
            if func is main._answer_chat and args[0].question == next_question:
                next_submitted.set()
            return future

        # Observe the real queue, without replacing its worker or guessing when
        # the second request has reached it.
        monkeypatch.setattr(main._rag_executor, "submit", observe_submission)

        def respond(request):
            attempts.append(request.url.path)
            if len(attempts) == 1:
                return _successful_completion_response('{"appropriate": true}')
            if len(attempts) == 2:
                return _successful_completion_response(search_query)
            if len(attempts) != 3:
                pytest.fail("Unexpected extra provider call")
            loop.call_soon_threadsafe(entered_synthesis.set)
            assert release_synthesis.wait(timeout=5), "Test never released stalled synthesis"
            if cancel_running:
                return httpx.Response(
                    429, headers={"Retry-After": "45"}, json={"error": {"message": "Too many requests"}},
                )
            return _successful_completion_response(answer)

        async def cancel_request(task):
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)

        with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
            llm_client = transport_client(provider, seconds="30")
            chat_client(monkeypatch, llm_client, search)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test",
            ) as api:
                first = asyncio.create_task(api.post("/chat", json={"question": first_question}))
                chats = [first]
                try:
                    await asyncio.wait_for(entered_synthesis.wait(), timeout=2)
                    if cancel_running:
                        await cancel_request(first)

                    queued = asyncio.create_task(api.post("/chat", json={"question": next_question}))
                    chats.append(queued)
                    await asyncio.wait_for(next_submitted.wait(), timeout=2)
                    assert not queued.done(), "The next chat must wait behind the stalled completion"
                    assert not llm_client.is_rate_limited()
                    assert len(attempts) == 3
                    if not cancel_running:
                        await cancel_request(queued)
                finally:
                    release_synthesis.set()
                    await asyncio.wait_for(asyncio.gather(*chats, return_exceptions=True), timeout=2)
                    # A cancelled caller has no reply to await. Drain the worker
                    # so late provider calls cannot escape the final assertions.
                    await asyncio.wait_for(main._run_rag_work(lambda: None), timeout=2)

                response = (queued if cancel_running else first).result()
                assert response.status_code == 200
                if cancel_running:
                    assert response.json()["answer"] == (
                        "The AI provider is temporarily unavailable due to a rate limit. "
                        "Please try again in a few minutes."
                    )
                    assert response.json()["sources"] == []
                    assert llm_client.rate_limit_remaining_seconds() == 45
                else:
                    assert response.json()["answer"] == answer
                    assert not llm_client.is_rate_limited()
                assert attempts == ["/v1/chat/completions"] * 3
                search.assert_called_once_with(search_query, n_results=8, authority_boost=True)

    asyncio.run(scenario())


def test_background_warmup_keeps_cooldown_from_first_chat(transport_client, monkeypatch):
    from app import main

    respond = Mock(return_value=httpx.Response(
        429, headers={"Retry-After": "45"}, json={"error": {"message": "Too many requests"}},
    ))
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
        llm_client = transport_client(provider, seconds="30")
        api = chat_client(monkeypatch, llm_client, Mock(return_value=[]))
        system = main._rag_system
        system.vector_store.embedder = Mock()
        system.initialized = False
        system.initialize = Mock(side_effect=lambda: setattr(system, "initialized", True))
        monkeypatch.setattr(main, "_startup_complete", False)
        monkeypatch.setattr(main, "_rag_system", None)
        monkeypatch.setattr("rag.rag_system.get_rag_system", lambda: system)

        question = {"question": "How do I apply for PIP?"}
        first = api.post("/chat", json=question)
        assert first.status_code == 200
        assert "temporarily unavailable due to a rate limit" in first.json()["answer"]

        asyncio.run(main.initialize_rag_background())
        assert main._startup_complete
        system.initialize.assert_called_once_with()
        system.vector_store.embedder.encode.assert_called_once_with(["warmup"])

        second = api.post("/chat", json=question)
        assert second.status_code == 200
        assert second.json()["answer"] == first.json()["answer"]
        respond.assert_called_once()


def test_chat_returns_uk_crisis_guidance_during_provider_cooldown(transport_client, monkeypatch):
    provider_time = Mock(wraps=llm_module.time)
    provider_time.monotonic.return_value = 100.0
    monkeypatch.setattr(llm_module, "time", provider_time)

    respond = Mock(return_value=httpx.Response(
        429, headers={"Retry-After": "45"}, json={"error": {"message": "Too many requests"}},
    ))
    search = Mock()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        llm_client = transport_client(http_client, seconds="30")
        api = chat_client(monkeypatch, llm_client, search)
        first = api.post("/chat", json={"question": "How do I apply for PIP?"})

        assert first.status_code == 200
        assert "temporarily unavailable due to a rate limit" in first.json()["answer"]
        assert llm_client.is_rate_limited()
        respond.assert_called_once()

        crisis = api.post("/chat", json={"question": "I feel suicidal. What should I do?"})

        assert crisis.status_code == 200
        data = crisis.json()
        assert "temporarily unavailable" not in data["answer"]
        assert "call 999" in data["answer"]
        assert "go to A&E" in data["answer"]
        assert "NHS urgent mental health help" in data["answer"]
        assert "Samaritans on 116 123" in data["answer"]
        assert data["sources"] == []
        respond.assert_called_once()
        search.assert_not_called()


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
def test_all_provider_stages_activate_shared_cooldown(client_factory, clock, caplog, stage):
    create = Mock(side_effect=_rate_limit_error({"Retry-After": "45"}))
    client = client_factory(create, seconds="12.5")
    calls = {
        "moderation": lambda: client.check_content_appropriateness("PIP support"),
        "enhancement": lambda: client.enhance_query("PIP support"),
        "synthesis": lambda: client.synthesize_response("PIP support", []),
    }
    assert calls[stage]()["rate_limited"] is True
    assert client.is_rate_limited()
    assert _cooldown_log_records(caplog) == [(
        logging.WARNING, f"event=llm_provider_429 stage={stage} wait_seconds=45 provider_hint_extended=True",
    )]
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


def test_repeated_rate_limits_restart_cooldown(client_factory, clock, caplog):
    caplog.set_level(logging.INFO, logger=llm_module.__name__)
    create = Mock(side_effect=_rate_limit_error())
    client = client_factory(create)
    client.enhance_query("PIP support")
    client.enhance_query("PIP support")
    clock[0] = 130.0
    client.enhance_query("PIP support")
    clock[0] = 159.9
    assert client.is_rate_limited()
    client.enhance_query("PIP support")
    assert create.call_count == 2
    fresh_429 = (logging.WARNING, "event=llm_provider_429 stage=enhancement wait_seconds=30 provider_hint_extended=False")
    assert _cooldown_log_records(caplog) == [
        fresh_429,
        (logging.INFO, "event=llm_cooldown_suppressed stage=enhancement remaining_seconds=30"),
        fresh_429,
        (logging.INFO, "event=llm_cooldown_suppressed stage=enhancement remaining_seconds=0.1"),
    ]


def test_overlapping_provider_429s_report_effective_wait_without_relogging_suppression(
    client_factory, clock, caplog,
):
    caplog.set_level(logging.INFO, logger=llm_module.__name__)
    create = Mock(side_effect=_rate_limit_error({"Retry-After": "90"}))
    client = client_factory(create)
    client.enhance_query("PIP support")
    client.enhance_query("PIP support")

    # Simulate requests already in flight when the first 429 came back.
    clock[0] = 110.0
    client._record_provider_429("moderation", httpx.Headers({"Retry-After": "60"}))
    clock[0] = 120.0
    client._record_provider_429("synthesis", httpx.Headers({"Retry-After": "100"}))
    assert client.enhance_query("PIP support")["rate_limited"] is True
    assert create.call_count == 1

    assert _cooldown_log_records(caplog) == [
        (logging.WARNING, "event=llm_provider_429 stage=enhancement wait_seconds=90 provider_hint_extended=True"),
        (logging.INFO, "event=llm_cooldown_suppressed stage=enhancement remaining_seconds=90"),
        (logging.WARNING, "event=llm_provider_429 stage=moderation wait_seconds=80 provider_hint_extended=False"),
        (logging.WARNING, "event=llm_provider_429 stage=synthesis wait_seconds=100 provider_hint_extended=True"),
    ]
    clock[0] = 219.99
    assert client.is_rate_limited()
    clock[0] = 220.0
    assert not client.is_rate_limited()


def test_zero_disables_cooldown_even_with_retry_after(transport_client, clock, caplog):
    caplog.set_level(logging.INFO, logger=llm_module.__name__)
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
        assert _cooldown_log_records(caplog) == [(
            logging.WARNING, "event=llm_provider_429 stage=enhancement wait_seconds=0 provider_hint_extended=False",
        )] * 2


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