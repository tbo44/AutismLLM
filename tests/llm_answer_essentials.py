"""Fixed sources and lightweight content checks for the live reading-level cases."""

import re
from dataclasses import dataclass

from tests.llm_test_helpers import search_hit


@dataclass(frozen=True)
class Essential:
    description: str
    signals: tuple[str, ...]

    def appears_in(self, statements: list[str]) -> bool:
        # Related signals must appear together, not just somewhere in the reply.
        return any(all(re.search(signal, statement) for signal in self.signals)
                   for statement in statements)


@dataclass(frozen=True)
class Contradiction:
    description: str
    pattern: str


@dataclass(frozen=True)
class ReadingCase:
    id: str
    question: str
    hit: dict
    essentials: tuple[Essential, ...]
    contradictions: tuple[Contradiction, ...] = ()


COUNCIL = r"(?:local (?:education )?authority|(?:local )?council)"
DOCTOR = r"(?:gp|general practitioner|(?:family )?doctor)"
PLAN_OUTCOME = r"(?:keep|change|amend|end|stop|maintain)"
PLAN = r"(?:it|the (?:(?:ehc |school )?plan|ehcp))"
PLAN_DECISION = rf"(?:what happens to {PLAN}|whether to {PLAN_OUTCOME}(?:,? (?:or )?{PLAN_OUTCOME})* {PLAN})"
CLAIM_FORM = r"(?:it|(?:(?:the|your) )?(?:completed |filled[ -]in )?(?:dla )?(?:claim form|form|claim|application))"
ONLINE = r"(?:online|via (?:the |an )?online portal)"
PIP_DECISION = r"(?:it|(?:(?:the|your|their|its|this) )?(?:original )?(?:pip |benefit )?decision)"
PIP_DECISION_DATE = (
    r"(?:date (?:on|of|shown on|printed on) (?:the |your )?(?:pip )?(?:decision(?: letter)?|letter)"
    r"|(?:pip )?decision date|(?:the |your )?(?:pip )?decision was (?:made|issued))"
)

ANNUAL_REVIEW = ReadingCase(
    "school-plan-annual-review",
    "What is the EHCP annual review process for autism?",
    search_hit(
        "EHCP annual review",
        "https://www.gov.uk/children-with-special-educational-needs",
        text=(
            "An Education, Health and Care plan (EHCP) is reviewed at least once a year. "
            "The annual review checks whether the support still meets the child's needs. "
            "The school invites parents to share how the child is getting on and what support "
            "they need. The school can recommend changes, but the local authority decides "
            "whether to keep, amend or end the plan."
        ),
    ),
    (
        Essential("The plan is reviewed at least once a year.", (
            r"\b(?:review\w*|check\w*|look(?:ed|s)? at)\b",
            r"\b(?:annually|yearly|once (?:a|per) year|(?:each|every) (?:year|12 months))\b",
        )),
        Essential("The council decides what happens to the plan after the review.", (
            rf"\b(?:{COUNCIL}\b[^;]*\b(?:decides?|makes? (?:the |a )?(?:final )?decision|"
            rf"determines?|chooses?)\b|decision\b[^;]*\b(?:by|rests with) (?:the |your )?{COUNCIL}\b)",
        )),
    ),
    (
        # SEND Regulations 2014, regulation 20(7)-(10): the school reports
        # recommendations; the authority decides on the plan.
        # https://www.legislation.gov.uk/uksi/2014/1530/regulation/20
        Contradiction(
            "The council, not the school, makes the final decision about the plan.",
            r"^(?:(?:the|your|your child's) )?(?:school|headteacher) "
            r"(?:(?:can|could|may|will|must) )?"
            rf"(?:decides? {PLAN_DECISION}\b|(?:makes?|takes?|has|have) the final (?:decision|say)"
            rf"(?:$| (?:on|about) (?:{PLAN}|{PLAN_DECISION})\b))",
        ),
    ),
)

