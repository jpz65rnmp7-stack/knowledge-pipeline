"""Deduplication system — URL tracking + title similarity."""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DedupTracker:
    """Tracks processed articles to prevent duplicates across runs."""

    def __init__(self, db_path: str = "data/seen_urls.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(str(self.db_path)) as conn:
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_title_hash ON seen_urls(title_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed ON seen_urls(processed)
            """)
            conn.commit()

    def is_seen(self, url: str, title: str = "") -> bool:
        """Check if a URL has already been processed."""
        url_hash = self._hash(url)

        with sqlite3.connect(str(self.db_path)) as conn:
            # Exact URL match
            row = conn.execute(
                "SELECT 1 FROM seen_urls WHERE url_hash = ?", (url_hash,)
            ).fetchone()
            if row:
                return True

            # Title similarity check (if title provided)
            if title:
                title_hash = self._hash(self._normalize_title(title))
                row = conn.execute(
                    "SELECT title FROM seen_urls WHERE title_hash = ? LIMIT 1",
                    (title_hash,)
                ).fetchone()
                if row:
                    # Check Jaccard similarity as confirmation
                    if self._title_similar(title, row[0]) > 0.7:
                        return True

        return False

    def mark_seen(self, url: str, title: str, content: str, source: str):
        """Record an article as processed."""
        url_hash = self._hash(url)
        title_hash = self._hash(self._normalize_title(title))
        content_hash = self._hash(content[:200] if content else "")

        from datetime import datetime
        ts = datetime.now().isoformat()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO seen_urls
                   (url_hash, title_hash, content_hash, source, title, timestamp, processed)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (url_hash, title_hash, content_hash, source, title, ts),
            )
            conn.commit()

    def get_stats(self) -> dict:
        """Get dedup statistics."""
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0]
            by_source = {}
            for row in conn.execute("SELECT source, COUNT(*) FROM seen_urls GROUP BY source"):
                by_source[row[0]] = row[1]
            return {"total_processed": total, "by_source": by_source}

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize title for comparison."""
        import re
        title = title.lower().strip()
        title = re.sub(r"[^\w一-鿿]", "", title)  # Keep Chinese + alphanumeric
        return title

    @staticmethod
    def _title_similar(title_a: str, title_b: str) -> float:
        """Compute Jaccard similarity between two titles using character bigrams."""
        def bigrams(s):
            return {s[i:i+2] for i in range(len(s)-1)} if len(s) > 1 else {s}

        a = DedupTracker._normalize_title(title_a)
        b = DedupTracker._normalize_title(title_b)

        ba = bigrams(a)
        bb = bigrams(b)

        if not ba or not bb:
            return 0.0

        intersection = ba & bb
        union = ba | bb
        return len(intersection) / len(union) if union else 0.0
