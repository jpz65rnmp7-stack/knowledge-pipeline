"""Bilibili scraper — uses B站 search API for article discovery."""

import asyncio
import logging
from typing import Optional

import httpx

from .base import BaseScraper, ScrapedArticle
from .utils import random_ua, clean_html, rate_limit_async

logger = logging.getLogger(__name__)


class BilibiliScraper(BaseScraper):
    """Scrape B站 search results via API."""

    SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all/v2"
    VIDEO_URL = "https://www.bilibili.com/video/{}"

    def __init__(self, platform_config: dict):
        super().__init__(platform_config)
        self._client = None

    @property
    def platform_name(self) -> str:
        return "bilibili"

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random_ua(),
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def search_keywords(self, keywords: list[str]) -> list[ScrapedArticle]:
        """Search B站 for all keywords concurrently."""
        articles = []
        seen_urls = set()

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=15) as client:
            self._client = client

            # Search keywords concurrently with a semaphore to avoid rate limiting
            semaphore = asyncio.Semaphore(3)

            async def _search_with_limit(kw: str) -> list[ScrapedArticle]:
                async with semaphore:
                    try:
                        results = await self._search_single(kw)
                        await rate_limit_async(0.8, 1.5)
                        return results
                    except Exception as e:
                        logger.error(f"[B站] Search error for '{kw}': {e}")
                        return []

            tasks = [_search_with_limit(kw) for kw in keywords]
            results_per_kw = await asyncio.gather(*tasks)

            for results in results_per_kw:
                for article in results:
                    if article.url not in seen_urls:
                        seen_urls.add(article.url)
                        articles.append(article)

        logger.info(f"[B站] Total unique articles: {len(articles)} from {len(keywords)} keywords")
        return articles

    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search B站 for one keyword, return parsed articles."""
        params = {
            "keyword": keyword,
            "page": 1,
            "search_type": "video",
            "order": "click",
            "duration": 0,
        }
        try:
            resp = await self._client.get(self.SEARCH_API, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[B站] API request failed for '{keyword}': {e}")
            return []

        if data.get("code") != 0:
            logger.warning(f"[B站] API error for '{keyword}': code={data.get('code')}")
            return []

        result_data = data.get("data", {}).get("result", [])
        articles = []

        for item_group in result_data:
            if item_group.get("result_type") != "video":
                continue

            for item in item_group.get("data", [])[:self.max_results]:
                try:
                    article = self._parse_video_item(item)
                    if article and article.content:
                        articles.append(article)
                except Exception as e:
                    logger.debug(f"[B站] Parse error: {e}")
                    continue

        return articles

    def _parse_video_item(self, item: dict) -> Optional[ScrapedArticle]:
        """Parse a B站 video item from API response."""
        bvid = item.get("bvid", "")
        if not bvid:
            return None

        title = clean_html(item.get("title", ""))
        description = clean_html(item.get("description", ""))

        tag_list = item.get("tag", "").split(",") if item.get("tag") else []
        author = item.get("author", "")
        pubdate = item.get("pubdate", 0)
        play = item.get("play", 0)

        content_parts = [title]
        if description:
            content_parts.append(description)
        if play > 10000:
            content_parts.append(f"(播放量: {play:,})")

        content = "\n\n".join(content_parts)

        if len(content) < 100:
            return None

        publish_date = None
        if pubdate:
            from datetime import datetime
            try:
                publish_date = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d")
            except Exception:
                pass

        return ScrapedArticle(
            platform=self.platform_name,
            title=title,
            url=self.VIDEO_URL.format(bvid),
            content=content,
            author=author,
            publish_date=publish_date,
            tags_original=tag_list,
        )
