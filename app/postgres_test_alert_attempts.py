"""Shared retry protection on explicitly configured PostgreSQL.

Schema is installed by the operator, never by application startup. Transactions
end before external delivery; ambiguous ownership is never reclaimed.
"""
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.test_alert_attempts import AttemptCapacityError, TestAlertAttemptStore


class PostgresTestAlertAttemptStore:
    def __init__(self, database_url: str, ttl: float = 86400, limit: int = 1024):
        if not database_url.strip():
            raise ValueError("TEST_ALERT_ATTEMPTS_DATABASE_URL is required for PostgreSQL.")
        self._database_url = database_url
        self.ttl = ttl
        self.limit = limit

    def _connect(self):
        try:
            return psycopg.connect(
                self._database_url, connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=5000 -c synchronous_commit=on",
            )
        except Exception:
            # Do not expose connection strings/credentials in HTTP or log traces.
            raise RuntimeError("Shared test-email storage connection failed.") from None

    @staticmethod
    def _lock(connection):
        # Serialize the capacity check + insert across ALL keys and instances.
        # The lock is released at commit, before any mail service is called.
        connection.execute(
            "LOCK TABLE public.maya_test_alert_attempts IN SHARE ROW EXCLUSIVE MODE"
        )

    def claim(self, key: str) -> bool:
        with self._connect() as connection:
            self._lock(connection)
            connection.execute(
                """DELETE FROM public.maya_test_alert_attempts
                   WHERE state = 'completed'
                   AND completed_at <= EXTRACT(EPOCH FROM clock_timestamp()) - %s""",
                (self.ttl,),
            )
            if connection.execute(
                "SELECT 1 FROM public.maya_test_alert_attempts WHERE key = %s", (key,),
            ).fetchone():
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM public.maya_test_alert_attempts WHERE state != 'archived'"
            ).fetchone()[0]
            if count >= self.limit:
                raise AttemptCapacityError
            connection.execute(
                "INSERT INTO public.maya_test_alert_attempts (key, state) VALUES (%s, 'pending')",
                (key,),
            )
            return True

    def read(self, key: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, result FROM public.maya_test_alert_attempts WHERE key = %s",
                (key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Test email attempt disappeared.")
            return row

    def finish(self, key: str, result: dict):
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE public.maya_test_alert_attempts
                   SET state = 'completed', result = %s,
                       completed_at = EXTRACT(EPOCH FROM clock_timestamp())
                   WHERE key = %s AND state = 'pending'""",
                (Jsonb(result), key),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Test email ownership was lost before recording its result.")

    def mark_uncertain(self, key: str):
        with self._connect() as connection:
            connection.execute(
                """UPDATE public.maya_test_alert_attempts
                   SET state = 'uncertain' WHERE key = %s AND state = 'pending'""",
                (key,),
            )

    def review(self):
        with self._connect() as connection:
            # One statement gives the list and counts from the same snapshot.
            rows = connection.execute(
                "SELECT key, state FROM public.maya_test_alert_attempts ORDER BY key"
            ).fetchall()
        return {
            "attempts": [
                {"key": key, "state": state}
                for key, state in rows if state in ("pending", "uncertain")
            ],
            "retained": sum(state != "archived" for _, state in rows),
            "archived": sum(state == "archived" for _, state in rows),
            "limit": self.limit,
        }

    def resolve(self, key: str, resolution: str):
        with self._connect() as connection:
            row = connection.execute(
                """SELECT state, result FROM public.maya_test_alert_attempts
                   WHERE key = %s FOR UPDATE""", (key,),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if row[0] == "archived":
                return row[1]
            if row[0] not in ("pending", "uncertain"):
                raise ValueError("Only unresolved attempts can be archived.")
            timestamp = connection.execute(
                "SELECT EXTRACT(EPOCH FROM clock_timestamp())::double precision"
            ).fetchone()[0]
            result = {"resolution": resolution, "resolved_at": timestamp, "inbox_checked": True}
            connection.execute(
                """UPDATE public.maya_test_alert_attempts
                   SET state = 'archived', result = %s WHERE key = %s""",
                (Jsonb(result), key),
            )
            return result


def create_test_alert_attempt_store():
    """Explicit opt-in only; unrelated DATABASE_URL must not switch storage."""
    backend = os.environ.get("TEST_ALERT_ATTEMPTS_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        return TestAlertAttemptStore(
            Path(os.environ.get("TEST_ALERT_ATTEMPTS_PATH", "logs/test_alert_attempts.sqlite3"))
        )
    if backend == "postgres":
        return PostgresTestAlertAttemptStore(
            os.environ.get("TEST_ALERT_ATTEMPTS_DATABASE_URL", "")
        )
    raise ValueError("TEST_ALERT_ATTEMPTS_BACKEND must be sqlite or postgres.")
