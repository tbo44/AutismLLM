"""Run the opt-in tests in a child pytest session with no provider access."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACCESS_CASE = "test_live_model_access"
READING_CASE = "test_live_clear_answers_are_simpler[school-plan-annual-review]"
STRUCTURED_CASES = (
    "test_live_model_access_and_structured_answer",
    "test_live_model_access_second_case",
)


@pytest.fixture
def isolated_live_run(tmp_path):
    (tmp_path / "static").mkdir()
    (tmp_path / "test_live.py").write_text(
        (ROOT / "tests/test_llm_live.py").read_text() + """

def test_live_model_access_second_case(live_answer):
    test_live_model_access_and_structured_answer(live_answer)
"""
    )
    (tmp_path / "conftest.py").write_text("""
import json
import os
from pathlib import Path
import socket

import httpx
import pytest

from rag import llm_client
from tests.conftest import (
    pytest_addoption, pytest_collection_finish, pytest_configure, pytest_runtest_protocol,
)


@pytest.fixture(autouse=True)
def offline_provider(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Offline live budget check tried to open a connection.", pytrace=False)

    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    attempts = Path("attempts.log")
    rejection = json.loads(os.environ["PROVIDER_REJECTION"])

    def respond(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        prompt = messages[0]["content"]
        if prompt == "Reply with one word: OK.":
            assert request.method == "POST"
            assert request.url.path == "/v1/chat/completions"
            assert payload["model"] == "test-model"
            assert len(messages) == 1 and messages[0]["role"] == "user"
            assert 0 < payload["max_tokens"] <= 16
            stage = "access"
            content = " " if os.environ["ACCESS_RESPONSE"] == "empty" else "Ready."
        elif prompt.startswith("You are a content filter"):
            stage = "moderation"
            content = '{"appropriate": true, "reason": "", "category": "autism-related"}'
        elif prompt.startswith("Rewrite the following question"):
            stage = "enhancement"
            content = "school plan annual review"
        else:
            stage = "synthesis"
            content = "## Short Answer\\nThe school reviews the plan every year."

        first = not attempts.exists()
        with attempts.open("a") as log:
            print(stage, file=log)
        if rejection and stage == rejection["stage"]:
            return httpx.Response(rejection["status"], json={"error": rejection["error"]})
        if stage == "access" and os.environ["ACCESS_RESPONSE"] == "error":
            return httpx.Response(500, headers={"retry-after-ms": "1"})
        if first and stage != "access":
            # Exercise an SDK retry in the structured check without its default backoff.
            return httpx.Response(500, headers={"retry-after-ms": "1"})
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

    build_client = llm_client._build_openai_client
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(llm_client, "_build_openai_client", lambda: build_client(http_client=http))
        yield
""")
    env = {
        "PYTHONPATH": os.pathsep.join(str(path) for path in [
            ROOT, *(Path(path).resolve() for path in sys.path if path),
        ]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "LLM_PROVIDER": "custom",
        "LLM_BASE_URL": "https://provider.invalid/v1",
        "LLM_API_KEY": "test-only",
        "LLM_MODEL": "test-model",
    }

    def run(*args, cases=STRUCTURED_CASES, access_response="ok", rejection=None):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-c", str(ROOT / "pytest.ini"), "-q",
             "-m", "llm_live", *(f"test_live.py::{case}" for case in cases), *args],
            cwd=tmp_path, env={
                **env,
                "ACCESS_RESPONSE": access_response,
                "PROVIDER_REJECTION": json.dumps(rejection),
            },
            capture_output=True, text=True, timeout=60,
        )
        attempts = tmp_path / "attempts.log"
        return result.returncode, result.stdout + result.stderr, (
            attempts.read_text().splitlines() if attempts.exists() else []
        )

    return run


@pytest.mark.parametrize("response,expected_failure", [
    ("ok", None),
    ("error", "Live LLM provider request failed for 'test-model' (HTTP 500)."),
    ("empty", "Live LLM model 'test-model' returned no completion text."),
])
def test_model_access_uses_one_attempt(isolated_live_run, response, expected_failure):
    status, output, attempts = isolated_live_run(
        "--llm-request-limit=1", cases=(ACCESS_CASE,), access_response=response,
    )

    if expected_failure:
        assert status == pytest.ExitCode.TESTS_FAILED, output
        assert expected_failure in output, output
        assert "budget exhausted" not in output, output
    else:
        assert status == pytest.ExitCode.OK, output
        assert "1 passed" in output, output
    assert attempts == ["access"]


def test_model_access_counts_against_shared_budget(isolated_live_run):
    status, output, attempts = isolated_live_run(
        "--llm-request-limit=1", cases=(ACCESS_CASE, STRUCTURED_CASES[0]),
    )

    assert status == pytest.ExitCode.TESTS_FAILED, output
    assert "1 passed" in output, output
    assert "budget exhausted (1/1 HTTP attempts used)" in output, output
    assert attempts == ["access"]


@pytest.mark.parametrize("status,fields,stage,diagnostic", [
    (401, {"code": "invalid_api_key"}, "access", "provider denied access"),
    (403, {}, "access", "provider denied access"),
    (429, {"code": "insufficient_quota"}, "access", "provider quota exhausted"),
    (429, {"type": "billing_hard_limit_reached"}, "moderation", "provider quota exhausted"),
])
def test_account_wide_rejection_fails_once_and_skips_remaining_live_cases(
    isolated_live_run, status, fields, stage, diagnostic,
):
    cases = (ACCESS_CASE, STRUCTURED_CASES[0], READING_CASE)
    if stage == "moderation":
        # The first failure can come from inside /chat, without the access check running first.
        cases = (STRUCTURED_CASES[0], ACCESS_CASE, READING_CASE)
    result, output, attempts = isolated_live_run(
        "--llm-request-limit=20", "-rs", cases=cases,
        rejection={"stage": stage, "status": status, "error": {
            **fields, "message": "raw provider diagnostic must stay hidden",
        }},
    )

    assert result == pytest.ExitCode.TESTS_FAILED, output
    assert diagnostic in output and f"(HTTP {status})" in output, output
    assert "1 failed, 2 skipped" in output, output
    assert "An earlier live check received an account-wide provider rejection" in output, output
    assert "raw provider diagnostic must stay hidden" not in output, output
    assert attempts == [stage]


@pytest.mark.parametrize("status,code,diagnostic", [
    (429, "rate_limit_exceeded", "provider rate-limited"),
    (403, "model_not_available", "model unavailable"),
])
def test_other_provider_failures_do_not_skip_later_live_cases(isolated_live_run, status, code, diagnostic):
    result, output, attempts = isolated_live_run(
        "--llm-request-limit=20", cases=(ACCESS_CASE, STRUCTURED_CASES[0]),
        rejection={"stage": "access", "status": status, "error": {"code": code}},
    )

    assert result == pytest.ExitCode.TESTS_FAILED, output
    assert diagnostic in output, output
    assert "1 failed, 1 passed" in output, output
    assert attempts == ["access", "moderation", "enhancement", "synthesis"]


@pytest.mark.parametrize("limit,passed", [(1, 0), (4, 1)])
def test_budget_blocks_retry_and_is_shared_between_live_cases(isolated_live_run, limit, passed):
    status, output, attempts = isolated_live_run("-x", f"--llm-request-limit={limit}")

    assert status == pytest.ExitCode.TESTS_FAILED, output
    assert f"budget exhausted ({limit}/{limit} HTTP attempts used)" in output, output
    assert "the next attempt was blocked before sending" in output, output
    assert "1 failed" in output, output
    assert ("1 passed" in output) == bool(passed), output
    assert attempts == ["moderation", "moderation", "enhancement", "synthesis"][:limit]


@pytest.mark.parametrize("args", [(), ("--llm-request-limit=0",), ("--llm-request-limit=bad",)])
def test_live_run_requires_a_positive_budget(isolated_live_run, args):
    status, output, attempts = isolated_live_run(*args)

    assert status == pytest.ExitCode.USAGE_ERROR, output
    assert "--llm-request-limit" in output, output
    assert not attempts
