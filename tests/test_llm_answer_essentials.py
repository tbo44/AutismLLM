"""Exercise the live answer checks offline with correct, incomplete and conflicting advice."""

import pytest

from tests.llm_answer_essentials import (
    ANNUAL_REVIEW,
    ASSESSMENT_REFERRAL,
    DLA_CLAIM,
    READING_CASES,
    assert_essential_advice,
    missing_essentials,
)
from tests.llm_reading_checks import check_reading_pair


@pytest.mark.parametrize("case", READING_CASES, ids=lambda case: case.id)
def test_source_passage_supports_every_essential(case):
    assert not missing_essentials(case, case.hit["text"])
    assert_essential_advice(case, clear=case.hit["text"], complex_answer=case.hit["text"])


@pytest.mark.parametrize(("case", "clear", "complex_answer"), [
    (
        ANNUAL_REVIEW,
        "Your child's plan gets checked **every year**. Your council chooses what happens next.",
        "The plan undergoes a review no less frequently than every 12 months. "
        "The final decision on whether to retain, modify, or discontinue it rests with the local authority.",
    ),
    (
        DLA_CLAIM,
        "This benefit is for kids who need more looking after or have a harder time getting around "
        "than other kids their age. Fill in the form, then put it in the post.",
        "Eligibility depends on whether a child has additional care or mobility needs "
        "relative to age-matched peers. Submit the completed claim form via post.",
    ),
    (
        ASSESSMENT_REFERRAL,
        "Your family doctor can send your child for an autism assessment. "
        "Tell them what you've seen at home.",
        "A referral for an autism assessment can be arranged through a general practitioner. "
        "Bring examples of your child's interactions at school to the appointment.",
    ),
], ids=["school-plan-annual-review", "disability-benefit-claim", "autism-assessment-referral"])
def test_complete_paraphrases_pass_at_both_levels(case, clear, complex_answer):
    assert_essential_advice(case, clear=clear, complex_answer=complex_answer)


@pytest.mark.parametrize("level", ["Clear", "Complex"])
def test_fluent_reply_without_same_age_caveat_fails_at_either_level(level):
    incomplete = (
        "## Short Answer\nThis benefit can help if your child needs extra care or has trouble walking.\n"
        "## Steps\nGet the form. Fill it in. Send it back by post."
    )
    complete = incomplete.replace("trouble walking.", "more trouble walking than other children their age.")

    with pytest.raises(AssertionError) as exc:
        assert_essential_advice(
            DLA_CLAIM,
            clear=incomplete if level == "Clear" else complete,
            complex_answer=incomplete if level == "Complex" else complete,
        )

    assert str(exc.value).splitlines()[1] == (
        f"- {level}: Eligibility depends on extra care needs or walking difficulties "
        "compared with children the same age."
    )
    assert f"- {'Complex' if level == 'Clear' else 'Clear'}:" not in str(exc.value)


@pytest.mark.parametrize(("case", "answer", "missing"), [
    (
        ANNUAL_REVIEW,
        "The plan gets checked. Your council decides what happens next. A new school year is a good time to ask.",
        "The plan is reviewed at least once a year.",
    ),
    (
        ANNUAL_REVIEW,
        "The plan is checked yearly. The school decides what happens next and tells the council.",
        "The council decides what happens to the plan after the review.",
    ),
    (
        DLA_CLAIM,
        "Extra care or mobility needs compared with children the same age may qualify. "
        "Download the form and fill it in.\n"
        "## How to send it back by post\n[Send your claim form by post](https://example.test/claim)",
        "Send the claim form back by post.",
    ),
    (
        ASSESSMENT_REFERRAL,
        "You can talk to your family doctor about autism. Describe what you've noticed at home.",
        "A GP or family doctor can make a referral for an autism assessment.",
    ),
    (
        ASSESSMENT_REFERRAL,
        "Your doctor can refer your child for an autism assessment. Ask how long the wait might be.",
        "Tell the doctor what you have noticed or give concrete examples.",
    ),
])
def test_each_missing_step_has_a_specific_diagnostic(case, answer, missing):
    assert missing_essentials(case, answer) == [missing]


DLA_ELIGIBILITY = "Your child must need more care or help to walk than others of the same age."
ANNUAL_REVIEW_FREQUENCY = "The plan is checked every year."


@pytest.mark.parametrize("level", ["Clear", "Complex"])
@pytest.mark.parametrize(("case", "answer", "diagnostic"), [
    pytest.param(
        DLA_CLAIM,
        f"{DLA_ELIGIBILITY} Post the completed form, or submit it online instead.",
        "send the completed child DLA claim form by post, not online",
        id="online-submission-as-alternative",
    ),
    pytest.param(
        DLA_CLAIM,
        f"{DLA_ELIGIBILITY}\n- Send the completed form by post.\n"
        "- The completed claim form can also be submitted online.",
        "send the completed child DLA claim form by post, not online",
        id="online-submission-as-separate-step",
    ),
    pytest.param(
        ANNUAL_REVIEW,
        f"{ANNUAL_REVIEW_FREQUENCY} The council decides what happens to the plan, "
        "but the school can decide whether to keep, change or end it.",
        "The council, not the school, makes the final decision about the plan",
        id="school-as-final-decision-maker",
    ),
])
def test_conflicting_advice_fails_even_when_all_essentials_are_present(level, case, answer, diagnostic):
    assert not missing_essentials(case, answer)

    with pytest.raises(AssertionError) as exc:
        check_reading_pair(
            case,
            clear=answer if level == "Clear" else case.hit["text"],
            complex_answer=answer if level == "Complex" else case.hit["text"],
        )

    message = str(exc.value)
    assert f"({case.id}): advice contradicts the fixed source:" in message
    assert message.splitlines()[1].startswith(f"- {level}: ")
    assert diagnostic in message
    assert len(message.splitlines()) == 2


@pytest.mark.parametrize(("case", "clear", "complex_answer"), [
    pytest.param(
        DLA_CLAIM,
        f"{DLA_ELIGIBILITY} Download the form or call to ask for a copy. "
        "Send the completed form by post. Do not submit it online.",
        "Eligibility depends on whether a child has additional care or mobility needs relative to age-matched peers. "
        "You can complete the downloaded form electronically or request and complete a paper copy, "
        "but you cannot submit the form online; return the completed claim form by post.",
        id="download-or-request-form",
    ),
    pytest.param(
        ANNUAL_REVIEW,
        f"{ANNUAL_REVIEW_FREQUENCY} The school can suggest changes. "
        "The council decides whether to keep, change or end the plan.",
        "The plan undergoes a review every 12 months, with the school gathering information and recommending changes. "
        "The school does not make the final decision about the plan; the local authority determines "
        "whether to maintain, amend, or end it.",
        id="school-recommends-council-decides",
    ),
])
def test_correct_explanations_of_alternatives_pass(case, clear, complex_answer):
    check_reading_pair(case, clear=clear, complex_answer=complex_answer)
