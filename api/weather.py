"""
高德天气 API 封装

接口文档: https://lbs.amap.com/api/webservice/guide/api/weatherinfo
- 实时天气: extensions=base
- 天气预报（未来3天）: extensions=all

同一个 AMAP_API_KEY，无需额外申请。
"""

import logging
from typing import Optional

import requests

import config
from api.models import WeatherInfo

logger = logging.getLogger(__name__)

WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"


def get_weather_forecast(city: str, days: int = 3) -> list[WeatherInfo]:
    """
    获取城市天气预报（最多未来 3 天）

    Args:
        city: 城市名，如 "深圳"
        days: 需要几天的预报（1-3）
    Returns:
        WeatherInfo 列表，按日期排序；失败时返回空列表
    """
    if not config.AMAP_API_KEY:
        logger.warning("未配置 AMAP_API_KEY，无法获取天气")
        return []

    try:
        resp = requests.get(
            WEATHER_URL,
            params={
                "key": config.AMAP_API_KEY,
                "city": city,
                "extensions": "all",  # 获取预报（base 是实况）
                "output": "json",
            },
            timeout=5,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"天气 API 请求失败 [{city}]: {e}")
        return []

    if data.get("status") != "1":
        logger.warning(f"天气 API 错误: {data.get('info')} (city={city})")
        return []

    forecasts = data.get("forecasts", [])
    if not forecasts:
        return []

    casts = forecasts[0].get("casts", [])[:days]
    result = []
    for cast in casts:
        result.append(
            WeatherInfo(
                city=city,
                date=cast.get("date"),
                weather=cast.get("dayweather"),  # 白天天气
                temperature=cast.get("daytemp"),  # 白天温度
                temperature_night=cast.get("nighttemp"),  # 夜间温度
                wind_direction=cast.get("daywind"),
                wind_power=cast.get("daypower"),
                humidity=None,  # 预报接口不返回湿度
            )
        )

    return result


def get_current_weather(city: str) -> Optional[WeatherInfo]:
    """
    获取实时天气（用于当天展示）
    """
    if not config.AMAP_API_KEY:
        return None

    try:
        resp = requests.get(
            WEATHER_URL,
            params={
                "key": config.AMAP_API_KEY,
                "city": city,
                "extensions": "base",
                "output": "json",
            },
            timeout=5,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"实时天气请求失败 [{city}]: {e}")
        return None

    if data.get("status") != "1":
        return None

    lives = data.get("lives", [])
    if not lives:
        return None

    live = lives[0]
    return WeatherInfo(
        city=city,
        date=live.get("reporttime", "")[:10],
        weather=live.get("weather"),
        temperature=live.get("temperature"),
        wind_direction=live.get("winddirection"),
        wind_power=live.get("windpower"),
        humidity=live.get("humidity"),
    )


def format_weather_summary(weathers: list[WeatherInfo]) -> str:
    """将天气预报列表转成给 LLM 用的文字摘要"""
    if not weathers:
        return "天气信息暂不可用"

    parts = []
    for w in weathers:
        day_str = f"{w.date} " if w.date else ""
        temp = f"{w.temperature_night}~{w.temperature}°C" if w.temperature else ""
        parts.append(f"{day_str}{w.weather or ''} {temp}".strip())

    return "；".join(parts)
