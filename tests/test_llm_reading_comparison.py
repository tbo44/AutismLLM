"""Exercise the comparison with the real prompt/SDK and an offline HTTP transport."""

import json

import httpx
import pytest

from scripts.compare_llm_reading import ComparisonStopped, RequestBudget, _live_sampler, main
from tests.llm_answer_essentials import ANNUAL_REVIEW, READING_CASES


NOTIONAL_REQUESTS = 2 * len(READING_CASES) * 2 * 2  # Two levels, models, and samples per topic.
ARGS = [
    "--live", "--model", "candidate-a", "--model", "candidate-b",
    "--samples-per-topic", "2", "--request-limit", str(NOTIONAL_REQUESTS),
]
ANSWERS = {
    "school-plan-annual-review": (
        "The school checks the plan every year. The council decides what to do with it.",
        "Following an annual review at least once a year, the local authority determines whether "
        "the existing educational support arrangements should be maintained, amended, or discontinued.",
    ),
    "disability-benefit-claim": (
        "Your child needs more care or help to walk than others of the same age. Send the form by post.",
        "Eligibility depends on whether a child has additional care needs or greater mobility difficulties "
        "than children of the same age. Applicants should return the completed documentation by post.",
    ),
    "autism-assessment-referral": (
        "Your family doctor can refer your child for an autism assessment. Tell them what you noticed.",
        "Your general practitioner can refer your child for a specialist autism assessment if appropriate. "
        "Describe concrete examples and observations gathered from different situations to support the referral.",
    ),
    "pip-mandatory-reconsideration": (
        "Ask the benefits office to look at the decision again. You have a month from the date on your letter.",
        "Request mandatory reconsideration from the benefits office responsible for the original decision. "
        "The usual deadline is one calendar month from the date of the decision letter.",
    ),
}


@pytest.fixture(autouse=True)
def fake_provider_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "stub-key-only")
    monkeypatch.setenv("TEMPERATURE", "0.2")
    monkeypatch.setenv("TOP_P", "0.9")


