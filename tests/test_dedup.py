"""Tests for dedup.py — URL/title deduplication with SQLite."""

import tempfile
from pathlib import Path

import pytest

from src.dedup import DedupTracker


class TestDedupTracker:
    @pytest.fixture
    def tracker(self):
        """Create a DedupTracker with a temp database."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_seen.db"
            tracker = DedupTracker(str(db_path))
            yield tracker
            tracker.close()

    def test_new_url_not_seen(self, tracker):
        assert not tracker.is_seen("https://example.com/article-1")

    def test_seen_url_is_seen(self, tracker):
        tracker.mark_seen("https://example.com/article-1", "Test Title", "content", "bilibili")
        assert tracker.is_seen("https://example.com/article-1")

    def test_different_urls_are_unique(self, tracker):
        tracker.mark_seen("https://a.com/1", "Title A", "content", "bilibili")
        assert not tracker.is_seen("https://a.com/2", "Title B")

    def test_similar_title_detected(self, tracker):
        # Nearly identical title — only punctuation and one char differ
        tracker.mark_seen("https://a.com/1", "认知升级突破思维局限的方法", "content", "zhihu")
        assert tracker.is_seen(
            "https://a.com/2",
            "认知升级 突破思维局限的方法！"
        )

    def test_different_titles_pass(self, tracker):
        tracker.mark_seen("https://a.com/1", "如何学习Python编程", "content", "bilibili")
        assert not tracker.is_seen(
            "https://a.com/2",
            "减脂餐食谱推荐大全"
        )

    def test_stats_empty(self, tracker):
        stats = tracker.get_stats()
        assert stats["total_processed"] == 0
        assert stats["by_source"] == {}

    def test_stats_with_data(self, tracker):
        tracker.mark_seen("https://a.com/1", "Title 1", "content", "bilibili")
        tracker.mark_seen("https://a.com/2", "Title 2", "content", "bilibili")
        tracker.mark_seen("https://a.com/3", "Title 3", "content", "zhihu")
        stats = tracker.get_stats()
        assert stats["total_processed"] == 3
        assert stats["by_source"]["bilibili"] == 2
        assert stats["by_source"]["zhihu"] == 1

    def test_hash_determinism(self):
        h1 = DedupTracker._hash("hello world")
        h2 = DedupTracker._hash("hello world")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256

    def test_title_similar_identical(self):
        assert DedupTracker._title_similar("认知升级", "认知升级") == 1.0

    def test_title_similar_completely_different(self):
        score = DedupTracker._title_similar("认知升级", "减脂餐谱")
        assert score < 0.3

    def test_normalize_title(self):
        result = DedupTracker._normalize_title("Hello World! 认知-升级？")
        # Should be lowercased, stripped of punctuation
        assert "!" not in result
        assert "？" not in result
        assert result.islower() or any("一" <= c <= "鿿" for c in result)
