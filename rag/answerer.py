from dataclasses import dataclass
import re
from typing import Optional

@dataclass
class GuardrailTemplates:
    clinical: str = (
        "I can't provide personalised clinical advice (diagnosis, treatment, or medication changes). "
        "A good next step is to speak to your GP or call NHS 111. "
        "If this is an emergency, call 999."
    )
    legal: str = (
        "I can't give case-specific legal advice. "
        "For SEND and education issues, you could contact IPSEA. "
        "You can also speak with Citizens Advice or a qualified solicitor."
    )
    crisis: str = (
        "If you're in crisis or feel unable to stay safe: call 999, go to A&E, or contact NHS urgent mental health help. "
        "You can also call Samaritans on 116 123 (free, 24/7)."
    )

TEMPLATES = GuardrailTemplates()

CLINICAL_PATTERNS = [
    r"\b(dose|dosage|increase|decrease|change)\b.*\b(med|medication)\b",
    r"\b(start|stop|switch)\b.*\b(med|medication)\b",
    r"\b(diagnos(e|is)|treatment plan|therapy plan)\b",
    r"\bshould I (take|give|increase|decrease)\b.*\b(med|medication|drug|pill)\b",
    r"\bwhat (med|medication|drug|treatment)\b.*\b(give|should|take)\b",
    r"\b(give|prescribe)\b.*\b(med|medication|drug|pill)\b",
]

LEGAL_PATTERNS = [
    r"\b(win|guarantee|ensure)\b.*\b(tribunal|appeal|case)\b",
    r"\blegal advice\b",
    r"\bhow (do|to) (appeal|win)\b.*",
    r"\bcan I sue\b",
    r"\bwhat are my legal rights\b",
]

CRISIS_PATTERNS = [
    r"\b(self[-\s]?harm(ing)?|suicid(al|e)|overdose|can.?t cope|in crisis)\b",
    r"\b(unable to stay safe|immediate danger)\b",
    r"\b(want to die|ending it all|hurt myself)\b",
]

def _match_any(text: str, patterns) -> Optional[str]:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE | re.MULTILINE):
            return p
    return None

def apply_guardrails(user_text: str) -> Optional[str]:
    if _match_any(user_text, CRISIS_PATTERNS):
        return TEMPLATES.crisis
    if _match_any(user_text, CLINICAL_PATTERNS):
        return TEMPLATES.clinical
    if _match_any(user_text, LEGAL_PATTERNS):
        return TEMPLATES.legal
    return None

async def answer_async(user_text: str) -> str:
    """
    Main async answer function for Maya - UK Autism Facts Assistant
    Uses background initialization for fast first response
    """
    try:
        from .rag_system import get_rag_system, InitializationState
        import logging
        import asyncio
        logger = logging.getLogger(__name__)
        
        # Get the RAG system instance
        rag_system = get_rag_system()
        
        # Check initialization state
        if rag_system.init_state == InitializationState.NOT_STARTED:
            # Trigger background initialization (non-blocking)
            logger.info("🚀 Triggering background RAG initialization on first request...")
            asyncio.create_task(rag_system.initialize_async())
            return _provide_initializing_response(user_text)
        
        elif rag_system.init_state == InitializationState.INITIALIZING:
            # Still initializing, return friendly message
            logger.info("⏳ RAG system still initializing, providing interim response")
            return _provide_initializing_response(user_text)
        
        elif rag_system.init_state == InitializationState.ERROR:
            # Initialization failed, provide fallback
            logger.error(f"❌ RAG system in error state: {rag_system.init_error}")
            return _provide_fallback_response(user_text)
        
        # System is ready, use full RAG pipeline
        return rag_system.answer_question(user_text)
        
    except Exception as e:
        # Fallback to basic guardrails if RAG system fails
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"RAG system error: {str(e)}")
        
        # Check guardrails as fallback
        refused = apply_guardrails(user_text)
        if refused:
            return refused
            
        return _provide_fallback_response(user_text)