DLA_CLAIM = ReadingCase(
    "disability-benefit-claim",
    "How do I apply for DLA for my autistic child in England?",
    search_hit(
        "Disability Living Allowance for children",
        "https://www.gov.uk/disability-living-allowance-children/how-to-claim",
        text=(
            "Disability Living Allowance (DLA) can help with the extra costs of looking after "
            "a child who needs more care or has more difficulty walking than a child of the "
            "same age. In England and Wales, download the claim form or ask the Department for "
            "Work and Pensions (DWP) to send you a printed copy. You can fill it in on a computer "
            "or by hand. Return the completed claim form by post."
        ),
    ),
    (
        Essential("Eligibility depends on extra care needs or walking difficulties compared with children the same age.", (
            r"\b(?:care|look(?:ing)? after|supervision|help with (?:daily|everyday|personal))\b",
            r"\b(?:walk\w*|mobility|moving around|get(?:ting)? around)\b",
            r"\b(?:or|either)\b",
            r"\b(?:more|extra|additional|greater|higher)\b",
            r"\b(?:(?:same|similar|their|that|comparable)[ -]age|age[ -]matched|"
            r"similarly aged|(?:aged|age) peers)\b",
        )),
        Essential("Send the claim form back by post.", (
            r"\b(?:(?:post|mail)(?:ing)? (?:it|(?:your |the )?(?:filled[ -]in |completed )?"
            r"(?:claim|application|form))|(?:return|send|submit|put)\w*\b[^;]*"
            r"\b(?:by (?:post(?:al mail)?|mail)|through the post|in the (?:post|mail)|via (?:post|mail)))\b",
        )),
    ),
    (
        # GOV.UK's claim form guidance distinguishes filling in the PDF from
        # sending the completed form to Freepost DWP DLA Child.
        # https://www.gov.uk/government/publications/disability-living-allowance-for-children-claim-form
        Contradiction(
            "In England and Wales, send the completed child DLA claim form by post, not online.",
            r"^(?:(?:alternatively|instead|also),? )?(?:"
            r"(?:you (?:can|could|may|should|must) (?:also )?)?"
            rf"(?:(?:post {CLAIM_FORM}|(?:send|return) {CLAIM_FORM} by post),? or )?"
            rf"(?:(?:submit|upload|send|return) {CLAIM_FORM} {ONLINE}\b"
            r"|apply online(?: for (?:child(?:ren's)? )?dla)?(?: instead| as well)?$)"
            rf"|{CLAIM_FORM} (?:can|could|may|should|must) (?:also )?be "
            rf"(?:submitted|uploaded|sent|returned) {ONLINE}\b)",
        ),
    ),
)

ASSESSMENT_REFERRAL = ReadingCase(
    "autism-assessment-referral",
    "Can I ask my GP about an autism assessment for my child?",
    search_hit(
        "How to get an autism assessment",
        "https://www.nhs.uk/conditions/autism/getting-diagnosed/how-to-get-diagnosed/",
        publisher="NHS",
        text=(
            "A general practitioner (GP) can refer a child for an autism assessment. "
            "Autism spectrum disorder (ASD) is another name for autism. Parents can "
            "describe what they have noticed at home and share examples from school. "
            "A specialist assessment team gathers information about the child's "
            "development and how they communicate and interact with other people."
        ),
    ),
    (
        Essential("A GP or family doctor can make a referral for an autism assessment.", (
            rf"\b(?:{DOCTOR}\b[^;]*\b(?:(?:can|may|could|will) (?:refer|"
            rf"send (?:your child|them) (?:for|to))|"
            rf"(?:can|may|could|will) (?:make|arrange|give) (?:you )?a referral|"
            rf"(?:for|about|to make) (?:a |the )?referral|to refer)|"
            rf"referr?(?:al|ed)\b[^;]*\b(?:from|by|through) (?:a |the |your |their )?{DOCTOR})\b",
            r"\b(?:assessment|evaluation)\b",
        )),
        Essential("Tell the doctor what you have noticed or give concrete examples.", (
            r"\b(?:tell|discuss|shar\w*|describ\w*|explain|bring|giv\w*|"
            r"writ\w* down|tak\w* (?:along )?)\b",
            r"\b(?:examples?|observations?|notic\w*|concerns?|signs?|what (?:you|they)(?:'ve| have)? seen)\b",
        )),
    ),
)

