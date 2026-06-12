"""Abstract base class for platform scrapers with shared HTTP client management."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from .utils import random_ua

logger = logging.getLogger(__name__)


@dataclass
class ScrapedArticle:
    """A single article scraped from a platform."""

    platform: str
    title: str
    url: str
    content: str  # Cleaned main text
    author: Optional[str] = None
    publish_date: Optional[str] = None
    summary: Optional[str] = None
    tags_original: list[str] = field(default_factory=list)
    scrape_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # Pipeline processing fields (populated during execution)
    major_category: str = ""
    sub_category: str = ""
    tags: list[str] = field(default_factory=list)
    is_monetizable: bool = False
    quality_score: float = 0.0

    @property
    def content_prefix(self) -> str:
        return self.content[:200]

    @property
    def url_hash(self) -> str:
        import hashlib
        return hashlib.sha256(self.url.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "author": self.author,
            "publish_date": self.publish_date,
            "summary": self.summary,
            "tags_original": self.tags_original,
            "scrape_timestamp": self.scrape_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapedArticle":
        return cls(**data)


class BaseScraper(ABC):
    """Abstract scraper with shared HTTP client, headers, and concurrency patterns.

    Subclasses must implement:
      - platform_name (property)
      - search_keywords() (async)
      - _search_single() (async, one keyword)
      - _parse_item() (sync, one result item → Optional[ScrapedArticle])
    """

    # ─── subclass overrides ──────────────────────────────────

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return platform identifier string (e.g. 'bilibili', 'zhihu')."""
        ...

    @abstractmethod
    async def _search_single(self, keyword: str) -> list[ScrapedArticle]:
        """Search one keyword, return parsed articles."""
        ...

    # ─── shared implementation ────────────────────────────────

    def __init__(self, platform_config: dict):
        self.config = platform_config
        self.max_results = platform_config.get("max_results_per_keyword", 10)
        self._concurrency = platform_config.get("concurrency", 3)
        self._timeout = platform_config.get("timeout", 15)
        self._client: Optional[httpx.AsyncClient] = None

    # HTTP headers — override in subclass if needed
    def _get_headers(self) -> dict:
        return {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared AsyncClient for this scraper run."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._get_headers(),
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def _close_client(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def get_keywords(self) -> list[str]:
        return [kw for kw in self.config.get("search_keywords", []) if kw.strip()]

    async def search_keywords(self, keywords: list[str]) -> list[ScrapedArticle]:
        """Search all keywords concurrently with semaphore rate limiting.

        Subclasses can override for sequential search (e.g. Sogou WeChat).
        """
        articles = []
        seen_urls = set()

        try:
            client = await self._get_client()
            semaphore = asyncio.Semaphore(self._concurrency)

            async def _search_with_limit(kw: str) -> list[ScrapedArticle]:
                async with semaphore:
                    try:
                        results = await self._search_single(kw)
                        await self._rate_limit()
                        return results
                    except Exception as e:
                        logger.error(f"[{self.platform_name}] Search error for '{kw}': {e}")
                        return []

            tasks = [_search_with_limit(kw) for kw in keywords]
            results_per_kw = await asyncio.gather(*tasks)

            for results in results_per_kw:
                for article in results:
                    norm_url = self._normalize_url(article.url)
                    if norm_url not in seen_urls:
                        seen_urls.add(norm_url)
                        articles.append(article)
        finally:
            await self._close_client()

        logger.info(
            f"[{self.platform_name}] {len(articles)} unique articles from {len(keywords)} keywords"
        )
        return articles

    async def _rate_limit(self):
        """Sleep briefly between requests. Override for platform-specific delays."""
        await asyncio.sleep(1.0)

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for dedup. Override for platform-specific stripping."""
        return url.split("?")[0]
