---
name: Seed schema normalisation
description: How the structured importer handles field name variants in the JSONL seed
---

The JSONL seed uses last_verified_date (not last_reviewed) and content_excerpt (not content).
The structured importer (rag/structured_importer.py) normalises these:
  - date_added    → entry.get('date_added') or entry.get('crawled_at')
  - last_reviewed → entry.get('last_reviewed') or entry.get('last_verified_date')
  - content       → entry.get('content') or entry.get('content_excerpt')
  - tags          → serialised as JSON string (ChromaDB metadata must be scalar)

**Why:** ChromaDB rejects list/dict metadata values; all complex fields must be serialised to string.
New seed entries (date 2025-06-08+) include date_added, last_reviewed, and content directly.