PIP_RECONSIDERATION = ReadingCase(
    "pip-mandatory-reconsideration",
    "My Personal Independence Payment (PIP) claim was turned down. What should I do first, and how long do I have?",
    search_hit(
        "Challenge a benefit decision: How to ask for mandatory reconsideration",
        "https://www.gov.uk/mandatory-reconsideration/how-to-ask-for-mandatory-reconsideration",
        text=(
            "If you disagree with a Personal Independence Payment (PIP) decision, ask the benefits "
            "office that made the decision to look at it again. This is called mandatory reconsideration. "
            "You can ask by phone, by letter, or by returning the mandatory reconsideration form. "
            "The contact details are on your decision letter. You usually need to ask for mandatory "
            "reconsideration within one month of the date on your decision letter. If you write, "
            "the letter or form must arrive by then. You can ask after one month if you have a "
            "good reason, but you must explain why your request is late."
        ),
    ),
    (
        Essential("Ask for mandatory reconsideration: ask the benefits office to look at the decision again.", (
            r"\b(?:ask(?:ing)? for|request(?:ing)?|apply(?:ing)? for) (?:a |the )?mandatory reconsideration\b"
            rf"|\b(?:ask(?:ing)?|request(?:ing)?|tell)\b[^;]*\b(?:"
            rf"(?:review|reconsider) (?:of )?{PIP_DECISION}"
            rf"|(?:look at|check) {PIP_DECISION} again|look again at {PIP_DECISION}"
            rf"|(?:take|have) another look at {PIP_DECISION}"
            rf"|{PIP_DECISION} to be (?:reviewed|reconsidered|looked at again))\b",
        )),
        Essential("Usually ask within one month of the date on the decision letter.", (
            # Count from the decision date, not when the claimant receives the letter.
            rf"\b(?:within |no later than |have |has |is |by )(?:a|one|1) (?:calendar )?month "
            rf"(?:of|from|after) (?:the )?{PIP_DECISION_DATE}\b",
        )),
    ),
)

READING_CASES = (ANNUAL_REVIEW, DLA_CLAIM, ASSESSMENT_REFERRAL, PIP_RECONSIDERATION)


def _answer_statements(answer: str) -> list[str]:
    # Match answer prose, not section titles, citations, or bare link labels.
    prose = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+.*$", "", answer)
    prose = re.sub(r"\[[^\]\n]+\]\(https?://[^\s)]+\)|<?https?://[^\s>]+>?", "", prose)
    prose = prose.replace("*", "").replace("_", "").replace("’", "'").casefold()
    return re.split(r"[.!?]+(?=\s|$)|\n+", prose)


def missing_essentials(case: ReadingCase, answer: str) -> list[str]:
    return [point.description for point in case.essentials if not point.appears_in(_answer_statements(answer))]


def _contradictions(case: ReadingCase, answer: str) -> list[str]:
    # Look for explicit affirmative advice at clause starts, not a keyword
    # anywhere in the answer (which would match "do not submit it online").
    clauses = [
        re.sub(r"^(?:[-+•]|\d+[.)])\s+", "", clause.strip())
        for statement in _answer_statements(answer)
        for clause in re.split(r";|\b(?:but|however)\b,?\s*", statement)
    ]
    return [
        point.description for point in case.contradictions
        if any(re.search(point.pattern, clause) for clause in clauses)
    ]


def assert_essential_advice(case: ReadingCase, *, clear: str, complex_answer: str) -> None:
    """Check that both replies keep the essentials and don't contradict them."""
    answers = (("Clear", clear), ("Complex", complex_answer))
    missing = [
        f"{level}: {point}"
        for level, answer in answers
        for point in missing_essentials(case, answer)
    ]
    conflicts = [
        f"{level}: {point}"
        for level, answer in answers
        for point in _contradictions(case, answer)
    ]
    findings = []
    if missing:
        findings.append("essential advice missing or not recognised:\n" + "\n".join(f"- {item}" for item in missing))
    if conflicts:
        findings.append("advice contradicts the fixed source:\n" + "\n".join(f"- {item}" for item in conflicts))
    assert not findings, f"Live answer content regression ({case.id}): " + "\n".join(findings)
