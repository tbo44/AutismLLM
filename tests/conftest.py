import pytest

from tests.llm_test_helpers import StubbedChat


@pytest.fixture
def chat_stub(monkeypatch):
    """Use an isolated provider stub only in tests that request it."""
    return StubbedChat(monkeypatch)
