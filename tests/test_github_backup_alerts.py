"""All email/network operations are mocked; receipts and claims are isolated."""
import asyncio
import json
import multiprocessing
from datetime import datetime, timedelta, timezone

import pytest

from app import github_backup_alerts as alerts, github_backup_status as status, notifications
from scripts import sync_to_github as sync

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    calls = []
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "staff@example.invalid")
    monkeypatch.setattr(notifications, "_send_smtp", lambda *args: calls.append(args))
    monkeypatch.setattr(notifications, "_send_replit_mail", lambda *args: calls.append(args))
    monkeypatch.setattr(notifications, "_THROTTLE_FILE", tmp_path / "reindex-throttle")
    return path, calls


def success(path, now=NOW):
    status.write_result(path, workspace_commit="a" * 40, remote_tip="b" * 40, now=now)


def test_failure_reminder_category_changes_restart_and_recovery(delivery):
    path, calls = delivery
    status.write_result(path, failure="sync", now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"
    status.write_result(path, failure="conflict", now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=23)) == "throttled"
    # No process-local state: a subsequent check acts like a restarted sender.
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24)) == "sent"
    success(path, NOW + timedelta(hours=24, minutes=1))
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24, minutes=1)) == "sent"
    assert "RECOVERED" in calls[-1][1]
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24, minutes=2)) == "healthy"
    status.write_result(path, failure="verification", now=NOW + timedelta(hours=24, minutes=3))
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24, minutes=3)) == "sent"
    assert len(calls) == 4
    assert not notifications._THROTTLE_FILE.exists()


@pytest.mark.parametrize("health", [*status.FAILURES, "missing", "unavailable", "stale"])
def test_safe_allowlisted_problem_messages(delivery, health):
    path, calls = delivery
    if health in status.FAILURES:
        status.write_result(path, failure=health, now=NOW)
        raw = json.loads(path.read_text())
        raw["command_output"] = "secret-command token=private-secret"
        raw["last_failure"]["message"] = "SMTP_PASSWORD=private-secret"
        path.write_text(json.dumps(raw))
    elif health == "unavailable":
        path.write_text("private-secret")
    elif health == "stale":
        success(path, NOW - timedelta(seconds=status.STALE_SECONDS + 1))
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"
    assert len(calls) == 1
    message = str(calls) + path.with_name(path.name + ".alerts.json").read_text()
    assert "private-secret" not in message
    assert "secret-command" not in message
    assert "SMTP_PASSWORD" not in message


def test_stale_clock_skew_missing_grace_and_corrupt_receipts(delivery):
    path, calls = delivery
    assert alerts.check_backup_alerts(path, now=NOW, missing_ready=False) == "grace"
    success(path)
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(seconds=900)) == "healthy"
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(seconds=901)) == "sent"
    success(path, NOW + timedelta(minutes=16))
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(minutes=16)) == "sent"
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"  # future success is not healthy
    path.unlink()
    assert alerts.check_backup_alerts(path, now=NOW, missing_ready=False) == "throttled"
    assert len(calls) == 3


def test_not_configured_is_noop_then_can_be_enabled(delivery, monkeypatch):
    path, calls = delivery
    monkeypatch.delenv("REINDEX_ALERT_EMAIL_TO")
    status.write_result(path, failure="sync", now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW) == "disabled"
    assert list(path.parent.iterdir()) == [path]
    monkeypatch.setenv("REINDEX_ALERT_EMAIL_TO", "replit")
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"
    assert len(calls) == 1 and len(calls[0]) == 2


