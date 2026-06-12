"""Tests for quality.py — heuristic checks and scoring logic."""

import pytest

from src.quality import QualityFilter


class TestHeuristicCheck:
    """Test the fast heuristic checks (no LLM required)."""

    @pytest.fixture
    def qf(self):
        """Create a minimal QualityFilter without LLM for heuristic tests."""
        # We only test heuristic_check which doesn't need LLM
        # Build a mock object that has the needed attributes
        from unittest.mock import MagicMock
        from src.utils import Config

        class MockQualityFilter(QualityFilter):
            def __init__(self):
                self.llm = MagicMock()
                self.config = MagicMock()
                self.quality_prompt = "mock"
                self.thresholds = {"min_score": 6.0, "auto_accept": 8.0}
                self.min_score = 6.0
                self.auto_accept = 8.0

        return MockQualityFilter()

    def test_good_content_passes(self, qf):
        content = "认知升级是每个人都应该关注的话题。" * 20  # ~280 chars
        title = "如何突破认知局限"
        passes, reason = qf.heuristic_check(content, title)
        assert passes
        assert reason == ""

    def test_too_short_rejected(self, qf):
        passes, reason = qf.heuristic_check("短内容", "短")
        assert not passes
        assert "过短" in reason

    def test_too_long_rejected(self, qf):
        content = "认知升级" * 3200  # 12800 chars, over the 12000 limit
        title = "正常标题"
        passes, reason = qf.heuristic_check(content, title)
        assert not passes
        assert "过长" in reason

    def test_title_too_short_rejected(self, qf):
        content = "这是一个正常长度的内容，涵盖了足够多的中文字符来通过基本检查。" * 10
        passes, reason = qf.heuristic_check(content, "AB")
        assert not passes
        assert "标题" in reason

    def test_spam_rejected(self, qf):
        content = (
            "加微信: test123 扫码领取 限时免费 好内容" * 10
        )
        title = "免费赚钱秘籍"
        passes, reason = qf.heuristic_check(content, title)
        assert not passes
        assert "广告" in reason

    def test_spam_below_threshold_passes(self, qf):
        """Only 2 spam indicators — should pass (threshold is 3)."""
        content = (
            "加微信: test123 限时免费 这是一篇很有价值的内容，讲述了认知升级的重要性。"
            "认知升级是每个人都应该关注的话题，它关系到我们的成长和未来。" * 10
        )
        title = "认知升级指南"
        passes, reason = qf.heuristic_check(content, title)
        assert passes

    def test_too_few_chinese_chars_rejected(self, qf):
        content = "hello world this is english content " * 20
        title = "Test Title"
        passes, reason = qf.heuristic_check(content, title)
        assert not passes
        assert "中文" in reason

    def test_normal_chinese_content_passes(self, qf):
        content = (
            "认知升级是一个非常重要的概念。在当今快速变化的世界中，"
            "我们需要不断提升自己的认知水平，才能跟上时代的步伐。"
            "这不仅仅是学习新知识，更是改变思维方式和看问题的角度。" * 10
        )
        title = "认知升级的五个关键步骤"
        passes, reason = qf.heuristic_check(content, title)
        assert passes
