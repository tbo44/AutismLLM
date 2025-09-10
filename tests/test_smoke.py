import pytest
from fastapi.testclient import TestClient
from app.main import app

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

def test_chat_basic():
    """Test the chat endpoint with a basic question"""
    resp = client.post("/chat", json={"question": "What is autism?"})
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "timestamp" in data
    assert "disclaimer" in data
    assert "NHS 111" in data["disclaimer"]