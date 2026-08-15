"""
Tests for crawling and re-indexing pipeline.

Covers:
  - dedupe_crawled_chunks(): URL already in seed is dropped; new URL chunks are kept.
  - save_crawled_chunks(): writes a JSONL file to data/raw/; no-ops on empty input.
  - scripts/reindex.py --crawl: crawled chunks are merged and counted (no real network).
"""

import json
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path so both `rag` and `scripts` are importable.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.crawler import dedupe_crawled_chunks, save_crawled_chunks


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_chunk(url: str, chunk_index: int = 0) -> dict:
    return {
        "text": f"Some content for {url} chunk {chunk_index}",
        "metadata": {
            "url": url,
            "title": "Test Page",
            "source_name": "Test Source",
            "authority": "official",
            "crawled_at": "2026-08-15T12:00:00+01:00",
            "chunk_index": chunk_index,
            "total_chunks": 1,
            "location_specific": False,
        },
    }


# ── dedupe_crawled_chunks ─────────────────────────────────────────────────────


class TestDedupeCrawledChunks:
    def test_drops_chunk_whose_url_is_in_existing_set(self):
        """A chunk whose URL already exists in the seed should be dropped."""
        existing = ["https://example.com/known-page"]
        crawled = [_make_chunk("https://example.com/known-page")]

        result = dedupe_crawled_chunks(crawled, existing)

        assert result == [], "Chunk with a seed URL should be filtered out."

    def test_keeps_chunk_with_genuinely_new_url(self):
        """A chunk whose URL is not in the seed should be kept."""
        existing = ["https://example.com/known-page"]
        crawled = [_make_chunk("https://example.com/new-page")]

        result = dedupe_crawled_chunks(crawled, existing)

        assert len(result) == 1
        assert result[0]["metadata"]["url"] == "https://example.com/new-page"

    def test_keeps_all_chunks_of_a_new_url(self):
        """All chunks that share a genuinely new URL must be kept."""
        existing = ["https://example.com/known-page"]
        crawled = [
            _make_chunk("https://example.com/new-page", chunk_index=0),
            _make_chunk("https://example.com/new-page", chunk_index=1),
            _make_chunk("https://example.com/new-page", chunk_index=2),
        ]

        result = dedupe_crawled_chunks(crawled, existing)

        assert len(result) == 3, "All three chunks of the new URL must be kept."
        for i, chunk in enumerate(result):
            assert chunk["metadata"]["chunk_index"] == i

    def test_drops_all_chunks_of_a_duplicate_url(self):
        """Every chunk whose URL matches an existing URL must be dropped."""
        existing = ["https://example.com/known-page"]
        crawled = [
            _make_chunk("https://example.com/known-page", chunk_index=0),
            _make_chunk("https://example.com/known-page", chunk_index=1),
        ]

        result = dedupe_crawled_chunks(crawled, existing)

        assert result == []

    def test_mixed_new_and_duplicate_urls(self):
        """New URL chunks are kept; duplicate URL chunks are dropped."""
        existing = ["https://example.com/old"]
        crawled = [
            _make_chunk("https://example.com/old", chunk_index=0),
            _make_chunk("https://example.com/new", chunk_index=0),
            _make_chunk("https://example.com/new", chunk_index=1),
        ]

        result = dedupe_crawled_chunks(crawled, existing)

        assert len(result) == 2
        for chunk in result:
            assert chunk["metadata"]["url"] == "https://example.com/new"

    def test_empty_crawled_list_returns_empty(self):
        """No crawled chunks → empty result."""
        result = dedupe_crawled_chunks([], ["https://example.com/old"])
        assert result == []

    def test_empty_existing_urls_keeps_all(self):
        """No existing URLs → all crawled chunks are kept."""
        crawled = [_make_chunk("https://example.com/a"), _make_chunk("https://example.com/b")]
        result = dedupe_crawled_chunks(crawled, [])
        assert len(result) == 2

    def test_chunk_missing_url_in_metadata_is_kept(self):
        """A chunk without a URL in its metadata is not treated as a duplicate."""
        existing = ["https://example.com/known"]
        crawled = [{"text": "Some orphan chunk", "metadata": {}}]

        result = dedupe_crawled_chunks(crawled, existing)

        assert len(result) == 1, "Chunk with no URL should pass through."


