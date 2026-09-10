---
name: TestClient background tasks
description: How to test endpoint-triggered asyncio background work that must remain alive across status polling.
---

Run trigger-and-poll tests for endpoint-created `asyncio` tasks inside one event loop rather than using separate synchronous `TestClient` calls.

**Why:** A synchronous `TestClient` request can close its per-request portal after the response and cancel tasks created with `asyncio.create_task`, so later polling requests observe behavior that differs from a persistent production server.

**How to apply:** For CI tests that trigger background work and poll another endpoint, use a normal pytest test with `asyncio.run()` around the complete scenario when no async pytest plugin is installed.