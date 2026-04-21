"""
批量预爬热门城市高德 POI 数据

用法:
  PYTHONPATH=. python scripts/precrawl.py
  PYTHONPATH=. python scripts/precrawl.py --count 30
  PYTHONPATH=. python scripts/precrawl.py --cities 北京 上海 深圳
"""

import argparse
import logging
import sys
import time

import config  # noqa: F401 — 触发 .env 加载
from scrapers.amap_scraper import AmapScraper

logger = logging.getLogger(__name__)

# 中国 100 个热门旅游目的地
DEFAULT_CITIES = [
    # 华北（12）
    "北京",
    "天津",
    "承德",
    "秦皇岛",
    "大同",
    "平遥",
    "太原",
    "五台山",
    "张家口",
    "呼和浩特",
    "呼伦贝尔",
    "额济纳旗",
    # 东北（6）
    "哈尔滨",
    "长白山",
    "大连",
    "沈阳",
    "延吉",
    "漠河",
    # 华东（22）
    "上海",
    "杭州",
    "苏州",
    "南京",
    "无锡",
    "扬州",
    "绍兴",
    "乌镇",
    "黄山",
    "婺源",
    "景德镇",
    "厦门",
    "鼓浪屿",
    "泉州",
    "福州",
    "武夷山",
    "青岛",
    "泰山",
    "威海",
    "济南",
    "舟山",
    "千岛湖",
    # 华中（10）
    "武汉",
    "长沙",
    "张家界",
    "凤凰古城",
    "恩施",
    "洛阳",
    "开封",
    "郑州",
    "神农架",
    "岳阳",
    # 华南（12）
    "广州",
    "深圳",
    "珠海",
    "佛山",
    "汕头",
    "阳朔",
    "桂林",
    "北海",
    "南宁",
    "三亚",
    "海口",
    "万宁",
    # 西南（18）
    "成都",
    "重庆",
    "丽江",
    "大理",
    "昆明",
    "西双版纳",
    "泸沽湖",
    "香格里拉",
    "腾冲",
    "贵阳",
    "黄果树",
    "荔波",
    "西江千户苗寨",
    "拉萨",
    "林芝",
    "日喀则",
    "纳木错",
    "稻城亚丁",
    # 西北（14）
    "西安",
    "敦煌",
    "兰州",
    "张掖",
    "青海湖",
    "茶卡盐湖",
    "西宁",
    "银川",
    "中卫",
    "乌鲁木齐",
    "喀什",
    "伊犁",
    "吐鲁番",
    "喀纳斯",
    # 近年爆火小众（4，去重后）
    "甘孜",
    "阿勒泰",
    "平潭",
    "淄博",
]


def precrawl(cities: list[str], count: int = 30):
    scraper = AmapScraper()
    results = {}

    for i, city in enumerate(cities):
        logger.info(f"[{i + 1}/{len(cities)}] 预爬: {city}")
        try:
            records = scraper.search(query=city, count=count)
            if records:
                filepath = scraper.save(records, city=city)
                results[city] = {"count": len(records), "file": filepath}
                logger.info(f"  ✓ {city}: {len(records)} 条 → {filepath}")
            else:
                results[city] = {"count": 0, "file": None}
                logger.warning(f"  ✗ {city}: 无数据")
        except Exception as e:
            results[city] = {"count": 0, "file": None, "error": str(e)}
            logger.error(f"  ✗ {city}: {e}")

        # 控制请求频率，避免高德限流
        if i < len(cities) - 1:
            time.sleep(1)

    # 汇总
    print("\n" + "=" * 50)
    print("预爬汇总")
    print("=" * 50)
    ok = 0
    for city, info in results.items():
        status = f"✓ {info['count']} 条" if info["count"] > 0 else "✗ 失败"
        if info.get("error"):
            status += f" ({info['error'][:40]})"
        print(f"  {city:6s}  {status}")
        if info["count"] > 0:
            ok += 1
    print(f"\n成功: {ok}/{len(cities)} 个城市")
    return ok == len(cities)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="批量预爬热门城市高德 POI")
    parser.add_argument(
        "--cities",
        nargs="+",
        default=DEFAULT_CITIES,
        help="城市列表（默认 15 个热门城市）",
    )
    parser.add_argument(
        "--count", type=int, default=30, help="每城市目标条数（默认 30）"
    )
    args = parser.parse_args()

    success = precrawl(args.cities, args.count)
    sys.exit(0 if success else 1)
