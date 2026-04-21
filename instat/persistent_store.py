"""Persistent, multi-run store for follower/following extraction.

Purpose: targets with >>50k followers hit the IG per-account quota
(~500-2000 profiles/fresh session). A single-process run can't ever
finish. This store lets you schedule daily runs and accumulate union
across them without losing progress.

Contrast with `ExtractionCheckpoint`:
  - ExtractionCheckpoint expires in 24h, one file per (profile, list).
    Designed to resume a crashed run.
  - PersistentStore has NO expiry. Tracks first/last-seen per username,
    source account, dedup across calls. Designed for multi-day accrual.

Schema choice:
  PRIMARY KEY (profile_id, list_type, username) — natural dedup.
  source_account + timestamps are metadata, useful for audit/stats.

File permissions are restricted to 0o600 (POSIX); Windows falls back
silently. Same pattern as SessionCache for session-token safety.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List


def _restrict_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


class PersistentStore:
    """SQLite-backed union of extraction results across multiple runs."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS profiles_seen (
        profile_id     TEXT NOT NULL,
        list_type      TEXT NOT NULL,
        username       TEXT NOT NULL,
        first_seen_at  REAL NOT NULL,
        last_seen_at   REAL NOT NULL,
        source_account TEXT NOT NULL,
        PRIMARY KEY (profile_id, list_type, username)
    );
    CREATE INDEX IF NOT EXISTS idx_profile_list
        ON profiles_seen(profile_id, list_type);
    CREATE INDEX IF NOT EXISTS idx_last_seen
        ON profiles_seen(profile_id, list_type, last_seen_at);
    """

    def __init__(self, path: str):
        self._path = Path(path)
        # Ensure parent dir exists (don't create file — sqlite3 will).
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self._path.exists()
        conn = sqlite3.connect(str(self._path))
        try:
            conn.executescript(self._SCHEMA)
            conn.commit()
        finally:
            conn.close()
        if fresh:
            _restrict_permissions(self._path)

    @property
    def path(self) -> str:
        return str(self._path)

    def add_batch(
        self, profile_id: str, list_type: str,
        usernames, source_account: str,
    ) -> int:
        """Insert or update. Returns count of NEW rows (not updates).

        Upsert semantics: first insertion records first_seen_at;
        subsequent calls bump last_seen_at only. No-op for known
        (profile_id, list_type, username) tuples beyond refreshing
        last_seen_at.
        """
        now = time.time()
        rows = [
            (profile_id, list_type, u, now, now, source_account)
            for u in usernames if isinstance(u, str) and u
        ]
        if not rows:
            return 0
        conn = sqlite3.connect(str(self._path))
        try:
            # Count existing first so we can compute delta.
            existing_before = set(
                r[0] for r in conn.execute(
                    "SELECT username FROM profiles_seen "
                    "WHERE profile_id = ? AND list_type = ?",
                    (profile_id, list_type),
                )
            )
            conn.executemany(
                """INSERT INTO profiles_seen
                   (profile_id, list_type, username,
                    first_seen_at, last_seen_at, source_account)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(profile_id, list_type, username)
                   DO UPDATE SET last_seen_at = excluded.last_seen_at""",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        incoming = {u for u in usernames if isinstance(u, str) and u}
        new = incoming - existing_before
        return len(new)

    def get_all(self, profile_id: str, list_type: str) -> List[str]:
        """Full union of usernames ever recorded for this target."""
        conn = sqlite3.connect(str(self._path))
        try:
            rows = conn.execute(
                "SELECT username FROM profiles_seen "
                "WHERE profile_id = ? AND list_type = ? "
                "ORDER BY username",
                (profile_id, list_type),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def get_delta_since(
        self, profile_id: str, list_type: str, since: float,
    ) -> List[str]:
        """Usernames whose first_seen_at >= since. Use for 'what's new
        today' queries across multi-day accrual."""
        conn = sqlite3.connect(str(self._path))
        try:
            rows = conn.execute(
                "SELECT username FROM profiles_seen "
                "WHERE profile_id = ? AND list_type = ? "
                "AND first_seen_at >= ? ORDER BY username",
                (profile_id, list_type, since),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def count(self, profile_id: str, list_type: str) -> int:
        conn = sqlite3.connect(str(self._path))
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM profiles_seen "
                "WHERE profile_id = ? AND list_type = ?",
                (profile_id, list_type),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def stats(self, profile_id: str, list_type: str) -> Dict:
        """Summary: total count, first_seen_at of earliest, last_seen_at
        of most recent, and distinct source accounts."""
        conn = sqlite3.connect(str(self._path))
        try:
            agg = conn.execute(
                "SELECT COUNT(*), MIN(first_seen_at), MAX(last_seen_at) "
                "FROM profiles_seen "
                "WHERE profile_id = ? AND list_type = ?",
                (profile_id, list_type),
            ).fetchone()
            sources = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT source_account FROM profiles_seen "
                    "WHERE profile_id = ? AND list_type = ? "
                    "ORDER BY source_account",
                    (profile_id, list_type),
                ).fetchall()
            ]
            return {
                'total': int(agg[0] or 0),
                'first_seen_at': agg[1],
                'last_seen_at': agg[2],
                'source_accounts': sources,
            }
        finally:
            conn.close()

    def source_breakdown(
        self, profile_id: str, list_type: str,
    ) -> Dict[str, int]:
        """How many profiles each source account contributed."""
        conn = sqlite3.connect(str(self._path))
        try:
            rows = conn.execute(
                "SELECT source_account, COUNT(*) FROM profiles_seen "
                "WHERE profile_id = ? AND list_type = ? "
                "GROUP BY source_account",
                (profile_id, list_type),
            ).fetchall()
            return {r[0]: int(r[1]) for r in rows}
        finally:
            conn.close()


__all__ = ['PersistentStore']
