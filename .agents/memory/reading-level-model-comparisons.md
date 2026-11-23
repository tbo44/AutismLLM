---
name: Reading-level model comparisons
description: Keep answer quality distinct from provider availability and retrieval behavior
---

When comparing candidate models' reading levels, count only complete Clear/Complex pairs in the pass-rate denominator. Run candidates against the same fixed sources and sampling settings so the comparison measures answer generation rather than moderation or retrieval.

**Why:** A provider failure on either request makes the pair incomparable, even if the other reply already looks weak. Counting it as a quality miss or hiding it entirely can make small-sample comparisons misleading.

**How to apply:** Report quality counts alongside provider failures and unfinished pairs. Report no pass rate when nothing could be scored, and treat the counts as evidence for review, not a release gate or proof of a regression.

Keep the benchmark identity separate from the report format and the prompt under test.

**Why:** A report format change can leave scores comparable, and changing the production prompt is something the same benchmark should be able to measure.

**How to apply:** Treat changes to fixed test inputs or scoring rules as benchmark changes, and compare scores under matching benchmarks and sampling settings before interpreting a difference.
