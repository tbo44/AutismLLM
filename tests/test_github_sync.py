"""Exercise sync against local Git repositories, without network or credentials."""
import subprocess

import pytest

from scripts import sync_to_github as sync


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


def test_sync_never_overwrites_divergent_remote(repositories, tmp_path):
    workspace, remote = repositories
    sync.sync_once()
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-b", "main", str(remote), str(other)],
                   check=True, capture_output=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.invalid")
    (other / "remote.txt").write_text("GitHub-only work\n")
    git(other, "add", ".")
    git(other, "commit", "-m", "Remote work")
    git(other, "push", "origin", "main")
    remote_head = git(remote, "rev-parse", "main")
    (workspace / "local.txt").write_text("Local work\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "Local work")
    with pytest.raises(RuntimeError, match="Command failed"):
        sync.sync_once()
    assert git(remote, "rev-parse", "main") == remote_head