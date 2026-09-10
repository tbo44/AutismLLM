"""Tests for the /admin dashboard: auth behaviour and log parsing."""

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app.main import (
    app,
    _check_admin_token,
    _read_feedback_log,
    _read_questions_stats,
)

client = TestClient(app)

VALID_TOKEN = "test-admin-token-123"


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setattr(main, "_ADMIN_TOKEN", VALID_TOKEN)
    return VALID_TOKEN


# ── _check_admin_token ────────────────────────────────────────────────


def test_check_admin_token_missing_raises_401(admin_token):
    with pytest.raises(HTTPException) as exc:
        _check_admin_token(None, None, None)
    assert exc.value.status_code == 401


def test_check_admin_token_wrong_raises_403(admin_token):
    with pytest.raises(HTTPException) as exc:
        _check_admin_token("wrong-token", None, None)
    assert exc.value.status_code == 403


def test_check_admin_token_valid_passes(admin_token):
    # Valid token in any position should not raise
    _check_admin_token(VALID_TOKEN, None, None)
    _check_admin_token(None, VALID_TOKEN, None)
    _check_admin_token(None, None, VALID_TOKEN)


# ── /admin endpoint auth ──────────────────────────────────────────────


def test_admin_no_token_returns_401(admin_token):
    resp = client.get("/admin")
    assert resp.status_code == 401


def test_admin_wrong_token_returns_403(admin_token):
    resp = client.get("/admin?token=wrong-token")
    assert resp.status_code == 403


def test_admin_wrong_header_token_returns_403(admin_token):
    resp = client.get("/admin", headers={"X-Admin-Token": "wrong-token"})
    assert resp.status_code == 403


