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

def answer(user_text: str) -> str:
    """
    Main answer function for Compass - UK Autism Facts Assistant
    """
    # Check guardrails first
    refused = apply_guardrails(user_text)
    if refused:
        return refused
    
    # For now, return a basic response indicating this is a stub
    # In the future, this will include RAG retrieval and LLM synthesis
    response = """I'm Compass, a UK autism facts assistant. I can help with:

• General information about autism in the UK
• Support services and charities 
• Government benefits and rights
• Education and EHCP processes
• Local services in Hounslow

However, I cannot provide medical diagnoses, treatment plans, medication advice, or individual legal advice.

[This is currently a basic version - RAG system with citations will be added when ML dependencies are available]

For your question: """ + user_text + """

I'd recommend checking these trusted UK sources:
- NHS: nhs.uk/conditions/autism/
- National Autistic Society: autism.org.uk
- For benefits: gov.uk (search for autism or disability benefits)
- For education: gov.uk SEND guidance"""

    return response