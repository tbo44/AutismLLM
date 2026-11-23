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
class ReadingCase:
    id: str
    question: str
    hit: dict
    essentials: tuple[Essential, ...]


COUNCIL = r"(?:local (?:education )?authority|(?:local )?council)"
DOCTOR = r"(?:gp|general practitioner|(?:family )?doctor)"

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
            "they need. The local authority decides whether to keep, amend or end the plan."
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
)

DLA_CLAIM = ReadingCase(
    "disability-benefit-claim",
    "How do I apply for DLA for my autistic child?",
    search_hit(
        "Disability Living Allowance for children",
        "https://www.gov.uk/disability-living-allowance-children/how-to-claim",
        text=(
            "Disability Living Allowance (DLA) can help with the extra costs of looking after "
            "a child who needs more care or has more difficulty walking than a child of the "
            "same age. To apply, download the claim form or ask the Department for Work and "
            "Pensions (DWP) to send you a copy. Fill it in with details of the child's "
            "care or mobility needs and return it by post."
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

READING_CASES = (ANNUAL_REVIEW, DLA_CLAIM, ASSESSMENT_REFERRAL)


def missing_essentials(case: ReadingCase, answer: str) -> list[str]:
    # Match answer prose, not section titles, citations, or bare link labels.
    prose = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+.*$", "", answer)
    prose = re.sub(r"\[[^\]\n]+\]\(https?://[^\s)]+\)|<?https?://[^\s>]+>?", "", prose)
    prose = prose.replace("*", "").replace("_", "").replace("’", "'").casefold()
    statements = re.split(r"[.!?]+(?=\s|$)|\n+", prose)
    return [point.description for point in case.essentials if not point.appears_in(statements)]


def assert_essential_advice(case: ReadingCase, *, clear: str, complex_answer: str) -> None:
    missing = [
        f"{level}: {point}"
        for level, answer in (("Clear", clear), ("Complex", complex_answer))
        for point in missing_essentials(case, answer)
    ]
    assert not missing, (
        f"Live answer content regression ({case.id}): essential advice missing or not recognised:\n"
        + "\n".join(f"- {item}" for item in missing)
    )
