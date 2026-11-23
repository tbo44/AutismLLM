"""Run an explicitly opted-in, bounded reading-level comparison on one provider."""

import argparse
import json
import math
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path

import httpx
import openai

from rag.llm_client import UKAutismLLMClient
from tests.llm_answer_essentials import READING_CASES
from tests.llm_reading_checks import READING_RUBRIC_ID, check_answer_structure, check_reading_pair


REPORT_SCHEMA_VERSION = 1


class ComparisonStopped(Exception):
    """The runner cannot safely continue; do not count this as a provider result."""


class ProviderFailure(Exception):
    def __init__(self, reason, *, stop=False):
        super().__init__(reason)
        self.stop = stop


@dataclass
class RequestBudget:
    limit: int
    used: int = 0
    exhausted: bool = False

    def reserve(self, _request):
        # Count every outgoing HTTP attempt, not just successful completions.
        if self.used >= self.limit:
            self.exhausted = True
            raise ComparisonStopped("Request limit reached before the comparison finished.")
        self.used += 1


def _provider_failure(error):
    # Never report SDK exception text, which can contain response bodies or URLs.
    if isinstance(error, openai.APITimeoutError):
        return ProviderFailure("provider request timed out")
    if isinstance(error, openai.APIConnectionError):
        return ProviderFailure("provider unreachable")
    if isinstance(error, openai.APIStatusError):
        status = error.status_code
        if status == 429:
            return ProviderFailure("provider rate or quota limit (HTTP 429)", stop=True)
        if status in {401, 403}:
            return ProviderFailure(f"provider access denied (HTTP {status})")
        if error.code in ("model_not_found", "model_decommissioned", "model_not_available"):
            return ProviderFailure(f"model unavailable (HTTP {status})")
        if status == 404:
            return ProviderFailure("model or endpoint not found (HTTP 404)")
        if status >= 500:
            return ProviderFailure(f"provider service error (HTTP {status})")
        return ProviderFailure(f"provider request rejected (HTTP {status})")
    return ProviderFailure("provider response could not be read")


@contextmanager
def _live_sampler(budget, *, transport=None):
    try:
        llm = UKAutismLLMClient()
    except (ValueError, openai.OpenAIError, httpx.InvalidURL):
        raise ComparisonStopped("Could not configure the LLM provider; check the endpoint and credentials.") from None

    # The SDK normally retries some errors and follows redirects. Neither is
    # appropriate here: each level should take at most one HTTP attempt.
    with llm.client as original, httpx.Client(
        transport=transport, follow_redirects=False, event_hooks={"request": [budget.reserve]},
    ) as http:
        with original.with_options(http_client=http, max_retries=0, timeout=30.0) as provider:
            llm.client = provider
            create = provider.chat.completions.create
            issue = None

            def checked_create(**kwargs):
                # Inspect the outcome before production synthesis can turn it into a fallback.
                nonlocal issue
                try:
                    response = create(**kwargs)
                except openai.APIError as error:
                    issue = _provider_failure(error)
                except JSONDecodeError:
                    issue = ProviderFailure("provider returned an invalid completion")
                else:
                    choices = getattr(response, "choices", None)
                    message = getattr(choices[0], "message", None) if isinstance(choices, list) and choices else None
                    content = getattr(message, "content", ...)
                    if content is None or isinstance(content, str):
                        return response
                    issue = ProviderFailure("provider returned an invalid completion")
                # Synthesis logs exceptions; pass through only the safe label.
                raise RuntimeError(str(issue))

            provider.chat.completions.create = checked_create

            def ask(model, case, level):
                nonlocal issue
                issue = None
                llm.model = model
                result = llm.synthesize_response(case.question, [case.hit], comprehension_level=level)
                if budget.exhausted:
                    raise ComparisonStopped("Request limit reached before the comparison finished.")
                if issue is not None:
                    raise issue
                if not result["success"]:
                    raise ComparisonStopped("Maya could not generate a test answer from the fixed source.")
                return result["response"]

            yield ask, {"temperature": llm.temperature, "top_p": llm.top_p}


@dataclass
class TopicResults:
    passed: int = 0
    quality_failed: int = 0
    provider_failures: Counter = field(default_factory=Counter)

    @property
    def scored(self):
        return self.passed + self.quality_failed

    @property
    def provider_failed(self):
        return sum(self.provider_failures.values())

    def unfinished(self, samples):
        return samples - self.scored - self.provider_failed


def _collect(models, samples, ask, results):
    # Interleave models for each topic rather than running all of one model first.
    for sample in range(1, samples + 1):
        for case in READING_CASES:
            for model in models:
                row = results[model, case.id]
                try:
                    clear = ask(model, case, "clear")
                    complex_answer = ask(model, case, "complex")
                except ProviderFailure as error:
                    row.provider_failures[str(error)] += 1
                    if error.stop:
                        return "Stopped after a provider rate or quota limit; no further requests were sent."
                    continue

                # Score only complete pairs, even when the first answer would
                # have failed a check on its own.
                try:
                    check_answer_structure(clear, "clear")
                    check_answer_structure(complex_answer, "complex")
                    check_reading_pair(case, clear=clear, complex_answer=complex_answer)
                except AssertionError as error:
                    row.quality_failed += 1
                    print(f"Quality check failed: {model!r}, {case.id}, pair {sample}: {error}")
                else:
                    row.passed += 1
    return None


