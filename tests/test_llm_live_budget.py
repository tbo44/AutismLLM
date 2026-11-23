"""Run the opt-in tests in a child pytest session with no provider access."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


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

    def respond(request):
        messages = json.loads(request.content)["messages"]
        prompt = messages[0]["content"]
        if prompt.startswith("You are a content filter"):
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
        if first:
            # Exercise a real SDK retry without waiting for its default backoff.
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
    }

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-c", str(ROOT / "pytest.ini"), "-qx",
             "-m", "llm_live", "-k", "test_live_model_access", "test_live.py", *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        )
        attempts = tmp_path / "attempts.log"
        return result.returncode, result.stdout + result.stderr, (
            attempts.read_text().splitlines() if attempts.exists() else []
        )

    return run


@pytest.mark.parametrize("limit,passed", [(1, 0), (4, 1)])
def test_budget_blocks_retry_and_is_shared_between_live_cases(isolated_live_run, limit, passed):
    status, output, attempts = isolated_live_run(f"--llm-request-limit={limit}")

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
