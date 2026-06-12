"""Quality filter — heuristic checks + LLM scoring."""

import logging
import re
from typing import Optional

from .utils import LLMClient, Config

logger = logging.getLogger(__name__)

# Spam indicators in Chinese content
SPAM_PATTERNS = [
    r"加微信[：:]\s*\w+",
    r"扫码[领加获].{0,10}",
    r"限时免费",
    r"点击购买",
    r"添加.*微信",
    r"关注公众号.{0,5}回复",
    r"转发.*朋友圈",
]


class QualityFilter:
    """Two-stage quality filter: heuristic (fast) + LLM (deep)."""

    def __init__(self, llm: LLMClient, config: Config):
        self.llm = llm
        self.config = config
        self.quality_prompt = config.load_prompt("quality")
        self.thresholds = config.categories.get("quality_thresholds", {})
        self.min_score = self.thresholds.get("min_score", 6.0)
        self.auto_accept = self.thresholds.get("auto_accept", 8.0)

    def heuristic_check(self, content: str, title: str) -> tuple[bool, str]:
        """
        Fast heuristic checks. Returns (passes, reason).
        Rejects obvious garbage before expensive LLM calls.
        """
        # Length check
        if len(content) < 150:
            return False, "内容过短 (<150字)"

        if len(content) > 12000:
            return False, "内容过长，疑似聚合"

        # Spam check
        spam_count = 0
        for pattern in SPAM_PATTERNS:
            if re.search(pattern, content):
                spam_count += 1
        if spam_count >= 3:
            return False, "疑似广告/垃圾内容"

        # Title quality
        if len(title) < 3:
            return False, "标题过短"

        # Gibberish check — high ratio of non-Chinese non-punctuation chars
        chinese_chars = len(re.findall(r"[一-鿿]", content))
        if chinese_chars < 100:
            return False, "中文字符不足"

        # Too many emoji = likely low-quality social media repost
        emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF]", content))
        if emoji_count > 20:
            return False, "表情符号过多"

        return True, ""

    def llm_score(self, title: str, content: str, platform: str, category: str) -> dict:
        """Use LLM to score content quality across 5 dimensions."""
        # Truncate content for the LLM call
        content_truncated = content[:1500] if len(content) > 1500 else content

        user_msg = (
            f"标题：{title}\n"
            f"来源：{platform}\n"
            f"分类：{category}\n"
            f"内容：{content_truncated}"
        )

        try:
            result = self.llm.call_json(self.quality_prompt, user_msg, max_tokens=1024)
            if "error" in result:
                logger.warning(f"Quality scoring failed: {result.get('error')}")
                return {"passed": True, "total_score": 5.0, "reasoning": "LLM评分失败，默认放行"}
            return result
        except Exception as e:
            logger.error(f"Quality scoring error: {e}")
            return {"passed": True, "total_score": 5.0, "reasoning": f"评分错误: {e}"}

    def should_process(self, title: str, content: str, platform: str, category: str) -> tuple[bool, dict]:
        """
        Full quality check. Returns (passes, score_detail).
        """
        # Stage 1: Heuristic
        passes, reason = self.heuristic_check(content, title)
        if not passes:
            return False, {"passed": False, "total_score": 0, "reasoning": reason}

        # Stage 2: LLM scoring
        score = self.llm_score(title, content, platform, category)
        total = score.get("total_score", 5.0)

        if total >= self.min_score:
            return True, score
        else:
            return False, score
