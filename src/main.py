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
import os
import signal
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .utils import LLMClient, Config, setup_logging, ConfigError
from .scrapers.bilibili import BilibiliScraper
from .scrapers.zhihu import ZhihuScraper
from .scrapers.wechat_sogou import WechatSogouScraper
from .classifier import ContentClassifier
from .dedup import DedupTracker
from .quality import QualityFilter
from .humanizer import ContentHumanizer
from .writer import ObsidianWriter

logger = logging.getLogger(__name__)

SCRAPE_CONCURRENCY = 3
LLM_CONCURRENCY = 4


class PipelineInterrupted(Exception):
    """Raised when the pipeline is interrupted by a signal."""


class Pipeline:
    """Orchestrates the full scrape → classify → dedup → quality → humanize → write pipeline."""

    def __init__(
        self,
        config_dir: str = "config",
        vault_path: str = "/Users/jingyi/Documents/景一obsidian/景一",
        data_dir: str = "data",
        dry_run: bool = False,
        platform_filter: Optional[str] = None,
        parallel: bool = True,
    ):
        self.config_dir = Path(config_dir)
        self.vault_path = vault_path
        self.data_dir = Path(data_dir)
        self.dry_run = dry_run
        self.platform_filter = platform_filter
        self.parallel = parallel
        self.run_id = uuid.uuid4().hex[:8]
        self._interrupted = False

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

    def _check_interrupted(self):
        if self._interrupted:
            raise PipelineInterrupted("Pipeline interrupted by signal")

    # ─── Progress helpers ──────────────────────────────────────

    def _progress(self, phase: str, current: int, total: int, label: str = ""):
        """Log a progress line with run_id."""
        pct = f"{current}/{total}" if total else str(current)
        suffix = f" — {label}" if label else ""
        logger.info(f"[{self.run_id}] {phase}: {pct}{suffix}")

    # ─── Main run ──────────────────────────────────────────────

    async def run(self) -> dict:
        report = {
            "run_id": self.run_id,
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

        try:
            # Phase 1: Scrape
            all_articles = await self._phase_scrape(report)
            self._check_interrupted()

            # Phase 2: Classify
            await self._phase_classify(all_articles, report)
            self._check_interrupted()

            # Phase 3: Dedup
            deduped = self._phase_dedup(all_articles, report)
            self._check_interrupted()

            # Phase 4: Quality filter
            quality_articles = await self._phase_quality(deduped, report)
            self._check_interrupted()

            # Phase 5: Humanize + Write
            await self._phase_humanize_write(quality_articles, report)

        except PipelineInterrupted:
            logger.warning(f"[{self.run_id}] Pipeline interrupted — partial results saved")
            report["errors"].append("Pipeline interrupted by signal")

        finally:
            self.dedup.close()

        return report

    # ─── Phase implementations ─────────────────────────────────

    async def _phase_scrape(self, report: dict) -> list:
        logger.info("=" * 60)
        logger.info(f"[{self.run_id}] PHASE 1: Scraping {len(self.scrapers)} platforms")
        logger.info("=" * 60)

        all_articles = []
        scrape_tasks = []

        for name, scraper in self.scrapers.items():
            keywords = scraper.get_keywords()
            if not keywords:
                logger.warning(f"[{name}] No keywords configured, skipping")
                continue
            scrape_tasks.append((name, scraper, keywords))

        if not scrape_tasks:
            logger.warning("No scrape tasks to run")
            return []

        if self.parallel:
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
            for i, (name, scraper, keywords) in enumerate(scrape_tasks):
                self._progress("Scraping", i + 1, len(scrape_tasks), name)
                try:
                    articles = await scraper.search_keywords(keywords)
                    all_articles.extend(articles)
                    report["scraped"][name] = len(articles)
                except Exception as e:
                    msg = f"Scraper {name}: {e}"
                    report["errors"].append(msg)
                    logger.error(msg)

        logger.info(f"[{self.run_id}] Total scraped: {len(all_articles)} articles")

        max_total = self.config.platforms.get("limits", {}).get("max_total_articles", 30)
        if len(all_articles) > max_total:
            all_articles.sort(key=lambda a: len(a.content), reverse=True)
            all_articles = all_articles[:max_total]
            logger.info(f"Trimmed to {max_total} articles (daily limit)")

        return all_articles

    async def _phase_classify(self, all_articles: list, report: dict):
        total = len(all_articles)
        logger.info("=" * 60)
        logger.info(f"[{self.run_id}] PHASE 2: Classifying {total} articles")
        logger.info("=" * 60)

        semaphore = asyncio.Semaphore(LLM_CONCURRENCY) if self.parallel else None
        done = 0

        async def _classify_one(article):
            nonlocal done
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
            finally:
                done += 1
                if done % 5 == 0 or done == total:
                    self._progress("Classifying", done, total)

        if all_articles:
            if self.parallel:
                await asyncio.gather(*[_classify_one(a) for a in all_articles])
            else:
                for article in all_articles:
                    await _classify_one(article)

        report["classified"] = len([a for a in all_articles if a.major_category])
        logger.info(f"[{self.run_id}] Classified: {report['classified']}/{total}")

    def _phase_dedup(self, all_articles: list, report: dict) -> list:
        total = len(all_articles)
        logger.info("=" * 60)
        logger.info(f"[{self.run_id}] PHASE 3: Deduplication ({total} articles)")
        logger.info("=" * 60)

        deduped = []
        for article in all_articles:
            if self.dedup.is_seen(article.url, article.title):
                report["dedup_removed"] += 1
                continue
            deduped.append(article)

        logger.info(
            f"[{self.run_id}] Dedup: removed {report['dedup_removed']}, remaining {len(deduped)}"
        )
        return deduped

    async def _phase_quality(self, deduped: list, report: dict) -> list:
        total = len(deduped)
        logger.info("=" * 60)
        logger.info(f"[{self.run_id}] PHASE 4: Quality filtering ({total} articles)")
        logger.info("=" * 60)

        quality_articles = []
        done = 0

        async def _quality_check(article):
            nonlocal done
            if not article.major_category or not article.is_monetizable:
                report["quality_rejected"] += 1
                done += 1
                return None

            passes, score = self.quality_filter.should_process(
                article.title, article.content, article.platform,
                f"{article.major_category}/{article.sub_category}",
            )
            done += 1
            if done % 5 == 0 or done == total:
                self._progress("Quality", done, total)

            if passes:
                article.quality_score = score.get("total_score", 0)
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
            f"[{self.run_id}] Quality: {len(quality_articles)} passed, {report['quality_rejected']} rejected"
        )
        return quality_articles

    async def _phase_humanize_write(self, quality_articles: list, report: dict):
        max_output = self.config.platforms.get("limits", {}).get("max_quality_output", 15)
        to_process = quality_articles[:max_output]
        total = len(to_process)

        logger.info("=" * 60)
        logger.info(f"[{self.run_id}] PHASE 5: Humanizing & Writing ({total} articles)")
        logger.info("=" * 60)

        for i, article in enumerate(to_process):
            self._check_interrupted()
            logger.info(f"[{self.run_id}] Humanizing [{i+1}/{total}]: {article.title[:40]}...")

            try:
                result = self.humanizer.humanize(
                    article.title, article.content, article.platform,
                    f"{article.major_category}/{article.sub_category}",
                )
            except Exception as e:
                report["errors"].append(f"Humanize failed: {article.title[:30]}: {e}")
                continue

            if not result:
                report["errors"].append(f"Humanize returned empty: {article.title[:30]}")
                continue

            report["humanized"] += 1

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


