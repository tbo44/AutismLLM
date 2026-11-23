"""Exercise the live answer checks offline with complete and incomplete paraphrases."""

import pytest

from tests.llm_answer_essentials import (
    ANNUAL_REVIEW,
    ASSESSMENT_REFERRAL,
    DLA_CLAIM,
    READING_CASES,
    assert_essential_advice,
    missing_essentials,
)


@pytest.mark.parametrize("case", READING_CASES, ids=lambda case: case.id)
def test_source_passage_supports_every_essential(case):
    assert not missing_essentials(case, case.hit["text"])


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
