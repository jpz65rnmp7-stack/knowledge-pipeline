"""Deduplication system — URL tracking + title similarity with WAL-mode SQLite."""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DedupTracker:
    """Tracks processed articles to prevent duplicates across runs.

    Uses SQLite WAL mode for concurrent read/write safety, and a shared
    connection to avoid per-call connection overhead.
    """

    _conn: Optional[sqlite3.Connection] = None

    def __init__(self, db_path: str = "data/seen_urls.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._get_conn()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the shared SQLite connection (WAL mode)."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
            self._init_tables()
        return self._conn

    def _init_tables(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                title_hash TEXT,
                content_hash TEXT,
                source TEXT,
                title TEXT,
                timestamp TEXT,
                processed BOOLEAN DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_title_hash ON seen_urls(title_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processed ON seen_urls(processed)")
        conn.commit()

    def is_seen(self, url: str, title: str = "") -> bool:
        """Check if a URL has already been processed."""
        url_hash = self._hash(url)
        conn = self._get_conn()

        row = conn.execute(
            "SELECT 1 FROM seen_urls WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        if row:
            return True

        if title:
            title_hash = self._hash(self._normalize_title(title))
            row = conn.execute(
                "SELECT title FROM seen_urls WHERE title_hash = ? LIMIT 1",
                (title_hash,)
            ).fetchone()
            if row and self._title_similar(title, row[0]) > 0.7:
                return True

        return False

    def mark_seen(self, url: str, title: str, content: str, source: str):
        """Record an article as processed."""
        url_hash = self._hash(url)
        title_hash = self._hash(self._normalize_title(title))
        content_hash = self._hash(content[:200] if content else "")
        ts = datetime.now().isoformat()

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO seen_urls
               (url_hash, title_hash, content_hash, source, title, timestamp, processed)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (url_hash, title_hash, content_hash, source, title, ts),
        )
        conn.commit()

    def get_stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0]
        by_source = {}
        for row in conn.execute("SELECT source, COUNT(*) FROM seen_urls GROUP BY source"):
            by_source[row[0]] = row[1]
        return {"total_processed": total, "by_source": by_source}

    def close(self):
        """Explicitly close the connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ─── static helpers ────────────────────────────────────────

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_title(title: str) -> str:
        title = title.lower().strip()
        title = re.sub(r"[^\w一-鿿]", "", title)
        return title

    @staticmethod
    def _title_similar(title_a: str, title_b: str) -> float:
        def bigrams(s):
            return {s[i:i+2] for i in range(len(s)-1)} if len(s) > 1 else {s}

        a = DedupTracker._normalize_title(title_a)
        b = DedupTracker._normalize_title(title_b)
        ba = bigrams(a)
        bb = bigrams(b)
        if not ba or not bb:
            return 0.0
        return len(ba & bb) / len(ba | bb)
