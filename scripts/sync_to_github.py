#!/usr/bin/env python3
"""
sync_to_github.py — push Replit main branch to GitHub (tbo44/AutismLLM).

Run from any directory:
    python scripts/sync_to_github.py

Uses the Replit GitHub integration (no personal token needed).
The script:
  1. Fetches an OAuth token from the Replit connector proxy.
  2. Clones the local workspace into /tmp/maya-sync-<pid>.
  3. Fetches and merges GitHub main in the temporary clone, then pushes safely.
  4. Removes the temporary clone.
"""

import json
import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GITHUB_REPO = "tbo44/AutismLLM"
WORKSPACE = str(Path(__file__).resolve().parents[1])
GITHUB_URL = f"https://github.com/{GITHUB_REPO}.git"

# Direct script execution puts scripts/, not the project root, on sys.path.
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
from app.github_backup_status import STATUS_PATH, write_result
from app.github_backup_alerts import check_backup_alerts


class BackupFailure(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def _record_failure(category):
    try:
        write_result(STATUS_PATH, failure=category)
    except (OSError, ValueError):
        print("Backup status could not be saved; check status storage.", flush=True)
    check_backup_alerts(STATUS_PATH)


def _get_github_token() -> str:
    """Retrieve the GitHub OAuth access token from the Replit connector proxy."""
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    repl_identity = os.environ.get("REPL_IDENTITY")

    if not hostname or not repl_identity:
        sys.exit(
            "ERROR: REPLIT_CONNECTORS_HOSTNAME or REPL_IDENTITY env vars are not set.\n"
            "This script must run inside the Replit workspace."
        )

    import urllib.request

    url = f"https://{hostname}/api/v2/connection?include_secrets=true"
    req = urllib.request.Request(
        url,
        headers={"X_REPLIT_TOKEN": f"repl {repl_identity}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        sys.exit(f"ERROR: Could not reach Replit connector proxy: {exc}")

    connections = (
        data if isinstance(data, list)
        else data.get("items", data.get("connections", []))
    )
    for conn in connections:
        connector = conn.get("connector_name", "") or conn.get("connector", "")
        if connector.lower() == "github":
            # Token is nested under settings (from include_secrets=true)
            settings = conn.get("settings", {})
            token = settings.get("access_token")
            if not token:
                # Fallback: check legacy secrets dict
                secrets = conn.get("secrets", {})
                token = secrets.get("access_token") or secrets.get("token")
            if token:
                return token

    sys.exit(
        "ERROR: GitHub connection not found in Replit connector proxy.\n"
        "Make sure the GitHub integration is connected to this Repl\n"
        "(Replit sidebar → Integrations → GitHub → Connect)."
    )


class GitCommandError(RuntimeError):
    """Keep command diagnostics available for narrowly scoped push retries."""

    def __init__(self, cmd: list[str], output: str):
        super().__init__(f"Command failed: {' '.join(cmd)}")
        self.output = output


def _run(cmd: list[str], cwd: str | None = None, env: dict | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise GitCommandError(cmd, result.stdout + result.stderr)
    return result.stdout.strip()


def sync_once() -> str:
    try:
        return _sync_once()
    except (RuntimeError, OSError, subprocess.TimeoutExpired, SystemExit) as exc:
        category = exc.category if isinstance(exc, BackupFailure) else (
            "authorization" if isinstance(exc, SystemExit) else "sync"
        )
        _record_failure(category)
        raise


def _sync_once() -> str:
    """Back up committed main plus remote history; return the workspace commit.

    The returned commit is the watcher's checkpoint, not necessarily GitHub's tip:
    temporary merge commits must not trigger another sync every minute.
    """
    print("Fetching fresh GitHub authorization.", flush=True)
    token = _get_github_token()

    temp_dir = tempfile.mkdtemp(prefix="maya-sync-")
    clone_dir = str(Path(temp_dir) / "repo")
    try:
        print(f"📂  Cloning workspace → {clone_dir} …")
        _run(["git", "clone", "--branch", "main", WORKSPACE, clone_dir])

        _run(["git", "remote", "set-url", "origin", GITHUB_URL], cwd=clone_dir)
        # Keep credentials out of command arguments, git config, and logs.
        askpass = Path(temp_dir) / "github-askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *) printf "%s\\n" "$MAYA_GITHUB_TOKEN" ;;\nesac\n'
        )
        askpass.chmod(0o700)

        env = {
            **os.environ, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(askpass), "MAYA_GITHUB_TOKEN": token,
        }
        git = ["git", "-c", "credential.helper="]
        local_head = _run(["git", "rev-parse", "refs/heads/main"], cwd=clone_dir)
        # A remote writer may advance main between fetch and push. Retry only
        # explicit non-fast-forward rejections, with a bounded fresh fetch/merge.
        for attempt in range(3):
            remote = _run(git + ["ls-remote", "--heads", "origin", "refs/heads/main"],
                          cwd=clone_dir, env=env)
            if remote:
                _run(git + ["fetch", "--no-tags", "origin", "refs/heads/main"],
                     cwd=clone_dir, env=env)
                try:
                    _run(git + [
                        "-c", "user.name=Maya GitHub Backup",
                        "-c", "user.email=maya-backup@users.noreply.github.com",
                        "merge", "--no-edit", "FETCH_HEAD",
                    ], cwd=clone_dir, env=env)
                except GitCommandError as exc:
                    conflicts = _run(["git", "diff", "--name-only", "--diff-filter=U"],
                                     cwd=clone_dir)
                    detail = f" Conflicting files: {conflicts}." if conflicts else ""
                    raise BackupFailure(
                        "conflict",
                        "Cannot safely merge GitHub main with workspace main."
                        + detail + " Resolve the histories in a working checkout and commit "
                        "the resolution before retrying. Nothing was pushed; workspace "
                        "and GitHub commits are unchanged."
                    ) from exc
            print(f"Pushing combined main history → github.com/{GITHUB_REPO} …", flush=True)
            try:
                _run(git + ["push", "origin", "refs/heads/main:refs/heads/main"],
                     cwd=clone_dir, env=env)
                break
            except GitCommandError as exc:
                if not ("[rejected]" in exc.output and
                        ("non-fast-forward" in exc.output or "fetch first" in exc.output)):
                    raise
                if attempt == 2:
                    raise BackupFailure(
                        "remote_changed",
                        "GitHub main kept advancing during sync; stopped after 3 attempts. "
                        "No commits were overwritten. Retry when remote updates settle."
                    ) from exc
                print("GitHub main advanced during sync; fetching and merging again.",
                      flush=True)
        combined_head = _run(["git", "rev-parse", "refs/heads/main"], cwd=clone_dir)
        try:
            remote = _run(git + ["ls-remote", "origin", "refs/heads/main"], cwd=clone_dir, env=env)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            raise BackupFailure("verification", "GitHub verification could not complete.") from exc
        if not remote or remote.split()[0] != combined_head:
            raise BackupFailure("verification", "GitHub verification failed: main changed during the sync.")

        write_result(STATUS_PATH, workspace_commit=local_head, remote_tip=combined_head)
        check_backup_alerts(STATUS_PATH)
        print(f"Verified GitHub main at {combined_head} "
              f"(workspace main {local_head}).", flush=True)
        return local_head
    finally:
        # Remove the clone and credential helper unconditionally.
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Check for new commits every 60 seconds")
    args = parser.parse_args()
    # Manual runs and the watcher must not race each other.
    with open(Path(tempfile.gettempdir()) / "maya-github-sync.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("GitHub sync is already running.")
        last_synced = None
        last_verified = 0.0
        while True:
            attempted = False
            try:
                head = _run(["git", "rev-parse", "refs/heads/main"], cwd=WORKSPACE)
                if head != last_synced or time.monotonic() - last_verified >= 300:
                    attempted = True
                    last_synced = sync_once()
                    last_verified = time.monotonic()
            except (RuntimeError, OSError, subprocess.TimeoutExpired, SystemExit) as exc:
                if not attempted:
                    _record_failure("sync")
                if not args.watch:
                    raise
                print(f"GitHub sync failed; retrying in 60 seconds: {exc}", flush=True)
            if not args.watch:
                break
            time.sleep(60)


if __name__ == "__main__":
    main()
