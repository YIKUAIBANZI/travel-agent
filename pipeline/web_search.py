# pipeline/web_search.py
"""
Exa 网络搜索封装 — 获取最新旅游攻略

功能：
  - 用 Exa REST API 搜索指定城市的旅游攻略
  - 返回攻略文本列表，供 LLM 做补充参考
  - 没有 EXA_API_KEY 或网络失败时优雅降级，返回空列表

依赖：
  - requests（标准库替代，无需额外安装）
  - EXA_API_KEY 环境变量
"""

import logging
import os

import requests


logger = logging.getLogger(__name__)

EXA_API_URL = "https://api.exa.ai/search"


def _get_exa_key() -> str:
    return os.getenv("EXA_API_KEY", "")


# 每条攻略最多保留多少字符，避免 token 爆炸
MAX_TEXT_LENGTH = 800


def search_travel_guides(
    city: str,
    preferences: str = "",
    num_results: int = 5,
) -> list[str]:
    """
    搜索指定城市的最新旅游攻略

    Args:
        city:         目的地城市，如"深圳"
        preferences:  用户偏好，如"美食 历史文化"，用于丰富查询词
        num_results:  返回结果数量（1-10）

    Returns:
        攻略文本列表，每条是一段有用的攻略内容。
        如果 Exa 不可用，返回空列表（调用方无需判断，直接合并即可）。
    """
    api_key = _get_exa_key()
    if not api_key:
        logger.debug("未配置 EXA_API_KEY，跳过网络搜索")
        return []

    # 构造查询词：城市 + 偏好关键词
    pref_keywords = _extract_keywords(preferences)
    if pref_keywords:
        query = f"{city}旅游攻略 {pref_keywords} 景点推荐"
    else:
        query = f"{city}旅游攻略 必去景点 美食推荐 避坑"

    payload = {
        "query": query,
        "numResults": min(max(1, num_results), 10),
        "contents": {
            "text": {
                "maxCharacters": MAX_TEXT_LENGTH,
            }
        },
        # 优先搜索近一年内的内容，保证时效性
        "startPublishedDate": "2024-01-01T00:00:00.000Z",
    }

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            EXA_API_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        logger.warning("Exa 搜索超时，跳过网络攻略")
        return []
    except requests.exceptions.RequestException as e:
        logger.warning(f"Exa 搜索请求失败: {e}，跳过网络攻略")
        return []
    except Exception as e:
        logger.warning(f"Exa 搜索异常: {e}，跳过网络攻略")
        return []

    results = data.get("results", [])
    if not results:
        logger.debug(f"Exa 未找到 {city} 的相关攻略")
        return []

    guides = []
    for r in results:
        title = r.get("title", "").strip()
        text = (r.get("text") or "").strip()
        url = r.get("url", "")

        if not text:
            continue

        # 拼装成一段带来源标注的攻略文本
        guide = f"【{title}】({url})\n{text}"
        guides.append(guide)

    logger.info(f"Exa 搜索完成：城市={city}，获取到 {len(guides)} 条攻略")
    return guides


def search_hotel_guides(
    city: str,
    traveler_type: str = "A",
    num_results: int = 3,
) -> list[str]:
    """
    搜索住宿推荐攻略

    Args:
        city:           目的地城市
        traveler_type:  旅行者类型 A-F，影响搜索关键词
        num_results:    返回数量
    Returns:
        住宿攻略文本列表
    """
    api_key = _get_exa_key()
    if not api_key:
        return []

    type_keywords = {
        "A": "情侣 设计感 拍照好看",
        "B": "亲子 家庭房 儿童友好",
        "C": "舒适 安静 电梯 无障碍",
        "D": "便宜 青旅 性价比 民宿",
        "E": "特色 老建筑 文化 精品酒店",
        "F": "美食街附近 交通方便 市中心",
    }
    kw = type_keywords.get(traveler_type, "交通方便")
    query = f"{city}住宿推荐 {kw} 酒店民宿攻略"

    payload = {
        "query": query,
        "numResults": min(max(1, num_results), 5),
        "contents": {"text": {"maxCharacters": 600}},
        "startPublishedDate": "2024-01-01T00:00:00.000Z",
    }
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    try:
        resp = requests.post(EXA_API_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Exa 住宿搜索异常: {e}")
        return []

    guides = []
    for r in data.get("results", []):
        title = r.get("title", "").strip()
        text = (r.get("text") or "").strip()
        if text:
            guides.append(f"【{title}】\n{text}")

    logger.info(f"Exa 住宿搜索完成：城市={city}，{len(guides)} 条")
    return guides


def search_food_guides(
    city: str,
    preferences: str = "",
    num_results: int = 3,
) -> list[str]:
    """
    搜索美食推荐攻略（补充 FoodMatcher 只有名字的不足）

    Args:
        city:         目的地城市
        preferences:  口味偏好
        num_results:  返回数量
    Returns:
        美食攻略文本列表
    """
    api_key = _get_exa_key()
    if not api_key:
        return []

    pref_kw = ""
    for kw in ["火锅", "串串", "小吃", "海鲜", "烧烤", "甜品", "咖啡", "夜市", "早餐"]:
        if kw in preferences:
            pref_kw += f" {kw}"
    if not pref_kw:
        pref_kw = "必吃 特色美食 本地人推荐"

    query = f"{city}美食攻略{pref_kw} 餐厅推荐 人均"

    payload = {
        "query": query,
        "numResults": min(max(1, num_results), 5),
        "contents": {"text": {"maxCharacters": 600}},
        "startPublishedDate": "2024-01-01T00:00:00.000Z",
    }
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    try:
        resp = requests.post(EXA_API_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Exa 美食搜索异常: {e}")
        return []

    guides = []
    for r in data.get("results", []):
        title = r.get("title", "").strip()
        text = (r.get("text") or "").strip()
        if text:
            guides.append(f"【{title}】\n{text}")

    logger.info(f"Exa 美食搜索完成：城市={city}，{len(guides)} 条")
    return guides


def _extract_keywords(preferences: str) -> str:
    """
    从偏好描述中提取搜索关键词（最多取前 3 个词）
    避免查询词过长导致搜索精度下降
    """
    if not preferences:
        return ""

    # 常见偏好关键词映射，丰富搜索词
    keyword_map = {
        "美食": "美食 特色小吃",
        "历史": "历史文化 古迹",
        "文化": "文化体验 博物馆",
        "自然": "自然风光 户外",
        "购物": "购物 商圈",
        "网红": "网红打卡 ins风",
        "亲子": "亲子游 儿童友好",
        "休闲": "休闲放松 慢节奏",
        "摄影": "摄影 拍照圣地",
        "夜景": "夜景 夜市",
    }

    matched = []
    for key, val in keyword_map.items():
        if key in preferences:
            matched.append(val)
            if len(matched) >= 2:
                break

    if matched:
        return " ".join(matched)

    # fallback：取偏好描述前 20 个字
    return preferences[:20]
