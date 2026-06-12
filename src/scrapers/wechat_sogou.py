"""WeChat Official Accounts scraper via Sogou WeChat search."""

import asyncio
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedArticle
from .utils import random_ua, rate_limit_async

logger = logging.getLogger(__name__)


class WechatSogouScraper(BaseScraper):
    """Scrape 公众号 articles via 搜狗微信搜索."""

    SOGOU_WEIXIN = "https://weixin.sogou.com/weixin"

    def __init__(self, platform_config: dict):
        super().__init__(platform_config)
        self._client = None

    @property
    def platform_name(self) -> str:
        return "wechat"

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://weixin.sogou.com/",
        }

    async def search_keywords(self, keywords: list[str]) -> list[ScrapedArticle]:
        """Search 搜狗微信 for all keywords — sequential to avoid anti-bot triggers."""
        articles = []
        seen_urls = set()

        async with httpx.AsyncClient(
            headers=self._get_headers(),
            timeout=20,
            follow_redirects=True,
        ) as client:
            self._client = client

            # Sogou is very rate-sensitive — search sequentially
            for kw in keywords:
                try:
                    results = await self._search_single(kw)
                    for article in results:
                        norm_url = article.url.split("&chksm")[0]
                        if norm_url not in seen_urls:
                            seen_urls.add(norm_url)
                            articles.append(article)
                    await rate_limit_async(2.0, 3.5)
                except Exception as e:
                    logger.error(f"[公众号] Search error for '{kw}': {e}")
                    continue

        logger.info(f"[公众号] Total unique articles: {len(articles)} from {len(keywords)} keywords")
        return articles

    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search Sogou WeChat for one keyword."""
        params = {
            "type": 2,
            "query": keyword,
            "ie": "utf8",
        }
        try:
            resp = await self._client.get(self.SOGOU_WEIXIN, params=params)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"[公众号] Request failed for '{keyword}': {e}")
            return []

        if "请输入验证码" in resp.text or "antispider" in resp.text.lower():
            logger.warning(f"[公众号] Anti-bot page detected for '{keyword}', skipping")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        items = soup.select(".news-list li, .news-list2 li, ul.news-list li")
        if not items:
            items = soup.select("[class*='news-list'] li, [class*='txt-box']")

        for item in items[:self.max_results]:
            try:
                article = self._parse_item(item)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[公众号] Parse item error: {e}")
                continue

        return articles

    def _parse_item(self, item) -> Optional[ScrapedArticle]:
        """Parse a Sogou search result item."""
        title_el = item.select_one("h3 a, .tit a, a[href*='mp.weixin.qq.com']")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        raw_url = title_el.get("href", "")

        url = raw_url  # Keep as-is; Sogou redirects work fine

        if not title or len(title) < 5:
            return None

        desc_el = item.select_one(".txt-info, .s-p, p[class*='info'], .summary")
        description = ""
        if desc_el:
            description = desc_el.get_text(strip=True)

        author_el = item.select_one(".account, .s2, [class*='account'], [class*='name']")
        author = ""
        if author_el:
            author = author_el.get_text(strip=True)

        content = f"{title}\n\n{description}"
        if author:
            content = f"作者: {author}\n\n{content}"

        if len(content) < 80:
            return None

        return ScrapedArticle(
            platform=self.platform_name,
            title=title,
            url=url,
            content=content,
            author=author,
        )