def test_delivery_failure_and_recovery_are_reserved_before_send(delivery, monkeypatch, caplog):
    path, calls = delivery
    status.write_result(path, failure="sync", now=NOW)
    attempts = []

    def fail(*args):
        state = alerts._read_state(path.with_name(path.name + ".alerts.json"))
        attempts.append(state)
        raise RuntimeError("token=private-secret ENV=private-secret")

    monkeypatch.setattr(notifications, "_send_smtp", fail)
    assert alerts.check_backup_alerts(path, now=NOW) == "delivery_uncertain"
    assert attempts[0]["incident"] is True
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(minutes=1)) == "throttled"
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24)) == "delivery_uncertain"
    success(path, NOW + timedelta(hours=24))
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24)) == "delivery_uncertain"
    assert attempts[-1]["incident"] is False
    assert alerts.check_backup_alerts(path, now=NOW + timedelta(hours=24)) == "healthy"
    assert "private-secret" not in caplog.text


@pytest.mark.parametrize("failure", ["corrupt", "write"])
def test_claim_storage_fails_closed_without_sending(delivery, monkeypatch, caplog, failure):
    path, calls = delivery
    status.write_result(path, failure="sync", now=NOW)
    if failure == "corrupt":
        path.with_name(path.name + ".alerts.json").write_text("private-secret")
    else:
        def fail(*args):
            raise OSError("private-secret")
        monkeypatch.setattr(alerts, "atomic_write_json", fail)
    assert alerts.check_backup_alerts(path, now=NOW) == "unavailable"
    assert not calls
    assert "private-secret" not in caplog.text


