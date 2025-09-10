import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

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

def test_static_html_from_root():
    """Test that root serves the HTML file"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    # Check HTML contains expected elements
    assert "Maya - UK Autism Facts Assistant" in resp.text
    assert "disclaimer-banner" in resp.text