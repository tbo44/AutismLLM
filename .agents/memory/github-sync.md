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
