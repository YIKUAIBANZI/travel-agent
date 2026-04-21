"""
批量预清洗所有城市数据

用法:
  PYTHONPATH=. python scripts/preclean.py
"""

import glob
import logging
import os
import time

import config
from pipeline.cleaner import Cleaner

logger = logging.getLogger(__name__)


def preclean():
    # 找出所有有原始数据但没清洗数据的城市
    raw_files = glob.glob(os.path.join(config.DATA_DIR, "amap_*.json"))
    cities = set()
    for f in raw_files:
        basename = os.path.basename(f)
        # amap_北京_20260415.json → 北京
        parts = basename.replace("amap_", "").rsplit("_", 1)
        if parts:
            cities.add(parts[0])

    # 检查哪些已清洗
    cleaned_files = glob.glob(os.path.join(config.CLEANED_DIR, "*.json"))
    cleaned_cities = set()
    for f in cleaned_files:
        basename = os.path.basename(f)
        parts = basename.rsplit("_", 1)
        if parts:
            cleaned_cities.add(parts[0])

    todo = sorted(cities - cleaned_cities)
    print(f"总城市: {len(cities)}, 已清洗: {len(cleaned_cities)}, 待清洗: {len(todo)}")

    if not todo:
        print("全部已清洗，无需操作")
        return

    cleaner = Cleaner()
    ok = 0
    fail = 0

    for i, city in enumerate(todo):
        logger.info(f"[{i + 1}/{len(todo)}] 清洗: {city}")
        try:
            places = cleaner.clean(city=city)
            if places:
                logger.info(f"  ✓ {city}: {len(places)} 个地点")
                ok += 1
            else:
                logger.warning(f"  ✗ {city}: 清洗后无数据")
                fail += 1
        except Exception as e:
            logger.error(f"  ✗ {city}: {e}")
            fail += 1

        # 控制 LLM 调用频率
        if i < len(todo) - 1:
            time.sleep(1)

    print(f"\n清洗完成: 成功 {ok}, 失败 {fail}, 总计 {len(todo)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    preclean()
