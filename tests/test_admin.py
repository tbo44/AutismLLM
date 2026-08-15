"""Tests for the /admin dashboard: auth behaviour and log parsing."""

import json
from datetime import datetime, timedelta, timezone

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
    assert "Top 10 Most-Retrieved Sources" in body
    assert "Last 50 Feedback Submissions" in body


def test_admin_valid_token_via_header(admin_token):
    resp = client.get("/admin", headers={"X-Admin-Token": VALID_TOKEN})
    assert resp.status_code == 200
    assert "Knowledge Base" in resp.text


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
