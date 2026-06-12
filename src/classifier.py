"""LLM-based content classifier for 知识付费 categories."""

import logging
from typing import Optional

from .utils import LLMClient, Config

logger = logging.getLogger(__name__)


class ContentClassifier:
    """Classify articles into 8 major categories + sub-categories using LLM."""

    def __init__(self, llm: LLMClient, config: Config):
        self.llm = llm
        self.config = config
        self.classify_prompt = config.load_prompt("classify")

        # Build category tree for the prompt
        self._category_tree = self._build_category_tree()

    def _build_category_tree(self) -> str:
        """Build a text representation of all categories for the LLM prompt."""
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
        """
        Classify an article. Returns dict with major_category_id, sub_category, tags, etc.
        Returns None on failure.
        """
        # Truncate content for classification (first 800 chars is enough)
        content_summary = content[:800] if len(content) > 800 else content

        # Inject category tree into the prompt template
        prompt = self.classify_prompt.replace("{category_tree}", self._category_tree)

        user_msg = f"标题：{title}\n来源平台：{platform}\n内容摘要：{content_summary}"

        try:
            result = self.llm.call_json(prompt, user_msg, max_tokens=1024)
            if "error" in result:
                logger.warning(f"Classification failed: {result.get('error')}")
                return None
            return result
        except Exception as e:
            logger.error(f"Classification error for '{title[:30]}...': {e}")
            return None

    def get_major_categories(self) -> list[str]:
        """Return list of major category IDs."""
        return [c["id"] for c in self.config.categories["major_categories"]]