def test_process_crash_after_reservation_does_not_resend(delivery, monkeypatch):
    path, calls = delivery
    status.write_result(path, failure="sync", now=NOW)
    def crash(*args):
        raise SystemExit()
    monkeypatch.setattr(notifications, "send_github_backup_alert", crash)
    with pytest.raises(SystemExit):
        alerts.check_backup_alerts(path, now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"
    summary = status.notification_summary(path, NOW)
    assert summary["last_notification"]["outcome"] == "reserved"
    assert summary["throttled_until"] == (NOW + timedelta(hours=24)).isoformat()


@pytest.mark.parametrize("result", [True, False, "exception"])
def test_safe_outcome_persists_without_changing_claim(delivery, monkeypatch, result):
    path, _ = delivery
    status.write_result(path, failure="sync", now=NOW)
    def send(*args):
        reserved = status.notification_summary(path, NOW)["last_notification"]
        assert reserved == {"at": NOW.isoformat(), "kind": "problem", "outcome": "reserved"}
        if result == "exception":
            raise RuntimeError("private-secret")
        return result
    monkeypatch.setattr(notifications, "send_github_backup_alert", send)
    expected = "sent" if result is True else "delivery_uncertain"
    assert alerts.check_backup_alerts(path, now=NOW) == expected
    state_path = path.with_name(path.name + ".alerts.json")
    state = alerts._read_state(state_path)
    assert state["last_attempt"] == NOW.isoformat() and state["incident"] is True
    assert state["last_notification"]["outcome"] == expected
    assert "private-secret" not in state_path.read_text()
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"
    assert alerts._read_state(state_path) == state


def test_outcome_write_failure_retains_unknown_reservation(delivery, monkeypatch):
    path, calls = delivery
    status.write_result(path, failure="sync", now=NOW)
    write = alerts.atomic_write_json
    def fail_result(path, record):
        if record["last_notification"]["outcome"] != "reserved":
            raise OSError("private-secret")
        write(path, record)
    monkeypatch.setattr(alerts, "atomic_write_json", fail_result)
    assert alerts.check_backup_alerts(path, now=NOW) == "unavailable"
    assert len(calls) == 1
    summary = status.notification_summary(path, NOW)
    assert summary["last_notification"]["outcome"] == "reserved"
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"


def test_uncertain_recovery_history_survives_healthy_checks(delivery, monkeypatch):
    path, _ = delivery
    status.write_result(path, failure="sync", now=NOW)
    assert alerts.check_backup_alerts(path, now=NOW) == "sent"
    success(path, NOW + timedelta(minutes=1))
    monkeypatch.setattr(notifications, "send_github_backup_alert", lambda *args: False)
    when = NOW + timedelta(minutes=1)
    assert alerts.check_backup_alerts(path, now=when) == "delivery_uncertain"
    assert alerts.check_backup_alerts(path, now=when) == "healthy"
    summary = status.notification_summary(path, when)
    assert summary["last_notification"] == {
        "at": when.isoformat(), "kind": "recovery", "outcome": "delivery_uncertain",
    }
    assert summary["throttled_until"] is None


def test_concurrent_processes_send_once(delivery, monkeypatch, tmp_path):
    path, _ = delivery
    status.write_result(path, failure="sync", now=NOW)
    sent = tmp_path / "sent"
    def transport(*args):
        with sent.open("a") as handle:
            handle.write("email\n")
    monkeypatch.setattr(notifications, "_send_smtp", transport)
    context = multiprocessing.get_context("fork")
    processes = [context.Process(target=alerts.check_backup_alerts, args=(path,), kwargs={"now": NOW})
                 for _ in range(5)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sent.read_text() == "email\n"
    assert alerts.check_backup_alerts(path, now=NOW) == "throttled"


def test_sync_failure_hook_uses_safe_category_and_never_renews(delivery, monkeypatch):
    path, calls = delivery
    monkeypatch.setattr(sync, "STATUS_PATH", path)
    def fail():
        raise SystemExit("private-secret")
    monkeypatch.setattr(sync, "_get_github_token", fail)
    with pytest.raises(SystemExit):
        sync.sync_once()
    assert status.read_record(path)["last_failure"]["category"] == "authorization"
    assert len(calls) == 1 and "private-secret" not in str(calls)
    # The server inspecting the same receipt cannot duplicate the sync alert.
    assert alerts.check_backup_alerts(path) == "throttled"


def test_sync_success_hook_sends_recovery_only_after_verified_receipt(delivery, monkeypatch):
    path, calls = delivery
    monkeypatch.setattr(sync, "STATUS_PATH", path)
    status.write_result(path, failure="sync", now=NOW)
    alerts.check_backup_alerts(path, now=NOW)
    monkeypatch.setattr(sync, "_get_github_token", lambda: "synthetic-token")
    def run(cmd, **kwargs):
        if "rev-parse" in cmd:
            return "a" * 40
        if "ls-remote" in cmd and "--heads" not in cmd:
            return "a" * 40 + "\trefs/heads/main"
        return ""
    monkeypatch.setattr(sync, "_run", run)
    assert sync.sync_once() == "a" * 40
    assert "RECOVERED" in calls[-1][1]
    assert len(calls) == 2


def test_server_watchdog_checks_without_dashboard_or_sync(delivery, monkeypatch):
    path, calls = delivery
    success(path, datetime.now(timezone.utc) - timedelta(minutes=16))
    monkeypatch.setattr(alerts, "STATUS_PATH", path)
    async def run():
        task = asyncio.create_task(alerts.monitor_backup_alerts())
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert len(calls) == 1
    assert "15-minute limit" in calls[0][2]


def test_watchdog_missing_receipt_grace_expires(delivery, monkeypatch):
    path, _ = delivery
    observations = []
    monkeypatch.setattr(alerts, "STATUS_PATH", path)
    real_check = alerts.check_backup_alerts
    def check(**kwargs):
        observations.append(real_check(**kwargs))
    monkeypatch.setattr(alerts, "check_backup_alerts", check)
    async def run():
        loop = asyncio.get_running_loop()
        # Only the watchdog's clock is faked, not asyncio's internal timer.
        class Clock:
            ticks = iter([0, 0, 900])
            def time(self):
                return next(self.ticks)
        monkeypatch.setattr(alerts.asyncio, "get_running_loop", lambda: ClockInstance)
        ClockInstance = Clock()
        async def sleep(seconds):
            assert seconds == 60
            if len(observations) == 2:
                raise asyncio.CancelledError
        monkeypatch.setattr(alerts.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await alerts.monitor_backup_alerts()
    asyncio.run(run())
    assert observations == ["grace", "sent"]
