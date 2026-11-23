"""Test data and wiring for isolated /chat integration checks."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


def completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def search_hit(title, url, *, publisher="Gov.UK", text=None):
    return {
        "text": text or title,
        "distance": 0.1,
        "metadata": {"title": title, "url": url, "source_name": publisher, "authority": 1},
    }


def chat_client(monkeypatch, llm_client, search):
    """Install a fresh pipeline without touching the workspace index or question logs."""
    from app import main
    from rag.rag_system import UKAutismRAGSystem
    from rag.retriever import UKAutismRetriever

    system = UKAutismRAGSystem()
    system.initialized = True
    system.llm_client = llm_client
    system.vector_store = SimpleNamespace(search=search)
    system.retriever = UKAutismRetriever(system.vector_store, llm_client)
    monkeypatch.setattr(main, "_rag_system", system)
    monkeypatch.setattr(main, "_log_question", lambda *_: None)
    return TestClient(main.app)


class StubbedChat:
    def __init__(self, monkeypatch):
        from rag import llm_client

        self.create = Mock(side_effect=lambda **_: pytest.fail("Unexpected provider call"))
        self.search = Mock(side_effect=lambda *_, **__: pytest.fail("Unexpected vector search"))
        provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))
        monkeypatch.setattr(llm_client, "_build_openai_client", lambda: provider)
        self.client = chat_client(monkeypatch, llm_client.UKAutismLLMClient(), self.search)

    def queue_answer(self, answer, hit, *, query):
        # Moderation allows the question, enhancement supplies the search text,
        # then synthesis returns the one expected answer.
        self.create.side_effect = [
            completion('{"appropriate": true, "reason": "", "category": "autism-related"}'),
            completion(query),
            completion(answer),
        ]
        self.search.side_effect = None
        self.search.return_value = [hit]

    def ask(self, question, **params):
        response = self.client.post("/chat", json={"question": question, **params})
        assert response.status_code == 200, (
            f"Expected 200 from /chat, got {response.status_code}: {response.text}"
        )
        return response.json()