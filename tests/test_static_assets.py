from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"
TITLE_PATTERN = re.compile(r"<title>(?P<title>.*?)</title>", re.DOTALL | re.IGNORECASE)


@pytest.fixture(scope="module")
def homepage_title():
    match = TITLE_PATTERN.search(INDEX_HTML.read_text(encoding="utf-8"))
    title = match.group("title").strip() if match else ""
    assert title, f"No <title> found in {INDEX_HTML}"
    return title

def test_static_css_loads():
    """Test that CSS file loads correctly"""
    resp = client.get("/static/styles.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/css; charset=utf-8"
    # Check it contains some expected CSS
    assert "body" in resp.text
    assert "font-family" in resp.text

def test_static_js_loads():
    """Test that JavaScript file loads correctly"""
    resp = client.get("/static/script.js")
    assert resp.status_code == 200
    content_type = resp.headers["content-type"]
    assert "javascript" in content_type
    # Check it contains expected JS
    assert "MayaApp" in resp.text
    assert "sendMessage" in resp.text

def test_static_html_from_root(homepage_title):
    """Test that root serves the HTML file"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    # Check HTML contains expected elements
    assert f"<title>{homepage_title}</title>" in resp.text
    assert "Low-stimulation mode" in resp.text
