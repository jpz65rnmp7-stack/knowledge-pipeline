"""Obsidian writer — formats and writes markdown notes to the 景一 vault."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .utils import sanitize_filename

logger = logging.getLogger(__name__)


class ObsidianWriter:
    """Write pipeline output as markdown files to Obsidian vault."""

    # Claude's output directory — separate from other agents for comparison
    OUTPUT_DIR = "Claude-商业蒸馏"

    CATEGORY_DIR_MAP = {
        "01-商业财经": "01-商业财经",
        "02-商业代码": "02-商业代码",
        "03-职业技能": "03-职业技能",
        "04-生活兴趣": "04-生活兴趣",
        "05-教育学习": "05-教育学习",
        "06-健康养生": "06-健康养生",
        "07-高潜赛道": "07-高潜赛道",
        "08-小众高客单": "08-小众高客单",
    }

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.output_root = self.vault_path / self.OUTPUT_DIR
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Create output directories for all categories."""
        self.output_root.mkdir(parents=True, exist_ok=True)
        for dir_name in self.CATEGORY_DIR_MAP.values():
            (self.output_root / dir_name).mkdir(parents=True, exist_ok=True)

    def write_article(
        self,
        title: str,
        body: str,
        major_category: str,
        sub_category: str,
        tags: list[str],
        source: str,
        source_url: str,
        quality_score: float = 0.0,
        overwrite: bool = False,
    ) -> Optional[str]:
        """
        Write a processed article to the Obsidian vault.

        Returns the file path on success, None on failure.
        """
        # Build YAML frontmatter
        today = datetime.now().strftime("%Y-%m-%d")
        tags_str = ", ".join(tags) if tags else ""
        topic = f"{major_category}/{sub_category}" if sub_category else major_category

        frontmatter = (
            f"---\n"
            f"source: \"{source}\"\n"
            f"source_url: \"{source_url}\"\n"
            f"date: {today}\n"
            f"topic: \"{topic}\"\n"
            f"tags: [{tags_str}]\n"
            f"quality_score: {quality_score:.1f}\n"
            f"---\n"
        )

        # Build full content
        full_markdown = f"{frontmatter}\n{body}\n"

        # Determine output path
        cat_dir = self.CATEGORY_DIR_MAP.get(major_category, "01-商业财经")
        filename = sanitize_filename(title) + ".md"
        output_path = self.output_root / cat_dir / filename

        # Handle filename conflicts
        if output_path.exists() and not overwrite:
            base = sanitize_filename(title)
            counter = 2
            while output_path.exists():
                output_path = self.output_root / cat_dir / f"{base}_{counter}.md"
                counter += 1

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(full_markdown, encoding="utf-8")
            logger.info(f"Written: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"Write error for '{title}': {e}")
            return None

    def write_daily_report(self, report: dict):
        """Write a daily pipeline summary report."""
        today = datetime.now().strftime("%Y-%m-%d")
        report_path = self.output_root / f"日报_{today}.md"

        lines = [
            f"# 日报 {today}",
            "",
            "## 抓取统计",
            "",
        ]

        scraped = report.get("scraped", {})
        for platform, count in scraped.items():
            lines.append(f"- **{platform}**: {count} 篇")

        lines.extend([
            "",
            "## 处理结果",
            f"- 分类成功: {report.get('classified', 0)} 篇",
            f"- 去重移除: {report.get('dedup_removed', 0)} 篇",
            f"- 质量通过: {report.get('quality_passed', report.get('humanized', 0))} 篇",
            f"- 写入成功: {report.get('written', 0)} 篇",
            "",
            "## 错误日志",
            "",
        ])

        errors = report.get("errors", [])
        if errors:
            for e in errors:
                lines.append(f"- {e}")
        else:
            lines.append("✅ 无错误")

        lines.extend([
            "",
            "---",
            f"*由 Claude 知识付费聚合 Pipeline 自动生成*",
        ])

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Report written: {report_path}")
