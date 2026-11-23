"""Exercise /chat behavior with scripted provider completions and search hits.

The route, RAG pipeline, guardrails, retriever, and prompt construction are real.
Model output quality is not measured by canned prose; these tests check what the
app sends to the provider and how it handles the provider's response.
"""

from unittest.mock import call

import pytest

from tests.llm_test_helpers import completion, search_hit


def test_blue_badge_answer_preserves_generated_text_and_source(chat_stub):
    question = "How do I apply for a Blue Badge for my autistic child?"
    answer = "## Short Answer\nYou can apply for a Blue Badge through your council."
    hit = search_hit("Apply for a Blue Badge", "https://www.gov.uk/apply-blue-badge")
    chat_stub.queue_answer(answer, hit, query="Blue Badge application")

    data = chat_stub.ask(question)

    assert data["answer"] == answer
    assert data["sources"] == [{
        "title": "Apply for a Blue Badge",
        "url": "https://www.gov.uk/apply-blue-badge",
        "publisher": "Gov.UK",
    }]
    chat_stub.search.assert_called_once_with("Blue Badge application", n_results=8, authority_boost=True)


def test_ehcp_answer_uses_structured_prompt_and_preserves_sections(chat_stub):
    question = "What is the EHCP annual review process for autism?"
    headings = (
        "## Short Answer", "## Steps", "## Who to Contact", "## Useful Links", "## Important Note",
    )
    answer = (
        "## Short Answer\nAn EHCP is reviewed each year.\n\n"
        "## Steps\n1. Check the current plan.\n\n"
        "## Who to Contact\nYour school's support team.\n\n"
        "## Useful Links\nGov.UK — SEND guidance.\n\n"
        "## Important Note\nThis is general guidance."
    )
    context = "The annual review checks whether the plan still meets the child's needs."
    hit = search_hit("EHCP annual review", "https://www.gov.uk/children-with-special-educational-needs",
                     text=context)
    chat_stub.queue_answer(answer, hit, query="Education Health and Care Plan annual review")

    assert chat_stub.ask(question)["answer"] == answer
    assert chat_stub.create.call_count == 3
    messages = chat_stub.create.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert [line for line in messages[0]["content"].splitlines() if line.startswith("## ")] == [
        *headings, "## Source 1: EHCP annual review",
    ]
    assert context in messages[0]["content"]
    assert question in messages[1]["content"]


@pytest.mark.parametrize("question", [
    "What is the mandatory reconsideration process if my PIP claim is refused?",
    "My PIP claim was turned down — what options do I have?",
    "Can I challenge a PIP decision and what evidence do I need?",
])
def test_pip_appeal_reaches_synthesis_instead_of_a_guardrail(chat_stub, question):
    answer = "## Short Answer\nYou can ask for a mandatory reconsideration of a PIP decision."
    hit = search_hit("Challenge a benefit decision", "https://www.gov.uk/mandatory-reconsideration")
    chat_stub.queue_answer(answer, hit, query="PIP mandatory reconsideration")

    assert chat_stub.ask(question)["answer"] == answer
    assert chat_stub.create.call_count == 3


def test_hounslow_question_searches_locally_and_cites_hounslow_source(chat_stub):
    question = "What autism support services are available in Hounslow?"
    query = "Hounslow autism support"
    answer = "## Short Answer\nHounslow Council lists local support services."
    local_hit = search_hit("Hounslow Local Offer", "https://www.hounslow.gov.uk/",
                           publisher="Hounslow Council")
    chat_stub.queue_answer(answer, local_hit, query=query)
    chat_stub.search.side_effect = [[local_hit], []]

    data = chat_stub.ask(question)

    assert data["answer"] == answer
    assert data["sources"] == [{
        "title": "Hounslow Local Offer",
        "url": "https://www.hounslow.gov.uk/",
        "publisher": "Hounslow Council",
    }]
    assert chat_stub.search.call_args_list == [
        call(query, n_results=4, hounslow_specific=True),
        call(query, n_results=7, hounslow_specific=False),
    ]


@pytest.mark.parametrize(("crisis_message", "expected_fragment"), [
    ("I can't cope anymore and want to hurt myself", "Samaritans"),
    ("I've been feeling suicidal lately", "999"),
    ("I'm in crisis and I don't feel safe", "If you're in crisis"),
])
def test_crisis_message_bypasses_the_provider(chat_stub, crisis_message, expected_fragment):
    data = chat_stub.ask(crisis_message)

    assert expected_fragment in data["answer"]
    assert "If you're in crisis" in data["answer"]
    assert data["sources"] == []
    chat_stub.create.assert_not_called()
    chat_stub.search.assert_not_called()


@pytest.mark.parametrize(("level", "prompt_label", "guideline", "answer"), [
    ("clear", "CLEAR (Simple)", "No acronyms", "## Short Answer\nAsk for a school plan."),
    ("standard", "STANDARD", "Explain jargon the first time you use it.",
     "## Short Answer\nAsk the council to assess your child's support needs."),
    ("complex", "COMPLEX (Detailed)", "Use precise language",
     "## Short Answer\nRequest an Education, Health and Care needs assessment."),
])
def test_comprehension_level_selects_the_provider_instructions(
    chat_stub, level, prompt_label, guideline, answer,
):
    hit = search_hit("Education, Health and Care plans", "https://www.gov.uk/children-with-special-educational-needs")
    chat_stub.queue_answer(answer, hit, query="apply for Education Health and Care plan")

    data = chat_stub.ask("How do I apply for an EHCP for my autistic child?", comprehension_level=level)

    assert data["answer"] == answer
    assert chat_stub.create.call_count == 3
    system_prompt = chat_stub.create.call_args.kwargs["messages"][0]["content"]
    assert [line for line in system_prompt.splitlines() if line.startswith("LANGUAGE LEVEL")] == [
        f"LANGUAGE LEVEL — {prompt_label}:",
    ]
    assert guideline in system_prompt
    if level == "clear":
        assert 'write "autism" not "ASD", "doctor" not "GP", "school plan" not "EHCP"' in system_prompt


@pytest.mark.parametrize("question", [
    "What is the weather like today?",
    "Can you recommend a good football team to support?",
    "What's a tasty recipe for chocolate cake?",
])
def test_off_topic_moderation_returns_redirect_without_retrieval(chat_stub, question):
    chat_stub.create.return_value = completion(
        '{"appropriate": false, "reason": "That is outside autism support.", "category": "off-topic"}'
    )
    chat_stub.create.side_effect = None

    data = chat_stub.ask(question)

    assert data["answer"].startswith("I'm focused on providing information about autism in the UK.")
    assert "That is outside autism support." in data["answer"]
    assert data["sources"] == []
    chat_stub.create.assert_called_once()
    messages = chat_stub.create.call_args.kwargs["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert "content filter" in messages[0]["content"]
    assert question in messages[0]["content"]
    chat_stub.search.assert_not_called()
