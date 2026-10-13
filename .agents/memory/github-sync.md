---
name: GitHub sync method
description: How to push this Repl's main branch to GitHub when direct git ops are blocked
---
Use a throwaway clone for pushes when the main-agent environment blocks writes
to the workspace Git metadata.

**Why:** Even fetching into the workspace has been blocked here. A temporary clone
preserves committed history without mutating the workspace. Earlier connector
proxy filtering returned no connections even though listing and filtering locally
found GitHub; an empty filtered response alone did not prove disconnection.

**How to apply:** Prefer the project's existing sync tooling. Keep credentials
out of Git URLs and command arguments, use only non-force pushes, verify the
remote commit, and clean up temporary resources. Do not auto-commit unfinished
workspace edits just to claim the remote contains every current file.

Automatic divergence recovery belongs only in the temporary clone. Preserve
remote and workspace commits with a normal merge; do not import remote changes
into the running workspace or invent a conflict resolution.

**Why:** This is a backup, not a deployment or a bidirectional workspace update.
Changing the running checkout could disturb unfinished work or the live app.

**How to apply:** Keep the watcher's successful checkpoint tied to the workspace
commit, even when the verified remote tip is a temporary merge commit. Otherwise
an unchanged workspace looks unsynced on every polling cycle.