# ─── CLI Entry Point ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="知识付费内容聚合 Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写入 Obsidian")
    parser.add_argument("--platform", type=str, help="只跑指定平台")
    parser.add_argument("--config-dir", type=str, default="config", help="配置文件目录")
    parser.add_argument("--vault", type=str,
                       default=os.environ.get(
                           "OBSIDIAN_VAULT",
                           "/Users/jingyi/Documents/景一obsidian/景一"
                       ),
                       help="Obsidian vault 路径")
    parser.add_argument("--data-dir", type=str, default="data", help="数据缓存目录")
    parser.add_argument("--no-parallel", action="store_true", help="禁用并发")

    args = parser.parse_args()

    project_dir = Path(__file__).parent.parent
    os.chdir(project_dir)

    today = datetime.now().strftime("%Y-%m-%d")
    run_id = uuid.uuid4().hex[:8]
    setup_logging(str(project_dir / "logs"), today)

    logger.info(f"🚀 Pipeline 启动 [run={run_id}]")
    logger.info(f"   Vault: {args.vault}")
    logger.info(f"   Dry run: {args.dry_run}")
    logger.info(f"   Parallel: {not args.no_parallel}")
    if args.platform:
        logger.info(f"   Platform: {args.platform}")

    # Signal handling
    interrupted = False

    def _handle_signal(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            logger.warning(f"Received signal {signum}, shutting down gracefully...")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        pipeline = Pipeline(
            config_dir=args.config_dir,
            vault_path=args.vault,
            data_dir=args.data_dir,
            dry_run=args.dry_run,
            platform_filter=args.platform,
            parallel=not args.no_parallel,
        )
    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        print(f"\n❌ Config error: {e}")
        return 1

    try:
        report = asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")
        print("\n⚠️ Pipeline interrupted")
        return 1

    # ─── Print Summary ──────────────────────────────────────
    print("\n" + "=" * 50)
    print(f"📊 PIPELINE 报告 [run={report['run_id']}]")
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
        print(f"\n⚠️ 错误 ({len(report['errors'])}):")
        for e in report["errors"][:5]:
            print(f"  - {e}")
        if len(report["errors"]) > 5:
            print(f"  ... 还有 {len(report['errors']) - 5} 个错误")

    if report["articles"]:
        print(f"\n📝 产出文章:")
        for a in report["articles"]:
            print(f"  [{a['category']}] {a['title']} (质量: {a['quality_score']:.0f}/10)")
            print(f"    路径: {a['path']}")

    if not args.dry_run and report["written"] > 0:
        pipeline.writer.write_daily_report(report)

    print(f"\n✅ Pipeline 完成！")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
