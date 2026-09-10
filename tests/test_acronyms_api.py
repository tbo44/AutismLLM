"""Acronym mutation API contracts, using real JSON I/O in a temporary directory."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


VALID_TOKEN = "acronyms-test-admin-token"
INITIAL_GLOSSARY = {"GP": "General Practitioner"}


@pytest.fixture
def glossary_path(tmp_path, monkeypatch):
    path = tmp_path / "data" / "acronyms.json"
    path.parent.mkdir()
    path.write_text(json.dumps(INITIAL_GLOSSARY), encoding="utf-8")
    monkeypatch.setattr(main, "_ACRONYMS_PATH", path)
    return path


@pytest.fixture
def client(monkeypatch, glossary_path):
    monkeypatch.setattr(main, "_ADMIN_TOKEN", VALID_TOKEN)
    # Like test_admin.py, omit the lifespan context to avoid starting RAG/jobs.
    api = TestClient(main.app)
    yield api
    api.close()


def credentials(client, channel, token):
    if channel == "query":
        return {"params": {"token": token}}
    if channel == "header":
        return {"headers": {"X-Admin-Token": token}}
    client.cookies.set(main._ADMIN_COOKIE_NAME, token)
    return {}


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize("channel", ["missing", "query", "header", "cookie"])
def test_mutations_reject_unauthenticated_requests(
    client, glossary_path, method, channel
):
    before = glossary_path.read_bytes()
    auth = {} if channel == "missing" else credentials(client, channel, "wrong-token")
    path = "/api/acronyms" if method == "POST" else "/api/acronyms/GP"
    body = (
        {"json": {"key": "GP", "definition": "Unauthorized change"}}
        if method != "DELETE"
        else {}
    )

    response = client.request(method, path, **body, **auth)

    assert response.status_code == (401 if channel == "missing" else 403)
    assert glossary_path.read_bytes() == before


@pytest.mark.parametrize(
    "key,definition",
    [
        ("", "Valid definition"),
        (" \t\n", "Valid definition"),
        ("GP", ""),
        ("GP", " \t\n"),
    ],
)
def test_post_rejects_empty_fields(client, glossary_path, key, definition):
    before = glossary_path.read_bytes()

    response = client.post(
        "/api/acronyms",
        headers={"X-Admin-Token": VALID_TOKEN},
        json={"key": key, "definition": definition},
    )

    assert response.status_code == 422
    assert glossary_path.read_bytes() == before


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_missing_entry_returns_404(client, glossary_path, method):
    before = glossary_path.read_bytes()
    body = (
        {"json": {"key": "MISSING", "definition": "Valid definition"}}
        if method == "PUT"
        else {}
    )

    response = client.request(
        method,
        "/api/acronyms/MISSING",
        headers={"X-Admin-Token": VALID_TOKEN},
        **body,
    )

    assert response.status_code == 404
    assert glossary_path.read_bytes() == before


@pytest.mark.parametrize("channel", ["query", "header", "cookie"])
def test_add_update_delete_persists_each_change(client, glossary_path, channel):
    auth = credentials(client, channel, VALID_TOKEN)
    definition = "Education, Health and Care Plan"
    updated_definition = "A child’s updated education, health and care plan"

    added = client.post(
        "/api/acronyms",
        json={"key": " ehcp ", "definition": f"  {definition}  "},
        **auth,
    )
    assert added.status_code == 201
    assert added.json() == {"key": "EHCP", "definition": definition}
    assert json.loads(glossary_path.read_text(encoding="utf-8")) == {
        **INITIAL_GLOSSARY,
        "EHCP": definition,
    }

    updated = client.put(
        "/api/acronyms/ehcp",
        json={"key": "EHCP", "definition": f"  {updated_definition}  "},
        **auth,
    )
    assert updated.status_code == 200
    assert updated.json() == {"key": "EHCP", "definition": updated_definition}
    assert json.loads(glossary_path.read_text(encoding="utf-8")) == {
        **INITIAL_GLOSSARY,
        "EHCP": updated_definition,
    }

    deleted = client.delete("/api/acronyms/ehcp", **auth)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "EHCP"}
    assert json.loads(glossary_path.read_text(encoding="utf-8")) == INITIAL_GLOSSARY


def mutate(client, method):
    path = "/api/acronyms" if method == "POST" else "/api/acronyms/GP"
    body = {"json": {"key": "GP", "definition": "Changed"}} if method != "DELETE" else {}
    return client.request(method, path, headers={"X-Admin-Token": VALID_TOKEN}, **body)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize("content", [
    b'{"GP": "unfinished', b"\xff\xfe", b"[]", b"null", b'{"GP": 42}',
])
def test_damaged_glossary_preserved(client, glossary_path, method, content):
    glossary_path.write_bytes(content)
    response = mutate(client, method)
    assert response.status_code == 503
    assert "No changes were saved" in response.json()["detail"]
    assert glossary_path.read_bytes() == content
    assert list(glossary_path.parent.iterdir()) == [glossary_path]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_unreadable_glossary_preserved(client, glossary_path, monkeypatch, method):
    before = glossary_path.read_bytes()
    original_read = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == glossary_path:
            raise PermissionError("Access denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    response = mutate(client, method)
    assert response.status_code == 503
    assert glossary_path.read_bytes() == before


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_failed_atomic_save_preserves_original(
    client, glossary_path, monkeypatch, method, failure_point,
):
    before = glossary_path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("Simulated interrupted save")

    monkeypatch.setattr(main.os, failure_point, fail)
    response = mutate(client, method)
    assert response.status_code == 503
    assert "original file has not been changed" in response.json()["detail"]
    assert glossary_path.read_bytes() == before
    assert list(glossary_path.parent.iterdir()) == [glossary_path]


def test_save_replaces_only_after_complete_json_is_written(client, glossary_path, monkeypatch):
    before = glossary_path.read_bytes()
    original_replace = main.os.replace
    replacements = []

    def verify_replace(source, target):
        assert Path(source).parent == glossary_path.parent
        assert Path(source) != glossary_path
        assert Path(target) == glossary_path
        assert glossary_path.read_bytes() == before
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"GP": "Changed"}
        replacements.append(source)
        original_replace(source, target)

    monkeypatch.setattr(main.os, "replace", verify_replace)
    assert mutate(client, "PUT").status_code == 200
    assert len(replacements) == 1
    assert json.loads(glossary_path.read_text(encoding="utf-8")) == {"GP": "Changed"}
    assert list(glossary_path.parent.iterdir()) == [glossary_path]


def test_missing_glossary_can_be_created(client, glossary_path):
    glossary_path.unlink()
    assert mutate(client, "POST").status_code == 201
    assert json.loads(glossary_path.read_text(encoding="utf-8")) == {"GP": "Changed"}


def test_public_read_reports_corruption_instead_of_empty_glossary(client, glossary_path):
    glossary_path.write_bytes(b"invalid-json")
    assert client.get("/api/acronyms").status_code == 503
    assert glossary_path.read_bytes() == b"invalid-json"