# ── save_crawled_chunks ───────────────────────────────────────────────────────


class TestSaveCrawledChunks:
    def test_returns_none_on_empty_input(self, tmp_path):
        """save_crawled_chunks should return None and write nothing when given an empty list."""
        result = save_crawled_chunks([], raw_dir=str(tmp_path))

        assert result is None
        assert list(tmp_path.iterdir()) == [], "No file should be created for empty input."

    def test_writes_jsonl_file(self, tmp_path):
        """save_crawled_chunks should write a .jsonl file containing one line per chunk."""
        chunks = [
            _make_chunk("https://example.com/page-a"),
            _make_chunk("https://example.com/page-b"),
        ]

        result = save_crawled_chunks(chunks, raw_dir=str(tmp_path))

        assert result is not None, "Should return the path of the written file."
        assert result.endswith(".jsonl"), "Written file should have a .jsonl extension."

        written = Path(result)
        assert written.exists(), "The returned path must exist on disk."

        lines = written.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, "One JSONL line per chunk."

        for i, line in enumerate(lines):
            data = json.loads(line)
            assert "text" in data
            assert "metadata" in data

    def test_file_is_placed_inside_raw_dir(self, tmp_path):
        """The written file should be inside the specified raw_dir directory."""
        chunks = [_make_chunk("https://example.com/page")]

        result = save_crawled_chunks(chunks, raw_dir=str(tmp_path))

        assert result is not None
        assert Path(result).parent == tmp_path

    def test_creates_raw_dir_if_missing(self, tmp_path):
        """save_crawled_chunks should create data/raw/ if it does not yet exist."""
        new_dir = tmp_path / "does_not_exist"
        assert not new_dir.exists()

        save_crawled_chunks([_make_chunk("https://example.com/x")], raw_dir=str(new_dir))

        assert new_dir.exists(), "raw_dir should be created automatically."

    def test_chunk_content_round_trips(self, tmp_path):
        """Each JSONL line should deserialise back to the original chunk."""
        chunk = _make_chunk("https://example.com/roundtrip", chunk_index=3)

        result = save_crawled_chunks([chunk], raw_dir=str(tmp_path))

        lines = Path(result).read_text(encoding="utf-8").splitlines()
        recovered = json.loads(lines[0])
        assert recovered["text"] == chunk["text"]
        assert recovered["metadata"]["url"] == chunk["metadata"]["url"]
        assert recovered["metadata"]["chunk_index"] == 3


# ── scripts/reindex.py --crawl (mocked network) ───────────────────────────────