def test_admin_browser_no_token_redirects_to_login(admin_token):
    """Browsers (Accept: text/html) with no credentials get the login form."""
    resp = client.get(
        "/admin", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_valid_token_returns_dashboard(admin_token):
    resp = client.get(f"/admin?token={VALID_TOKEN}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Knowledge Base" in body
    assert 'id="testAlertBtn"' in body
    assert "Send test email" in body
    assert "fetch('/admin/alerts/test'" in body
    assert "Top 10 Most-Retrieved Sources" in body
    assert "Last 50 Feedback Submissions" in body
    assert 'id="cacheTotal"' in body
    assert 'id="cacheExpiring"' in body
    assert "Re-crawl expiring pages" in body
    assert "fetch('/admin/crawl/expiring'" in body


def test_admin_valid_token_via_header(admin_token):
    resp = client.get("/admin", headers={"X-Admin-Token": VALID_TOKEN})
    assert resp.status_code == 200
    assert "Knowledge Base" in resp.text


def test_admin_rendered_javascript_is_valid(admin_token, tmp_path):
    """Check the emitted JavaScript, including Python template escaping."""
    resp = client.get("/admin", headers={"X-Admin-Token": VALID_TOKEN})
    assert resp.status_code == 200
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", resp.text, re.DOTALL)
    assert scripts, "The dashboard must include its interactive controls"
    for index, source in enumerate(scripts):
        script = tmp_path / f"admin-{index}.js"
        script.write_text(source, encoding="utf-8")
        result = subprocess.run(
            ["node", "--check", str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr


# ── live re-index status history ──────────────────────────────────────


def test_seed_reindex_completion_appears_in_polled_history(tmp_path, monkeypatch):
    original_rag_system = main._rag_system
    original_startup_complete = main._startup_complete
    token = "crawl-test-token"
    monkeypatch.setenv("ADMIN_CRAWL_TOKEN", token)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()

    history_logger = logging.getLogger(f"test.reindex-history.{id(tmp_path)}")
    history_logger.setLevel(logging.INFO)
    history_logger.propagate = False
    handler = logging.FileHandler(tmp_path / "logs" / "reindex.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    history_logger.addHandler(handler)
    monkeypatch.setattr(main, "_reindex_logger", history_logger)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="indexed", stderr=""),
    )
    monkeypatch.setattr(main, "_initialize_rag_sync", lambda: SimpleNamespace())
    monkeypatch.setattr(main.notifications, "reset_throttle", lambda: None)

    main._crawl_status.update(
        {"running": False, "last_run": None, "last_result": None, "alert": None}
    )
    headers = {"Authorization": f"Bearer {token}"}

    async def run_reindex_and_poll():
        started = await main.admin_reindex(authorization=headers["Authorization"])
        assert started["status"] == "started"
        assert main._crawl_status["running"] is True

        completed_entry = None
        for _ in range(200):
            payload = await main.admin_crawl_status(
                authorization=headers["Authorization"]
            )
            completed_entry = next(
                (
                    entry
                    for entry in payload["history"]
                    if entry["source"] == "manual-reindex"
                    and entry["outcome"] == "SUCCESS"
                ),
                None,
            )
            if not payload["running"] and completed_entry is not None:
                break
            await asyncio.sleep(0.01)

        assert completed_entry is not None
        assert datetime.strptime(
            completed_entry["ts"], "%Y-%m-%d %H:%M:%S,%f"
        )

    try:
        asyncio.run(run_reindex_and_poll())
    finally:
        main._rag_system = original_rag_system
        main._startup_complete = original_startup_complete
        handler.close()
        history_logger.removeHandler(handler)


def test_dashboard_polling_renders_completed_history_without_reload(admin_token):
    response = client.get(f"/admin?token={admin_token}")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", response.text, re.DOTALL)
    runner = str(Path(__file__).parent / "admin_history_runner.cjs")
    result = subprocess.run(
        ["node", runner],
        input=json.dumps({"script": scripts[0]}),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["requests"] == [
        ["POST", "/admin/crawl"],
        ["GET", "/admin/crawl/status"],
        ["GET", "/admin/crawl/status"],
    ]
    assert "2026-09-10 08:45:00,000" in output["historyHtml"]
    assert "manual-crawl" in output["historyHtml"]
    assert 'class="ok">SUCCESS' in output["historyHtml"]
    assert output["pollingStopped"] is True


# ── log parsing helpers ───────────────────────────────────────────────


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "logs"
    d.mkdir()
    return d


def _write_lines(path, entries):
    path.write_text(
        "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in entries) + "\n",
        encoding="utf-8",
    )


def test_read_feedback_log_missing_file(logs_dir):
    assert _read_feedback_log() == []


def test_read_feedback_log_parses_entries_newest_first(logs_dir):
    entries = [
        {"ts": "2026-07-01T10:00:00", "issue_type": "wrong_info", "q_len": 10, "has_comment": True},
        {"ts": "2026-07-02T11:00:00", "issue_type": "unclear", "q_len": 20, "has_comment": False},
    ]
    _write_lines(logs_dir / "feedback.log", entries)
    result = _read_feedback_log()
    assert len(result) == 2
    assert result[0]["ts"] == "2026-07-02T11:00:00"
    assert result[1]["issue_type"] == "wrong_info"


def test_read_feedback_log_skips_malformed_and_blank_lines(logs_dir):
    _write_lines(
        logs_dir / "feedback.log",
        [
            {"ts": "2026-07-01T10:00:00", "issue_type": "other"},
            "not-json {{{",
            "",
            {"ts": "2026-07-02T10:00:00"},  # missing optional fields
        ],
    )
    result = _read_feedback_log()
    assert len(result) == 2
    assert result[0] == {"ts": "2026-07-02T10:00:00"}


def test_read_feedback_log_respects_limit(logs_dir):
    entries = [{"ts": f"2026-07-01T10:00:{i:02d}"} for i in range(10)]
    _write_lines(logs_dir / "feedback.log", entries)
    result = _read_feedback_log(limit=3)
    assert len(result) == 3
    assert result[0]["ts"] == "2026-07-01T10:00:09"


def test_read_questions_stats_missing_file(logs_dir):
    stats = _read_questions_stats()
    assert stats == {"top_sources": [], "questions_7d": 0, "total_questions": 0}


def test_read_questions_stats_counts_and_top_sources(logs_dir):
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(days=30)).isoformat()
    entries = [
        # Entry without optional "question" field
        {"ts": recent, "q_len": 12, "source_ids": ["https://a.example", "https://b.example"]},
        {"ts": recent, "question": "What is an EHCP?", "source_ids": ["https://a.example"]},
        {"ts": old, "question": "Old one", "source_ids": ["https://a.example"]},
        # Missing source_ids entirely
        {"ts": recent, "question": "No sources"},
        # Bad timestamp — still counted in total, not in 7d
        {"ts": "not-a-date", "source_ids": ["https://c.example"]},
        "malformed line",
    ]
    _write_lines(logs_dir / "questions.log", entries)
    stats = _read_questions_stats()
    assert stats["total_questions"] == 5
    assert stats["questions_7d"] == 3
    top = dict(stats["top_sources"])
    assert top["https://a.example"] == 3
    assert top["https://b.example"] == 1
    assert top["https://c.example"] == 1
    # Most-retrieved source is first
    assert stats["top_sources"][0][0] == "https://a.example"


def test_read_questions_stats_naive_timestamp_treated_as_utc(logs_dir):
    naive_recent = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    _write_lines(logs_dir / "questions.log", [{"ts": naive_recent, "source_ids": []}])
    stats = _read_questions_stats()
    assert stats["total_questions"] == 1
    assert stats["questions_7d"] == 1


def test_read_questions_stats_includes_rotated_backups(logs_dir):
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(days=8)).isoformat()
    _write_lines(
        logs_dir / "questions.log",
        [{"ts": recent, "source_ids": ["https://current.example"]}],
    )
    _write_lines(
        logs_dir / "questions.log.1",
        [
            {"ts": recent, "source_ids": ["https://backup.example"]},
            {"ts": old, "source_ids": ["https://backup.example"]},
        ],
    )
    _write_lines(
        logs_dir / "questions.log.3",
        [{"ts": recent, "source_ids": ["https://backup.example"]}],
    )

    stats = _read_questions_stats()

    assert stats["total_questions"] == 4
    assert stats["questions_7d"] == 3
    assert dict(stats["top_sources"]) == {
        "https://backup.example": 3,
        "https://current.example": 1,
    }


def test_read_questions_stats_uses_backup_when_current_log_is_missing(logs_dir):
    recent = datetime.now(timezone.utc).isoformat()
    _write_lines(
        logs_dir / "questions.log.1",
        [{"ts": recent, "source_ids": ["https://backup.example"]}],
    )

    stats = _read_questions_stats()

    assert stats["total_questions"] == 1
    assert stats["questions_7d"] == 1
    assert stats["top_sources"] == [("https://backup.example", 1)]


# ── /admin/login  (GET) ───────────────────────────────────────────────


def test_login_get_shows_form(admin_token):
    """GET /admin/login with no cookie returns the sign-in form."""
    resp = client.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Sign in" in body
    assert "Admin password" in body


def test_login_get_already_signed_in_redirects_to_admin(admin_token):
    """GET /admin/login with a valid session cookie goes straight to /admin."""
    resp = client.get(
        "/admin/login",
        cookies={main._ADMIN_COOKIE_NAME: VALID_TOKEN},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"


# ── /admin/login  (POST) ──────────────────────────────────────────────


@pytest.fixture(autouse=False)
def clear_login_attempts():
    """Reset the in-memory login-attempts store before and after each test."""
    main._login_attempts.clear()
    yield
    main._login_attempts.clear()


def test_login_post_correct_token_redirects_and_sets_cookie(admin_token, clear_login_attempts):
    """Submitting the correct token redirects to /admin and sets the session cookie."""
    resp = client.post(
        "/admin/login",
        data={"token": VALID_TOKEN},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    # The session cookie must be present in the response
    set_cookie = resp.headers.get("set-cookie", "")
    assert main._ADMIN_COOKIE_NAME in set_cookie


def test_login_post_wrong_token_rerenders_form_with_error(admin_token, clear_login_attempts):
    """Submitting a wrong token returns 401 and shows the form with an error message."""
    resp = client.post(
        "/admin/login",
        data={"token": "definitely-wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "text/html" in resp.headers["content-type"]
    assert "Incorrect password" in resp.text


def test_login_post_empty_token_rerenders_form(admin_token, clear_login_attempts):
    """Submitting an empty token also returns 401 with the sign-in form."""
    resp = client.post(
        "/admin/login",
        data={"token": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "Sign in" in resp.text


def test_login_lockout_triggers_429_after_max_attempts(admin_token, monkeypatch, clear_login_attempts):
    """After LOGIN_MAX_ATTEMPTS wrong passwords the next attempt returns 429."""
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 3)

    # Submit exactly _LOGIN_MAX_ATTEMPTS wrong passwords to exhaust the allowance.
    for _ in range(3):
        resp = client.post(
            "/admin/login",
            data={"token": "definitely-wrong"},
            follow_redirects=False,
        )
        # Each of these fails but is not yet blocked (or triggers the lockout on
        # the last one, which still returns 401 with the lockout message).
        assert resp.status_code in (401, 429)

    # The NEXT attempt must be rate-limited regardless of password.
    resp = client.post(
        "/admin/login",
        data={"token": "definitely-wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text


def test_login_lockout_blocks_correct_password(admin_token, monkeypatch, clear_login_attempts):
    """A correct password submitted while locked out still returns 429, not 303."""
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 3)

    # Exhaust attempts so the IP is locked.
    for _ in range(3):
        client.post(
            "/admin/login",
            data={"token": "wrong"},
            follow_redirects=False,
        )

    # Submit the correct password — must still be blocked.
    resp = client.post(
        "/admin/login",
        data={"token": VALID_TOKEN},
        follow_redirects=False,
    )
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text


def test_login_failed_attempt_count_resets_after_window_expires(
    admin_token, monkeypatch, clear_login_attempts
):
    """A failure from an expired window starts a new count instead of locking out."""
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main, "_is_locked_out", lambda ip: (False, 0))
    main._login_attempts["testclient"] = {
        "count": main._LOGIN_MAX_ATTEMPTS - 1,
        "window_start": (
            main._time.monotonic() - main._LOGIN_LOCKOUT_SECONDS - 1
        ),
        "locked_until": 0,
    }

    resp = client.post(
        "/admin/login",
        data={"token": "wrong"},
        follow_redirects=False,
    )

    assert resp.status_code == 401
    assert main._login_attempts["testclient"]["count"] == 1


def test_login_success_clears_failed_attempts(admin_token, monkeypatch, clear_login_attempts):
    """A successful login below the limit removes the client's failure record."""
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 3)

    for _ in range(main._LOGIN_MAX_ATTEMPTS - 1):
        resp = client.post(
            "/admin/login",
            data={"token": "wrong"},
            follow_redirects=False,
        )
        assert resp.status_code == 401

    assert len(main._login_attempts) == 1

    resp = client.post(
        "/admin/login",
        data={"token": VALID_TOKEN},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    assert main._login_attempts == {}


def test_login_remaining_attempts_warning_shown(admin_token, monkeypatch, clear_login_attempts):
    """When within 2 attempts of the limit, the form shows how many attempts remain."""
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 5)

    # Three wrong attempts → 2 remaining → warning should appear.
    for _ in range(3):
        resp = client.post(
            "/admin/login",
            data={"token": "wrong"},
            follow_redirects=False,
        )

    assert resp.status_code == 401
    assert "attempt" in resp.text.lower()
    assert "remaining" in resp.text.lower()


# ── /admin/logout ─────────────────────────────────────────────────────


def test_logout_clears_cookie_and_redirects(admin_token):
    """GET /admin/logout deletes the session cookie and redirects to /admin/login."""
    resp = client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"
    # The cookie should be cleared (max-age=0 or expires in the past)
    set_cookie = resp.headers.get("set-cookie", "")
    assert main._ADMIN_COOKIE_NAME in set_cookie
    # FastAPI's delete_cookie sets max-age=0
    assert "max-age=0" in set_cookie.lower()


def test_logout_works_without_existing_cookie(admin_token):
    """Logout with no cookie still redirects cleanly (no crash)."""
    resp = client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


# ── Cookie-based /admin access ────────────────────────────────────────


def test_admin_valid_cookie_returns_dashboard(admin_token):
    """A valid session cookie grants access to /admin without a query param."""
    resp = client.get(
        "/admin",
        cookies={main._ADMIN_COOKIE_NAME: VALID_TOKEN},
    )
    assert resp.status_code == 200
    assert "Knowledge Base" in resp.text
