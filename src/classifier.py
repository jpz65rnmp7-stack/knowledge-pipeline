"""LLM-based content classifier for 知识付费 categories."""

import logging
from typing import Optional

from .utils import LLMClient, Config, LLMError

logger = logging.getLogger(__name__)


class ContentClassifier:
    """Classify articles into 8 major categories + sub-categories using LLM."""

    # Reject classification below this confidence threshold
    MIN_CONFIDENCE = 0.4

    def __init__(self, llm: LLMClient, config: Config):
        self.llm = llm
        self.config = config
        self.classify_prompt = config.load_prompt("classify")
        self._category_tree = self._build_category_tree()

    def _build_category_tree(self) -> str:
        parts = []
        for cat in self.config.categories["major_categories"]:
            cat_id = cat["id"]
            cat_desc = cat["description"]
            subs = cat.get("sub_categories", [])
            sub_str = ", ".join(subs)
            parts.append(f"- **{cat_id}** ({cat_desc})")
            parts.append(f"  子分类: {sub_str}")
            parts.append("")
        return "\n".join(parts)

    def classify(self, title: str, content: str, platform: str) -> Optional[dict]:
        """Classify an article. Returns dict or None on failure (logged, not fatal)."""
        content_summary = content[:800] if len(content) > 800 else content
        prompt = self.classify_prompt.replace("{category_tree}", self._category_tree)
        user_msg = f"标题：{title}\n来源平台：{platform}\n内容摘要：{content_summary}"

        try:
            result = self.llm.call_json(prompt, user_msg, max_tokens=1024)
        except LLMError as e:
            logger.warning(f"Classification LLM error for '{title[:30]}...': {e}")
            return None
        except Exception as e:
            logger.error(f"Classification unexpected error for '{title[:30]}...': {e}")
            return None

        if not isinstance(result, dict):
            logger.warning(f"Classification returned non-dict: {type(result)}")
            return None

        confidence = result.get("confidence", 0)
        if confidence < self.MIN_CONFIDENCE:
            logger.debug(f"Low confidence ({confidence:.2f}) for '{title[:30]}...'")
            return None

        return result

    def get_major_categories(self) -> list[str]:
        return [c["id"] for c in self.config.categories["major_categories"]]
