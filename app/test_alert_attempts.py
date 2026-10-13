"""Durable claims for test emails; ambiguous sends are never reclaimed."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class AttemptCapacityError(Exception):
    pass


class TestAlertAttemptStore:
    __test__ = False

    def __init__(self, path: Path, ttl: float = 86400, limit: int = 1024):
        self.path = path
        self.ttl = ttl
        self.limit = limit

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS test_alert_attempts (
                    key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    result TEXT,
                    completed_at REAL
                )
            """)
        except BaseException:
            connection.close()
            raise
        return connection

    def claim(self, key: str) -> bool:
        """Commit exclusive ownership BEFORE calling the external mail service."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            # Unresolved claims deliberately never expire: they may have sent mail.
            connection.execute(
                "DELETE FROM test_alert_attempts WHERE state = 'completed' AND completed_at <= ?",
                (time.time() - self.ttl,),
            )
            if connection.execute(
                "SELECT 1 FROM test_alert_attempts WHERE key = ?", (key,),
            ).fetchone():
                return False
            count = connection.execute("SELECT COUNT(*) FROM test_alert_attempts").fetchone()[0]
            if count >= self.limit:
                raise AttemptCapacityError
            connection.execute(
                "INSERT INTO test_alert_attempts (key, state) VALUES (?, 'pending')", (key,),
            )
            return True

    def read(self, key: str):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state, result FROM test_alert_attempts WHERE key = ?", (key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Test email attempt disappeared.")
            return row[0], json.loads(row[1]) if row[1] is not None else None

    def finish(self, key: str, result: dict):
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                """UPDATE test_alert_attempts
                   SET state = 'completed', result = ?, completed_at = ?
                   WHERE key = ? AND state = 'pending'""",
                (json.dumps(result), time.time(), key),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Test email ownership was lost before recording its result.")

    def mark_uncertain(self, key: str):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE test_alert_attempts SET state = 'uncertain' WHERE key = ? AND state = 'pending'",
                (key,),
            )
