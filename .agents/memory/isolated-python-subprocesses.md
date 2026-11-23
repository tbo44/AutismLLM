---
name: Isolated Python subprocesses
description: Keep Replit dependencies importable in tests that run with a minimal child environment.
---

Use an allowlisted environment and carry over the current interpreter's resolved import paths when a fresh Python test process needs to run without workspace credentials.

**Why:** Replit's Python launcher can find installed packages via `PYTHONPATH`. Passing only the repo root in the child's `PYTHONPATH` can make imports fail even when the child uses the same executable as pytest.

**How to apply:** Resolve paths before changing the child's working directory and preserve them without inheriting unrelated variables from the workspace.