class TestReindexWithCrawl:
    """
    Exercise the --crawl path of scripts/reindex.py without hitting the network.

    Strategy:
      - Stub crawl_and_chunk_all() to return fake chunks with URLs not in the seed.
      - Stub save_crawled_chunks() to avoid filesystem side-effects.
      - Stub UKAutismVectorStore so no ChromaDB is needed.
      - Stub StructuredKnowledgeImporter so no real seed file is needed.
      - Stub Path.glob so the extra-files scan in data/ returns nothing, preventing
        the importer from being called more than once and multiplying the seed data.
    """

    def _run_reindex_main(self, tmp_path, crawled_chunks, seed_chunks):
        """
        Call reindex.main() with --crawl under a fully mocked environment.
        Returns the mock vector store instance for assertions.
        """
        import scripts.reindex as reindex_mod

        # Fake seed file so the path-existence check passes.
        seed_file = tmp_path / "seed.jsonl"
        seed_file.write_text("{}\n", encoding="utf-8")

        mock_store = MagicMock()
        mock_store.get_collection_stats.return_value = {
            "total_chunks": len(seed_chunks) + len(crawled_chunks)
        }

        mock_importer_instance = MagicMock()
        # First call = seed file; any further calls (extra data/ files) return nothing.
        mock_importer_instance.import_file.side_effect = [seed_chunks] + [[]] * 20

        mock_importer_cls = MagicMock(return_value=mock_importer_instance)

        with (
            patch("rag.crawler.crawl_and_chunk_all", new=AsyncMock(return_value=crawled_chunks)),
            patch("rag.crawler.save_crawled_chunks", return_value=str(tmp_path / "crawled_fake.jsonl")),
            patch("rag.vector_store.UKAutismVectorStore", return_value=mock_store),
            patch("rag.structured_importer.StructuredKnowledgeImporter", mock_importer_cls),
            patch("sys.argv", ["reindex.py", "--seed-file", str(seed_file), "--crawl"]),
            # Suppress the extra-file scan so only the seed importer call fires.
            patch("pathlib.Path.glob", return_value=iter([])),
        ):
            reindex_mod.main()

        return mock_store

    def test_crawled_chunks_are_passed_to_add_documents(self, tmp_path):
        """add_documents should receive both seed and (deduped) crawled chunks."""
        seed_chunks = [
            {"text": "seed text", "metadata": {"url": "https://example.com/seed", "category": "seed"}}
        ]
        crawled_chunks = [
            _make_chunk("https://example.com/crawled-new", chunk_index=0),
            _make_chunk("https://example.com/crawled-new", chunk_index=1),
        ]

        mock_store = self._run_reindex_main(tmp_path, crawled_chunks, seed_chunks)

        mock_store.add_documents.assert_called_once()
        submitted = mock_store.add_documents.call_args[0][0]

        urls = [c.get("metadata", {}).get("url") for c in submitted]
        assert "https://example.com/seed" in urls
        assert "https://example.com/crawled-new" in urls

    def test_duplicate_crawled_url_is_not_added_twice(self, tmp_path):
        """A crawled URL that is already in the seed must not appear twice."""
        seed_url = "https://example.com/shared"
        seed_chunks = [
            {"text": "seed text", "metadata": {"url": seed_url, "category": "seed"}}
        ]
        # Crawler returns a chunk for the *same* URL that's already in the seed.
        crawled_chunks = [_make_chunk(seed_url, chunk_index=0)]

        mock_store = self._run_reindex_main(tmp_path, crawled_chunks, seed_chunks)

        submitted = mock_store.add_documents.call_args[0][0]
        matching = [c for c in submitted if c.get("metadata", {}).get("url") == seed_url]
        assert len(matching) == 1, (
            "The duplicate crawled chunk must be deduped; seed entry should appear exactly once."
        )

    def test_crawl_failure_falls_back_to_seed_only(self, tmp_path):
        """If crawl_and_chunk_all raises, reindex should continue with seed data only."""
        import scripts.reindex as reindex_mod

        seed_chunks = [
            {"text": "seed text", "metadata": {"url": "https://example.com/seed", "category": "seed"}}
        ]
        seed_file = tmp_path / "seed.jsonl"
        seed_file.write_text("{}\n", encoding="utf-8")

        mock_store = MagicMock()
        mock_store.get_collection_stats.return_value = {"total_chunks": 1}

        mock_importer_instance = MagicMock()
        mock_importer_instance.import_file.side_effect = [seed_chunks] + [[]] * 20
        mock_importer_cls = MagicMock(return_value=mock_importer_instance)

        with (
            patch("rag.crawler.crawl_and_chunk_all", new=AsyncMock(side_effect=RuntimeError("Network down"))),
            patch("rag.vector_store.UKAutismVectorStore", return_value=mock_store),
            patch("rag.structured_importer.StructuredKnowledgeImporter", mock_importer_cls),
            patch("sys.argv", ["reindex.py", "--seed-file", str(seed_file), "--crawl"]),
            patch("pathlib.Path.glob", return_value=iter([])),
        ):
            reindex_mod.main()  # must not raise

        mock_store.add_documents.assert_called_once()
        submitted = mock_store.add_documents.call_args[0][0]
        assert len(submitted) == 1
        assert submitted[0]["metadata"]["url"] == "https://example.com/seed"
