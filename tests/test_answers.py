"""Exercise /chat behavior with scripted provider completions and search hits.

The route, RAG pipeline, guardrails, retriever, and prompt construction are real.
Model output quality is not measured by canned prose; these tests check what the
app sends to the provider and how it handles the provider's response.
"""

import asyncio
from threading import Event
from unittest.mock import Mock, call

import httpx
import pytest

from tests.llm_answer_essentials import PIP_RECONSIDERATION
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
    PIP_RECONSIDERATION.question,
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


@pytest.fixture
def uninitialized_system(chat_stub, monkeypatch):
    from app import main

    system = main._rag_system
    system.initialized = False
    system.vector_store.embedder = Mock()
    monkeypatch.setattr(main, "_rag_system", None)
    monkeypatch.setattr(main, "_startup_complete", False)
    monkeypatch.setattr("rag.rag_system.get_rag_system", lambda: system)
    return system


def _assert_uk_crisis_guidance(data):
    for fragment in ("call 999", "go to A&E", "NHS urgent mental health help", "Samaritans on 116 123"):
        assert fragment in data["answer"]
    assert data["sources"] == []
    assert data["timestamp"].startswith("Last checked: ")
    assert data["disclaimer"]


@pytest.mark.parametrize(("advice_request", "disclaimer", "crisis_signal"), [
    pytest.param(
        "Should I stop taking my medication?",
        "I can't provide personalised clinical advice",
        "I'm feeling suicidal and don't know what to do.",
        id="medication-and-crisis",
    ),
    pytest.param(
        "Can I sue the school for not providing support?",
        "I can't give case-specific legal advice",
        "I'm in crisis and feel unable to stay safe.",
        id="legal-advice-and-crisis",
    ),
])
def test_mixed_crisis_message_prioritizes_urgent_guidance(
    chat_stub, advice_request, disclaimer, crisis_signal,
):
    # Verify the non-crisis part really triggers the competing guardrail.
    assert chat_stub.ask(advice_request)["answer"].startswith(disclaimer)

    data = chat_stub.ask(f"{advice_request} {crisis_signal}")

    assert data["answer"].startswith("If you're in crisis")
    _assert_uk_crisis_guidance(data)
    chat_stub.create.assert_not_called()
    chat_stub.search.assert_not_called()


def test_chat_returns_crisis_guidance_while_initialization_is_blocked(chat_stub, uninitialized_system):
    from app import main

    ordinary_answer = "## Short Answer\nAsk about an EHCP at school."

    async def scenario():
        init_started = asyncio.Event()
        release_init = Event()
        loop = asyncio.get_running_loop()

        def initialize():
            loop.call_soon_threadsafe(init_started.set)
            if not release_init.wait(timeout=5):
                pytest.fail("Knowledge base initialization was not released")
            uninitialized_system.initialized = True

        uninitialized_system.initialize = Mock(side_effect=initialize)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as api:
            startup = asyncio.create_task(main.initialize_rag_background())
            ordinary = None
            try:
                await asyncio.wait_for(init_started.wait(), timeout=2)
                ordinary = asyncio.create_task(api.post("/chat", json={"question": "What is an EHCP?"}))
                crisis = await asyncio.wait_for(api.post("/chat", json={
                    "question": "I feel suicidal. What should I do?",
                }), timeout=2)

                assert crisis.status_code == 200
                _assert_uk_crisis_guidance(crisis.json())
                assert not startup.done()
                assert not ordinary.done()
                chat_stub.create.assert_not_called()
                chat_stub.search.assert_not_called()
                chat_stub.queue_answer(
                    ordinary_answer,
                    search_hit("EHCP", "https://www.gov.uk/children-with-special-educational-needs"),
                    query="Education Health and Care Plan",
                )
            finally:
                release_init.set()
                await asyncio.gather(
                    startup, *([ordinary] if ordinary is not None else []), return_exceptions=True,
                )

            response = ordinary.result()
            assert response.status_code == 200
            assert response.json()["answer"] == ordinary_answer
            assert main._startup_complete
            uninitialized_system.initialize.assert_called_once_with()
            assert chat_stub.create.call_count == 3

    asyncio.run(scenario())


def test_chat_returns_crisis_guidance_after_initialization_fails(chat_stub, uninitialized_system):
    from app import main

    uninitialized_system.initialize = Mock(side_effect=RuntimeError("Knowledge base unavailable"))
    asyncio.run(main.initialize_rag_background())
    assert not main._startup_complete
    uninitialized_system.initialize.assert_called_once_with()

    _assert_uk_crisis_guidance(chat_stub.ask("I feel suicidal. What should I do?"))
    uninitialized_system.initialize.assert_called_once_with()

    ordinary = chat_stub.ask("What is an EHCP?")
    assert ordinary["answer"] == "Sorry, I encountered an error. Please try your question again."
    assert uninitialized_system.initialize.call_args_list == [call(), call()]
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
