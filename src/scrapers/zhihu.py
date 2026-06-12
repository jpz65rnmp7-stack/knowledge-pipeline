"""Zhihu scraper — uses Zhihu search API for content discovery."""

import logging
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedArticle

logger = logging.getLogger(__name__)


class ZhihuScraper(BaseScraper):
    """Scrape 知乎 search results via web page parsing."""

    SEARCH_URL = "https://www.zhihu.com/search"
    ZHIHU_URL = "https://www.zhihu.com"

    def __init__(self, platform_config: dict):
        super().__init__(platform_config)
        self._concurrency = platform_config.get("concurrency", 2)  # Zhihu is rate-sensitive

    @property
    def platform_name(self) -> str:
        return "zhihu"

    def _get_headers(self) -> dict:
        return {
            **super()._get_headers(),
            "Referer": "https://www.zhihu.com/",
        }

    async def _rate_limit(self):
        import asyncio
        import random
        await asyncio.sleep(random.uniform(1.0, 2.0))

    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search 知乎 for one keyword."""
        params = {"type": "content", "q": keyword}
        client = await self._get_client()
        try:
            resp = await client.get(self.SEARCH_URL, params=params)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"[知乎] Request failed for '{keyword}': {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        cards = soup.select(".List-item, .SearchResult-card, [data-za-detail-view-path-module]")
        if not cards:
            cards = soup.select("div[class*='List'] div[class*='ContentItem']")

        for card in cards[:self.max_results]:
            try:
                article = self._parse_item(card)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[知乎] Parse card error: {e}")

        return articles

    def _parse_item(self, card) -> Optional[ScrapedArticle]:
        """Parse a 知乎 search result card."""
        title_el = card.select_one("h2, .HighlightTitle, [class*='title'] a, a[class*='Title']")
        if not title_el:
            title_el = card.select_one("a[href*='/answer/'], a[href*='/p/'], a[href*='/question/']")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        url = title_el.get("href", "")
        if url and not url.startswith("http"):
            url = self.ZHIHU_URL + url

        if not title or len(title) < 5:
            return None

        content_el = card.select_one(
            ".RichText, .content, [class*='excerpt'], [class*='summary'], [class*='Content']"
        )
        if content_el:
            content_text = content_el.get_text(separator=" ", strip=True)
        else:
            content_text = card.get_text(separator=" ", strip=True)
            if len(content_text) > 1000:
                content_text = content_text[:1000]

        full_content = f"{title}\n\n{content_text}"
        if len(full_content) < 80:
            return None

        return ScrapedArticle(
            platform=self.platform_name,
            title=title,
            url=url,
            content=full_content,
        )
