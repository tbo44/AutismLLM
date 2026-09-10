---
name: Touch tooltip verification
description: Why touchscreen dismissal needs a rendered visibility assertion.
---

Verify tooltip dismissal using computed pseudo-element visibility and opacity in a touch-enabled browser, not only by checking that the open class was removed.

**Why:** A mobile regression test found Escape removed the open state while hover/focus styling still displayed the explanation. A class-only assertion would have falsely passed.

**How to apply:** Use real taps in a mobile browser context; check outside-tap and Escape dismissal and subsequent reopening. Preserve keyboard focus when explicitly dismissing a tooltip.