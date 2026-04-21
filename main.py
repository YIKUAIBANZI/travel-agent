"""
旅游规划 Agent — 主入口

用法:
  python main.py --city 深圳 --days 4 --preferences "美食为主，海边走走"

完整流程:
  1. 爬取数据（XHS + 高德 POI）
  2. LLM 清洗提取地点
  3. LLM 排序推荐
  4. 输出 Markdown 排行表 + 地图标注
"""

import argparse
import glob
import json
import logging
import os
import sys
import time as _time

import config

logger = logging.getLogger(__name__)

CACHE_MAX_AGE = 3 * 86400  # 缓存有效期：3 天


def _has_fresh_cache(directory: str, city: str, max_age: int = CACHE_MAX_AGE) -> bool:
    """检查是否有未过期的缓存数据"""
    os.path.join(directory, f"{city}*.json") if city else ""
    # 也检查 amap_ 和 xhs_ 前缀的文件
    patterns = [
        os.path.join(directory, f"{city}*.json"),
        os.path.join(directory, f"amap_{city}*.json"),
        os.path.join(directory, f"xhs_{city}*.json"),
    ]
    for pat in patterns:
        files = glob.glob(pat)
        for f in files:
            age = _time.time() - os.path.getmtime(f)
            if age < max_age:
                return True
    return False


def run(
    city: str,
    days: int = 3,
    preferences: str = "综合体验",
    travel_info: str = "2人，公共交通",
    skip_scrape: bool = False,
    skip_clean: bool = False,
    force: bool = False,
    xhs_count: int = 20,
    amap_count: int = 50,
    output_file: str = None,
):
    """执行完整管道"""

    # ── Step 1: 爬取 ──
    raw_dir = config.DATA_DIR
    has_cache = _has_fresh_cache(raw_dir, city) and not force

    if skip_scrape or has_cache:
        if has_cache and not skip_scrape:
            logger.info(f"发现 {city} 的有效缓存（3天内），跳过采集")
        else:
            logger.info("跳过数据采集（使用已有数据）")
    else:
        print(f"\n{'=' * 50}")
        print(f"  Step 1: 数据采集 — {city}")
        print(f"{'=' * 50}\n")

        # 高德 POI（优先，不需要浏览器）
        if config.AMAP_API_KEY:
            from scrapers.amap_scraper import AmapScraper

            logger.info("正在采集高德 POI...")
            amap = AmapScraper()
            amap_results = amap.search(query=city, count=amap_count)
            if amap_results:
                amap.save(amap_results, city)
        else:
            logger.warning("未配置 AMAP_API_KEY，跳过高德 POI 采集")

        # XHS
        from scrapers.xhs_scraper import XhsScraper

        logger.info("正在采集小红书...")
        xhs = XhsScraper()
        try:
            xhs_results = xhs.search(query=f"{city}攻略", count=xhs_count)
            if xhs_results:
                xhs.save(xhs_results, city)
        except RuntimeError as e:
            logger.warning(f"XHS 采集跳过: {e}")

    # ── Step 2: LLM 清洗 ──
    cleaned_cache = _has_fresh_cache(config.CLEANED_DIR, city) and not force

    if skip_clean or cleaned_cache:
        if cleaned_cache and not skip_clean:
            logger.info(f"发现 {city} 的清洗缓存（3天内），跳过 LLM 清洗")
        else:
            logger.info("跳过清洗（使用已有清洗数据）")

        pattern = os.path.join(config.CLEANED_DIR, f"{city}*.json")
        files = sorted(glob.glob(pattern), reverse=True)
        if not files:
            print(f"[ERROR] 未找到 {city} 的清洗数据")
            return
        with open(files[0], encoding="utf-8") as f:
            places = json.load(f)
    else:
        print(f"\n{'=' * 50}")
        print("  Step 2: LLM 清洗 — 提取结构化地点")
        print(f"{'=' * 50}\n")

        from pipeline.cleaner import Cleaner

        cleaner = Cleaner()
        places = cleaner.clean(city=city)

    if not places:
        print("[ERROR] 没有可用的地点数据")
        return

    # ── Step 3: 排行推荐 ──
    print(f"\n{'=' * 50}")
    print(f"  Step 3: 推荐排行 — {preferences}")
    print(f"{'=' * 50}\n")

    from pipeline.ranker import Ranker, to_markdown_table

    if config.LLM_API_KEY:
        ranker = Ranker()
        ranked = ranker.rank(
            city=city,
            preferences=preferences,
            days=days,
            travel_info=travel_info,
        )
    else:
        logger.warning("未配置 LLM_API_KEY，跳过 LLM 排序，按原始顺序输出")
        ranked = places

    # ── Step 3.5: 景点附近美食匹配 ──
    if config.AMAP_API_KEY:
        from pipeline.food_matcher import FoodMatcher, to_food_markdown

        logger.info("匹配景点附近美食...")
        matcher = FoodMatcher()
        ranked = matcher.match(ranked, top_n=10, food_per_place=3, radius=1000)

    # ── Step 4: 输出 ──
    print(f"\n{'=' * 50}")
    print("  结果")
    print(f"{'=' * 50}\n")

    md = to_markdown_table(ranked, city, preferences)
    print(md)

    # 附近美食
    if config.AMAP_API_KEY:
        food_md = to_food_markdown(ranked)
        if food_md:
            print(f"\n{food_md}")

    # 地图
    from pipeline.mapper import generate_map_report, generate_web_map_url

    map_report = generate_map_report(ranked, city)
    print(f"\n{map_report}")

    web_url = generate_web_map_url([p for p in ranked if p.get("lat") and p.get("lng")])
    if web_url:
        print(f"\n高德地图链接: {web_url}")

    # 保存完整报告
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(md)
            if config.AMAP_API_KEY and food_md:
                f.write(f"\n\n{food_md}")
            f.write(f"\n\n{map_report}")
        print(f"\n[SUCCESS] 报告已保存到 {output_file}")

    # 统计
    total = len(ranked)
    with_coords = sum(1 for p in ranked if p.get("lat"))
    with_reason = sum(1 for p in ranked if p.get("reason"))
    with_warnings = sum(1 for p in ranked if p.get("warnings"))
    print("\n--- 统计 ---")
    print(f"总地点: {total}")
    print(f"有坐标: {with_coords}/{total}")
    print(f"有推荐理由: {with_reason}/{total}")
    print(f"有注意事项: {with_warnings}/{total}")


