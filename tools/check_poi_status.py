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

# 规则
- 证据强度低(snippet 模糊/过旧/无直接提及)时设 unknown,confidence ≤ 0.5
- 只引用 snippet 里真实出现的 URL,不要编
- **status=unknown 时,advice 必须给用户可执行的兜底动作**:
  建议在大众点评/抖音/小红书搜"<poi名>近期",或出发前电话/小程序确认
- 中文回答
"""


def _llm_judge(poi: dict, date: str, snippets: list[dict]) -> dict:
    """用 LLM 综合判断。snippets 为空时也要尝试给结论(通常 unknown)。"""
    user_msg = json.dumps(
        {
            "poi": {
                "name": poi.get("name"),
                "district": poi.get("district"),
                "type": poi.get("type"),
                "opentime": poi.get("opentime"),
            },
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
    """单 POI 状态检查(同步调用)。
    poi:需要含 name / district / type 字段
    date:YYYY-MM-DD
    city:用于 Exa 降级查询时加地名
    """
    # 1. 规则层
    fast = _rule_layer(poi)
    if fast:
        return {"query": poi.get("name"), "date": date, **fast}

    # 2. Exa 搜索(2 级降级)
    snippets = _exa_search(poi.get("name", ""), date, city=city)

    # 3. LLM 综合
    judged = _llm_judge(poi, date, snippets)
    return {"query": poi.get("name"), "date": date, **judged}
