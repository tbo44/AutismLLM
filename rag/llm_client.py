"""
Configurable LLM Client for UK Autism Assistant
Uses OpenAI-compatible API — works with Groq, Ollama, OpenAI, or any compatible endpoint.

Provider selection (priority order):
  LLM_PROVIDER=groq       → Groq endpoint (requires GROQ_API_KEY or LLM_API_KEY)
  LLM_PROVIDER=openai     → OpenAI endpoint (requires LLM_API_KEY or OPENAI_API_KEY)
  LLM_PROVIDER=ollama     → Local Ollama endpoint (LLM_BASE_URL, default http://localhost:11434/v1)
  LLM_PROVIDER=custom     → LLM_BASE_URL + LLM_API_KEY

  If LLM_PROVIDER is not set:
    - If GROQ_API_KEY present (and no LLM_BASE_URL overriding)  → Groq
    - If LLM_BASE_URL present                                    → custom endpoint
    - Otherwise                                                  → Ollama (local, Qwen default)
"""

import os
import json
import logging
import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from functools import partial
from threading import Lock
from typing import List, Dict, Any, Optional, Union

import httpx
from openai import OpenAI
import openai

from .structured_formatter import StructuredDataFormatter

logger = logging.getLogger(__name__)

_MAX_PROVIDER_RETRY_AFTER_SECONDS = 300
_SHORT_WEEKDAY = "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_LONG_WEEKDAY = "(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_MONTH = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_CLOCK_TIME = r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
_HTTP_DATE = re.compile(
    rf"{_SHORT_WEEKDAY}, [0-9]{{2}} {_MONTH} [0-9]{{4}} {_CLOCK_TIME} GMT"
    rf"|{_LONG_WEEKDAY}, [0-9]{{2}}-{_MONTH}-[0-9]{{2}} {_CLOCK_TIME} GMT"
    rf"|{_SHORT_WEEKDAY} {_MONTH} (?:[0-9]{{2}}| [0-9]) {_CLOCK_TIME} [0-9]{{4}}"
)


def _provider_retry_after_seconds(headers: httpx.Headers) -> float:
    """Return the provider's bounded wait, or zero if no valid wait was given."""
    values = headers.get_list("retry-after")
    if len(values) != 1:
        return 0.0
    value = values[0].strip(" \t")
    if value.isascii() and value.isdecimal():
        # Float accepts even huge digit strings (as infinity), which the cap handles.
        delay = float(value)
    elif _HTTP_DATE.fullmatch(value):
        # The email parser alone also accepts non-HTTP dates and trailing text.
        try:
            date = parsedate_to_datetime(value)
        except ValueError:
            return 0.0
        if date.tzinfo is None:  # The obsolete asctime HTTP-date format is UTC.
            date = date.replace(tzinfo=timezone.utc)
        delay = date.timestamp() - time.time()
    else:
        return 0.0
    return max(0.0, min(delay, _MAX_PROVIDER_RETRY_AFTER_SECONDS))


class _CooldownAwareOpenAI(OpenAI):
    def _should_retry(self, response: httpx.Response) -> bool:
        # The SDK has no public per-status retry setting. Pass 429 through
        # immediately so the shared cooldown, not an SDK retry, handles it.
        if response.status_code == 429:
            return False
        return super()._should_retry(response)


def _build_openai_client(*, http_client: httpx.Client | None = None) -> OpenAI:
    build_client = partial(_CooldownAwareOpenAI, http_client=http_client)
    provider     = os.environ.get("LLM_PROVIDER", "").strip().lower()
    llm_base_url = os.environ.get("LLM_BASE_URL", "").strip()
    llm_api_key  = os.environ.get("LLM_API_KEY", "").strip()
    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
    openai_key   = os.environ.get("OPENAI_API_KEY", "").strip()

    # --- Explicit provider ---
    if provider == "groq":
        key = llm_api_key or groq_api_key
        if not key:
            raise ValueError("LLM_PROVIDER=groq but no LLM_API_KEY or GROQ_API_KEY found.")
        logger.info("LLM provider: Groq (explicit)")
        return build_client(base_url="https://api.groq.com/openai/v1", api_key=key)

    if provider == "openai":
        key = llm_api_key or openai_key
        if not key:
            raise ValueError("LLM_PROVIDER=openai but no LLM_API_KEY or OPENAI_API_KEY found.")
        logger.info("LLM provider: OpenAI (explicit)")
        return build_client(api_key=key)

    if provider in ("ollama", "local"):
        base = llm_base_url or "http://localhost:11434/v1"
        logger.info(f"LLM provider: Ollama/local at {base}")
        return build_client(base_url=base, api_key=llm_api_key or "ollama")

    if provider == "custom":
        if not llm_base_url:
            raise ValueError("LLM_PROVIDER=custom but LLM_BASE_URL is not set.")
        logger.info(f"LLM provider: custom endpoint {llm_base_url}")
        return build_client(base_url=llm_base_url, api_key=llm_api_key or "none")

    # --- Auto-detect (no LLM_PROVIDER set) ---
    if groq_api_key and not llm_base_url and not llm_api_key:
        logger.info("LLM provider: Groq (auto-detected via GROQ_API_KEY)")
        return build_client(base_url="https://api.groq.com/openai/v1", api_key=groq_api_key)

    if llm_base_url and llm_api_key:
        logger.info(f"LLM provider: custom endpoint {llm_base_url} (auto-detected)")
        return build_client(base_url=llm_base_url, api_key=llm_api_key)

    if llm_api_key:
        logger.info("LLM provider: OpenAI (auto-detected via LLM_API_KEY)")
        return build_client(api_key=llm_api_key)

    if llm_base_url:
        logger.info(f"LLM provider: local endpoint {llm_base_url} (no auth)")
        return build_client(base_url=llm_base_url, api_key="ollama")

    # Default: Ollama-compatible local endpoint with Qwen
    default_base = "http://localhost:11434/v1"
    logger.info(f"LLM provider: Ollama default at {default_base} (set LLM_PROVIDER or GROQ_API_KEY to change)")
    return build_client(base_url=default_base, api_key="ollama")


def _default_model() -> str:
    explicit = os.environ.get("LLM_MODEL", "").strip()
    if explicit:
        return explicit

    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()

    # Explicit Groq → Llama
    if provider == "groq":
        return "llama-3.3-70b-versatile"
    # Explicit OpenAI → GPT-4o-mini
    if provider == "openai":
        return "gpt-4o-mini"
    # Explicit Ollama/local/custom → Qwen
    if provider in ("ollama", "local", "custom"):
        return "qwen2.5:72b"

    # Auto-detect: GROQ_API_KEY present → Llama; otherwise Qwen
    if os.environ.get("GROQ_API_KEY") and not os.environ.get("LLM_BASE_URL") and not os.environ.get("LLM_API_KEY"):
        return "llama-3.3-70b-versatile"

    return "qwen2.5:72b"


class UKAutismLLMClient:
    def __init__(self):
        self.client      = None
        self.model       = _default_model()
        self.temperature = float(os.environ.get("TEMPERATURE", "0"))
        self.top_p       = float(os.environ.get("TOP_P", "1.0"))
        self.formatter   = StructuredDataFormatter()
        self.rate_limit_cooldown_seconds = float(os.environ.get("LLM_RATE_LIMIT_COOLDOWN_SECONDS", "30"))
        if not math.isfinite(self.rate_limit_cooldown_seconds) or self.rate_limit_cooldown_seconds < 0:
            raise ValueError("LLM_RATE_LIMIT_COOLDOWN_SECONDS must be a finite, non-negative number")
        self._cooldown_until = 0.0
        self._logged_cooldown_suppression = False
        self._cooldown_lock = Lock()
        self._initialize_client()

    def _initialize_client(self):
        self.client = _build_openai_client()
        logger.info(f"LLM client ready  model={self.model}  temp={self.temperature}  top_p={self.top_p}")

    def is_rate_limited(self) -> bool:
        """Share the cooldown across requests using this client, without sleeping."""
        with self._cooldown_lock:
            return time.monotonic() < self._cooldown_until

    def _skip_during_cooldown(self, stage: str) -> bool:
        with self._cooldown_lock:
            remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return False
            log_suppression = not self._logged_cooldown_suppression
            self._logged_cooldown_suppression = True
        if log_suppression:
            logger.info(
                "event=llm_cooldown_suppressed stage=%s remaining_seconds=%.6g",
                stage, remaining,
            )
        return True

    def _record_provider_429(self, stage: str, headers: httpx.Headers) -> None:
        minimum = self.rate_limit_cooldown_seconds
        wait = 0.0
        hint_extended = False
        if minimum > 0:
            hint = _provider_retry_after_seconds(headers)
            with self._cooldown_lock:
                now = time.monotonic()
                # Overlapping 429s can extend a pause, but do not start a new
                # suppression log window until the previous one expires.
                if now >= self._cooldown_until:
                    self._logged_cooldown_suppression = False
                without_hint = max(self._cooldown_until, now + minimum)
                self._cooldown_until = max(without_hint, now + hint)
                wait = self._cooldown_until - now
                hint_extended = self._cooldown_until > without_hint
        logger.warning(
            "event=llm_provider_429 stage=%s wait_seconds=%.6g provider_hint_extended=%s",
            stage, wait, hint_extended,
        )

    # ─────────────────────────────────────────────────────────────────
    # Main response generation
    # ─────────────────────────────────────────────────────────────────

    def synthesize_response(
        self,
        user_question: str,
        retrieved_chunks: List[Dict[str, Any]],
        context: Optional[str] = None,
        comprehension_level: str = "standard"
    ) -> Dict[str, Any]:
        """Generate a structured response using retrieved context."""
        if not self.client:
            return {"response": "LLM client not available", "sources_used": [], "chunks_used": 0, "model_used": self.model, "success": False, "error": "Client not initialized"}
        if self._skip_during_cooldown("synthesis"):
            return {"response": "", "sources_used": [], "chunks_used": 0, "model_used": self.model, "success": False, "rate_limited": True, "error": "Provider rate-limit cooldown active"}

        try:
            sources_used = set()
            context_text = self.formatter.format_results_for_synthesis(retrieved_chunks)

            for chunk in retrieved_chunks:
                meta = chunk["metadata"]
                sources_used.add((meta["source_name"], meta["url"], meta["title"]))

            language_guidelines = {
                "clear": """LANGUAGE LEVEL — CLEAR (Simple):
Write for someone who finds reading difficult. Use very short sentences (under 10 words each).
Use only simple everyday words. No acronyms — write "autism" not "ASD", "doctor" not "GP", "school plan" not "EHCP".
Use bullet points. Keep the whole answer brief. One idea per sentence.""",

                "standard": """LANGUAGE LEVEL — STANDARD:
Use clear, plain English suitable for a general UK audience.
Explain jargon the first time you use it. Keep sentences reasonably short.
Aim for a reading age of about 14–16.""",

                "complex": """LANGUAGE LEVEL — COMPLEX (Detailed):
Use precise language including legal, medical, and bureaucratic terminology where appropriate.
Provide comprehensive detail with nuanced explanations for a professional or highly-informed audience."""
            }

            system_prompt = """You are Maya, a UK autism facts assistant for Autism Hounslow.
You provide helpful, accurate information about autism in the UK — especially Hounslow and the London Borough of Hounslow.

RULES:
1. Answer only from the provided context. Do not invent facts.
2. Focus on UK-specific information, services, and legislation.
3. If the context does not contain enough information, say so clearly.
4. Never give medical diagnoses, treatment plans, or medication advice.
5. Never give specific legal advice for individual cases.
6. If someone appears to be in crisis, direct them to 999 / Samaritans 116 123.
7. For Hounslow-specific questions, prioritise local information.

{language_instruction}

ANSWER FORMAT — use these exact section headings in your response:

## Short Answer
(2–3 sentences answering the core question directly)

## Steps
(Numbered list of practical steps, if applicable. Omit section if no process is involved.)

## Who to Contact
(Relevant UK services, phone numbers, addresses. Omit if not applicable.)

## Useful Links
(List source names used. Use the format: Source Name — brief description)

## Important Note
(One short sentence reminding the user this is general guidance, not personal advice.)

---
Would you like this in simpler language? (tap the button below)

CONTEXT INFORMATION:
{context}"""

            user_prompt = f"Question: {user_question}\n\nPlease answer using the context above."
            language_instruction = language_guidelines.get(comprehension_level, language_guidelines["standard"])

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt.format(language_instruction=language_instruction, context=context_text)},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=1200
            )

            generated_text = response.choices[0].message.content or ""
            return {"response": generated_text, "sources_used": list(sources_used), "chunks_used": len(retrieved_chunks), "model_used": self.model, "success": True}

        except openai.RateLimitError as e:
            self._record_provider_429("synthesis", e.response.headers)
            return {"response": "", "sources_used": [], "chunks_used": 0, "model_used": self.model, "success": False, "rate_limited": True, "error": "Provider returned HTTP 429"}
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return {"response": "I'm sorry, I'm having trouble generating a response right now. Please try again in a moment.", "sources_used": [], "chunks_used": 0, "model_used": self.model, "success": False, "error": str(e)}

    # ─────────────────────────────────────────────────────────────────
    # Query enhancement
    # ─────────────────────────────────────────────────────────────────

    def enhance_query(self, user_question: str) -> Union[str, Dict[str, Any]]:
        if not self.client:
            return user_question
        if self._skip_during_cooldown("enhancement"):
            return {"query": user_question, "rate_limited": True}
        try:
            prompt = (
                "Rewrite the following question as a concise search query for a UK autism information system. "
                "Include UK-specific terms, service names, or benefit names where relevant. "
                "Return only the rewritten query — nothing else.\n\n"
                f"Original question: {user_question}"
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=80
            )
            enhanced = (response.choices[0].message.content or "").strip()
            if enhanced:
                logger.info(f"Query enhanced: '{user_question}' → '{enhanced}'")
                return enhanced
            return user_question
        except openai.RateLimitError as e:
            self._record_provider_429("enhancement", e.response.headers)
            return {"query": user_question, "rate_limited": True}
        except Exception as e:
            logger.error(f"Query enhancement error: {str(e)}")
            return user_question

    # ─────────────────────────────────────────────────────────────────
    # Content appropriateness
    # ─────────────────────────────────────────────────────────────────

    def check_content_appropriateness(self, user_question: str) -> Dict[str, Any]:
        if not self.client:
            return {"appropriate": True, "reason": "Moderation unavailable", "category": "unknown"}
        if self._skip_during_cooldown("moderation"):
            return {"appropriate": True, "reason": "Rate limited", "category": "unknown", "rate_limited": True}
        try:
            prompt = (
                "You are a content filter for a UK autism facts assistant. "
                "Decide if the following question is appropriate for this assistant to answer.\n\n"
                "Appropriate topics: autism information, UK support services, benefits (DLA, PIP, UC), "
                "education (EHCP, SEND), NHS services, local authority services, Hounslow services, "
                "carer support, transition to adulthood, social care.\n\n"
                "Inappropriate: questions completely unrelated to autism or disability support; "
                "requests for entertainment, gambling, violence, or explicit content.\n\n"
                f"Question: {user_question}\n\n"
                'Respond with JSON: {"appropriate": true/false, "reason": "brief explanation", "category": "autism-related|off-topic|other"}'
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=120
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass
            return {"appropriate": True, "reason": "Parse error", "category": "unknown"}
        except openai.RateLimitError as e:
            self._record_provider_429("moderation", e.response.headers)
            return {"appropriate": True, "reason": "Rate limited", "category": "unknown", "rate_limited": True}
        except Exception as e:
            logger.error(f"Appropriateness check error: {str(e)}")
            return {"appropriate": True, "reason": "Check failed", "category": "unknown"}
