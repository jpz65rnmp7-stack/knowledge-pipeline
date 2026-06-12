#!/usr/bin/env python3
"""Read Obsidian articles, extract 即梦提示词, and generate images via dreamina CLI."""

import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = "/Users/jingyi/Documents/景一obsidian/景一/Claude-商业蒸馏"
TRACKER_FILE = Path(__file__).parent.parent / "data" / "generated_images.json"


def load_tracker() -> dict:
    """Load the tracker of already-generated images."""
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE) as f:
            return json.load(f)
    return {}


def save_tracker(tracker: dict):
    """Save the image generation tracker."""
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def find_articles() -> list[dict]:
    """Find all articles with 即梦提示词 that haven't been generated yet."""
    tracker = load_tracker()
    articles = []

    for md_file in Path(VAULT_PATH).rglob("*.md"):
        if "日报" in md_file.name:
            continue

        content = md_file.read_text(encoding="utf-8")
        match = re.search(r"\*\*即梦提示词[：:]\s*(.+?)\*\*", content)
        if not match:
            continue

        prompt = match.group(1).strip()
        file_id = str(md_file.relative_to(VAULT_PATH))

        if file_id not in tracker or tracker[file_id].get("status") != "success":
            articles.append({
                "path": str(md_file),
                "relative_path": file_id,
                "prompt": prompt,
            })

    return articles


def generate_image(prompt: str, ratio: str = "16:9", model: str = "4.1") -> dict:
    """Call dreamina CLI to generate an image. Returns submit_id and status."""
    cmd = [
        "dreamina", "text2image",
        "--prompt", prompt,
        "--ratio", ratio,
        "--model_version", model,
        "--poll", "60",  # Wait up to 60s for result
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        stdout = result.stdout + result.stderr

        # Parse submit_id from output
        submit_id = ""
        sid_match = re.search(r"submit_id[=:]\s*(\S+)", stdout)
        if sid_match:
            submit_id = sid_match.group(1)

        return {
            "status": "success" if result.returncode == 0 else "failed",
            "submit_id": submit_id,
            "output": stdout.strip()[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "submit_id": "", "output": ""}
    except Exception as e:
        return {"status": "error", "submit_id": "", "output": str(e)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="根据即梦提示词批量生图")
    parser.add_argument("--dry-run", action="store_true", help="只列出待生成的文章，不实际生图")
    parser.add_argument("--ratio", type=str, default="16:9", help="图片比例 (default: 16:9)")
    parser.add_argument("--model", type=str, default="4.1", help="模型版本 (default: 4.1)")
    parser.add_argument("--limit", type=int, default=5, help="最多生成几张 (default: 5)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    articles = find_articles()
    logger.info(f"发现 {len(articles)} 篇待生图的文章")

    if args.dry_run:
        for a in articles:
            print(f"\n📄 {a['relative_path']}")
            print(f"   🎨 {a['prompt'][:80]}...")
        return

    tracker = load_tracker()
    generated = 0

    for article in articles:
        if generated >= args.limit:
            break

        logger.info(f"[{generated+1}/{min(len(articles), args.limit)}] 生成: {article['relative_path']}")
        print(f"\n🎨 {article['prompt']}")

        result = generate_image(article["prompt"], ratio=args.ratio, model=args.model)

        tracker[article["relative_path"]] = {
            "prompt": article["prompt"],
            "generated_at": datetime.now().isoformat(),
            **result,
        }
        save_tracker(tracker)

        if result["status"] == "success":
            generated += 1
            print(f"   ✅ 成功 submit_id={result['submit_id']}")
        else:
            print(f"   ⚠️ {result['status']}: {result['output'][:100]}")

    logger.info(f"完成: {generated} 张图片生成成功")
    print(f"\n📊 追踪文件: {TRACKER_FILE}")


if __name__ == "__main__":
    main()
