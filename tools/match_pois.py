"""
Step 2 · 清单 → POI 匹配

输入:
  - 女友必去清单(名字列表,原始文本)
  - 城市(深圳)

数据源(就地读,不调网络):
  - data/cleaned/{city}_*.json  清洗后的 XHS POI
  - data/raw/amap_{city}_*.json 高德原始 POI(坐标权威,评分权威)

匹配策略(从严到松,命中一条即停):
  1. 精确:normalize 后完全相等
  2. 包含:normalize 后 A 包含 B 或 B 包含 A
  3. 模糊:SequenceMatcher.ratio() >= 0.7

normalize 规则:
  - 去标点/空格
  - 去地铁方位词前缀(c出口、b出口、B口、A出口 等)
  - 去店名后缀 (xxx店) / (购物公园店)
  - 小写

返回:
  {
    "matched":  [ {query, poi, score, how} ... ],
    "unmatched": [ "名字1", "名字2" ... ]
  }

验收:21 项清单中匹配到 ≥ 18 项的坐标。
"""

import glob
import json
import os
import re
from difflib import SequenceMatcher

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ── 归一化 ───────────────────────────────────────────────────────────────
_METRO_PREFIX = re.compile(r"^[a-zA-Z]\s*出口|^[a-zA-Z]口|出口", flags=re.IGNORECASE)
_SHOP_SUFFIX = re.compile(r"[\(\（][^)\）]*店[\)\）]")
_PUNCT = re.compile(r"[\s\.,，。!！\?\?\-\_\~·（）\(\)\[\]【】]+")

# 区名/城市黑名单:candidate 若 normalize 后只是一个区名/城市,禁止 contain 命中
_DISTRICT_STOPWORDS = {
    "深圳",
    "南山",
    "福田",
    "罗湖",
    "宝安",
    "龙岗",
    "盐田",
    "龙华",
    "坪山",
    "光明",
    "大鹏",
    "南山区",
    "福田区",
    "罗湖区",
    "宝安区",
    "龙岗区",
    "盐田区",
    "龙华区",
    "坪山区",
    "光明区",
    "大鹏新区",
}
_MIN_MATCH_LEN = 3  # contain/fuzzy 至少 3 个字符才算
_FUZZY_THRESHOLD = 0.85  # fuzzy 阈值(0.7 太宽会误判)

# 手工修正:amap/XHS 对某些点的分类不准,强制覆盖 type/reason
# key 匹配 query 或 POI name 的子串
_MANUAL_OVERRIDES: dict[str, dict] = {
    "沅芷小筑": {
        "type": "咖啡馆/拍照打卡",
        "reason": "小红书网红拍照打卡咖啡店(非酒店/非住宿)",
    },
}


def apply_manual_overrides(query: str, poi: dict) -> dict:
    """如果 query 或 poi.name 命中 override,注入正确的 type/reason。"""
    name_blob = (query or "") + " " + (poi.get("name") or "")
    for k, patch in _MANUAL_OVERRIDES.items():
        if k in name_blob:
            merged = dict(poi)
            merged.update(patch)
            return merged
    return poi


def normalize(name: str) -> str:
    """名字规范化:去地铁方位、去店名后缀、去标点、小写。"""
    if not name:
        return ""
    s = name.strip()
    s = _SHOP_SUFFIX.sub("", s)
    s = _METRO_PREFIX.sub("", s)
    s = _PUNCT.sub("", s)
    return s.lower()


# ── 数据加载 ──────────────────────────────────────────────────────────────
def load_all_pois(city: str) -> list[dict]:
    """合并 cleaned(XHS)+ raw(高德)POI。统一字段:name/lat/lng/district/type/source/rating。"""
    pois: list[dict] = []

    # 1) cleaned 目录(XHS)
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "cleaned", f"{city}_*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else list(data.values())
        for it in items:
            if not it.get("name"):
                continue
            pois.append(
                {
                    "name": it.get("name", ""),
                    "lat": it.get("lat"),
                    "lng": it.get("lng"),
                    "district": it.get("district", ""),
                    "type": it.get("type", "景点"),
                    "rating": it.get("rating"),
                    "address": it.get("address", ""),
                    "reason": it.get("reason", ""),
                    "source": "xhs",
                    "source_url": it.get("source_url", ""),
                }
            )

    # 2) raw amap
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "raw", f"amap_{city}_*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("pois", [])
        for it in items:
            title = it.get("title") or it.get("name")
            if not title:
                continue
            pois.append(
                {
                    "name": title,
                    "lat": it.get("lat"),
                    "lng": it.get("lng"),
                    "district": it.get("district", ""),
                    "type": it.get("poi_type", "景点"),
                    "rating": it.get("rating"),
                    "address": it.get("body") or it.get("address", ""),
                    "reason": "",
                    "source": "amap",
                    "source_url": it.get("source_url", ""),
                }
            )

    return pois


