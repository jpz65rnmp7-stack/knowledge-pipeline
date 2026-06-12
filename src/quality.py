"""Quality filter — heuristic checks + LLM scoring."""

import logging
import re
from typing import Optional

from .utils import LLMClient, Config, LLMError

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
        """Fast heuristic checks. Returns (passes, reason)."""
        if len(content) < 150:
            return False, "内容过短 (<150字)"

        if len(content) > 12000:
            return False, "内容过长，疑似聚合"

        spam_count = sum(1 for p in SPAM_PATTERNS if re.search(p, content))
        if spam_count >= 3:
            return False, "疑似广告/垃圾内容"

        if len(title) < 3:
            return False, "标题过短"

        chinese_chars = len(re.findall(r"[一-鿿]", content))
        if chinese_chars < 100:
            return False, "中文字符不足"

        emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF]", content))
        if emoji_count > 20:
            return False, "表情符号过多"

        return True, ""

    def llm_score(self, title: str, content: str, platform: str, category: str) -> dict:
        """Use LLM to score content quality across 5 dimensions.

        Returns a dict. Falls back to a safe default on error (logged, not fatal).
        """
        content_truncated = content[:1500] if len(content) > 1500 else content
        user_msg = (
            f"标题：{title}\n"
            f"来源：{platform}\n"
            f"分类：{category}\n"
            f"内容：{content_truncated}"
        )

        try:
            return self.llm.call_json(self.quality_prompt, user_msg, max_tokens=1024)
        except LLMError as e:
            logger.warning(f"Quality LLM scoring failed for '{title[:30]}...': {e}")
            # Fail-safe: reject on LLM error so bad content doesn't silently pass
            return {
                "passed": False,
                "total_score": 0,
                "reasoning": f"评分失败: {e}",
            }
        except Exception as e:
            logger.error(f"Quality unexpected error: {e}")
            return {
                "passed": False,
                "total_score": 0,
                "reasoning": f"评分异常: {e}",
            }

    def should_process(self, title: str, content: str, platform: str, category: str) -> tuple[bool, dict]:
        """Full quality check. Returns (passes, score_detail)."""
        passes, reason = self.heuristic_check(content, title)
        if not passes:
            return False, {"passed": False, "total_score": 0, "reasoning": reason}

        score = self.llm_score(title, content, platform, category)
        total = score.get("total_score", 0)

        if total >= self.min_score:
            return True, score
        else:
            return False, score
