"""
Web Crawler for UK Autism Sources
Extracts clean content from trusted sources with metadata
"""

import hashlib
import httpx
import trafilatura
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Iterable, Set, Tuple
import asyncio
import json
import os
from urllib.parse import urljoin, urlparse
import time
import logging
from datetime import datetime
import pytz

from .sources import UK_SOURCES, Source, SourceAuthority

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Crawl cache — persists HTTP caching signals + pre-built chunks per URL so
# that unchanged pages can be skipped on subsequent runs.
# ---------------------------------------------------------------------------

CACHE_FILE_NAME = "crawl_cache.json"


class CrawlCache:
    """
    Disk-backed per-URL cache that stores:
      - etag            : ETag response header value (may be None)
      - last_modified   : Last-Modified response header value (may be None)
      - content_hash    : SHA-256 of the extracted text content
      - cached_at       : ISO-8601 timestamp of when the entry was written
      - chunks          : the pre-built chunk dicts for this URL

    The cache is stored as a single JSON file at ``{raw_dir}/crawl_cache.json``.
    Reads and writes are synchronous; the file is loaded once at construction
    and flushed after each URL update so partial progress is retained even if
    the crawl is interrupted.
    """

    def __init__(self, raw_dir: str = "data/raw"):
        self.raw_dir = raw_dir
        self._path = os.path.join(raw_dir, CACHE_FILE_NAME)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.debug(f"CrawlCache: loaded {len(self._data)} cached URL(s) from {self._path}")
            except Exception as e:
                logger.warning(f"CrawlCache: could not read cache file ({e}); starting fresh.")
                self._data = {}

    def _save(self) -> None:
        os.makedirs(self.raw_dir, exist_ok=True)
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp_path, self._path)
        except Exception as e:
            logger.warning(f"CrawlCache: failed to persist cache ({e})")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url: str) -> Optional[Dict[str, Any]]:
        """Return the cached entry for *url*, or None if not cached."""
        return self._data.get(url)

    def update(
        self,
        url: str,
        *,
        etag: Optional[str],
        last_modified: Optional[str],
        content_hash: str,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """Persist a fresh entry for *url* and flush the cache to disk."""
        uk_tz = pytz.timezone("Europe/London")
        self._data[url] = {
            "etag": etag,
            "last_modified": last_modified,
            "content_hash": content_hash,
            "cached_at": datetime.now(uk_tz).isoformat(),
            "chunks": chunks,
        }
        self._save()

    def invalidate(self, url: str) -> None:
        """Remove a URL from the cache (forces a fresh fetch next time)."""
        if url in self._data:
            del self._data[url]
            self._save()

    def __len__(self) -> int:
        return len(self._data)


def _content_hash(text: str) -> str:
    """Return the SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

@dataclass
class CrawledDocument:
    url: str
    title: str
    content: str
    source_name: str
    authority: SourceAuthority
    crawled_at: datetime
    word_count: int
    section_headers: List[str]
    metadata: Dict[str, Any]

class UKAutismCrawler:
    def __init__(
        self,
        max_concurrent: int = 5,
        delay: float = 1.0,
        cache: Optional[CrawlCache] = None,
    ):
        self.max_concurrent = max_concurrent
        self.delay = delay
        self.session = None
        self.uk_timezone = pytz.timezone('Europe/London')
        self.cache = cache  # None means caching is disabled

    async def __aenter__(self):
        self.session = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=self.max_concurrent),
            headers={
                'User-Agent': 'Maya UK Autism Assistant (Educational/Non-commercial use)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()

    def _build_conditional_headers(self, cached_entry: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """Return If-None-Match / If-Modified-Since headers from a cached entry."""
        headers: Dict[str, str] = {}
        if not cached_entry:
            return headers
        if cached_entry.get("etag"):
            headers["If-None-Match"] = cached_entry["etag"]
        if cached_entry.get("last_modified"):
            headers["If-Modified-Since"] = cached_entry["last_modified"]
        return headers

    async def crawl_url(
        self,
        url: str,
        source: Source,
    ) -> Tuple[Optional[CrawledDocument], bool]:
        """Crawl a single URL and extract clean content.

        Returns
        -------
        (doc, from_cache)
            doc        : CrawledDocument if content was obtained, else None.
            from_cache : True when the server confirmed the page is unchanged
                         (304 or matching content hash) and cached chunks
                         should be reused.  Always False when cache is disabled.
        """
        try:
            # Rate limiting
            await asyncio.sleep(self.delay)

            cached_entry = self.cache.get(url) if self.cache else None
            conditional_headers = self._build_conditional_headers(cached_entry)

            if conditional_headers:
                logger.info(f"Conditional crawl: {url}")
            else:
                logger.info(f"Crawling: {url}")

            response = await self.session.get(url, headers=conditional_headers)

            # ---- 304 Not Modified -------------------------------------------
            if response.status_code == 304:
                if cached_entry and cached_entry.get("chunks"):
                    logger.info(f"Unchanged (304): {url} — reusing {len(cached_entry['chunks'])} cached chunk(s)")
                    return None, True  # caller should use cached_entry["chunks"]
                else:
                    # Server says 304 but we have no cached chunks — must re-fetch
                    logger.warning(f"304 from {url} but no cached chunks; re-fetching without conditions.")
                    response = await self.session.get(url)

            response.raise_for_status()

            # ---- Extract content --------------------------------------------
            content = trafilatura.extract(
                response.text,
                include_comments=False,
                include_tables=True,
                include_links=False,
                favor_precision=True
            )

            if not content or len(content.strip()) < 100:
                logger.warning(f"Insufficient content extracted from {url}")
                return None, False

            content = content.strip()

            # ---- Content-hash deduplication (fallback when server ignores caching) ----
            new_hash = _content_hash(content)
            if (
                cached_entry
                and cached_entry.get("content_hash") == new_hash
                and cached_entry.get("chunks")
            ):
                logger.info(f"Unchanged (hash match): {url} — reusing {len(cached_entry['chunks'])} cached chunk(s)")
                # Refresh the caching headers from the new response so future requests
                # can use them even though the content didn't change.
                if self.cache:
                    self.cache.update(
                        url,
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                        content_hash=new_hash,
                        chunks=cached_entry["chunks"],
                    )
                return None, True

            # ---- Parse with BeautifulSoup for additional metadata -----------
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract title
            title = "Untitled"
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            else:
                h1_tag = soup.find('h1')
                if h1_tag:
                    title = h1_tag.get_text().strip()

            # Extract section headers for better chunking
            headers = []
            for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
                header_text = tag.get_text().strip()
                if header_text and len(header_text) < 200:
                    headers.append(header_text)

            # Create document
            doc = CrawledDocument(
                url=url,
                title=title,
                content=content,
                source_name=source.name,
                authority=source.authority,
                crawled_at=datetime.now(self.uk_timezone),
                word_count=len(content.split()),
                section_headers=headers,
                metadata={
                    'source_description': source.description,
                    'location_specific': source.location_specific,
                    'response_status': response.status_code,
                    'content_length': len(response.text),
                    'etag': response.headers.get("etag"),
                    'last_modified': response.headers.get("last-modified"),
                    'content_hash': new_hash,
                }
            )

            logger.info(f"Successfully crawled: {url} ({doc.word_count} words)")
            return doc, False

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error crawling {url}: {e.response.status_code}")
            return None, False
        except Exception as e:
            logger.error(f"Error crawling {url}: {str(e)}")
            return None, False

    async def crawl_source(
        self, source: Source
    ) -> Tuple[List[CrawledDocument], List[Dict[str, Any]]]:
        """Crawl all URLs for a specific source.

        Returns
        -------
        (fresh_documents, reused_chunks)
            fresh_documents : CrawledDocument objects for pages whose content changed.
            reused_chunks   : pre-built chunk dicts for pages confirmed unchanged.
        """
        fresh_documents: List[CrawledDocument] = []
        reused_chunks: List[Dict[str, Any]] = []

        for path in source.crawl_paths:
            url = source.base_url + path
            doc, from_cache = await self.crawl_url(url, source)
            if from_cache:
                cached_entry = self.cache.get(url) if self.cache else None
                if cached_entry and cached_entry.get("chunks"):
                    reused_chunks.extend(cached_entry["chunks"])
            elif doc:
                fresh_documents.append(doc)

        return fresh_documents, reused_chunks

    async def crawl_all_sources(
        self,
    ) -> Tuple[List[CrawledDocument], List[Dict[str, Any]]]:
        """Crawl all configured UK autism sources.

        Returns
        -------
        (fresh_documents, reused_chunks)
            fresh_documents : CrawledDocument objects for changed/new pages.
            reused_chunks   : pre-built chunk dicts for unchanged pages (from cache).
        """
        all_fresh: List[CrawledDocument] = []
        all_reused: List[Dict[str, Any]] = []

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def crawl_source_with_semaphore(source: Source):
            async with semaphore:
                return await self.crawl_source(source)

        # Crawl all sources concurrently
        tasks = [crawl_source_with_semaphore(source) for source in UK_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten results
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Source crawling failed: {result}")
            else:
                fresh, reused = result
                all_fresh.extend(fresh)
                all_reused.extend(reused)

        logger.info(
            f"Crawling complete: {len(all_fresh)} page(s) fetched/updated, "
            f"{len({c.get('metadata', {}).get('url') for c in all_reused})} page(s) "
            f"skipped (unchanged, {len(all_reused)} cached chunk(s) reused)."
        )
        return all_fresh, all_reused

def chunk_document(doc: CrawledDocument, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
    """Split document into chunks for vector storage"""
    words = doc.content.split()
    chunks = []
    
    # If document is small, return as single chunk
    if len(words) <= chunk_size:
        return [{
            'text': doc.content,
            'metadata': {
                'url': doc.url,
                'title': doc.title,
                'source_name': doc.source_name,
                'authority': doc.authority.value,
                'crawled_at': doc.crawled_at.isoformat(),
                'chunk_index': 0,
                'total_chunks': 1,
                'location_specific': doc.metadata.get('location_specific', False)
            }
        }]
    
    # Create overlapping chunks
    start = 0
    chunk_index = 0
    
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = ' '.join(chunk_words)
        
        chunks.append({
            'text': chunk_text,
            'metadata': {
                'url': doc.url,
                'title': doc.title,
                'source_name': doc.source_name,
                'authority': doc.authority.value,
                'crawled_at': doc.crawled_at.isoformat(),
                'chunk_index': chunk_index,
                'total_chunks': -1,  # Will be set after all chunks created
                'location_specific': doc.metadata.get('location_specific', False)
            }
        })
        
        # Move start position with overlap
        start = end - overlap if end < len(words) else len(words)
        chunk_index += 1
    
    # Update total chunks count
    for chunk in chunks:
        chunk['metadata']['total_chunks'] = len(chunks)
    
    return chunks

async def crawl_and_chunk_all(
    raw_dir: str = "data/raw",
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Crawl all configured sources and return chunked documents.

    When *use_cache* is True (the default) an on-disk cache is consulted
    before each request.  Pages whose content hasn't changed since the last
    run return their previously built chunks without hitting the network
    again (304 Not Modified or SHA-256 content-hash match).  Pages that are
    new or changed are fetched, chunked, and the cache is updated.

    Parameters
    ----------
    raw_dir:
        Directory used for the crawl cache file (and for
        :func:`save_crawled_chunks`).  Defaults to ``data/raw``.
    use_cache:
        Pass ``False`` to force a full re-crawl regardless of cache state.

    Returns
    -------
    List of chunk dicts suitable for loading into the vector store.
    """
    cache = CrawlCache(raw_dir=raw_dir) if use_cache else None

    async with UKAutismCrawler(cache=cache) as crawler:
        fresh_documents, reused_chunks = await crawler.crawl_all_sources()

    # Chunk freshly fetched documents and persist to cache
    fresh_chunks: List[Dict[str, Any]] = []
    for doc in fresh_documents:
        doc_chunks = chunk_document(doc)
        fresh_chunks.extend(doc_chunks)
        # Persist the new chunks so future runs can reuse them
        if cache is not None:
            cache.update(
                doc.url,
                etag=doc.metadata.get("etag"),
                last_modified=doc.metadata.get("last_modified"),
                content_hash=doc.metadata.get("content_hash", _content_hash(doc.content)),
                chunks=doc_chunks,
            )

    all_chunks = fresh_chunks + reused_chunks

    logger.info(
        f"Chunking complete: {len(fresh_chunks)} chunk(s) from "
        f"{len(fresh_documents)} freshly fetched page(s), "
        f"{len(reused_chunks)} chunk(s) reused from cache "
        f"({len({c.get('metadata', {}).get('url') for c in reused_chunks})} unchanged page(s))."
    )
    return all_chunks


def save_crawled_chunks(chunks: List[Dict[str, Any]], raw_dir: str = "data/raw") -> Optional[str]:
    """
    Persist crawled chunks to a timestamped JSONL file in data/raw/ so the raw
    crawl output is retained alongside ChromaDB. Returns the path written, or
    None if there was nothing to save.
    """
    if not chunks:
        logger.info("No crawled chunks to save to data/raw/")
        return None

    os.makedirs(raw_dir, exist_ok=True)
    timestamp = datetime.now(pytz.timezone("Europe/London")).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(raw_dir, f"crawled_{timestamp}.jsonl")

    with open(path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    logger.info(f"Saved {len(chunks)} crawled chunks to {path}")
    return path


def dedupe_crawled_chunks(
    crawled_chunks: List[Dict[str, Any]],
    existing_urls: Iterable[str],
) -> List[Dict[str, Any]]:
    """
    Drop crawled chunks whose URL is already represented in `existing_urls`
    (e.g. URLs from the curated seed data). All chunks belonging to a genuinely
    new URL are kept — only documents whose URL already exists are skipped, so
    the same URL is never stored twice.
    """
    existing: Set[str] = {u for u in existing_urls if u}
    kept: List[Dict[str, Any]] = []
    skipped = 0

    for chunk in crawled_chunks:
        url = chunk.get("metadata", {}).get("url")
        if url and url in existing:
            skipped += 1
            continue
        kept.append(chunk)

    if skipped:
        logger.info(
            f"Duplicate detection: skipped {skipped} crawled chunk(s) whose URL "
            f"already exists in the knowledge base."
        )
    return kept