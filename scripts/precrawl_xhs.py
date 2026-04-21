"""
批量预爬热门城市小红书攻略数据（需要 Chrome debug port + 登录态）

前置条件:
  1. open -a "Google Chrome" --args --remote-debugging-port=9222 --no-first-run
  2. 在 Chrome 里登录小红书

用法:
  PYTHONPATH=. python scripts/precrawl_xhs.py
  PYTHONPATH=. python scripts/precrawl_xhs.py --count 5 --skip-detail
  PYTHONPATH=. python scripts/precrawl_xhs.py --cities 北京 上海 成都
"""

import argparse
import glob
import logging
import os
import time

import config  # noqa: F401
from scrapers.xhs_scraper import XhsScraper

logger = logging.getLogger(__name__)

# 从 precrawl.py 导入城市列表
from scripts.precrawl import DEFAULT_CITIES


def precrawl_xhs(
    cities: list[str],
    count: int = 5,
    skip_existing: bool = True,
):
    scraper = XhsScraper()
    results = {}
    skipped = 0

    for i, city in enumerate(cities):
        query = f"{city}攻略"

        # 断点续爬：跳过已有数据的城市
        if skip_existing:
            existing = glob.glob(os.path.join(config.DATA_DIR, f"xhs_{query}_*.json"))
            if existing:
                logger.info(f"[{i + 1}/{len(cities)}] {city}: 已有数据，跳过")
                skipped += 1
                continue

        logger.info(f"[{i + 1}/{len(cities)}] 爬取: {city}")
        try:
            records = scraper.search(query=query, count=count)
            if records:
                filepath = scraper.save(records, city=query)
                body_count = sum(1 for r in records if r.get("body"))
                results[city] = {
                    "count": len(records),
                    "with_body": body_count,
                    "file": filepath,
                }
                logger.info(f"  ✓ {city}: {len(records)} 条（{body_count} 条有正文）")
            else:
                results[city] = {"count": 0, "file": None}
                logger.warning(f"  ✗ {city}: 无数据")
        except Exception as e:
            results[city] = {"count": 0, "file": None, "error": str(e)}
            logger.error(f"  ✗ {city}: {e}")

        # 城市间间隔，避免触发安全验证
        if i < len(cities) - 1:
            time.sleep(15)

    # 汇总
    print("\n" + "=" * 50)
    print("XHS 预爬汇总")
    print("=" * 50)
    ok = 0
    for city, info in results.items():
        if info["count"] > 0:
            body = info.get("with_body", 0)
            print(f"  {city:8s}  ✓ {info['count']} 条（{body} 条有正文）")
            ok += 1
        else:
            err = info.get("error", "无数据")
            print(f"  {city:8s}  ✗ {err[:40]}")

    total = len(results)
    print(f"\n新爬取: {ok}/{total} 成功 | 跳过已有: {skipped}")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="批量预爬热门城市小红书攻略")
    parser.add_argument(
        "--cities",
        nargs="+",
        default=DEFAULT_CITIES,
        help="城市列表（默认全部 98 个）",
    )
    parser.add_argument("--count", type=int, default=5, help="每城市爬取条数（默认 5）")
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="不跳过已有数据的城市（默认跳过）",
    )
    args = parser.parse_args()

    precrawl_xhs(
        cities=args.cities,
        count=args.count,
        skip_existing=not args.no_skip,
    )
