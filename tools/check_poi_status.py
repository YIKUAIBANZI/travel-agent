"""
景点状态检查(open/closed/appointment/limited/unknown)

单 POI 检查函数 — 3 层组合:
  1. 规则层:POI 名字含"暂停营业/停业"→ closed(0.95 置信)
  2. Exa 搜索:<poi> <date 月份> 预约 限流 → 拿最新 snippet
  3. LLM judge(qwen-turbo):读 snippet + POI meta → 输出结构化 JSON

返回:
  {
    "status":     "ok|closed|appointment|limited|unknown",
    "confidence": 0.0-1.0,
    "reason":     "...",
    "sources":    ["url1", ...],
    "advice":     "...",
  }

调用方:批量并发用 asyncio.to_thread,见 api/custom_plan.py 的 /plan/check-pois
"""

from __future__ import annotations

import json
import logging
import os
import re

import requests
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_EXA_URL = "https://api.exa.ai/search"
_STATUSES = ("ok", "closed", "appointment", "limited", "unknown")

_llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
_MODEL_LIGHT = "qwen-turbo"  # judge 用轻量模型,省钱


_AMAP_TEXT_URL = "https://restapi.amap.com/v3/place/text"


def _amap_detail(name: str, city: str) -> dict | None:
    """从高德 POI 拿权威元数据:opentime / tel / biz_ext / rating / status。
    不命中返 None。不抛异常。
    """
    key = os.getenv("AMAP_API_KEY", "")
    if not key or not name:
        return None
    params = {
        "key": key,
        "keywords": name,
        "city": city,
        "citylimit": "true",
        "output": "json",
        "offset": 3,
        "page": 1,
        "extensions": "all",  # 要 biz_ext
    }
    try:
        resp = requests.get(_AMAP_TEXT_URL, params=params, timeout=5)
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[check_poi_status] amap detail fail '{name}': {e}")
        return None
    if data.get("status") != "1" or not data.get("pois"):
        return None
    r = data["pois"][0]
    biz = r.get("biz_ext") or {}
    return {
        "name": r.get("name", ""),
        "type": r.get("type", ""),
        "opentime": biz.get("open_time") or r.get("business_area", ""),
        "cost": biz.get("cost", ""),
        "rating": biz.get("rating", ""),
        "tel": r.get("tel", ""),
        "address": r.get("address", ""),
        "status": r.get("status", ""),  # 1=正常 0=歇业/迁移,但高德很少主动给
        "source_url": f"https://www.amap.com/detail/{r.get('id', '')}"
        if r.get("id")
        else "",
    }


def _rule_layer(poi: dict) -> dict | None:
    """规则层兜底:一眼能判定的 closed 情况直接返回,不走网络。"""
    name = poi.get("name", "") or ""
    if "暂停营业" in name or "停业" in name or "关闭" in name:
        return {
            "status": "closed",
            "confidence": 0.95,
            "reason": f"POI 名字含停业关键字:'{name}'",
            "sources": [],
            "advice": "建议替换为同片区其他 POI",
        }
    return None


def _exa_query(
    query: str, num: int, key: str, since: str = "2025-01-01T00:00:00.000Z"
) -> list[dict]:
    """单次 Exa 查询,失败或 0 结果返 []。"""
    payload = {
        "query": query,
        "numResults": min(num, 5),
        "contents": {"text": {"maxCharacters": 500}},
        "startPublishedDate": since,
    }
    try:
        resp = requests.post(
            _EXA_URL,
            json=payload,
            headers={"x-api-key": key, "Content-Type": "application/json"},
            timeout=8,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[check_poi_status] Exa fail '{query[:40]}': {e}")
        return []
    data = resp.json()
    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "text": (r.get("text") or "")[:500],
            "published": r.get("publishedDate", ""),
        }
        for r in (data.get("results") or [])[:num]
    ]


def _exa_search(
    poi_name: str, date: str, city: str = "深圳", num: int = 4
) -> list[dict]:
    """2 级降级:先严格查预约/限流关键词,0 结果就宽泛查店名+地名,保证 LLM 有材料。"""
    key = os.getenv("EXA_API_KEY", "")
    if not key:
        return []

    # 1. 严格查:带时效和关键词
    ym_match = re.match(r"(\d{4})-(\d{2})", date or "")
    period = f"{ym_match.group(1)}年{int(ym_match.group(2))}月" if ym_match else ""
    strict_q = f"{poi_name} {period} 预约 限流 开放时间 闭园"
    results = _exa_query(strict_q, num, key)
    if results:
        return results

    # 2. 降级:只搜店名 + 城市(拿任何最新网页)
    loose_q = f"{poi_name} {city}"
    logger.info(f"[check_poi_status] fallback to loose search: {loose_q}")
    return _exa_query(loose_q, num, key)


