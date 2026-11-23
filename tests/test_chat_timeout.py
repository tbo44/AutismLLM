"""Exercise chat deadlines without sending any requests to a live provider."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

from app import main
from rag import answerer, llm_client
from tests.llm_test_helpers import chat_client, search_hit


QUESTION = {"question": "How do I apply for PIP?"}
SOURCE = search_hit("How to claim PIP", "https://www.gov.uk/pip/how-to-claim")
ANSWER_TEXT = "You can apply for PIP through GOV.UK."
UNAVAILABLE = (
    "The AI provider is temporarily unavailable due to a rate limit. "
    "Please try again in a few minutes."
)


def _completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def provider_for(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_RATE_LIMIT_COOLDOWN_SECONDS", "30")
    build_client = llm_client._build_openai_client

    def make(transport_client):
        monkeypatch.setattr(
            llm_client, "_build_openai_client", lambda: build_client(http_client=transport_client),
        )
        return llm_client.UKAutismLLMClient()

    return make


def _assert_timeout(response):
    assert response.status_code == 200
    data = response.json()
    assert data["answer"].startswith("Maya took too long to answer. Please try")
    for domain in ("www.nhs.uk", "www.autism.org.uk", "www.gov.uk"):
        assert f"(https://{domain}/" in data["answer"]
    assert data["sources"] == []


@pytest.mark.parametrize("stage", [0, 1, 2], ids=["moderation", "enhancement", "synthesis"])
def test_socket_timeout_returns_retry_fallback_without_extra_attempts(
    provider_for, monkeypatch, stage,
):
    now = [100.0]
    monkeypatch.setattr(llm_client, "monotonic", lambda: now[0])
    monkeypatch.setattr(answerer, "monotonic", lambda: now[0])
    attempts = []
    replies = ['{"appropriate": true}', "PIP application", ANSWER_TEXT]

    # Match the provider's fake clock without changing the asyncio clock.
    monkeypatch.setattr(main, "time", Mock(wraps=main.time, monotonic=lambda: now[0]))

    def respond(request):
        index = len(attempts)
        attempts.append(request)
        if index < stage:
            return _completion(replies[index])
        assert index == stage, "The expired provider stage must not retry"
        read_timeout = request.extensions["timeout"]["read"]
        assert read_timeout == 20.0
        assert request.extensions["timeout"]["connect"] == 5.0
        now[0] += read_timeout
        raise httpx.ReadTimeout("mock provider stalled", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        llm = provider_for(transport)
        search = Mock(return_value=[SOURCE])
        api = chat_client(monkeypatch, llm, search)
        _assert_timeout(api.post("/chat", json=QUESTION))

    assert len(attempts) == stage + 1
    assert search.call_count == (1 if stage == 2 else 0)
    assert not llm.is_rate_limited()


@pytest.mark.parametrize("late_429", [False, True], ids=["late-answer", "late-rate-limit"])
def test_stalled_worker_outlives_chat_deadline_without_overlapping_calls(
    provider_for, monkeypatch, late_429,
):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = Event()
        attempts = []
        search = Mock(return_value=[SOURCE])

        def respond(request):
            attempts.append(request)
            attempt = len(attempts)
            if attempt == 1:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(timeout=5), "Test never released mock transport"
                if late_429:
                    return httpx.Response(429, headers={"Retry-After": "45"})
                return _completion('{"appropriate": true}')
            assert release.is_set(), "A second call overlapped the stalled provider request"
            return _completion(['{"appropriate": true}', "PIP application", ANSWER_TEXT][attempt - 2])

        with ThreadPoolExecutor(max_workers=1) as worker, httpx.Client(
            transport=httpx.MockTransport(respond),
        ) as transport:
            monkeypatch.setattr(main, "_rag_executor", worker)
            monkeypatch.setattr(main, "_CHAT_TIMEOUT_SECONDS", 0.5)
            llm = provider_for(transport)
            chat_client(monkeypatch, llm, search)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test",
            ) as api:
                first = asyncio.create_task(api.post("/chat", json=QUESTION))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    queued = asyncio.create_task(api.post("/chat", json={"question": "What is EHCP?"}))
                    health = await asyncio.wait_for(api.get("/health"), timeout=1)
                    assert health.json() == {"status": "ok"}
                    crisis = await asyncio.wait_for(api.post(
                        "/chat", json={"question": "I feel suicidal. What should I do?"},
                    ), timeout=1)
                    assert "call 999" in crisis.json()["answer"]
                    assert not first.done(), "Unrelated requests must respond before the chat deadline"
                    for response in await asyncio.wait_for(asyncio.gather(first, queued), timeout=2):
                        _assert_timeout(response)
                    assert len(attempts) == 1
                    assert 0 < attempts[0].extensions["timeout"]["read"] <= 0.5
                    assert not llm.is_rate_limited()
                finally:
                    release.set()
                    # Drain the worker before checking for calls from expired chats.
                    await asyncio.wait_for(main._run_rag_work(lambda: None), timeout=2)

                assert len(attempts) == 1
                search.assert_not_called()
                monkeypatch.setattr(main, "_CHAT_TIMEOUT_SECONDS", 35.0)
                retry = await api.post("/chat", json=QUESTION)
                if late_429:
                    assert 30 < llm.rate_limit_remaining_seconds() <= 45
                    assert retry.json()["answer"] == UNAVAILABLE
                    assert len(attempts) == 1
                else:
                    assert retry.json()["answer"] == ANSWER_TEXT
                    assert len(attempts) == 4

    asyncio.run(scenario())


def test_chat_browser_timeout_and_recovery():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("chat_timeout_runner.cjs"))],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "chat timeout and retry verified"
