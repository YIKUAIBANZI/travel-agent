"""
景点附近美食匹配 — 给每个推荐景点搜附近餐饮

用法:
  matcher = FoodMatcher()
  enriched = matcher.match(ranked_places, top_n=10, food_per_place=3)
  print(to_food_markdown(enriched))
"""

import logging
import time

from scrapers.amap_scraper import AmapScraper

logger = logging.getLogger(__name__)


class FoodMatcher:
    """给景点匹配附近美食"""

    def __init__(self):
        self.amap = AmapScraper()

    def match(
        self,
        places: list[dict],
        top_n: int = 10,
        food_per_place: int = 3,
        radius: int = 1000,
    ) -> list[dict]:
        """
        给排行表中的非餐饮地点搜附近美食

        Args:
            places: 排序后的地点列表
            top_n: 只给前 N 个地点匹配（控制 API 调用量）
            food_per_place: 每个地点匹配几家餐厅
            radius: 搜索半径（米）
        Returns:
            原列表，每个地点增加 nearby_food 字段
        """
        matched = 0
        for place in places[:top_n]:
            lat, lng = place.get("lat"), place.get("lng")
            if not lat or not lng:
                continue

            # 餐饮类地点跳过（自己就是餐厅）
            ptype = (place.get("type") or place.get("poi_type") or "").lower()
            if "餐" in ptype or "美食" in ptype or "food" in ptype:
                continue

            try:
                nearby = self.amap.search_nearby(
                    lng=str(lng),
                    lat=str(lat),
                    radius=radius,
                    count=food_per_place,
                )
                if nearby:
                    place["nearby_food"] = nearby
                    matched += 1
                    logger.info(
                        f"'{place.get('name', '')[:20]}' 附近找到 {len(nearby)} 家餐厅"
                    )
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"附近美食搜索失败: {e}")

        logger.info(f"附近美食匹配完成: {matched} 个地点有推荐")
        return places


def to_food_markdown(places: list[dict]) -> str:
    """生成附近美食 Markdown"""
    lines = ["## 景点附近美食推荐", ""]

    has_food = False
    for place in places:
        foods = place.get("nearby_food")
        if not foods:
            continue
        has_food = True
        name = place.get("name", "")
        lines.append(f"### {name}")
        for f in foods:
            rating = f"评分 {f['rating']}" if f.get("rating") else ""
            dist = f"{f['distance']}m" if f.get("distance") else ""
            tel = f"tel:{f['tel']}" if f.get("tel") else ""
            parts = [x for x in [rating, dist, tel] if x]
            info = " | ".join(parts)
            link = f"[地图]({f['source_url']})" if f.get("source_url") else ""
            lines.append(f"- **{f['name']}** — {info} {link}")
        lines.append("")

    if not has_food:
        return ""
    return "\n".join(lines)
