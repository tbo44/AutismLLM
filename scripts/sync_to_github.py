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
import os
import shutil
import subprocess
import sys
import tempfile

GITHUB_REPO = "tbo44/AutismLLM"
WORKSPACE = "/home/runner/workspace"


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


def _run(cmd: list[str], cwd: str | None = None, env: dict | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        sys.exit(f"ERROR: command failed: {' '.join(cmd)}")


def main() -> None:
    print("🔑  Fetching GitHub token from Replit connector proxy …")
    token = _get_github_token()

    clone_dir = tempfile.mkdtemp(prefix="maya-sync-")
    try:
        print(f"📂  Cloning workspace → {clone_dir} …")
        _run(["git", "clone", WORKSPACE, clone_dir])

        remote_url = f"https://x-access-token:{token}@github.com/{GITHUB_REPO}.git"
        _run(["git", "remote", "set-url", "origin", remote_url], cwd=clone_dir)

        print(f"🚀  Pushing main → github.com/{GITHUB_REPO} …")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        _run(["git", "push", "origin", "main"], cwd=clone_dir, env=env)

        print(f"✅  Done — github.com/{GITHUB_REPO} is now up to date.")
    finally:
        # Wipe the clone (and the embedded token) unconditionally.
        shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
