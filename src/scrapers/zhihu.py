"""Zhihu scraper — uses Zhihu search API for content discovery."""

import asyncio
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedArticle
from .utils import random_ua, rate_limit_async

logger = logging.getLogger(__name__)


class ZhihuScraper(BaseScraper):
    """Scrape 知乎 search results via web page parsing."""

    SEARCH_URL = "https://www.zhihu.com/search"
    ZHIHU_URL = "https://www.zhihu.com"

    def __init__(self, platform_config: dict):
        super().__init__(platform_config)
        self._client = None

    @property
    def platform_name(self) -> str:
        return "zhihu"

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.zhihu.com/",
        }

    async def search_keywords(self, keywords: list[str]) -> list[ScrapedArticle]:
        """Search 知乎 for all keywords concurrently."""
        articles = []
        seen_urls = set()

        async with httpx.AsyncClient(
            headers=self._get_headers(),
            timeout=15,
            follow_redirects=True,
        ) as client:
            self._client = client

            semaphore = asyncio.Semaphore(2)  # Zhihu is aggressive with rate limiting

            async def _search_with_limit(kw: str) -> list[ScrapedArticle]:
                async with semaphore:
                    try:
                        results = await self._search_single(kw)
                        await rate_limit_async(1.0, 2.0)
                        return results
                    except Exception as e:
                        logger.error(f"[知乎] Search error for '{kw}': {e}")
                        return []

            tasks = [_search_with_limit(kw) for kw in keywords]
            results_per_kw = await asyncio.gather(*tasks)

            for results in results_per_kw:
                for article in results:
                    norm_url = article.url.split("?")[0]
                    if norm_url not in seen_urls:
                        seen_urls.add(norm_url)
                        articles.append(article)

        logger.info(f"[知乎] Total unique articles: {len(articles)} from {len(keywords)} keywords")
        return articles

    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search 知乎 for one keyword."""
        params = {
            "type": "content",
            "q": keyword,
        }
        try:
            resp = await self._client.get(self.SEARCH_URL, params=params)
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
                article = self._parse_search_card(card)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[知乎] Parse card error: {e}")
                continue

        return articles

    def _parse_search_card(self, card) -> Optional[ScrapedArticle]:
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
        content_text = ""
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