# ── 匹配 ─────────────────────────────────────────────────────────────────
def match_one(query: str, pois: list[dict]) -> tuple[dict | None, float, str]:
    """返回(最佳POI, 分数, 匹配方式)。未命中返回 (None, 0, '')。"""
    nq = normalize(query)
    if not nq:
        return None, 0.0, ""

    # 1) 精确
    for p in pois:
        if normalize(p["name"]) == nq:
            return p, 1.0, "exact"

    # 2) 包含(只在有坐标的里面找,避免没坐标的 XHS 项干扰)
    #    拒绝 candidate 是区名这种"假阳性"(如 "南山荷兰花卉小镇" 不应匹到 "南山")
    #    要求短方至少 _MIN_MATCH_LEN 个字符
    best_contain = None
    for p in pois:
        np_ = normalize(p["name"])
        if not np_ or not p.get("lat"):
            continue
        if np_ in _DISTRICT_STOPWORDS:
            continue
        shorter = min(len(nq), len(np_))
        if shorter < _MIN_MATCH_LEN:
            continue
        if nq in np_ or np_ in nq:
            score = shorter / max(len(nq), len(np_))
            if best_contain is None or score > best_contain[1]:
                best_contain = (p, score, "contain")
    if best_contain:
        return best_contain

    # 3) fuzzy(阈值从严,避免"香蜜公园 → 荔香公园"的误匹)
    best = (None, 0.0, "")
    for p in pois:
        if not p.get("lat"):
            continue
        np_ = normalize(p["name"])
        if not np_ or np_ in _DISTRICT_STOPWORDS:
            continue
        r = SequenceMatcher(None, nq, np_).ratio()
        if r > best[1]:
            best = (p, r, "fuzzy")
    if best[1] >= _FUZZY_THRESHOLD:
        return best
    return None, best[1], ""  # 返回最高分供 debug


# ── 高德 keyword fallback ────────────────────────────────────────────────
# 直接调高德 Web Service API,显式 city=深圳 + citylimit=true,
# 避免 scrapers/amap_scraper.py 把 query 当 city 导致搜到全国同名店的坑。
_AMAP_TEXT_URL = "https://restapi.amap.com/v3/place/text"


def fallback_amap_search(query: str, city: str) -> dict | None:
    """本地池没命中时,调高德 keyword 搜索。强制 city 限定,只取首条。"""
    import requests
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("AMAP_API_KEY")
    if not api_key:
        return None

    params = {
        "key": api_key,
        "keywords": query,
        "city": city,
        "citylimit": "true",  # ← 关键:强制只返回城市内结果
        "output": "json",
        "offset": 5,
        "page": 1,
        "extensions": "all",
    }
    # 3 次指数退避 retry(网络抖动/高德 QPS 限流的典型)
    import time as _time

    data = None
    for attempt in range(3):
        try:
            resp = requests.get(_AMAP_TEXT_URL, params=params, timeout=6)
            data = resp.json()
            if data.get("status") == "1" and data.get("pois"):
                break  # 成功
            # 高德 status=0 时 infocode 常见为 10044(并发超限)→ 值得重试
            if attempt < 2:
                _time.sleep(0.5 * (2**attempt))
                continue
        except Exception:
            if attempt < 2:
                _time.sleep(0.5 * (2**attempt))
                continue
            return None
    if not data or data.get("status") != "1" or not data.get("pois"):
        return None

    # 坐标字段高德返回 "location": "lng,lat"
    r = data["pois"][0]
    loc = r.get("location", "")
    if not loc or "," not in loc:
        return None
    lng, lat = loc.split(",", 1)

    return {
        "name": r.get("name") or query,
        "lat": lat,
        "lng": lng,
        "district": r.get("adname") or r.get("district", ""),
        "type": r.get("type", "未知"),
        "rating": (r.get("biz_ext") or {}).get("rating") or None,
        "address": r.get("address", ""),
        "reason": "",
        "source": "amap_fallback",
        "source_url": f"https://www.amap.com/detail/{r.get('id', '')}",
    }


def match_must_visit(
    queries: list[str], city: str = "深圳", use_fallback: bool = True
) -> dict:
    """主入口:清单 → 匹配结果。
    use_fallback=True 时,本地未命中的 query 会调高德 keyword 搜索补齐。
    """
    pois = load_all_pois(city)
    # 去重(按 normalize 后的 name + 坐标)
    seen = set()
    dedup = []
    for p in pois:
        key = (normalize(p["name"]), p.get("lat"), p.get("lng"))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(p)

    matched = []
    unmatched = []
    for q in queries:
        poi, score, how = match_one(q, dedup)
        if poi:
            matched.append(
                {
                    "query": q,
                    "poi": apply_manual_overrides(q, poi),
                    "score": round(score, 3),
                    "how": how,
                }
            )
            continue
        # 本地未命中 → fallback 高德 keyword
        if use_fallback:
            fb = fallback_amap_search(q, city)
            if fb and fb.get("lat"):
                matched.append(
                    {
                        "query": q,
                        "poi": apply_manual_overrides(q, fb),
                        "score": 0.95,
                        "how": "amap_api",
                    }
                )
                continue
        unmatched.append({"query": q, "best_score": round(score, 3)})

    return {
        "city": city,
        "pool_size": len(dedup),
        "matched": matched,
        "unmatched": unmatched,
    }


# ── CLI 调试入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from examples.shenzhen_may_day import REQUEST

    result = match_must_visit(REQUEST["must_visit"], city=REQUEST["city"])

    print("=" * 60)
    print(f"  POI 匹配 · {result['city']} · POI 池 {result['pool_size']} 条")
    print("=" * 60)

    print(f"\n✅ 命中 {len(result['matched'])} / {len(REQUEST['must_visit'])}\n")
    for m in result["matched"]:
        p = m["poi"]
        print(
            f"  [{m['how']:7s} {m['score']:.2f}] {m['query']:30s} → {p['name']}  "
            f"({p.get('district', '?')}, {p.get('lat', '?')},{p.get('lng', '?')}) [{p['source']}]"
        )

    if result["unmatched"]:
        print(f"\n❌ 未命中 {len(result['unmatched'])}:")
        for u in result["unmatched"]:
            print(f"  - {u['query']} (最高分 {u['best_score']:.2f})")
