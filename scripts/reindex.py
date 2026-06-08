#!/usr/bin/env python3
"""
Re-index script for Maya's knowledge base.

Usage:
    python scripts/reindex.py [--seed-file PATH] [--crawl]

Clears the existing ChromaDB collection and repopulates it from:
  1. The structured JSONL seed file (data/maya_hounslow_knowledge_seed.jsonl by default)
  2. Any additional JSONL/CSV files in the data/ directory
  3. (with --crawl) freshly crawled pages from the trusted UK sources in
     rag/sources.py — saved to data/raw/ and added alongside the seed entries

Run this whenever the seed data is updated, or with --crawl to also pull fresh
web content.
"""

import sys
import os
import asyncio
import argparse
import logging
from pathlib import Path
from collections import defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("reindex")


def main():
    parser = argparse.ArgumentParser(description="Rebuild Maya's vector knowledge base from seed data.")
    parser.add_argument(
        "--seed-file",
        default="data/maya_hounslow_knowledge_seed.jsonl",
        help="Path to the primary JSONL seed file (default: data/maya_hounslow_knowledge_seed.jsonl)"
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Skip resetting the collection (add only, do not delete existing chunks)"
    )
    parser.add_argument(
        "--crawl",
        action="store_true",
        help="Also crawl the trusted UK web sources (rag/sources.py) before importing "
             "seed data. Crawled pages are saved to data/raw/ and added to ChromaDB "
             "alongside the seed entries (duplicate URLs are skipped)."
    )
    args = parser.parse_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        logger.error(f"Seed file not found: {seed_path}")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 1. Initialise vector store
    # ------------------------------------------------------------------ #
    logger.info("Initialising vector store...")
    from rag.vector_store import UKAutismVectorStore

    vector_store = UKAutismVectorStore()
    vector_store.initialize()

    if not args.no_reset:
        logger.info("Resetting existing collection...")
        vector_store.reset_collection()
        logger.info("Collection cleared.")

    # ------------------------------------------------------------------ #
    # 1b. (optional) Crawl live UK web sources before importing seed data
    # ------------------------------------------------------------------ #
    crawled_chunks = []
    if args.crawl:
        from rag.crawler import crawl_and_chunk_all, save_crawled_chunks

        logger.info("Crawling trusted UK web sources (this may take a minute)...")
        try:
            crawled_chunks = asyncio.run(crawl_and_chunk_all())
            logger.info(f"Crawled {len(crawled_chunks)} chunks from live sources.")
            if crawled_chunks:
                saved_path = save_crawled_chunks(crawled_chunks)
                if saved_path:
                    logger.info(f"Raw crawl output saved to {saved_path}")
        except Exception as e:
            logger.warning(f"Web crawl failed: {e} — continuing with seed data only.")
            crawled_chunks = []

    # ------------------------------------------------------------------ #
    # 2. Load structured seed data
    # ------------------------------------------------------------------ #
    from rag.structured_importer import StructuredKnowledgeImporter

    all_chunks = []
    category_counts = defaultdict(int)

    logger.info(f"Loading seed file: {seed_path}")
    importer = StructuredKnowledgeImporter()
    chunks = importer.import_file(str(seed_path))
    all_chunks.extend(chunks)

    for chunk in chunks:
        cat = chunk["metadata"].get("category", "unknown")
        category_counts[cat] += 1

    logger.info(f"Loaded {len(chunks)} chunks from seed file.")

    # ------------------------------------------------------------------ #
    # 3. Also load any other JSONL/CSV files in data/
    # ------------------------------------------------------------------ #
    data_dir = Path("data")
    extra_files = [
        f for f in data_dir.glob("*.jsonl")
        if f != seed_path and f.name != seed_path.name
    ] + [
        f for f in data_dir.glob("*.csv")
    ]

    for extra_file in extra_files:
        logger.info(f"Loading extra file: {extra_file}")
        try:
            extra_importer = StructuredKnowledgeImporter()
            extra_chunks = extra_importer.import_file(str(extra_file))
            all_chunks.extend(extra_chunks)
            for chunk in extra_chunks:
                cat = chunk["metadata"].get("category", "unknown")
                category_counts[cat] += 1
            logger.info(f"  → {len(extra_chunks)} chunks loaded")
        except Exception as e:
            logger.warning(f"  Skipping {extra_file}: {e}")

    # ------------------------------------------------------------------ #
    # 3b. Merge crawled chunks, skipping URLs already present in the seed data
    # ------------------------------------------------------------------ #
    if crawled_chunks:
        from rag.crawler import dedupe_crawled_chunks

        existing_urls = {
            c["metadata"].get("url")
            for c in all_chunks
            if c.get("metadata", {}).get("url")
        }
        deduped = dedupe_crawled_chunks(crawled_chunks, existing_urls)
        all_chunks.extend(deduped)
        category_counts["web_crawl"] += len(deduped)
        logger.info(
            f"Added {len(deduped)} crawled chunks "
            f"({len(crawled_chunks) - len(deduped)} skipped as duplicates)."
        )

    # ------------------------------------------------------------------ #
    # 4. Add all chunks to vector store
    # ------------------------------------------------------------------ #
    if not all_chunks:
        logger.error("No chunks to add. Aborting.")
        sys.exit(1)

    logger.info(f"Adding {len(all_chunks)} chunks to vector store...")
    vector_store.add_documents(all_chunks)

    # ------------------------------------------------------------------ #
    # 5. Summary
    # ------------------------------------------------------------------ #
    stats = vector_store.get_collection_stats()
    total = stats.get("total_chunks", len(all_chunks))

    print("\n" + "=" * 50)
    print(f"  Re-index complete — {total} chunks stored")
    print("=" * 50)
    print("\nChunks by category:")
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat:<40} {count:>4}")
    print(f"\n  {'TOTAL':<40} {sum(category_counts.values()):>4}")
    print()


if __name__ == "__main__":
    main()
