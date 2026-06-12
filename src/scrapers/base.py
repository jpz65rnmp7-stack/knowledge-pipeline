"""Abstract base class for platform scrapers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ScrapedArticle:
    """A single article scraped from a platform."""
    platform: str
    title: str
    url: str
    content: str           # Cleaned main text
    author: Optional[str] = None
    publish_date: Optional[str] = None
    summary: Optional[str] = None
    tags_original: list[str] = field(default_factory=list)
    scrape_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # Pipeline processing fields (set during pipeline execution)
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
    """Abstract scraper — each platform implements search_keywords()."""

    def __init__(self, platform_config: dict):
        self.config = platform_config
        self.max_results = platform_config.get("max_results_per_keyword", 10)

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return platform identifier string."""
        ...

    @abstractmethod
    async def search_keywords(self, keywords: list[str]) -> list[ScrapedArticle]:
        """Search the platform for given keywords. Async — supports concurrent scraping."""
        ...

    def get_keywords(self) -> list[str]:
        """Get configured search keywords for this platform."""
        return self.config.get("search_keywords", [])