def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="旅游规划 Agent — 一句话出攻略",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整流程（需配置 .env）
  python main.py --city 深圳 --days 4 --preferences "美食为主，海边走走"

  # 跳过采集，只用已有数据做清洗+排行
  python main.py --city 深圳 --skip-scrape

  # 跳过采集和清洗，只做排行输出
  python main.py --city 深圳 --skip-scrape --skip-clean --preferences "文艺小众"

  # 导出报告文件
  python main.py --city 深圳 --output 深圳攻略.md
        """,
    )
    parser.add_argument("--city", required=True, help="目的地城市")
    parser.add_argument("--days", type=int, default=3, help="出行天数（默认3）")
    parser.add_argument(
        "--preferences", default="综合体验", help="偏好，如 '美食为主' '海边+文艺'"
    )
    parser.add_argument("--travel-info", default="2人，公共交通", help="出行方式")
    parser.add_argument(
        "--skip-scrape", action="store_true", help="跳过爬取，使用已有原始数据"
    )
    parser.add_argument(
        "--skip-clean", action="store_true", help="跳过清洗，使用已有清洗数据"
    )
    parser.add_argument(
        "--force", action="store_true", help="忽略缓存，强制重新采集和清洗"
    )
    parser.add_argument("--xhs-count", type=int, default=20, help="XHS 爬取条数")
    parser.add_argument("--amap-count", type=int, default=50, help="高德 POI 爬取条数")
    parser.add_argument("--output", default=None, help="输出报告文件路径")
    args = parser.parse_args()

    try:
        run(
            city=args.city,
            days=args.days,
            preferences=args.preferences,
            travel_info=args.travel_info,
            skip_scrape=args.skip_scrape,
            skip_clean=args.skip_clean,
            force=args.force,
            xhs_count=args.xhs_count,
            amap_count=args.amap_count,
            output_file=args.output,
        )
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户取消")
        sys.exit(0)


if __name__ == "__main__":
    main()
