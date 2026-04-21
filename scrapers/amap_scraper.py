"""
高德地图 POI 爬虫（官方 API，免费）

用途：搜索目的地的景点/餐饮/娱乐 POI，返回名称、坐标、评分、地址。
与小红书 UGC 内容互补：XHS 提供真实口碑，高德提供官方位置数据。

API 申请：https://lbs.amap.com/ → 控制台 → 创建应用 → Web服务 Key
免费配额：100 QPS，日调用无上限，够用 MVP

POI 类型代码（type_code 参数）:
  景点:    110000  餐饮:    050000
  购物:    060000  住宿:    100000
  娱乐:    080000  交通枢纽: 150000
"""

import logging
import os
import time
from datetime import datetime


from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

AMAP_POI_URL = "https://restapi.amap.com/v3/place/text"
AMAP_AROUND_URL = "https://restapi.amap.com/v3/place/around"

# 每次搜索的 POI 类型组合（景点+餐饮+娱乐+购物）
DEFAULT_TYPES = "110000|050000|080000|060000"
# 餐饮类型
FOOD_TYPES = "050000"


class AmapScraper(BaseScraper):
    """高德地图 POI 搜索，返回与 XHS 相同 schema 的结构化数据"""

    PLATFORM = "amap"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("AMAP_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "未配置高德地图 API Key！\n"
                "申请地址: https://lbs.amap.com/ → 控制台 → 创建应用 → Web服务 Key\n"
                "然后在 .env 中添加: AMAP_API_KEY=你的key"
            )

    def search(
        self, query: str, count: int = 50, type_code: str = DEFAULT_TYPES
    ) -> list:
        """
        搜索目的地 POI

        Args:
            query: 城市名，如 "深圳"
            count: 目标条数（最多200，高德单次最多25条，自动翻页）
            type_code: POI 类型代码，默认景点+餐饮+娱乐
        Returns:
            标准化记录列表，符合 DATA_SCHEMA，额外包含高德专有字段
        """
        logger.info(f"正在搜索高德 POI: {query}（目标={count}条）")
        records: list[dict] = []
        page = 1
        page_size = 25  # 高德单次最多 25

        while len(records) < count:
            params = {
                "key": self.api_key,
                "keywords": query,
                "types": type_code,
                "city": query,
                "citylimit": "true",  # 只返回指定城市结果
                "output": "json",
                "offset": page_size,
                "page": page,
                "extensions": "all",  # 返回详细信息（评分、营业时间等）
            }

            try:
                resp = self._request("GET", AMAP_POI_URL, params=params)
                data = resp.json()
            except Exception as e:
                logger.error(f"高德 API 请求失败（第 {page} 页）: {e}")
                break

            if data.get("status") != "1":
                logger.error(
                    f"高德 API 错误: {data.get('info')} (infocode={data.get('infocode')})"
                )
                break

            pois = data.get("pois", [])
            if not pois:
                logger.info("没有更多 POI")
                break

            for poi in pois:
                # 坐标: "经度,纬度" 格式，GCJ-02
                location = poi.get("location", "")
                lng, lat = location.split(",") if "," in location else ("", "")

                # 评分: 可能为空字符串
                rating_raw = poi.get("biz_ext", {}).get("rating", "") or poi.get(
                    "rating", ""
                )
                try:
                    rating = float(rating_raw) if rating_raw else None
                except ValueError:
                    rating = None

                # 营业时间
                opentime = poi.get("biz_ext", {}).get("opentime", "") or ""

                record = {
                    "id": poi.get("id", ""),
                    "platform": "amap",
                    "title": poi.get("name", ""),
                    "body": poi.get("address", ""),  # 地址作为 body
                    "author": "",
                    "source_url": (
                        f"https://www.amap.com/detail/{poi.get('id', '')}"
                        if poi.get("id")
                        else ""
                    ),
                    "images": [],
                    "likes": int(rating * 20)
                    if rating
                    else 0,  # 转换为 0-100 伪点赞数用于排序
                    "created_at": "",
                    "city": query,
                    "scraped_at": datetime.now().isoformat(),
                    # 高德专有字段（Phase 2 直接用，无需 Geocoding）
                    "lat": lat,
                    "lng": lng,
                    "poi_type": poi.get("type", ""),
                    "poi_typecode": poi.get("typecode", ""),
                    "rating": rating,
                    "opentime": opentime,
                    "tel": poi.get("tel", ""),
                    "district": poi.get("adname", ""),
                }
                records.append(record)

                if len(records) >= count:
                    break

            logger.info(f"第 {page} 页: +{len(pois)} 条，累计 {len(records)} 条")

            # 高德分页：count 字段是总数
            total = int(data.get("count", 0))
            if len(records) >= total or len(records) >= count:
                break

            page += 1
            time.sleep(0.5)  # 高德 API 无需大延迟

        logger.info(f"高德 POI 搜索完成，共获取 {len(records)} 条")
        return records[:count]

    def search_nearby(
        self,
        lng: str,
        lat: str,
        radius: int = 1000,
        count: int = 5,
        type_code: str = FOOD_TYPES,
    ) -> list[dict]:
        """
        周边搜索：给定坐标，搜附近的餐饮 POI

        Args:
            lng, lat: 中心点坐标 (GCJ-02)
            radius: 搜索半径（米），默认 1000m
            count: 返回条数
            type_code: POI 类型，默认餐饮
        Returns:
            [{name, rating, distance, address, opentime, tel, lat, lng, source_url}, ...]
        """
        params = {
            "key": self.api_key,
            "location": f"{lng},{lat}",
            "types": type_code,
            "radius": radius,
            "offset": min(count, 25),
            "page": 1,
            "extensions": "all",
            "sortrule": "weight",  # 按综合排序（评分+距离+热度）
        }

        try:
            resp = self._request("GET", AMAP_AROUND_URL, params=params)
            data = resp.json()
        except Exception as e:
            logger.debug(f"周边搜索失败: {e}")
            return []

        if data.get("status") != "1":
            return []

        results = []
        for poi in data.get("pois", [])[:count]:
            location = poi.get("location", "")
            plng, plat = location.split(",") if "," in location else ("", "")
            rating_raw = poi.get("biz_ext", {}).get("rating", "") or ""
            try:
                rating = float(rating_raw) if rating_raw else None
            except ValueError:
                rating = None

            results.append(
                {
                    "name": poi.get("name", ""),
                    "rating": rating,
                    "distance": int(poi.get("distance", 0)),
                    "address": poi.get("address", ""),
                    "opentime": poi.get("biz_ext", {}).get("opentime", ""),
                    "tel": poi.get("tel", ""),
                    "lat": plat,
                    "lng": plng,
                    "poi_type": poi.get("type", ""),
                    "source_url": f"https://www.amap.com/detail/{poi.get('id', '')}",
                }
            )

        return results


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="高德地图 POI 爬虫")
    parser.add_argument("--city", required=True, help="城市名，如 '深圳'")
    parser.add_argument("--count", type=int, default=50, help="目标条数（默认50）")
    args = parser.parse_args()

    try:
        scraper = AmapScraper()
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    results = scraper.search(query=args.city, count=args.count)

    if not results:
        print("[WARN] 未获取到任何结果")
        sys.exit(1)

    filepath = scraper.save(results, city=args.city)
    print(f"\n[SUCCESS] {len(results)} 条 POI 已保存到 {filepath}")
    print(f"示例: {results[0]['title']} @ ({results[0]['lat']}, {results[0]['lng']})")
