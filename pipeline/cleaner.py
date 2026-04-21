"""
LLM 清洗管道 — Phase 2

功能:
  1. 读取 data/raw/ 下的爬虫 JSON
  2. XHS 数据 → LLM 提取地点名称/类型/推荐理由/注意事项
  3. 高德 Geocoding API 补全坐标（XHS 数据没有坐标）
  4. Amap POI 数据 → 直接归一化（已有坐标）
  5. 合并去重，输出到 data/cleaned/{city}_{date}.json

输出 schema (每条记录):
  {
    "name": "深圳湾公园",
    "type": "景点",          # 景点/餐饮/购物/娱乐/住宿
    "lat": "22.517",
    "lng": "113.941",
    "reason": "傍晚看日落超美，沿海散步",
    "source_url": "https://...",
    "source_platform": "xhs",
    "rating": 4.5,           # null if unavailable
    "opentime": "全天开放",
    "warnings": "周末人多",   # 空字符串 if none
    "address": "南山区深圳湾公园",
    "district": "南山区"
  }
"""

import glob
import json
import logging
import os
from datetime import datetime

import requests
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

# --- LLM 提取 prompt ---

EXTRACT_SYSTEM_PROMPT = """你是一个旅游信息提取助手。
从用户提供的旅游攻略文本中，提取所有提到的具体地点。

对每个地点，提取以下字段：
- name: 地点全名（如"深圳湾公园"，不要简称）
- type: 分类（只能是以下之一：景点/餐饮/购物/娱乐/住宿）
- reason: 推荐理由（用原文口吻简述，20字以内）
- warnings: 注意事项（人多、排队、季节限制等，没有则为空字符串）

严格按 JSON 数组格式返回，不要加任何其他文字。示例：
[
  {"name": "深圳湾公园", "type": "景点", "reason": "傍晚日落超美", "warnings": "周末人非常多"},
  {"name": "南头古城", "type": "景点", "reason": "历史街区有文艺小店", "warnings": ""}
]

如果文本中没有提到任何具体地点，返回空数组 []"""

EXTRACT_USER_TEMPLATE = """城市：{city}
攻略标题：{title}
攻略正文：{body}
图片文字（OCR）：{ocr}

请提取所有具体地点："""


