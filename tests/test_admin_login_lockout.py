"""Tests for admin login rate-limiting / lockout."""

import time

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app, _get_client_ip

VALID_TOKEN = "test-lockout-token-xyz"

client = TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    """Patch token + limits to known values and clear in-memory attempt store."""
    monkeypatch.setattr(main, "_ADMIN_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main, "_LOGIN_LOCKOUT_SECONDS", 900)
    monkeypatch.setattr(main, "_LOGIN_LOCKOUTS_PATH", tmp_path / "login_lockouts.json")
    # Clear any leftover attempt records from previous tests
    main._login_attempts.clear()
    yield
    main._login_attempts.clear()


def _post_wrong():
    return client.post(
        "/admin/login",
        data={"token": "wrong"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


def _post_correct():
    return client.post(
        "/admin/login",
        data={"token": VALID_TOKEN},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


# ── Normal failure messages ────────────────────────────────────────────

def test_first_failure_shows_generic_message(monkeypatch):
    # Use a higher limit so the first attempt is well clear of the countdown zone
    monkeypatch.setattr(main, "_LOGIN_MAX_ATTEMPTS", 10)
    resp = _post_wrong()
    assert resp.status_code == 401
    assert "Incorrect password" in resp.text
    # 9 remaining — well above the ≤2 threshold — so no countdown shown
    assert "remaining" not in resp.text


def test_near_threshold_shows_remaining_count():
    """With max=3, the 2nd attempt leaves 1 remaining — show warning."""
    _post_wrong()            # attempt 1 of 3 → 2 remaining (countdown starts)
    resp = _post_wrong()     # attempt 2 of 3 → 1 remaining
    assert resp.status_code == 401
    assert "1 attempt remaining" in resp.text


# ── Lockout triggers ───────────────────────────────────────────────────

def test_lockout_triggers_on_threshold():
    """3rd wrong attempt should trigger the lockout (same request = 401)."""
    _post_wrong()
    _post_wrong()
    resp = _post_wrong()   # 3rd attempt hits the limit
    assert resp.status_code == 401
    assert "Too many failed attempts" in resp.text
    assert "minute" in resp.text


def test_subsequent_attempts_return_429():
    """After lockout is set, further attempts get 429."""
    _post_wrong()
    _post_wrong()
    _post_wrong()           # triggers lockout
    resp = _post_wrong()    # now locked
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text


# ── Successful login resets the counter ───────────────────────────────

def test_successful_login_clears_failure_counter():
    """A correct password after some failures resets the record."""
    _post_wrong()
    _post_wrong()
    resp = _post_correct()
    # Successful login should redirect (303)
    assert resp.status_code == 303
    # Counter should now be gone — a fresh wrong attempt is a plain 401, not 429
    resp2 = _post_wrong()
    assert resp2.status_code == 401
    assert "Too many failed attempts" not in resp2.text


# ── Trusted-proxy IP resolution ────────────────────────────────────────

def test_spoofed_xff_from_untrusted_peer_is_ignored(monkeypatch):
    """When TRUSTED_PROXY_IPS is empty (default), XFF is never trusted."""
    monkeypatch.setattr(main, "_TRUSTED_PROXY_IPS", set())  # no trusted proxies

    # Fire enough attempts to lock out under the spoofed IP
    for _ in range(3):
        client.post(
            "/admin/login",
            data={"token": "wrong"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": "1.2.3.4",  # attacker-supplied, different each time
            },
            follow_redirects=False,
        )

    # The attacker now claims a *different* IP via XFF
    resp = client.post(
        "/admin/login",
        data={"token": "wrong"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "9.9.9.9",
        },
        follow_redirects=False,
    )
    # Should still be locked out (peer IP was used, not XFF) → 429
    assert resp.status_code == 429


def test_trusted_proxy_uses_xff_client_ip(monkeypatch):
    """When the immediate peer IS a trusted proxy, we use XFF correctly."""
    # TestClient peer is "testclient"; mark it as trusted
    monkeypatch.setattr(main, "_TRUSTED_PROXY_IPS", {"testclient"})

    # Lock out "attacker-ip" via XFF
    for _ in range(3):
        client.post(
            "/admin/login",
            data={"token": "wrong"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": "attacker-ip",
            },
            follow_redirects=False,
        )

    resp = client.post(
        "/admin/login",
        data={"token": "wrong"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "attacker-ip",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 429

    # A *different* XFF IP is unaffected
    resp2 = client.post(
        "/admin/login",
        data={"token": "wrong"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "other-ip",
        },
        follow_redirects=False,
    )
    assert resp2.status_code == 401  # not locked out
    assert "Too many failed attempts" not in resp2.text


# ── Lockout expiry: correct password succeeds once window passes ───────

def test_pruner_removes_only_fully_expired_entries(monkeypatch):
    """Expired records are pruned without dropping active tracking or lockouts."""
    now = 10_000.0
    monkeypatch.setattr(main._time, "monotonic", lambda: now)

    expired_ip = "192.0.2.1"
    active_window_ip = "192.0.2.2"
    active_lockout_ip = "192.0.2.3"
    main._login_attempts.update({
        expired_ip: {
            "count": 2,
            "window_start": now - main._LOGIN_LOCKOUT_SECONDS - 1,
            "locked_until": now - 1,
        },
        active_window_ip: {
            "count": 1,
            "window_start": now - main._LOGIN_LOCKOUT_SECONDS + 1,
            "locked_until": 0,
        },
        active_lockout_ip: {
            "count": 3,
            "window_start": now - main._LOGIN_LOCKOUT_SECONDS - 1,
            "locked_until": now + 1,
        },
    })

    pruned = main._prune_login_attempts()

    assert pruned == 1
    assert set(main._login_attempts) == {active_window_ip, active_lockout_ip}


def test_correct_password_after_lockout_expires_succeeds(monkeypatch):
    """After the lockout window elapses, a correct password must be accepted (303)
    and the attempt record must be cleared so the next wrong attempt is a plain 401."""
    # Lock the IP out
    _post_wrong()
    _post_wrong()
    _post_wrong()  # triggers lockout → 401

    # Confirm locked
    assert _post_wrong().status_code == 429

    # Wind time forward past the lockout window by manipulating the stored entry
    # directly (same effect as the lockout window expiring on the real clock).
    for entry in main._login_attempts.values():
        entry["locked_until"] = 0            # lockout expired
        entry["window_start"] = 0            # tracking window also expired

    # Correct password should now succeed
    resp = _post_correct()
    assert resp.status_code == 303, (
        f"Expected 303 redirect after lockout expiry, got {resp.status_code}"
    )

    # Attempt record should be cleared — next wrong attempt is a fresh 401
    resp2 = _post_wrong()
    assert resp2.status_code == 401
    assert "Too many failed attempts" not in resp2.text


# ── Server-restart behaviour ───────────────────────────────────────────

def test_lockout_survives_simulated_server_restart():
    """An active lockout is reconstructed from disk after process state is lost."""
    # Lock the IP out
    _post_wrong()
    _post_wrong()
    _post_wrong()  # triggers lockout
    assert _post_wrong().status_code == 429, "IP should be locked out before restart"

    # Simulate server restart: process memory is wiped, then startup reloads disk.
    main._login_attempts.clear()
    loaded = main._load_login_lockouts()

    assert loaded == 1
    resp = _post_wrong()
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text


def test_expired_lockouts_are_not_loaded(monkeypatch):
    """Startup ignores persisted lockouts whose wall-clock expiry has passed."""
    main._LOGIN_LOCKOUTS_PATH.write_text(
        '{"expired-ip": 999.0}',
        encoding="utf-8",
    )
    monkeypatch.setattr(main._time, "time", lambda: 1_000.0)

    loaded = main._load_login_lockouts()

    assert loaded == 0
    assert "expired-ip" not in main._login_attempts
    assert main._LOGIN_LOCKOUTS_PATH.read_text(encoding="utf-8") == "{}"
