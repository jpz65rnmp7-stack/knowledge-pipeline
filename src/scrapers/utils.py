"""Shared scraper utilities — rate limiting, user-agent rotation, HTML cleaning."""

import asyncio
import random
import re


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]


def random_ua(mobile: bool = False) -> str:
    """Return a random User-Agent string."""
    pool = MOBILE_USER_AGENTS if mobile else USER_AGENTS
    return random.choice(pool)


async def rate_limit_async(min_sec: float, max_sec: float):
    """Async sleep for a random duration between min_sec and max_sec."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


def clean_html(text: str) -> str:
    """Basic HTML tag removal and whitespace normalization."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
