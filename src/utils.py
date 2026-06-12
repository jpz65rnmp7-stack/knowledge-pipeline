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

# ─── Config ────────────────────────────────────────────────────

class Config:
    """Load YAML config files from the config/ directory."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self.platforms = self._load("platforms.yaml")
        self.categories = self._load("categories.yaml")

    def _load(self, filename: str) -> dict:
        path = self.config_dir / filename
        with open(path) as f:
            return yaml.safe_load(f)

    def load_prompt(self, name: str) -> str:
        path = self.config_dir / "prompts" / f"{name}.txt"
        with open(path) as f:
            return f.read()

    def get_style_examples(self, vault_path: str) -> str:
        """Load 2-3 existing notes as style examples for few-shot prompting."""
        examples_dir = Path(vault_path) / "08-商业认知蒸馏" / "01-商业财经"
        if not examples_dir.exists():
            return "(暂无风格参考笔记)"

        files = sorted(examples_dir.glob("*.md"))[:3]
        if not files:
            return "(暂无风格参考笔记)"

        parts = []
        for f in files:
            content = f.read_text(encoding="utf-8")
            # Take title + first 300 chars as example
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


# ─── LLM Client ────────────────────────────────────────────────

class LLMClient:
    """Wrapper for DeepSeek API (Anthropic-compatible endpoint)."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not self.api_key:
            raise ValueError(
                "Missing API key. Set ANTHROPIC_AUTH_TOKEN environment variable "
                "or pass api_key parameter to LLMClient()."
            )
        self.base_url = base_url or os.environ.get(
            "ANTHROPIC_BASE_URL",
            "https://api.deepseek.com/anthropic"
        )
        self.model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")
        self.max_retries = 3
        self.retry_delay = 2

    def call(self, system_prompt: str, user_message: str, max_tokens: int = 2048) -> str:
        """Send a request to the LLM and return the response text."""
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
        # Include system prompt as a separate field if the API supports it
        if system_prompt:
            payload["system"] = system_prompt

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = httpx.post(
                    f"{self.base_url}/messages",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
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

            except Exception as e:
                last_error = e
                logger.warning(f"LLM call attempt {attempt+1}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts: {last_error}")

    def call_json(self, system_prompt: str, user_message: str, max_tokens: int = 2048) -> dict:
        """Call LLM and parse JSON response. Handles markdown code fences and truncation."""
        text = self.call(system_prompt, user_message, max_tokens=max_tokens)
        if not text or not text.strip():
            return {"error": "Empty response from LLM"}

        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
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

        logger.error(f"Failed to parse JSON: {text[:300]}")
        return {"error": "JSON parse failed", "raw": text[:500]}


# ─── File Utilities ─────────────────────────────────────────────

def sanitize_filename(title: str) -> str:
    """Convert a title to a safe Obsidian filename."""
    # Remove special chars, limit length
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
