"""The cache detail list follows the same summary as the count and crawl action."""
import json
import re
import subprocess
from pathlib import Path

import app.main as main


def test_cache_details_render_and_refresh():
    url = 'https://example.com/page?q=<script>alert("x")</script>'
    cache = {
        "total_entries": 2, "expiring_entries": 1, "within_days": 3,
        "expiring_urls": [url],
        "expiring_pages": [{"url": url, "expires_at": "2026-09-12T12:00:00+00:00"}],
    }
    html = main._render_admin_html(
        [], {"top_sources": [], "questions_7d": 0, "total_questions": 0},
        {"cache": cache},
    )
    assert '<details class="cache-details" id="cacheExpiryDetails">' in html
    assert 'id="cacheDetailCount">1</span>' in html
    assert "12 Sep 2026 13:00 BST" in html
    assert "&lt;script&gt;" in html
    assert url not in html

    empty = {**cache, "expiring_entries": 0, "expiring_urls": [], "expiring_pages": []}
    script, = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL)
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("admin_history_runner.cjs"))],
        input=json.dumps({"script": script, "cacheSnapshots": [cache, empty]}),
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    populated, cleared = json.loads(result.stdout)
    assert populated["count"] == populated["detailCount"] == "1"
    assert populated["window"] == "3"
    assert populated["disabled"] is False
    assert "&lt;script&gt;" in populated["html"]
    assert "12 Sept 2026" in populated["html"] or "12 Sep 2026" in populated["html"]
    assert "13:00" in populated["html"]
    assert cleared["count"] == cleared["detailCount"] == "0"
    assert cleared["disabled"] is True
    assert "No cached pages due to expire" in cleared["html"]
    assert "example.com" not in cleared["html"]