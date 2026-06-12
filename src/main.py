#!/usr/bin/env python3
"""
知识付费内容聚合 Pipeline · 主入口

用法:
  python -m src.main                    # 完整 pipeline
  python -m src.main --dry-run          # 预览模式 (不写入 Obsidian)
  python -m src.main --platform bilibili  # 只跑指定平台
  python -m src.main --dry-run --no-parallel  # 串行模式（调试用）
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .utils import LLMClient, Config, setup_logging
from .scrapers.bilibili import BilibiliScraper
from .scrapers.zhihu import ZhihuScraper
from .scrapers.wechat_sogou import WechatSogouScraper
from .classifier import ContentClassifier
from .dedup import DedupTracker
from .quality import QualityFilter
from .humanizer import ContentHumanizer
from .writer import ObsidianWriter

logger = logging.getLogger(__name__)

# Concurrency controls
SCRAPE_CONCURRENCY = 3   # Max concurrent scraper instances
LLM_CONCURRENCY = 4      # Max concurrent LLM API calls


class Pipeline:
    """Orchestrates the full scrape → classify → dedup → quality → humanize → write pipeline."""

    def __init__(
        self,
        config_dir: str = "config",
        vault_path: str = "/Users/jingyi/Documents/景一obsidian/景一",
        data_dir: str = "data",
        dry_run: bool = False,
        platform_filter: str = None,
        parallel: bool = True,
    ):
        self.config_dir = Path(config_dir)
        self.vault_path = vault_path
        self.data_dir = Path(data_dir)
        self.dry_run = dry_run
        self.platform_filter = platform_filter
        self.parallel = parallel

        # Init components
        self.config = Config(str(self.config_dir))
        self.llm = LLMClient()
        self.dedup = DedupTracker(str(self.data_dir / "seen_urls.db"))
        self.classifier = ContentClassifier(self.llm, self.config)
        self.quality_filter = QualityFilter(self.llm, self.config)
        self.humanizer = ContentHumanizer(self.llm, self.config, vault_path)
        self.writer = ObsidianWriter(vault_path)

        # Init scrapers
        self.scrapers = {}
        self._init_scrapers()

    def _init_scrapers(self):
        """Initialize platform scrapers based on config."""
        platforms_config = self.config.platforms.get("platforms", {})

        scrapers_map = {
            "bilibili": (BilibiliScraper, "bilibili"),
            "zhihu": (ZhihuScraper, "zhihu"),
            "wechat_sogou": (WechatSogouScraper, "wechat_sogou"),
        }

        for key, (ScraperClass, config_key) in scrapers_map.items():
            if self.platform_filter and key != self.platform_filter:
                continue

            platform_cfg = platforms_config.get(config_key, {})
            if platform_cfg.get("enabled", False):
                self.scrapers[key] = ScraperClass(platform_cfg)
                logger.info(f"Scraper enabled: {key}")

    async def run(self) -> dict:
        """Execute the full pipeline. Returns a summary report."""
        report = {
            "date": datetime.now().isoformat(),
            "scraped": {},
            "classified": 0,
            "dedup_removed": 0,
            "quality_rejected": 0,
            "quality_passed": 0,
            "humanized": 0,
            "written": 0,
            "errors": [],
            "articles": [],
        }

        # ─── Phase 1: Scrape (concurrent) ──────────────────────
        logger.info("=" * 60)
        logger.info("PHASE 1: Scraping platforms...")
        logger.info("=" * 60)

        all_articles = []
        scrape_tasks = []

        for name, scraper in self.scrapers.items():
            keywords = scraper.get_keywords()
            if not keywords:
                logger.warning(f"[{name}] No keywords configured, skipping")
                continue
            scrape_tasks.append((name, scraper, keywords))

        if scrape_tasks:
            if self.parallel:
                # Run all scrapers concurrently
                results = await asyncio.gather(
                    *[scraper.search_keywords(kws) for _, scraper, kws in scrape_tasks],
                    return_exceptions=True,
                )

                for (name, _, _), result in zip(scrape_tasks, results):
                    if isinstance(result, Exception):
                        msg = f"Scraper {name}: {result}"
                        report["errors"].append(msg)
                        logger.error(msg)
                        report["scraped"][name] = 0
                    else:
                        all_articles.extend(result)
                        report["scraped"][name] = len(result)
                        logger.info(f"[{name}] Found {len(result)} articles")
            else:
                # Sequential fallback
                for name, scraper, keywords in scrape_tasks:
                    try:
                        articles = await scraper.search_keywords(keywords)
                        all_articles.extend(articles)
                        report["scraped"][name] = len(articles)
                        logger.info(f"[{name}] Found {len(articles)} articles")
                    except Exception as e:
                        msg = f"Scraper {name}: {e}"
                        report["errors"].append(msg)
                        logger.error(msg)

        logger.info(f"Total scraped: {len(all_articles)} articles")

        # Apply daily limit
        max_total = self.config.platforms.get("limits", {}).get("max_total_articles", 30)
        if len(all_articles) > max_total:
            all_articles.sort(key=lambda a: len(a.content), reverse=True)
            all_articles = all_articles[:max_total]
            logger.info(f"Trimmed to {max_total} articles (daily limit)")

        # ─── Phase 2: Classify (concurrent LLM calls) ─────────
        logger.info("=" * 60)
        logger.info("PHASE 2: Classifying...")
        logger.info("=" * 60)

        semaphore = asyncio.Semaphore(LLM_CONCURRENCY) if self.parallel else None

        async def _classify_article(article):
            try:
                if semaphore:
                    async with semaphore:
                        result = self.classifier.classify(
                            article.title, article.content, article.platform
                        )
                else:
                    result = self.classifier.classify(
                        article.title, article.content, article.platform
                    )

                if result and result.get("confidence", 0) > 0.4:
                    article.major_category = result.get("major_category_id", "")
                    article.sub_category = result.get("sub_category", "")
                    article.tags = result.get("tags", [])
                    article.is_monetizable = result.get("is_monetizable", False)
                else:
                    article.major_category = ""
                    article.sub_category = ""
                    article.tags = []
                    article.is_monetizable = False
            except Exception as e:
                logger.warning(f"Classification error: {article.title[:30]}...: {e}")
                article.major_category = ""
                article.sub_category = ""
                article.tags = []
                article.is_monetizable = False

        if all_articles:
            if self.parallel:
                await asyncio.gather(*[_classify_article(a) for a in all_articles])
            else:
                for article in all_articles:
                    await _classify_article(article)

        report["classified"] = len([a for a in all_articles if a.major_category])

        # ─── Phase 3: Dedup ───────────────────────────────────
        logger.info("=" * 60)
        logger.info("PHASE 3: Deduplication...")
        logger.info("=" * 60)

        deduped = []
        for article in all_articles:
            if self.dedup.is_seen(article.url, article.title):
                report["dedup_removed"] += 1
                logger.debug(f"Dedup: {article.title[:40]}")
                continue
            deduped.append(article)

        logger.info(f"Dedup: removed {report['dedup_removed']}, remaining {len(deduped)}")

        # ─── Phase 4: Quality Filter (concurrent LLM calls) ───
        logger.info("=" * 60)
        logger.info("PHASE 4: Quality filtering...")
        logger.info("=" * 60)

        quality_articles = []

        async def _quality_check(article):
            if not article.major_category or not article.is_monetizable:
                report["quality_rejected"] += 1
                return None

            passes, score = self.quality_filter.should_process(
                article.title,
                article.content,
                article.platform,
                f"{article.major_category}/{article.sub_category}",
            )

            if passes:
                article.quality_score = score.get("total_score", 5.0)
                report["quality_passed"] += 1
                return article
            else:
                report["quality_rejected"] += 1
                return None

        if deduped:
            if self.parallel:
                results = await asyncio.gather(*[_quality_check(a) for a in deduped])
                quality_articles = [a for a in results if a is not None]
            else:
                for article in deduped:
                    result = await _quality_check(article)
                    if result:
                        quality_articles.append(result)

        logger.info(
            f"Quality: {len(quality_articles)} passed, {report['quality_rejected']} rejected"
        )

        # ─── Phase 5: Humanize + Write (sequential — each output depends on context) ─
        logger.info("=" * 60)
        logger.info("PHASE 5: Humanizing & Writing...")
        logger.info("=" * 60)

        max_output = self.config.platforms.get("limits", {}).get("max_quality_output", 15)
        to_process = quality_articles[:max_output]

        for i, article in enumerate(to_process):
            logger.info(f"[{i+1}/{len(to_process)}] Humanizing: {article.title[:40]}...")

            try:
                result = self.humanizer.humanize(
                    article.title,
                    article.content,
                    article.platform,
                    f"{article.major_category}/{article.sub_category}",
                )
            except Exception as e:
                report["errors"].append(f"Humanize failed: {article.title[:30]}: {e}")
                continue

            if not result:
                report["errors"].append(f"Humanize failed: {article.title[:30]}")
                continue

            report["humanized"] += 1

            # Write
            if not self.dry_run:
                filepath = self.writer.write_article(
                    title=result["title"],
                    body=result["body"],
                    major_category=article.major_category,
                    sub_category=article.sub_category,
                    tags=article.tags,
                    source=article.platform,
                    source_url=article.url,
                    quality_score=article.quality_score,
                )

                if filepath:
                    report["written"] += 1
                    report["articles"].append({
                        "title": result["title"],
                        "path": filepath,
                        "category": article.major_category,
                        "quality_score": article.quality_score,
                    })
                    self.dedup.mark_seen(
                        article.url, article.title, article.content, article.platform,
                    )
                else:
                    report["errors"].append(f"Write failed: {result['title']}")
            else:
                logger.info(f"  [DRY RUN] Would write: {result['title']}")
                report["articles"].append({
                    "title": result["title"],
                    "path": "(dry-run)",
                    "category": article.major_category,
                    "quality_score": article.quality_score,
                })

        return report


# ─── CLI Entry Point ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="知识付费内容聚合 Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写入 Obsidian")
    parser.add_argument("--platform", type=str, help="只跑指定平台 (bilibili/zhihu/wechat_sogou)")
    parser.add_argument("--config-dir", type=str, default="config", help="配置文件目录")
    parser.add_argument("--vault", type=str,
                       default="/Users/jingyi/Documents/景一obsidian/景一",
                       help="Obsidian vault 路径")
    parser.add_argument("--data-dir", type=str, default="data", help="数据缓存目录")
    parser.add_argument("--no-parallel", action="store_true", help="禁用并发（调试用）")

    args = parser.parse_args()

    # Change to project directory
    project_dir = Path(__file__).parent.parent
    import os
    os.chdir(project_dir)

    # Setup logging
    today = datetime.now().strftime("%Y-%m-%d")
    setup_logging(str(project_dir / "logs"), today)

    logger.info("🚀 知识付费内容聚合 Pipeline 启动")
    logger.info(f"   Vault: {args.vault}")
    logger.info(f"   Dry run: {args.dry_run}")
    logger.info(f"   Parallel: {not args.no_parallel}")
    if args.platform:
        logger.info(f"   Platform: {args.platform}")

    # Run pipeline
    pipeline = Pipeline(
        config_dir=args.config_dir,
        vault_path=args.vault,
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        platform_filter=args.platform,
        parallel=not args.no_parallel,
    )

    report = asyncio.run(pipeline.run())

    # ─── Print Summary ──────────────────────────────────────
    print("\n" + "=" * 50)
    print("📊 PIPELINE 执行报告")
    print("=" * 50)
    print(f"日期: {report['date'][:10]}")
    print(f"抓取: {report['scraped']}")
    print(f"分类: {report['classified']} 篇")
    print(f"去重移除: {report['dedup_removed']} 篇")
    print(f"质量淘汰: {report['quality_rejected']} 篇")
    print(f"质量通过: {report['quality_passed']} 篇")
    print(f"人化改写: {report['humanized']} 篇")
    print(f"写入成功: {report['written']} 篇")

    if report["errors"]:
        print(f"\n⚠️  错误 ({len(report['errors'])}):")
        for e in report["errors"][:5]:
            print(f"  - {e}")

    if report["articles"]:
        print(f"\n📝 产出文章:")
        for a in report["articles"]:
            print(f"  [{a['category']}] {a['title']} (质量: {a['quality_score']:.0f}/10)")
            print(f"    路径: {a['path']}")

    # Write daily report
    if not args.dry_run:
        pipeline.writer.write_daily_report(report)

    print(f"\n✅ Pipeline 完成！")

    if report["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
