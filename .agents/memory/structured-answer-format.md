---
name: Structured answer format
description: How Maya structures LLM answers and how the frontend renders them
---

System prompt enforces these exact headings:
  ## Short Answer
  ## Steps
  ## Who to Contact
  ## Useful Links
  ## Important Note

Frontend (static/script.js) parses these via regex and renders each section with a colour-coded heading class.

The /chat endpoint returns field "answer" (not "response") — confirmed from the Pydantic ChatResponse model.
The structured formatter (rag/structured_formatter.py) assembles retrieved chunks into the context block.

**Why:** Ensures consistent, accessible output regardless of which LLM backend is used.