def answer(user_text: str) -> str:
    """
    Synchronous wrapper for backward compatibility
    Uses lazy initialization to avoid blocking deployment startup
    """
    try:
        from .rag_system import get_rag_system
        import logging
        logger = logging.getLogger(__name__)
        
        # Get the RAG system instance (lazy initialization)
        rag_system = get_rag_system()
        
        # Initialize if needed (happens on first request only)
        if not rag_system.initialized:
            logger.info("Initializing RAG system on first request...")
            init_result = rag_system.initialize()
            
            # If knowledge base is empty, provide helpful response
            if init_result.get("needs_population", False):
                return _provide_bootstrap_response(user_text)
            
            logger.info("RAG system initialized successfully")
        
        # Use the full RAG system to answer
        return rag_system.answer_question(user_text)
        
    except Exception as e:
        # Fallback to basic guardrails if RAG system fails
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"RAG system error: {str(e)}")
        
        # Check guardrails as fallback
        refused = apply_guardrails(user_text)
        if refused:
            return refused
            
        return _provide_fallback_response(user_text)

def _provide_initializing_response(user_text: str) -> str:
    """Response while RAG system is initializing in background"""
    refused = apply_guardrails(user_text)
    if refused:
        return refused
        
    return f"""Hi! I'm Maya, your UK autism facts assistant. I'm currently loading my knowledge base (this takes about 30 seconds on first use).

Your question: {user_text}

While I'm getting ready, here are trusted UK sources you can check:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk
• **Gov.UK SEND guidance**: gov.uk/special-educational-needs-support-council
• **IPSEA**: ipsea.org.uk (for education advice)

If you're in Hounslow:
• **Hounslow Council SEND**: hounslow.gov.uk

**Please ask your question again in a moment** and I'll have my full knowledge base ready to help!

**Important:** This information is for guidance only. For personalised advice:
• **Medical questions:** Contact your GP or call NHS 111
• **Legal/SEND issues:** Contact IPSEA, Citizens Advice, or a qualified solicitor
• **Crisis support:** Call 999, contact Samaritans (116 123), or go to A&E"""

def _provide_bootstrap_response(user_text: str) -> str:
    """Response when knowledge base is empty"""
    refused = apply_guardrails(user_text)
    if refused:
        return refused
        
    return f"""I'm Maya, your UK autism facts assistant! I'm currently building my knowledge base from trusted sources like the NHS, National Autistic Society, and government guidance.

For your question: {user_text}

Please check these trusted sources while I get up to speed:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk  
• **Gov.UK SEND guidance**: gov.uk/special-educational-needs-support-council
• **IPSEA**: ipsea.org.uk (for education advice)

If you're in Hounslow:
• **Hounslow Council SEND**: hounslow.gov.uk

**Important:** This information is for guidance only. For personalised advice:
• **Medical questions:** Contact your GP or call NHS 111
• **Legal/SEND issues:** Contact IPSEA, Citizens Advice, or a qualified solicitor  
• **Crisis support:** Call 999, contact Samaritans (116 123), or go to A&E"""

def _provide_fallback_response(user_text: str) -> str:
    """Fallback response when RAG system is unavailable"""
    return f"""I'm experiencing some technical difficulties right now, but I can still help with basic information.

For your question: {user_text}

Please try these trusted UK autism sources:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk
• **Gov.UK**: gov.uk (search for autism or SEND guidance)
• **IPSEA**: ipsea.org.uk (for education and legal advice)

For urgent support, contact NHS 111 or your GP.

**Important:** For personalised advice:
• **Medical questions:** Contact your GP or call NHS 111
• **Legal/SEND issues:** Contact IPSEA, Citizens Advice, or a qualified solicitor  
• **Crisis support:** Call 999, contact Samaritans (116 123), or go to A&E"""