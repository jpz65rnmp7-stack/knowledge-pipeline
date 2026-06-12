"""去AI味改写 — rewrite scraped content in 景一's voice."""

import logging
import re
from typing import Optional

from .utils import LLMClient, Config

logger = logging.getLogger(__name__)


class ContentHumanizer:
    """Rewrite AI/scraped content into 景一's human, opinionated style."""

    def __init__(self, llm: LLMClient, config: Config, vault_path: str):
        self.llm = llm
        self.config = config
        self.vault_path = vault_path
        self.humanize_prompt = config.load_prompt("humanize")
        # Load style examples once
        self.style_examples = config.get_style_examples(vault_path)

    def humanize(
        self,
        original_title: str,
        original_content: str,
        platform: str,
        category: str,
    ) -> Optional[dict]:
        """
        Rewrite content in 景一's style.

        Returns dict with: title, body, golden_quote, monetization, koubo_title
        Returns None on failure.
        """
        # Clean input content
        content = self._clean_input(original_content)

        # Build the user message
        user_msg = (
            f"### 原始信息\n\n"
            f"- 原标题: {original_title}\n"
            f"- 来源平台: {platform}\n"
            f"- 分类: {category}\n"
            f"- 原始内容:\n\n{content}\n"
        )

        # Replace placeholder in prompt
        prompt = self.humanize_prompt.replace("{style_examples}", self.style_examples)

        try:
            response = self.llm.call(prompt, user_msg, max_tokens=3072)
            return self._parse_response(response, original_title)
        except Exception as e:
            logger.error(f"Humanization error for '{original_title[:30]}...': {e}")
            return None

    def _clean_input(self, content: str) -> str:
        """Clean scraped content before feeding to LLM."""
        # Remove excessive whitespace
        content = re.sub(r"\n{4,}", "\n\n\n", content)
        content = re.sub(r" {2,}", " ", content)
        # Truncate if too long (LLM context limit)
        if len(content) > 3000:
            # Take first 1500 + last 500
            content = content[:1500] + "\n\n...(中略)...\n\n" + content[-500:]
        return content.strip()

    def _parse_response(self, response: str, fallback_title: str) -> Optional[dict]:
        """Parse the humanized response into structured fields."""
        if not response or len(response) < 100:
            return None

        lines = response.strip().split("\n")

        # Extract title (first H1 line)
        title = fallback_title[:20]
        body_start = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title = line[2:].strip()
                body_start = i + 1
                break

        body = "\n".join(lines[body_start:]).strip()

        # Clean up prompt artifacts that sometimes leak through
        body = re.sub(r"^#{1,3}\s*(改写结果|改写内容|输出|正文)[：:]?\s*", "", body)
        body = re.sub(r"\n#{1,3}\s*(改写结果|改写内容|输出|正文)[：:]?\s*", "\n", body)

        # Extract 变现方向 and 口播标题 and 即梦提示词 if present
        monetization = ""
        koubo_title = ""
        jimeng_prompt = ""

        m_match = re.search(r"\*\*变现方向[：:]\s*(.+?)\*\*", body)
        if m_match:
            monetization = m_match.group(1).strip()

        k_match = re.search(r"\*\*口播标题[：:]\s*(.+?)\*\*", body)
        if k_match:
            koubo_title = k_match.group(1).strip()

        j_match = re.search(r"\*\*即梦提示词[：:]\s*(.+?)\*\*", body)
        if j_match:
            jimeng_prompt = j_match.group(1).strip()

        # Extract a golden quote (bold text)
        gold_match = re.findall(r"\*\*(.+?)\*\*", body)
        golden_quote = gold_match[0] if gold_match else ""

        return {
            "title": title,
            "body": body,
            "golden_quote": golden_quote,
            "monetization_directions": monetization,
            "koubo_title": koubo_title,
            "jimeng_prompt": jimeng_prompt,
        }
