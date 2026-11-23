---
name: Chat provider admission
description: Why the chat work queue preserves single-flight provider admission.
---

Keep one provider-backed answer pipeline at a time per server process unless explicitly changing the provider admission policy.

**Why:** The original synchronous flow serialised answers. Simply using a general worker pool would let multiple chats start provider requests before the first 429 could establish the shared pause. A worker that keeps running after its caller is cancelled must still count as in flight.

**How to apply:** Keep lightweight routes independent of the chat queue. When changing chat offloading or timeouts, verify that the next waiting chat sees a 429 cooldown before it can make its own provider call.
