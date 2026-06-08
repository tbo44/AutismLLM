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
