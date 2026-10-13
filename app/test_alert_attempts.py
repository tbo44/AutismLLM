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
            count = connection.execute(
                "SELECT COUNT(*) FROM test_alert_attempts WHERE state != 'archived'"
            ).fetchone()[0]
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

    def review(self):
        """Read unresolved claims without expiring or reclaiming anything."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT key, state FROM test_alert_attempts WHERE state IN ('pending', 'uncertain') ORDER BY key"
            ).fetchall()
            active, archived = connection.execute(
                """SELECT COUNT(CASE WHEN state != 'archived' THEN 1 END),
                          COUNT(CASE WHEN state = 'archived' THEN 1 END)
                   FROM test_alert_attempts"""
            ).fetchone()
        return {
            "attempts": [{"key": key, "state": state} for key, state in rows],
            "retained": active, "archived": archived, "limit": self.limit,
        }

    def history(self, limit: int = 25, offset: int = 0):
        """Read permanent archives in key order, without pruning any claims."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            total = connection.execute(
                "SELECT COUNT(*) FROM test_alert_attempts WHERE state = 'archived'"
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT key, result FROM test_alert_attempts
                   WHERE state = 'archived' ORDER BY key LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return {
            "attempts": [{"key": key, **json.loads(result)} for key, result in rows],
            "total": total, "limit": limit, "offset": offset,
        }

    def resolve(self, key: str, resolution: str):
        """Archive after manual review; preserve the key forever, without delivery."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, result FROM test_alert_attempts WHERE key = ?", (key,),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if row[0] == "archived":
                return json.loads(row[1])
            if row[0] not in ("pending", "uncertain"):
                raise ValueError("Only unresolved attempts can be archived.")
            result = {"resolution": resolution, "resolved_at": time.time(), "inbox_checked": True}
            connection.execute(
                "UPDATE test_alert_attempts SET state = 'archived', result = ? WHERE key = ?",
                (json.dumps(result), key),
            )
            return result