def _completion(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def _read_request(request):
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    payload = json.loads(request.content)
    system, user = (message["content"] for message in payload["messages"])
    case = next(case for case in READING_CASES if case.question in user)
    assert case.hit["text"] in system
    assert payload["temperature"] == 0.2 and payload["top_p"] == 0.9
    level = "clear" if "LANGUAGE LEVEL — CLEAR" in system else "complex"
    assert f"LANGUAGE LEVEL — {level.upper()}" in system
    return payload["model"], case.id, level


def _passing_answer(case_id, level):
    return _completion("## Short Answer\n" + ANSWERS[case_id][int(level == "complex")])


@pytest.mark.parametrize(("args", "diagnostic"), [
    (ARGS[1:], "--live is required"),
    (["--li", *ARGS[1:]], "unrecognized arguments: --li"),
    (ARGS[:-2], "--request-limit"),
    ([*ARGS[:-1], "0"], "must be positive"),
    ([*ARGS[:-1], str(NOTIONAL_REQUESTS - 1)], f"needs up to {NOTIONAL_REQUESTS} requests"),
    ([*ARGS[:4], "candidate-a", *ARGS[5:]], "at least two distinct"),
    ([*ARGS[:6], "1", *ARGS[7:]], "must be at least 2"),
])
def test_invalid_invocations_do_not_set_up_a_client(args, diagnostic, capsys, monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("Client setup must not run for an invalid invocation.")

    monkeypatch.setattr("scripts.compare_llm_reading.UKAutismLLMClient", unexpected)
    report_path = tmp_path / "reading-run.json"
    with pytest.raises(SystemExit) as error:
        main([*args, "--report-json", str(report_path)])
    assert error.value.code == 2
    assert diagnostic in capsys.readouterr().err
    assert not report_path.exists()


def test_complete_comparison_reports_pass_frequency_without_failing_on_quality_miss(capsys, tmp_path):
    requests = []

    def provider(request):
        model, case_id, level = _read_request(request)
        requests.append((model, case_id, level))
        if model == "candidate-a" and case_id == ANNUAL_REVIEW.id and level == "clear" and len(requests) == 1:
            return _completion("## Short Answer\nThe plan gets checked every year.")
        return _passing_answer(case_id, level)

    assert main(ARGS, transport=httpx.MockTransport(provider)) == 0
    assert requests == [
        (model, case.id, level)
        for _ in range(2)
        for case in READING_CASES
        for model in ("candidate-a", "candidate-b")
        for level in ("clear", "complex")
    ]
    output = capsys.readouterr().out
    assert f"Provider requests attempted: {NOTIONAL_REQUESTS}/{NOTIONAL_REQUESTS}" in output
    assert f"'candidate-a' | {ANNUAL_REVIEW.id}: 1/2 passed (50%); 1 quality failures; 0 provider failures" in output
    assert "Clear: The council decides" in output
    assert output.count("2/2 passed (100%)") == 2 * len(READING_CASES) - 1

    baseline_requests = requests.copy()
    requests.clear()
    report_path = tmp_path / "reading-run.json"
    assert main([*ARGS, "--report-json", str(report_path)], transport=httpx.MockTransport(provider)) == 0
    assert requests == baseline_requests
    assert capsys.readouterr().out == output

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["schema_version"] == 1
    assert report["rubric_id"] == "maya-reading-level-v1"
    assert report["candidate_ids"] == ["candidate-a", "candidate-b"]
    assert report["sampling"] == {"samples_per_topic": 2, "temperature": 0.2, "top_p": 0.9}
    assert report["requests"] == {
        "attempted": NOTIONAL_REQUESTS, "limit": NOTIONAL_REQUESTS, "planned": NOTIONAL_REQUESTS,
    }
    assert report["status"] == "complete"
    assert report["stopped_reason"] is None
    assert [(row["candidate_id"], row["topic_id"]) for row in report["results"]] == [
        (model, case.id) for model in ("candidate-a", "candidate-b") for case in READING_CASES
    ]
    for row in report["results"]:
        failed = int(row["candidate_id"] == "candidate-a" and row["topic_id"] == ANNUAL_REVIEW.id)
        assert row["scored_pairs"] == 2
        assert row["passed_pairs"] == 2 - failed
        assert row["quality_failed_pairs"] == failed
        assert row["pass_rate"] == (2 - failed) / 2
        assert row["provider_failed_pairs"] == row["unfinished_pairs"] == 0
        assert row["provider_failure_counts"] == {}
    for private in ("stub-key-only", "provider.invalid", "The plan gets checked every year.", "Clear: The council decides"):
        assert private not in report_text
    assert all(answer not in report_text for answers in ANSWERS.values() for answer in answers)


@pytest.mark.parametrize("existing", [True, False])
def test_unavailable_report_path_stops_before_client_setup(existing, tmp_path, capsys, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Client setup must not run if the requested report cannot be created.")

    monkeypatch.setattr("scripts.compare_llm_reading.UKAutismLLMClient", unexpected)
    report_path = tmp_path / "reading-run.json" if existing else tmp_path / "missing" / "reading-run.json"
    if existing:
        report_path.write_text("previous report", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main([*ARGS, "--report-json", str(report_path)])
    assert error.value.code == 2
    assert "cannot create JSON report" in capsys.readouterr().err
    if existing:
        assert report_path.read_text(encoding="utf-8") == "previous report"
    else:
        assert not report_path.exists()


@pytest.mark.parametrize(("clear_answer", "diagnostic"), [
    ("The school checks the plan every year.", "missing the '## Short Answer' heading"),
    ("## Short Answer", "no answer text under the headings"),
    ("## Short Answer\nEHCPs get checked every year. The council decides what to do.", "first use: EHCP"),
    ("## Short Answer\n" + ANSWERS[ANNUAL_REVIEW.id][1], "Clear was not noticeably simpler"),
])
def test_complete_pairs_use_the_live_rubric(clear_answer, diagnostic, capsys):
    def provider(request):
        model, case_id, level = _read_request(request)
        if (model, case_id, level) == ("candidate-a", ANNUAL_REVIEW.id, "clear"):
            return _completion(clear_answer)
        return _passing_answer(case_id, level)

    assert main(ARGS, transport=httpx.MockTransport(provider)) == 0
    output = capsys.readouterr().out
    assert f"'candidate-a' | {ANNUAL_REVIEW.id}: 0/2 passed (0%); 2 quality failures; 0 provider failures" in output
    assert diagnostic in output


@pytest.mark.parametrize(("provider_error", "label"), [
    (httpx.Response(503, json={"error": {"message": "private-body"}}), "provider service error (HTTP 503)"),
    (httpx.Response(307, headers={"Location": "https://redirect.invalid/v1"}), "provider request rejected (HTTP 307)"),
    (httpx.Response(404, json={"error": {"code": "model_not_found"}}), "model unavailable (HTTP 404)"),
    (httpx.Response(200, json={"choices": []}), "provider returned an invalid completion"),
    (httpx.Response(200, text="private-body", headers={"Content-Type": "application/json"}),
     "provider returned an invalid completion"),
    (httpx.ConnectError("private-endpoint"), "provider unreachable"),
    (httpx.ReadTimeout("private-endpoint"), "provider request timed out"),
])
def test_provider_failures_excluded_from_quality_without_retries(
    provider_error, label, capsys, caplog, tmp_path,
):
    attempts = 0

    def provider(request):
        nonlocal attempts
        attempts += 1
        model, case_id, level = _read_request(request)
        # Even a structurally bad first answer is not scored if the second
        # request fails. The other candidate and later samples still run.
        if attempts == 1:
            return _completion("An answer without headings.")
        if attempts == 2:
            if isinstance(provider_error, Exception):
                raise provider_error
            return provider_error
        return _passing_answer(case_id, level)

    report_path = tmp_path / "reading-run.json"
    assert main([*ARGS, "--report-json", str(report_path)], transport=httpx.MockTransport(provider)) == 1
    assert attempts == NOTIONAL_REQUESTS
    output = capsys.readouterr().out
    assert f"'candidate-a' | {ANNUAL_REVIEW.id}: 1/1 passed (100%); 0 quality failures; 1 provider failures" in output
    assert output.count("2/2 passed (100%)") == 2 * len(READING_CASES) - 1
    assert f"1 × {label}" in output

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["status"] == "incomplete"
    assert report["stopped_reason"] is None
    row = report["results"][0]
    assert row["scored_pairs"] == row["passed_pairs"] == 1
    assert row["quality_failed_pairs"] == row["unfinished_pairs"] == 0
    assert row["provider_failed_pairs"] == 1
    assert row["provider_failure_counts"] == {label: 1}
    assert row["pass_rate"] == 1.0
    assert "An answer without headings." not in report_text
    for private in ("private-body", "private-endpoint", "redirect.invalid", "stub-key-only"):
        assert private not in output + caplog.text + report_text


def test_rate_limit_stops_other_requests_and_reports_unfinished_pairs(capsys, tmp_path):
    attempts = []

    def provider(request):
        _, case_id, level = _read_request(request)
        attempts.append(request)
        if len(attempts) == 3:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return _passing_answer(case_id, level)

    report_path = tmp_path / "reading-run.json"
    assert main([*ARGS, "--report-json", str(report_path)], transport=httpx.MockTransport(provider)) == 1
    assert len(attempts) == 3
    output = capsys.readouterr().out
    assert f"Provider requests attempted: 3/{NOTIONAL_REQUESTS}" in output
    assert "1/1 passed (100%); 0 quality failures; 0 provider failures; 1 not completed" in output
    assert "0/0 passed (no pairs scored); 0 quality failures; 1 provider failures; 1 not completed" in output
    assert "1 × provider rate or quota limit (HTTP 429)" in output

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "incomplete"
    assert report["stopped_reason"] in output
    assert report["requests"]["attempted"] == 3
    scored, provider_failed = (
        next(row for row in report["results"] if row["candidate_id"] == model and row["topic_id"] == ANNUAL_REVIEW.id)
        for model in ("candidate-a", "candidate-b")
    )
    assert scored["scored_pairs"] == scored["passed_pairs"] == scored["unfinished_pairs"] == 1
    assert provider_failed["scored_pairs"] == provider_failed["quality_failed_pairs"] == 0
    assert provider_failed["provider_failed_pairs"] == provider_failed["unfinished_pairs"] == 1
    assert provider_failed["pass_rate"] is None
    assert provider_failed["provider_failure_counts"] == {"provider rate or quota limit (HTTP 429)": 1}
    untouched = [row for row in report["results"] if row["topic_id"] != ANNUAL_REVIEW.id]
    assert len(untouched) == 2 * (len(READING_CASES) - 1)
    assert all(
        row["scored_pairs"] == row["provider_failed_pairs"] == 0
        and row["unfinished_pairs"] == 2 and row["pass_rate"] is None
        for row in untouched
    )


def test_report_records_provider_setup_failure_without_credentials(monkeypatch, tmp_path, capsys):
    def invalid_client():
        raise ValueError("private-configuration")

    monkeypatch.setattr("scripts.compare_llm_reading.UKAutismLLMClient", invalid_client)
    report_path = tmp_path / "reading-run.json"
    assert main([*ARGS, "--report-json", str(report_path)]) == 1
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["status"] == "incomplete"
    assert "Could not configure the LLM provider" in report["stopped_reason"]
    assert report["stopped_reason"] in capsys.readouterr().out
    assert report["requests"]["attempted"] == 0
    assert report["sampling"] == {"samples_per_topic": 2, "temperature": None, "top_p": None}
    assert all(row["unfinished_pairs"] == 2 and row["provider_failed_pairs"] == 0 for row in report["results"])
    assert "private-configuration" not in report_text


def test_request_budget_blocks_an_attempt_before_it_reaches_transport():
    attempts = []

    def provider(request):
        attempts.append(request)
        return _passing_answer(ANNUAL_REVIEW.id, "clear")

    budget = RequestBudget(limit=1)
    with _live_sampler(budget, transport=httpx.MockTransport(provider)) as (ask, _):
        ask("candidate-a", ANNUAL_REVIEW, "clear")
        with pytest.raises(ComparisonStopped, match="Request limit reached"):
            ask("candidate-b", ANNUAL_REVIEW, "clear")
    assert budget.used == len(attempts) == 1