_JUDGE_SYSTEM = """你是景点/餐厅的营业状态判断员。读 POI 元数据 + 网页摘要片段,
判断用户在指定日期能否正常前往,并给出结构化结论。

# 状态(status)含义
- ok:正常开放,无特别限制
- closed:当天关闭/停业/闭园/维修
- appointment:需提前预约才能进(小程序/官方渠道)
- limited:限流/限购/限时段(需排队或早到)
- unknown:信息不足,无法判断

# 输出(JSON)
{
  "status": "ok|closed|appointment|limited|unknown",
  "confidence": 0.0-1.0,   // 基于证据强度
  "reason": "一句话解释,引用来源里的关键事实",
  "sources": ["url1","url2"],  // 引用到的证据 URL
  "advice": "给用户的建议(一句话)"
}

# 证据分层(按可信度)
1. **poi.amap_authoritative** 来自高德官方数据,是最高置信度的权威证据。
   - 如果 opentime 明确(如 "09:00-22:00"),通常 status=ok
   - 如果 status 字段 = 0 / 名字含"暂停",status=closed
   - 如果 cost 有值,说明需付费但能去
2. **web_snippets** 是补充:看有没有提到"需预约/限流/临时闭园/改造"
3. poi.amap + snippet 冲突时,优先新的(看 snippet 发布日期)

# 规则
- 证据强度低(高德无返回 + snippet 模糊)时设 unknown,confidence ≤ 0.5
- 有高德 opentime 就基本给 ok @ 0.85+
- 只引用 snippet 里真实出现的 URL + 可加 amap source_url
- **status=unknown 时 advice 必须给兜底动作**:
  若高德返回了 tel,写"可拨打 xxxxx 确认";否则"在大众点评/小红书搜'<poi名>近期'"
- 中文回答
"""


def _llm_judge(
    poi: dict, date: str, snippets: list[dict], amap: dict | None = None
) -> dict:
    """用 LLM 综合判断。
    - poi: 用户传入的原始元数据
    - snippets: Exa 最新网页片段
    - amap: 高德 POI 权威字段(opentime/tel/rating 等),有则作为高置信证据
    """
    poi_block = {
        "name": poi.get("name"),
        "district": poi.get("district"),
        "type": poi.get("type"),
    }
    if amap:
        poi_block["amap_authoritative"] = amap  # 标注权威来源
    user_msg = json.dumps(
        {
            "poi": poi_block,
            "target_date": date,
            "web_snippets": snippets,
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        resp = _llm.chat.completions.create(
            model=_MODEL_LIGHT,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=30,
        )
        raw = resp.choices[0].message.content
        out = json.loads(raw)
    except Exception as e:
        logger.warning(f"[check_poi_status] llm judge fail: {e}")
        return {
            "status": "unknown",
            "confidence": 0.0,
            "reason": f"LLM 判断失败:{e}",
            "sources": [],
            "advice": "建议人工确认",
        }

    # 标准化
    st = str(out.get("status", "unknown")).lower()
    if st not in _STATUSES:
        st = "unknown"
    advice = out.get("advice", "") or ""
    # 兜底:unknown 必须有可执行建议
    if st == "unknown" and not advice:
        advice = f"建议在大众点评搜「{poi.get('name', '')}」近期评论,或出发前电话/小程序确认营业状态"
    return {
        "status": st,
        "confidence": float(out.get("confidence", 0.0) or 0.0),
        "reason": out.get("reason", "") or "",
        "sources": list(out.get("sources") or []),
        "advice": advice,
    }


def check_poi(poi: dict, date: str, city: str = "深圳") -> dict:
    """单 POI 状态检查(同步调用)。3 层证据金字塔:
      1. 规则层(0 调用): 名字含"暂停营业"→ closed
      2. 高德详情层(免费): opentime/tel/biz_ext 作为权威信号
      3. Exa 网页层(¥0.005): 最新动态(预约/限流变更)
    LLM judge 同时读 2+3 综合判断
    """
    # 1. 规则层
    fast = _rule_layer(poi)
    if fast:
        return {"query": poi.get("name"), "date": date, **fast}

    # 2. 高德权威 detail(免费,~300ms)
    amap = _amap_detail(poi.get("name", ""), city)

    # 3. Exa 搜索(2 级降级)
    snippets = _exa_search(poi.get("name", ""), date, city=city)

    # 4. LLM 综合判断
    judged = _llm_judge(poi, date, snippets, amap=amap)

    # 兜底 advice 升级:有高德电话就附上
    if judged.get("status") == "unknown" and amap and amap.get("tel"):
        if "大众点评" in judged.get("advice", ""):
            judged["advice"] = f"可拨打 {amap['tel']} 确认,或" + judged["advice"]

    # 附加高德 source_url 到 sources(如果判定非 unknown 且高德有 url)
    if judged.get("status") != "unknown" and amap and amap.get("source_url"):
        srcs = list(judged.get("sources") or [])
        if amap["source_url"] not in srcs:
            srcs.append(amap["source_url"])
            judged["sources"] = srcs

    return {"query": poi.get("name"), "date": date, **judged}
