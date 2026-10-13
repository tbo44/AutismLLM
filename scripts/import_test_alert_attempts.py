"""Explicit, offline SQLite -> PostgreSQL claim import. Never delivers email."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.postgres_test_alert_attempts import PostgresTestAlertAttemptStore

from psycopg.types.json import Jsonb


def import_attempts(source: Path, target: PostgresTestAlertAttemptStore) -> int:
    # mode=rw refuses missing files rather than creating an empty "migration".
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=rw", uri=True)) as local:
        local.execute("BEGIN IMMEDIATE")
        rows = local.execute(
            "SELECT key, state, result, completed_at FROM test_alert_attempts"
        ).fetchall()
        with target._connect() as shared:
            target._lock(shared)
            for key, state, encoded, completed_at in rows:
                result = json.loads(encoded) if encoded is not None else None
                existing = shared.execute(
                    """SELECT state, result, completed_at
                       FROM public.maya_test_alert_attempts WHERE key = %s""", (key,),
                ).fetchone()
                if existing is not None:
                    if existing != (state, result, completed_at):
                        # Never overwrite a completed/archived/unresolved claim.
                        raise ValueError("Conflicting attempt records; import rolled back. Review offline.")
                    continue
                shared.execute(
                    """INSERT INTO public.maya_test_alert_attempts
                       (key, state, result, completed_at) VALUES (%s, %s, %s, %s)""",
                    (key, state, Jsonb(result) if result is not None else None, completed_at),
                )
        # No source writes: original claims remain available for backup/recovery.
        return len(rows)


def main():
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--confirm-all-senders-stopped", required=True, action="store_true")
    args = parser.parse_args()
    try:
        target = PostgresTestAlertAttemptStore(
            os.environ.get("TEST_ALERT_ATTEMPTS_DATABASE_URL", "")
        )
        count = import_attempts(args.source, target)
    except Exception:
        # Avoid printing connection strings, recipient details or attempt records.
        parser.exit(1, "Import failed; no partial target changes committed. Check setup and conflicting records offline.\n")
    print(f"Verified/imported {count} attempt records. Source unchanged; no email sent.")


if __name__ == "__main__":
    main()
