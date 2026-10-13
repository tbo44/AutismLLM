"""Exercise sync against local Git repositories, without network or credentials."""
import subprocess

import pytest

from scripts import sync_to_github as sync
from app.github_backup_status import read_record


@pytest.fixture(autouse=True)
def isolated_status(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "STATUS_PATH", tmp_path / "backup-status.json")


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(workspace, "config", "user.name", "Sync test")
    git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / "example.txt").write_text("committed\n")
    git(workspace, "add", "example.txt")
    git(workspace, "commit", "-m", "Initial")
    monkeypatch.setattr(sync, "WORKSPACE", str(workspace))
    monkeypatch.setattr(sync, "GITHUB_URL", str(remote))
    monkeypatch.setattr(sync, "_get_github_token", lambda: "test-only-credential")
    return workspace, remote


def test_sync_pushes_commits_not_uncommitted_changes(repositories):
    workspace, remote = repositories
    (workspace / "example.txt").write_text("unfinished edit\n")
    expected = git(workspace, "rev-parse", "main")
    assert sync.sync_once() == expected
    assert git(remote, "rev-parse", "main") == expected
    assert git(remote, "show", "main:example.txt") == "committed"
    assert (workspace / "example.txt").read_text() == "unfinished edit\n"
    # An unchanged run is safe and verifies the same commit.
    assert sync.sync_once() == expected


