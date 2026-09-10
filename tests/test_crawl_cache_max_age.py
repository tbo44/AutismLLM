"""Tests for CrawlCache max-age expiry."""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.crawler import CrawlCache, UKAutismCrawler
from rag.sources import Source, SourceAuthority


def _write_cache_entry(raw_dir: Path, url: str, cached_at: datetime) -> dict:
    entry = {
        "etag": '"test-etag"',
        "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        "content_hash": "deadbeef",
        "cached_at": cached_at.isoformat(),
        "chunks": [{"text": "cached content", "metadata": {"url": url}}],
    }
    (raw_dir / "crawl_cache.json").write_text(
        json.dumps({url: entry}),
        encoding="utf-8",
    )
    return entry


def test_env_max_age_expires_entry_older_than_limit(tmp_path, monkeypatch):
    max_age_days = 7
    monkeypatch.setenv("CRAWL_CACHE_MAX_AGE_DAYS", str(max_age_days))
    url = "https://example.com/expired"
    _write_cache_entry(
        tmp_path,
        url,
        datetime.now(timezone.utc) - timedelta(days=max_age_days + 1),
    )

    cache = CrawlCache(raw_dir=str(tmp_path))

    assert cache.max_age_days == max_age_days
    assert cache.get(url) is None


def test_env_max_age_returns_recent_entry(tmp_path, monkeypatch):
    max_age_days = 7
    monkeypatch.setenv("CRAWL_CACHE_MAX_AGE_DAYS", str(max_age_days))
    url = "https://example.com/recent"
    expected = _write_cache_entry(
        tmp_path,
        url,
        datetime.now(timezone.utc) - timedelta(days=1),
    )

    cache = CrawlCache(raw_dir=str(tmp_path))

    assert cache.max_age_days == max_age_days
    assert cache.get(url) == expected


def test_expired_entry_forces_unconditional_full_fetch(tmp_path):
    max_age_days = 7
    url = "https://example.com/expired"
    _write_cache_entry(
        tmp_path,
        url,
        datetime.now(timezone.utc) - timedelta(days=max_age_days + 1),
    )
    cache = CrawlCache(raw_dir=str(tmp_path), max_age_days=max_age_days)
    crawler = UKAutismCrawler(delay=0, cache=cache)
    crawler.session = AsyncMock()
    crawler.session.get.return_value = httpx.Response(
        200,
        text="<html><head><title>Fresh page</title></head><body>Fresh</body></html>",
        request=httpx.Request("GET", url),
    )
    source = Source(
        name="Example",
        base_url="https://example.com",
        authority=SourceAuthority.NATIONAL_CHARITY,
        crawl_paths=["/expired"],
        description="Test source",
    )

    with patch(
        "rag.crawler.trafilatura.extract",
        return_value=" ".join(["fresh"] * 101),
    ):
        document, from_cache = asyncio.run(crawler.crawl_url(url, source))

    crawler.session.get.assert_awaited_once_with(url, headers={})
    assert document is not None
    assert document.title == "Fresh page"
    assert from_cache is False