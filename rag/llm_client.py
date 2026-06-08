"""
Configurable LLM Client for UK Autism Assistant
Uses OpenAI-compatible API - works with Groq, Ollama, OpenAI, or any compatible endpoint
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from openai import OpenAI

from .structured_formatter import StructuredDataFormatter

logger = logging.getLogger(__name__)

def _build_openai_client() -> OpenAI:
    """
    Build an OpenAI-compatible client from environment variables.

    Priority order:
      1. LLM_BASE_URL + LLM_API_KEY  →  custom endpoint (Ollama, local, etc.)
      2. LLM_API_KEY alone            →  standard OpenAI
      3. GROQ_API_KEY present         →  Groq endpoint (default / legacy)
      4. LLM_BASE_URL alone           →  local endpoint with no auth (Ollama default)
    """
    llm_base_url = os.environ.get("LLM_BASE_URL", "").strip()
    llm_api_key  = os.environ.get("LLM_API_KEY", "").strip()
    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()

    if llm_base_url and llm_api_key:
        logger.info(f"LLM: custom endpoint {llm_base_url}")
        return OpenAI(base_url=llm_base_url, api_key=llm_api_key)

    if llm_api_key:
        logger.info("LLM: standard OpenAI endpoint")
        return OpenAI(api_key=llm_api_key)

    if groq_api_key:
        logger.info("LLM: Groq endpoint (via GROQ_API_KEY)")
        return OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_api_key
        )

    if llm_base_url:
        logger.info(f"LLM: local endpoint {llm_base_url} (no auth)")
        return OpenAI(base_url=llm_base_url, api_key="ollama")

    raise ValueError(
        "No LLM credentials found. Set LLM_API_KEY, GROQ_API_KEY, or LLM_BASE_URL."
    )


def _default_model() -> str:
    """Return model name from env, falling back to a sensible default."""
    explicit = os.environ.get("LLM_MODEL", "").strip()
    if explicit:
        return explicit
    if os.environ.get("GROQ_API_KEY") and not os.environ.get("LLM_BASE_URL"):
        return "llama-3.3-70b-versatile"
    return "qwen2.5:72b"


class UKAutismLLMClient:
    def __init__(self):
        self.client = None
        self.model       = _default_model()
        self.temperature = float(os.environ.get("TEMPERATURE", "0"))
        self.top_p       = float(os.environ.get("TOP_P", "1.0"))
        self.formatter   = StructuredDataFormatter()
        self._initialize_client()

    def _initialize_client(self):
        self.client = _build_openai_client()
        logger.info(f"LLM client ready  model={self.model}  temp={self.temperature}  top_p={self.top_p}")

    # ------------------------------------------------------------------
    # Main response generation
    # ------------------------------------------------------------------

    def synthesize_response(
        self,
        user_question: str,
        retrieved_chunks: List[Dict[str, Any]],
        context: Optional[str] = None,
        comprehension_level: str = "standard"
    ) -> Dict[str, Any]:
        """
        Generate a structured response using retrieved context.

        Returns dict with keys: response, sources_used, chunks_used, model_used, success
        """
        if not self.client:
            return {
                "response": "LLM client not available",
                "sources_used": [],
                "chunks_used": 0,
                "model_used": self.model,
                "success": False,
                "error": "Client not initialized"
            }

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
                    {
                        "role": "system",
                        "content": system_prompt.format(
                            language_instruction=language_instruction,
                            context=context_text
                        )
                    },
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=1200
            )

            generated_text = response.choices[0].message.content or ""

            return {
                "response": generated_text,
                "sources_used": list(sources_used),
                "chunks_used": len(retrieved_chunks),
                "model_used": self.model,
                "success": True
            }

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return {
                "response": "I'm sorry, I'm having trouble generating a response right now. Please try again in a moment.",
                "sources_used": [],
                "chunks_used": 0,
                "model_used": self.model,
                "success": False,
                "error": str(e)
            }

    # ------------------------------------------------------------------
    # Query enhancement
    # ------------------------------------------------------------------

    def enhance_query(self, user_question: str) -> str:
        if not self.client:
            return user_question
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
        except Exception as e:
            logger.error(f"Query enhancement error: {str(e)}")
            return user_question

    # ------------------------------------------------------------------
    # Content appropriateness check
    # ------------------------------------------------------------------

    def check_content_appropriateness(self, user_question: str) -> Dict[str, Any]:
        if not self.client:
            return {"appropriate": True, "reason": "Moderation unavailable", "category": "unknown"}
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
        except Exception as e:
            logger.error(f"Appropriateness check error: {str(e)}")
            return {"appropriate": True, "reason": "Check failed", "category": "unknown"}