def _summarize(results, samples, budget, planned):
    print(f"\nProvider requests attempted: {budget.used}/{budget.limit} (up to {planned} planned).")
    print("Reading-level pass rates by model and topic (complete pairs only):")
    for (model, case_id), row in results.items():
        rate = f"{row.passed / row.scored:.0%}" if row.scored else "no pairs scored"
        print(
            f"  {model!r} | {case_id}: {row.passed}/{row.scored} passed ({rate}); "
            f"{row.quality_failed} quality failures; {row.provider_failed} provider failures; "
            f"{row.unfinished(samples)} not completed"
        )
        for reason, count in row.provider_failures.items():
            print(f"    {count} × {reason}")


def _write_report(file, models, results, samples, settings, budget, planned, stopped, incomplete):
    # Project only aggregate counts and known-safe labels; never serialize the
    # provider client, response bodies, answers, or quality assertion messages.
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "rubric_id": READING_RUBRIC_ID,
        "candidate_ids": models,
        "sampling": {
            "samples_per_topic": samples,
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
        },
        "requests": {"attempted": budget.used, "limit": budget.limit, "planned": planned},
        "status": "incomplete" if incomplete else "complete",
        "stopped_reason": stopped,
        "results": [
            {
                "candidate_id": model,
                "topic_id": case_id,
                "scored_pairs": row.scored,
                "passed_pairs": row.passed,
                "quality_failed_pairs": row.quality_failed,
                "provider_failed_pairs": row.provider_failed,
                "unfinished_pairs": row.unfinished(samples),
                "pass_rate": row.passed / row.scored if row.scored else None,
                "provider_failure_counts": dict(row.provider_failures),
            }
            for (model, case_id), row in results.items()
        ],
    }
    file.write(json.dumps(report, indent=2, allow_nan=False) + "\n")


def _compare(models, samples, request_limit, planned, *, transport, report=None):
    budget = RequestBudget(request_limit)
    results = {(model, case.id): TopicResults() for model in models for case in READING_CASES}
    settings = {"temperature": None, "top_p": None}
    try:
        with _live_sampler(budget, transport=transport) as (ask, actual_settings):
            # Invalid non-finite settings cannot be represented as JSON numbers.
            settings = {key: value if math.isfinite(value) else None for key, value in actual_settings.items()}
            stopped = _collect(models, samples, ask, results)
    except ComparisonStopped as error:
        stopped = str(error)
    if stopped:
        print(stopped)
    _summarize(results, samples, budget, planned)
    # Quality misses are measurements, not a pass/fail gate. Interrupted or
    # provider-failed comparisons are marked incomplete even if some pairs passed.
    incomplete = bool(stopped or any(row.provider_failed for row in results.values()))
    if report is not None:
        _write_report(report, models, results, samples, settings, budget, planned, stopped, incomplete)
    return int(incomplete)


def main(argv=None, *, transport=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--live", action="store_true", help="Opt in to real provider requests.")
    parser.add_argument("--model", action="append", default=[], help="Candidate model ID; repeat for each model.")
    parser.add_argument("--samples-per-topic", type=int, default=3, help="Pairs per model and topic (default: 3).")
    parser.add_argument("--request-limit", type=int, required=True, help="Maximum total provider HTTP attempts.")
    parser.add_argument("--report-json", type=Path, metavar="PATH", help="Save a JSON report to a new file.")
    args = parser.parse_args(argv)

    if not args.live:
        parser.error("--live is required to authorize real provider requests.")
    models = [model.strip() for model in args.model]
    if len(models) < 2 or not all(models) or len(models) != len(set(models)):
        parser.error("specify at least two distinct candidate IDs with --model.")
    if args.samples_per_topic < 2:
        parser.error("--samples-per-topic must be at least 2.")
    if args.request_limit < 1:
        parser.error("--request-limit must be positive.")
    planned = len(models) * len(READING_CASES) * args.samples_per_topic * 2
    if planned > args.request_limit:
        parser.error(
            f"the comparison needs up to {planned} requests, above the limit of {args.request_limit}; "
            "reduce the samples or models, or explicitly raise the limit."
        )

    comparison_args = models, args.samples_per_topic, args.request_limit, planned
    if args.report_json is None:
        return _compare(*comparison_args, transport=transport)

    # Reserve the destination before spending provider quota, without replacing
    # an earlier report. Invalid CLI arguments never create a file.
    try:
        report = args.report_json.open("x", encoding="utf-8")
    except OSError as error:
        parser.error(f"cannot create JSON report: {error}")
    try:
        with report:
            return _compare(*comparison_args, transport=transport, report=report)
    except OSError as error:
        print(f"Could not save JSON report: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