class Cleaner:
    """LLM 清洗管道"""

    def __init__(self):
        if not config.LLM_API_KEY:
            raise ValueError(
                "未配置 LLM API Key！\n"
                "在 .env 中设置 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL\n"
                "支持 OpenAI / Claude / Deepseek / 通义 等任何 OpenAI 兼容 API"
            )
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.model = config.LLM_MODEL
        self.amap_key = config.AMAP_API_KEY

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def clean(self, city: str, raw_dir: str = None) -> list[dict]:
        """
        清洗指定城市的所有原始数据

        Args:
            city: 城市名（用于匹配文件和 Geocoding）
            raw_dir: 原始数据目录，默认 config.DATA_DIR
        Returns:
            清洗后的结构化地点列表
        """
        raw_dir = raw_dir or config.DATA_DIR
        cleaned: list[dict] = []

        # 1. 读取所有相关原始 JSON
        xhs_records, amap_records = self._load_raw(city, raw_dir)
        logger.info(
            f"读取原始数据: XHS {len(xhs_records)} 条, Amap {len(amap_records)} 条"
        )

        # 2. 清洗 XHS 数据（LLM 提取 + Geocoding）
        if xhs_records:
            xhs_places = self._clean_xhs(xhs_records, city)
            cleaned.extend(xhs_places)
            logger.info(f"XHS 清洗完成: 提取出 {len(xhs_places)} 个地点")

        # 3. 归一化 Amap POI 数据
        if amap_records:
            amap_places = self._normalize_amap(amap_records)
            cleaned.extend(amap_places)
            logger.info(f"Amap 归一化完成: {len(amap_places)} 个 POI")

        # 4. 去重（同名地点合并，优先保留有坐标的）
        merged = self._deduplicate(cleaned)
        logger.info(f"去重后: {len(merged)} 个唯一地点")

        # 5. 保存
        filepath = self._save(merged, city)
        logger.info(f"清洗数据已保存到 {filepath}")

        return merged

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def _load_raw(self, city: str, raw_dir: str) -> tuple[list, list]:
        xhs_records = []
        amap_records = []

        pattern = os.path.join(raw_dir, f"*{city}*.json")
        files = glob.glob(pattern)
        if not files:
            logger.warning(f"未找到匹配文件: {pattern}")
            return xhs_records, amap_records

        for fpath in files:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            basename = os.path.basename(fpath)
            if basename.startswith("xhs_"):
                xhs_records.extend(data)
            elif basename.startswith("amap_"):
                amap_records.extend(data)
            elif basename.startswith("mafengwo_"):
                xhs_records.extend(data)  # 马蜂窝数据也走 LLM 提取
            else:
                logger.warning(f"未知平台文件，跳过: {basename}")

        return xhs_records, amap_records

    # ------------------------------------------------------------------
    # XHS 清洗: LLM 提取地点 + Geocoding 补坐标
    # ------------------------------------------------------------------
    def _clean_xhs(self, records: list[dict], city: str) -> list[dict]:
        # 按 note_id 去重（8 轮爬取有大量跨 query 重复）
        dedup: dict[str, dict] = {}
        for r in records:
            nid = r.get("id") or r.get("source_url")
            if not nid:
                continue
            if nid not in dedup:
                dedup[nid] = r
                continue
            # 合并：优先保留有正文/OCR 的
            ex = dedup[nid]
            if not ex.get("body") and r.get("body"):
                ex["body"] = r["body"]
            if not ex.get("ocr_text") and r.get("ocr_text"):
                ex["ocr_text"] = r["ocr_text"]
        records = list(dedup.values())
        logger.info(f"XHS 按 note_id 去重后: {len(records)} 条唯一笔记")

        places: list[dict] = []

        for i, rec in enumerate(records):
            title = rec.get("title", "")
            body = rec.get("body", "")
            ocr = rec.get("ocr_text", "")
            source_url = rec.get("source_url", "")
            text = f"{title}\n{body}\n{ocr}".strip()

            if not text or len(text) < 10:
                continue

            # LLM 提取（title + body + ocr 都喂进去）
            extracted = self._llm_extract(city, title, body, ocr)
            if not extracted:
                continue

            for place in extracted:
                place_name = place.get("name", "")
                if not place_name:
                    continue

                # Geocoding 补坐标
                lat, lng, address, district = self._geocode(place_name, city)

                places.append(
                    {
                        "name": place_name,
                        "type": place.get("type", "景点"),
                        "lat": lat,
                        "lng": lng,
                        "reason": place.get("reason", ""),
                        "source_url": source_url,
                        "source_platform": rec.get("platform", "xhs"),
                        "rating": None,
                        "opentime": "",
                        "warnings": place.get("warnings", ""),
                        "address": address,
                        "district": district,
                    }
                )

            logger.info(
                f"[{i + 1}/{len(records)}] '{title[:30]}...' → {len(extracted)} 个地点"
            )

        return places

    def _llm_extract(
        self, city: str, title: str, body: str, ocr: str = ""
    ) -> list[dict]:
        """调用 LLM 从攻略文本中提取地点列表"""
        user_msg = EXTRACT_USER_TEMPLATE.format(
            city=city, title=title, body=body[:2000], ocr=ocr[:2000]
        )

        try:
            resp = self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            text = resp.choices[0].message.content.strip()

            # 提取 JSON（有些模型会包裹在 ```json ... ``` 中）
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            result = json.loads(text)
            if isinstance(result, list):
                return result
            return []

        except json.JSONDecodeError as e:
            logger.warning(f"LLM 返回非法 JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return []

    # ------------------------------------------------------------------
    # Geocoding: 高德地理编码
    # ------------------------------------------------------------------
    def _geocode(self, place_name: str, city: str) -> tuple[str, str, str, str]:
        """
        用高德 Geocoding API 获取坐标
        Returns: (lat, lng, address, district) — 失败则全为空字符串
        """
        if not self.amap_key:
            return ("", "", "", "")

        try:
            resp = requests.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params={
                    "key": self.amap_key,
                    "address": place_name,
                    "city": city,
                },
                timeout=5,
            )
            data = resp.json()
            if data.get("status") != "1" or not data.get("geocodes"):
                # 退而求其次：搜 POI
                return self._poi_fallback(place_name, city)

            geo = data["geocodes"][0]
            location = geo.get("location", "")
            lng, lat = location.split(",") if "," in location else ("", "")
            address = geo.get("formatted_address", "")
            district = geo.get("district", "")
            return (lat, lng, address, district)

        except Exception as e:
            logger.debug(f"Geocoding 失败 [{place_name}]: {e}")
            return ("", "", "", "")

    def _poi_fallback(self, name: str, city: str) -> tuple[str, str, str, str]:
        """Geocoding 没结果时，用 POI 搜索兜底"""
        try:
            resp = requests.get(
                "https://restapi.amap.com/v3/place/text",
                params={
                    "key": self.amap_key,
                    "keywords": name,
                    "city": city,
                    "citylimit": "true",
                    "offset": "1",
                    "page": "1",
                },
                timeout=5,
            )
            data = resp.json()
            pois = data.get("pois", [])
            if not pois:
                return ("", "", "", "")

            poi = pois[0]
            location = poi.get("location", "")
            lng, lat = location.split(",") if "," in location else ("", "")
            return (lat, lng, poi.get("address", ""), poi.get("adname", ""))

        except Exception:
            return ("", "", "", "")

    # ------------------------------------------------------------------
    # Amap POI 归一化
    # ------------------------------------------------------------------
    def _normalize_amap(self, records: list[dict]) -> list[dict]:
        """把 Amap POI 数据归一化为统一 schema"""
        TYPE_MAP = {
            "11": "景点",
            "05": "餐饮",
            "06": "购物",
            "08": "娱乐",
            "10": "住宿",
        }
        places = []
        for rec in records:
            typecode = rec.get("poi_typecode", "")
            type_prefix = typecode[:2] if typecode else ""
            place_type = TYPE_MAP.get(type_prefix, "景点")

            places.append(
                {
                    "name": rec.get("title", ""),
                    "type": place_type,
                    "lat": rec.get("lat", ""),
                    "lng": rec.get("lng", ""),
                    "reason": "",  # Amap POI 没有推荐理由
                    "source_url": rec.get("source_url", ""),
                    "source_platform": "amap",
                    "rating": rec.get("rating"),
                    "opentime": rec.get("opentime", ""),
                    "warnings": "",
                    "address": rec.get("body", ""),  # Amap 的 body 存的是地址
                    "district": rec.get("district", ""),
                }
            )
        return places

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------
    def _deduplicate(self, places: list[dict]) -> list[dict]:
        """
        按地点名去重：
        - 同名地点合并：优先保留有坐标的、有 reason 的
        - XHS reason + Amap 坐标/评分 = 最完整记录
        """
        by_name: dict[str, dict] = {}

        for place in places:
            name = place["name"]
            if name not in by_name:
                by_name[name] = place
                continue

            existing = by_name[name]
            # 合并：补全缺失字段
            if not existing["lat"] and place["lat"]:
                existing["lat"] = place["lat"]
                existing["lng"] = place["lng"]
                existing["address"] = place.get("address") or existing["address"]
                existing["district"] = place.get("district") or existing["district"]
            if not existing["reason"] and place["reason"]:
                existing["reason"] = place["reason"]
                existing["source_url"] = (
                    place.get("source_url") or existing["source_url"]
                )
            if existing["rating"] is None and place.get("rating") is not None:
                existing["rating"] = place["rating"]
            if not existing["opentime"] and place.get("opentime"):
                existing["opentime"] = place["opentime"]
            if not existing["warnings"] and place.get("warnings"):
                existing["warnings"] = place["warnings"]

        return list(by_name.values())

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def _save(self, places: list[dict], city: str) -> str:
        os.makedirs(config.CLEANED_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"{city}_{date_str}.json"
        filepath = os.path.join(config.CLEANED_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(places, f, ensure_ascii=False, indent=2)
        return filepath


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="LLM 清洗管道: 原始攻略 → 结构化地点数据"
    )
    parser.add_argument("--city", required=True, help="城市名，如 '深圳'")
    parser.add_argument("--raw-dir", default=None, help="原始数据目录（默认 data/raw）")
    args = parser.parse_args()

    try:
        cleaner = Cleaner()
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    places = cleaner.clean(city=args.city, raw_dir=args.raw_dir)

    if not places:
        print("[WARN] 未提取到任何地点")
        sys.exit(1)

    # 统计
    with_coords = sum(1 for p in places if p["lat"])
    with_reason = sum(1 for p in places if p["reason"])
    print(f"\n[SUCCESS] 共 {len(places)} 个地点")
    print(
        f"  有坐标: {with_coords}/{len(places)} ({with_coords * 100 // len(places)}%)"
    )
    print(f"  有推荐理由: {with_reason}/{len(places)}")
    print("  类型分布: ", end="")
    type_counts: dict[str, int] = {}
    for p in places:
        type_counts[p["type"]] = type_counts.get(p["type"], 0) + 1
    print(" | ".join(f"{k}:{v}" for k, v in sorted(type_counts.items())))
