#!/usr/bin/env python3
"""
sync_to_github.py — push Replit main branch to GitHub (tbo44/AutismLLM).

Run from any directory:
    python scripts/sync_to_github.py

Uses the Replit GitHub integration (no personal token needed).
The script:
  1. Fetches an OAuth token from the Replit connector proxy.
  2. Clones the local workspace into /tmp/maya-sync-<pid>.
  3. Pushes main → github.com/tbo44/AutismLLM.
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


def _run(cmd: list[str], cwd: str | None = None, env: dict | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result.stdout.strip()


def sync_once() -> str:
    """Push only committed main history, without changing the workspace."""
    print("Fetching fresh GitHub authorization.", flush=True)
    token = _get_github_token()

    clone_dir = tempfile.mkdtemp(prefix="maya-sync-")
    try:
        print(f"📂  Cloning workspace → {clone_dir} …")
        _run(["git", "clone", WORKSPACE, clone_dir])

        _run(["git", "remote", "set-url", "origin", GITHUB_URL], cwd=clone_dir)
        # Keep credentials out of command arguments, git config, and logs.
        askpass = Path(clone_dir) / "github-askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *) printf "%s\\n" "$MAYA_GITHUB_TOKEN" ;;\nesac\n'
        )
        askpass.chmod(0o700)

        print(f"🚀  Pushing main → github.com/{GITHUB_REPO} …")
        env = {
            **os.environ, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(askpass), "MAYA_GITHUB_TOKEN": token,
        }
        git = ["git", "-c", "credential.helper="]
        local_head = _run(["git", "rev-parse", "refs/heads/main"], cwd=clone_dir)
        _run(git + ["push", "origin", "refs/heads/main:refs/heads/main"], cwd=clone_dir, env=env)
        remote = _run(git + ["ls-remote", "origin", "refs/heads/main"], cwd=clone_dir, env=env)
        if not remote or remote.split()[0] != local_head:
            raise RuntimeError("GitHub verification failed: main changed during the sync.")

        print(f"Verified GitHub main at {local_head}.", flush=True)
        return local_head
    finally:
        # Remove the clone and credential helper unconditionally.
        shutil.rmtree(clone_dir, ignore_errors=True)


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
            try:
                head = _run(["git", "rev-parse", "refs/heads/main"], cwd=WORKSPACE)
                if head != last_synced or time.monotonic() - last_verified >= 300:
                    last_synced = sync_once()
                    last_verified = time.monotonic()
            except (RuntimeError, OSError, subprocess.TimeoutExpired, SystemExit) as exc:
                if not args.watch:
                    raise
                print(f"GitHub sync failed; retrying in 60 seconds: {exc}", flush=True)
            if not args.watch:
                break
            time.sleep(60)


if __name__ == "__main__":
    main()
