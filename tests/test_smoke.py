from fastapi.testclient import TestClient
from app.main import app
from tests.llm_test_helpers import search_hit

client = TestClient(app)

def test_health():
    """Test the health endpoint"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_root():
    """Test the root endpoint serves HTML"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    # Check that it returns some HTML content
    assert len(resp.content) > 0

def test_chat_basic(chat_stub):
    """Test the chat endpoint with a basic question"""
    answer = "## Short Answer\nAutism is a lifelong developmental condition."
    hit = search_hit("Autism", "https://www.nhs.uk/conditions/autism/", publisher="NHS")
    chat_stub.queue_answer(answer, hit, query="autism overview")

    data = chat_stub.ask("What is autism?")

    assert data["answer"] == answer
    assert "timestamp" in data
    assert "disclaimer" in data
    assert "NHS 111" in data["disclaimer"]