def advance_remote(remote, tmp_path):
    """Make a real remote-only commit in an independent checkout."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-b", "main", str(remote), str(other)],
                   check=True, capture_output=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.invalid")
    (other / "remote.txt").write_text("GitHub-only work\n")
    git(other, "add", ".")
    git(other, "commit", "-m", "Remote work")
    git(other, "push", "origin", "main")
    return other


def test_sync_preserves_both_divergent_histories(repositories, tmp_path):
    workspace, remote = repositories
    sync.sync_once()
    advance_remote(remote, tmp_path)
    remote_head = git(remote, "rev-parse", "main")
    (workspace / "local.txt").write_text("Local work\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Local work")
    local_head = git(workspace, "rev-parse", "main")
    (workspace / "example.txt").write_text("unfinished edit\n")
    assert sync.sync_once() == local_head
    combined = git(remote, "rev-parse", "main")
    receipt = read_record(sync.STATUS_PATH)
    assert receipt["last_success"]["workspace_commit"] == local_head
    assert receipt["last_success"]["remote_tip"] == combined
    git(remote, "merge-base", "--is-ancestor", local_head, combined)
    git(remote, "merge-base", "--is-ancestor", remote_head, combined)
    assert git(remote, "show", "main:remote.txt") == "GitHub-only work"
    assert git(remote, "show", "main:local.txt") == "Local work"
    assert git(workspace, "rev-parse", "main") == local_head
    assert (workspace / "example.txt").read_text() == "unfinished edit\n"
    # Subsequent runs must not create redundant merge commits.
    assert sync.sync_once() == local_head
    assert git(remote, "rev-parse", "main") == combined
    (workspace / "later.txt").write_text("Next backup\n")
    git(workspace, "add", "later.txt")
    git(workspace, "commit", "-m", "Later local work")
    assert sync.sync_once() == git(workspace, "rev-parse", "main")
    git(remote, "merge-base", "--is-ancestor", combined, "main")
    assert git(remote, "show", "main:later.txt") == "Next backup"


def test_remote_ahead_is_preserved(repositories, tmp_path):
    workspace, remote = repositories
    local_head = sync.sync_once()
    advance_remote(remote, tmp_path)
    remote_head = git(remote, "rev-parse", "main")
    assert sync.sync_once() == local_head
    assert git(remote, "rev-parse", "main") == remote_head
    assert git(workspace, "rev-parse", "main") == local_head


def test_conflict_is_visible_and_leaves_both_repositories_unchanged(
    repositories, tmp_path, monkeypatch, capsys,
):
    workspace, remote = repositories
    sync.sync_once()
    other = advance_remote(remote, tmp_path)
    (other / "example.txt").write_text("remote change\n")
    git(other, "add", ".")
    git(other, "commit", "-m", "Conflicting remote edit")
    git(other, "push", "origin", "main")
    remote_head = git(remote, "rev-parse", "main")
    (workspace / "example.txt").write_text("local change\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Conflicting local edit")
    local_head = git(workspace, "rev-parse", "main")
    (workspace / "example.txt").write_text("unfinished change\n")
    original_mkdtemp = sync.tempfile.mkdtemp
    scratch = []

    def record_temp(**kwargs):
        path = original_mkdtemp(dir=tmp_path, **kwargs)
        scratch.append(path)
        return path

    monkeypatch.setattr(sync.tempfile, "mkdtemp", record_temp)
    with pytest.raises(RuntimeError, match="Conflicting files: example.txt"):
        sync.sync_once()
    receipt = read_record(sync.STATUS_PATH)
    assert receipt["outcome"] == "failure"
    assert receipt["last_failure"]["category"] == "conflict"
    assert receipt["last_success"] is not None
    assert "CONFLICT" in capsys.readouterr().out
    assert all(not sync.Path(path).exists() for path in scratch)
    assert git(remote, "rev-parse", "main") == remote_head
    assert git(workspace, "rev-parse", "main") == local_head
    assert (workspace / "example.txt").read_text() == "unfinished change\n"


def test_sync_uses_main_when_workspace_has_another_branch_checked_out(repositories):
    workspace, remote = repositories
    main_head = git(workspace, "rev-parse", "main")
    git(workspace, "checkout", "-b", "unfinished-feature")
    (workspace / "feature.txt").write_text("not main\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Feature")
    assert sync.sync_once() == main_head
    assert git(remote, "rev-parse", "main") == main_head
    assert git(workspace, "branch", "--show-current") == "unfinished-feature"


@pytest.mark.parametrize("advances, succeeds", [(1, True), (3, False)])
def test_remote_updates_during_push_are_retried_with_a_limit(
    repositories, tmp_path, monkeypatch, advances, succeeds,
):
    workspace, remote = repositories
    sync.sync_once()
    other = advance_remote(remote, tmp_path)
    (workspace / "local.txt").write_text("Local work\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Local work")
    local_head = git(workspace, "rev-parse", "main")
    original_run = sync._run
    pushes = []
    remote_heads = []

    def race(cmd, cwd=None, env=None):
        if "push" in cmd:
            pushes.append(cmd)
            if len(pushes) <= advances:
                (other / f"race-{len(pushes)}.txt").write_text("Remote update\n")
                git(other, "add", ".")
                git(other, "commit", "-m", "Concurrent remote update")
                git(other, "push", "origin", "main")
                remote_heads.append(git(remote, "rev-parse", "main"))
        return original_run(cmd, cwd=cwd, env=env)

    monkeypatch.setattr(sync, "_run", race)
    if succeeds:
        assert sync.sync_once() == local_head
        assert len(pushes) == 2
        git(remote, "merge-base", "--is-ancestor", local_head, "main")
        for head in remote_heads:
            git(remote, "merge-base", "--is-ancestor", head, "main")
    else:
        with pytest.raises(RuntimeError, match="stopped after 3 attempts"):
            sync.sync_once()
        assert len(pushes) == 3
        assert git(remote, "rev-parse", "main") == remote_heads[-1]
    assert git(workspace, "rev-parse", "main") == local_head
    assert all(not any(arg.startswith("--force") or arg.startswith("+")
                       for arg in cmd) for cmd in pushes)


def test_unrelated_remote_history_is_not_overwritten(repositories, tmp_path):
    workspace, remote = repositories
    other = tmp_path / "unrelated"
    subprocess.run(["git", "init", "-b", "main", str(other)],
                   check=True, capture_output=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.invalid")
    (other / "independent.txt").write_text("Separate history\n")
    git(other, "add", ".")
    git(other, "commit", "-m", "Unrelated root")
    git(other, "push", str(remote), "main")
    remote_head = git(remote, "rev-parse", "main")
    local_head = git(workspace, "rev-parse", "main")
    with pytest.raises(RuntimeError, match="Nothing was pushed"):
        sync.sync_once()
    assert git(remote, "rev-parse", "main") == remote_head
    assert git(workspace, "rev-parse", "main") == local_head


def test_other_push_failures_are_not_treated_as_divergence(repositories, monkeypatch):
    _, remote = repositories
    original_run = sync._run
    pushes = []

    def reject(cmd, cwd=None, env=None):
        if "push" in cmd:
            pushes.append(cmd)
            raise sync.GitCommandError(cmd, "remote: Permission denied")
        return original_run(cmd, cwd=cwd, env=env)

    monkeypatch.setattr(sync, "_run", reject)
    with pytest.raises(sync.GitCommandError):
        sync.sync_once()
    assert len(pushes) == 1
    assert git(remote, "for-each-ref", "refs/heads") == ""


def test_failed_verification_never_records_success(repositories, monkeypatch):
    _, remote = repositories
    original_run = sync._run

    def fail_verification(cmd, cwd=None, env=None):
        if "ls-remote" in cmd and "--heads" not in cmd:
            raise sync.GitCommandError(cmd, "private diagnostic")
        return original_run(cmd, cwd=cwd, env=env)

    monkeypatch.setattr(sync, "_run", fail_verification)
    with pytest.raises(sync.BackupFailure):
        sync.sync_once()
    receipt = read_record(sync.STATUS_PATH)
    assert receipt["last_success"] is None
    assert receipt["last_failure"]["category"] == "verification"
    # The push happened, but it must not be described as verified.
    assert git(remote, "rev-parse", "main")


def test_watcher_checkpoints_workspace_commit_not_temporary_merge(
    repositories, tmp_path, monkeypatch,
):
    workspace, remote = repositories
    sync.sync_once()
    advance_remote(remote, tmp_path)
    (workspace / "local.txt").write_text("Local work\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Local work")
    original_sync = sync.sync_once
    calls = []
    sleeps = []
    monkeypatch.setattr(sync.sys, "argv", ["sync_to_github.py", "--watch"])
    monkeypatch.setattr(sync.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(sync.tempfile, "gettempdir", lambda: str(tmp_path))

    def counted_sync():
        calls.append(True)
        return original_sync()

    def sleep(_):
        sleeps.append(True)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(sync, "sync_once", counted_sync)
    monkeypatch.setattr(sync.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        sync.main()
    assert len(calls) == 1


def test_watcher_reports_failure_and_retries(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sync.sys, "argv", ["sync_to_github.py", "--watch"])
    monkeypatch.setattr(sync.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(sync, "_run", lambda *a, **kw: "workspace-head")
    calls = []

    def fail_then_succeed():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Cannot safely merge GitHub main with workspace main.")
        return "workspace-head"

    def sleep(_):
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(sync, "sync_once", fail_then_succeed)
    monkeypatch.setattr(sync.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        sync.main()
    assert len(calls) == 2
    assert "GitHub sync failed; retrying in 60 seconds: Cannot safely merge" in (
        capsys.readouterr().out
    )