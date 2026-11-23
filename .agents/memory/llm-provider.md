---
name: LLM provider selection
description: How Maya selects and configures the LLM backend via env vars
---

Priority order in rag/llm_client.py:
1. LLM_PROVIDER=groq      → Groq API (requires GROQ_API_KEY or LLM_API_KEY)
2. LLM_PROVIDER=openai    → OpenAI (requires LLM_API_KEY or OPENAI_API_KEY)
3. LLM_PROVIDER=ollama    → Ollama at LLM_BASE_URL or http://localhost:11434/v1
4. LLM_PROVIDER=custom    → LLM_BASE_URL + LLM_API_KEY (required)
5. Auto-detect: GROQ_API_KEY present (no LLM_BASE_URL/LLM_API_KEY) → Groq
6. Auto-detect: LLM_BASE_URL + LLM_API_KEY present → custom
7. Default: Ollama at localhost:11434/v1, model qwen2.5:72b

Default model per provider: Groq→llama-3.3-70b-versatile, OpenAI→gpt-4o-mini, Ollama/custom→qwen2.5:72b.
All overridden by LLM_MODEL env var.

**Why:** Spec requires Ollama/Qwen as the pure default (no keys needed) while still auto-detecting Groq when GROQ_API_KEY is present in the current Replit environment.

Treat provider retry hints as advice for admission of new calls, not as permission to retry an answer request.

**Why:** One rate-limited stage should stop the rest of that answer's provider work. A quota reset far in the future shouldn't let a remote header take answers offline indefinitely, and the operator must be able to opt out of the pause without enabling retries.

**How to apply:** Preserve the operator's minimum and explicit off switch when adding provider-specific hints. Bound only the provider's contribution so existing operator settings still work.

Treat provider error text as untrusted request data in rate-limit diagnostics.

**Why:** Provider errors can echo prompts or authorization data. A new provider 429 is evidence of upstream throttling, while a locally skipped call is not.

**How to apply:** Log only fixed stage names and derived timing data. Log fresh 429s separately from the first locally skipped call; in-flight responses can lengthen the same pause without opening another suppression log window.

Keep application answer tests separate from live model evaluations.

**Why:** The chat route can return HTTP 200 with a user-friendly fallback after provider failure. Canned completions cannot demonstrate actual model quality, and model availability should not determine whether the app's prompt and response handling pass.

**How to apply:** Test prompts and response handling with controlled completions. For opt-in live checks, diagnose provider access at the SDK boundary before the chat fallback hides it and verify generated output separately.
