"""Tests for utils.py — config loading, sanitize_filename, LLM client validation."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.utils import Config, ConfigError, sanitize_filename, LLMClient


class TestSanitizeFilename:
    def test_normal_title(self):
        assert sanitize_filename("情绪溢价：为什么越贵越有人买") == "情绪溢价：为什么越贵越有人买"

    def test_special_chars_stripped(self):
        result = sanitize_filename("搞钱/心法*?:真相")
        assert "/" not in result
        assert "*" not in result
        assert "?" not in result
        assert ":" not in result

    def test_truncation(self):
        long_title = "A" * 60
        result = sanitize_filename(long_title)
        assert len(result) == 50

    def test_empty_title(self):
        assert sanitize_filename("") == ""

    def test_whitespace_only(self):
        assert sanitize_filename("   ") == ""


class TestConfig:
    def make_config_dir(self, tmp_path, platforms=None, categories=None, prompts=None):
        """Create a minimal config directory for testing."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        default_platforms = {
            "platforms": {
                "bilibili": {"enabled": True, "search_keywords": ["测试"], "max_results_per_keyword": 3}
            },
            "limits": {"max_total_articles": 30, "max_quality_output": 15},
        }
        default_categories = {
            "major_categories": [
                {"id": "01-商业财经", "description": "搞钱", "sub_categories": ["副业赚钱"]}
            ],
            "quality_thresholds": {"min_score": 6.0},
        }

        with open(config_dir / "platforms.yaml", "w") as f:
            yaml.dump(platforms or default_platforms, f)
        with open(config_dir / "categories.yaml", "w") as f:
            yaml.dump(categories or default_categories, f)

        prompts_dir = config_dir / "prompts"
        prompts_dir.mkdir()
        for name in ["classify", "quality", "humanize"]:
            prompt_content = prompts.get(name, "test prompt") if prompts else "test prompt"
            with open(prompts_dir / f"{name}.txt", "w") as f:
                f.write(prompt_content)

        return config_dir

    def test_loads_valid_config(self, tmp_path):
        config_dir = self.make_config_dir(tmp_path)
        config = Config(str(config_dir))
        assert config.platforms is not None
        assert config.categories is not None

    def test_missing_config_dir(self):
        with pytest.raises(ConfigError, match="not found"):
            Config("/nonexistent/path/config")

    def test_missing_required_file(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # platforms.yaml missing
        with open(config_dir / "categories.yaml", "w") as f:
            yaml.dump({"major_categories": []}, f)
        with pytest.raises(ConfigError, match="platforms"):
            Config(str(config_dir))

    def test_no_enabled_platforms_warns(self, tmp_path, caplog):
        config_dir = self.make_config_dir(
            tmp_path,
            platforms={
                "platforms": {"bilibili": {"enabled": False}},
                "limits": {},
            },
        )
        import logging
        with caplog.at_level(logging.WARNING):
            Config(str(config_dir))
        assert any("No platforms enabled" in r.message for r in caplog.records)

    def test_load_prompt(self, tmp_path):
        config_dir = self.make_config_dir(
            tmp_path, prompts={"classify": "你是分类器"}
        )
        config = Config(str(config_dir))
        assert config.load_prompt("classify") == "你是分类器"

    def test_missing_prompt(self, tmp_path):
        config_dir = self.make_config_dir(tmp_path)
        config = Config(str(config_dir))
        with pytest.raises(ConfigError, match="not found"):
            config.load_prompt("nonexistent")


class TestLLMClient:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        with pytest.raises(ConfigError, match="API key"):
            LLMClient(api_key=None)
