"""WeChat Official Accounts scraper via Sogou WeChat search.

Overrides search_keywords() for sequential search — Sogou is very rate-sensitive.
"""

import logging
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedArticle
from .utils import random_ua

logger = logging.getLogger(__name__)


class WechatSogouScraper(BaseScraper):
    """Scrape 公众号 articles via 搜狗微信搜索."""

    SOGOU_WEIXIN = "https://weixin.sogou.com/weixin"

    def __init__(self, platform_config: dict):
        super().__init__(platform_config)
        self._concurrency = 1  # Force single — Sogou anti-bot is aggressive

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
        """Sequential search — Sogou is too rate-sensitive for concurrency."""
        articles = []
        seen_urls = set()

        try:
            client = await self._get_client()

            for kw in keywords:
                try:
                    results = await self._search_single(kw)
                    for article in results:
                        norm_url = article.url.split("&chksm")[0]
                        if norm_url not in seen_urls:
                            seen_urls.add(norm_url)
                            articles.append(article)
                    await self._rate_limit()
                except Exception as e:
                    logger.error(f"[公众号] Search error for '{kw}': {e}")
                    continue
        finally:
            await self._close_client()

        logger.info(
            f"[公众号] {len(articles)} unique articles from {len(keywords)} keywords"
        )
        return articles

    async def _rate_limit(self):
        import asyncio
        import random
        await asyncio.sleep(random.uniform(2.0, 3.5))

    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search Sogou WeChat for one keyword."""
        params = {"type": 2, "query": keyword, "ie": "utf8"}
        client = await self._get_client()
        try:
            resp = await client.get(self.SOGOU_WEIXIN, params=params)
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

        return articles

    def _parse_item(self, item) -> Optional[ScrapedArticle]:
        """Parse a Sogou search result item."""
        title_el = item.select_one("h3 a, .tit a, a[href*='mp.weixin.qq.com']")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        url = title_el.get("href", "")

        if not title or len(title) < 5:
            return None

        desc_el = item.select_one(".txt-info, .s-p, p[class*='info'], .summary")
        description = desc_el.get_text(strip=True) if desc_el else ""

        author_el = item.select_one(".account, .s2, [class*='account'], [class*='name']")
        author = author_el.get_text(strip=True) if author_el else ""

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
