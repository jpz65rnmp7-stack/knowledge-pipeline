"""LLM client, config loader, and utilities."""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

# ─── Exceptions ──────────────────────────────────────────────────

class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""

class LLMError(Exception):
    """Raised when the LLM API call fails after all retries."""

    def __init__(self, message: str, status_code: Optional[int] = None, raw_response: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response


# ─── Config ──────────────────────────────────────────────────────

class Config:
    """Load YAML config files from the config/ directory with validation."""

    REQUIRED_FILES = ["platforms.yaml", "categories.yaml"]
    REQUIRED_PROMPTS = ["classify", "quality", "humanize"]

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self._validate_exists()
        self.platforms = self._load("platforms.yaml")
        self.categories = self._load("categories.yaml")
        self._validate_schema()

    def _validate_exists(self):
        """Ensure the config directory and required files exist."""
        if not self.config_dir.exists():
            raise ConfigError(f"Config directory not found: {self.config_dir}")
        for filename in self.REQUIRED_FILES:
            path = self.config_dir / filename
            if not path.exists():
                raise ConfigError(f"Required config file missing: {path}")
        for name in self.REQUIRED_PROMPTS:
            path = self.config_dir / "prompts" / f"{name}.txt"
            if not path.exists():
                raise ConfigError(f"Required prompt file missing: {path}")

    def _validate_schema(self):
        """Basic schema validation for config files."""
        categories = self.categories.get("major_categories", [])
        if not categories:
            raise ConfigError("No 'major_categories' defined in categories.yaml")
        platforms = self.platforms.get("platforms", {})
        if not platforms:
            raise ConfigError("No 'platforms' defined in platforms.yaml")
        enabled = [k for k, v in platforms.items() if isinstance(v, dict) and v.get("enabled")]
        if not enabled:
            logger.warning("No platforms enabled in platforms.yaml — pipeline will produce no output")

    def _load(self, filename: str) -> dict:
        path = self.config_dir / filename
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    def load_prompt(self, name: str) -> str:
        path = self.config_dir / "prompts" / f"{name}.txt"
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            raise ConfigError(f"Prompt file not found: {path}")

    def get_style_examples(self, vault_path: str) -> str:
        """Load 2-3 existing notes as style examples for few-shot prompting."""
        examples_dir = Path(vault_path) / "08-商业认知蒸馏" / "01-商业财经"
        if not examples_dir.exists():
            logger.info("No style reference notes found in vault — using defaults")
            return "(暂无风格参考笔记 — 请先产出几篇笔记作为风格锚点)"

        files = sorted(examples_dir.glob("*.md"))[:3]
        if not files:
            logger.info("No .md files found in style reference directory")
            return "(暂无风格参考笔记)"

        parts = []
        for f in files:
            content = f.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            title_line = ""
            body_start = 0
            for i, line in enumerate(lines):
                if line.startswith("# ") and not title_line:
                    title_line = line
                if line.startswith("---") and i > 0:
                    body_start = i + 1
                    break
            excerpt = "\n".join(lines[body_start:body_start+15]) if body_start else "\n".join(lines[:15])
            parts.append(f"### 示例：{title_line}\n\n{excerpt}\n")

        return "\n".join(parts)


# ─── LLM Client ──────────────────────────────────────────────────

class LLMClient:
    """Wrapper for DeepSeek API (Anthropic-compatible endpoint) with robust retry logic."""

    # HTTP status codes that should trigger a retry
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not self.api_key:
            raise ConfigError(
                "Missing API key. Set ANTHROPIC_AUTH_TOKEN environment variable "
                "or pass api_key parameter to LLMClient()."
            )
        self.base_url = base_url or os.environ.get(
            "ANTHROPIC_BASE_URL",
            "https://api.deepseek.com/anthropic"
        )
        self.model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def call(self, system_prompt: str, user_message: str, max_tokens: int = 2048) -> str:
        """Send a request to the LLM and return the response text.

        Raises:
            LLMError: if the API call fails after all retries.
        """
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "user", "content": user_message}
            ],
        }
        if system_prompt:
            payload["system"] = system_prompt

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = httpx.post(
                    f"{self.base_url}/messages",
                    headers=headers,
                    json=payload,
                    timeout=90,  # generous timeout for long generations
                )

                if resp.status_code in self.RETRYABLE_STATUSES:
                    last_error = LLMError(
                        f"API returned {resp.status_code}",
                        status_code=resp.status_code,
                        raw_response=resp.text[:500],
                    )
                    logger.warning(
                        f"LLM call attempt {attempt+1}/{self.max_retries}: {last_error}"
                    )
                    if attempt < self.max_retries - 1:
                        backoff = self.retry_delay * (2 ** attempt)  # exponential backoff
                        time.sleep(backoff)
                        continue

                resp.raise_for_status()
                data = resp.json()

                # Extract text from Anthropic-format response
                content = data.get("content", [])
                if isinstance(content, list):
                    text_parts = [b["text"] for b in content if b.get("type") == "text"]
                    return "".join(text_parts)
                elif isinstance(content, str):
                    return content
                return str(content)

            except httpx.HTTPStatusError as e:
                last_error = LLMError(
                    f"HTTP {e.response.status_code}: {e}",
                    status_code=e.response.status_code,
                    raw_response=e.response.text[:500] if e.response.text else None,
                )
                if e.response.status_code in self.RETRYABLE_STATUSES:
                    if attempt < self.max_retries - 1:
                        backoff = self.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {backoff:.1f}s...")
                        time.sleep(backoff)
                        continue

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = LLMError(f"Connection/timeout: {e}")
                logger.warning(f"LLM call attempt {attempt+1}/{self.max_retries}: {e}")
                if attempt < self.max_retries - 1:
                    backoff = self.retry_delay * (2 ** attempt)
                    time.sleep(backoff)
                    continue

        raise LLMError(
            f"LLM call failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    def call_json(self, system_prompt: str, user_message: str, max_tokens: int = 2048) -> dict:
        """Call LLM and parse JSON response.

        Handles markdown code fences and truncated JSON gracefully.

        Raises:
            LLMError: if the API call fails or response cannot be parsed as JSON.
        """
        text = self.call(system_prompt, user_message, max_tokens=max_tokens)

        if not text or not text.strip():
            raise LLMError("Empty response from LLM")

        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON object with balanced braces
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        continue

        # Last resort: regex match
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise LLMError(
            "Failed to parse JSON from LLM response",
            raw_response=text[:500],
        )


# ─── File Utilities ───────────────────────────────────────────────

def sanitize_filename(title: str) -> str:
    """Convert a title to a safe Obsidian filename."""
    name = re.sub(r'[\\/:*?"<>|]', '', title)
    name = name.strip()
    return name[:50] if len(name) > 50 else name


def setup_logging(log_dir: str, date_str: str):
    """Configure logging to file and console."""
    log_path = Path(log_dir) / f"pipeline_{date_str}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
