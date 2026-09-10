---
name: Rendered dashboard checks
description: Validate JavaScript after Python template rendering, not only HTTP responses.
---

Validate emitted JavaScript when changing scripts embedded in Python HTML templates.

**Why:** Python can turn JavaScript escape sequences into literal newlines. Login and dashboard requests can both succeed while the browser rejects the entire script and Preview reports an artifact error.

**How to apply:** Parse the rendered script with a JavaScript syntax checker. A successful HTTP response or a text-presence assertion does not establish that dashboard controls can run.