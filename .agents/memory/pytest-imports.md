---
name: Pytest imports
description: Reliable test invocation when the standalone pytest launcher cannot import the app package.
---

Run this project's tests with `python -m pytest` until the test configuration makes the project root available to the standalone `pytest` command.

**Why:** The bare launcher can fail test collection with `ModuleNotFoundError: No module named 'app'`, even though the same test passes through Python's module invocation.

**How to apply:** Prefer `python -m pytest <test path>` in local validation and automation; remove or revise this note once project configuration fixes the standalone command.