"""
数据模型定义 — Pydantic schemas for request/response
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── 会话状态机 ──────────────────────────────────────────────────────────────


class SessionState(str, Enum):
    """对话状态机的五个阶段"""

    INIT = "init"  # 初始：等待目的地+基本信息
    COLLECT_PREFERENCES = "collect_prefs"  # 追问偏好
    SELECT_SPOTS = "select_spots"  # 展示候选景点，等待用户选择
    GENERATING_ITINERARY = "generating"  # 行程生成中
    DONE = "done"  # 完成，返回行程


# ── 地点数据 ─────────────────────────────────────────────────────────────────


class Place(BaseModel):
    """景点/餐饮等地点"""

    name: str
    type: str = "景点"
    lat: Optional[str] = None
    lng: Optional[str] = None
    reason: Optional[str] = None
    rating: Optional[float] = None
    opentime: Optional[str] = None
    warnings: Optional[str] = None
    address: Optional[str] = None
    district: Optional[str] = None
    best_time: Optional[str] = None
    source_platform: Optional[str] = None
    source_url: Optional[str] = None
    nearby_food: Optional[list[dict]] = None


# ── API 请求 / 响应 ──────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """发送消息的请求体"""

    session_id: str = Field(..., description="会话 ID")
    message: str = Field(..., description="用户消息", min_length=1)


class NewSessionRequest(BaseModel):
    """创建新会话（可选，也可以在 /chat 里自动创建）"""

    pass


class SpotSelectionRequest(BaseModel):
    """用户选择景点（SELECT_SPOTS 阶段专用，也可用普通 /chat 传递）"""

    session_id: str
    selected_indices: list[int] = Field(
        ..., description="用户选择的景点索引（0-based）"
    )


class WeatherInfo(BaseModel):
    """天气信息"""

    city: str
    date: Optional[str] = None
    weather: Optional[str] = None  # 天气现象：晴/阴/小雨...
    temperature: Optional[str] = None  # 白天温度
    temperature_night: Optional[str] = None
    wind_direction: Optional[str] = None
    wind_power: Optional[str] = None
    humidity: Optional[str] = None


class DayItinerary(BaseModel):
    """单天行程"""

    day: int  # 第几天
    date: Optional[str] = None  # 日期（可选，用户指定出发日才有）
    theme: Optional[str] = None  # 当天主题（如"南山文艺慢半天"）
    rhythm: Optional[str] = None  # 节奏（缓启动/满日/半休息）
    district: Optional[str] = None  # 主要活动区域
    weather: Optional[WeatherInfo] = None  # 当天天气
    morning: list[dict] = Field(default_factory=list)  # 上午安排
    lunch: Optional[dict] = None  # 午餐推荐
    afternoon: list[dict] = Field(default_factory=list)  # 下午安排
    dinner: Optional[dict] = None  # 晚餐推荐
    evening: list[dict] = Field(default_factory=list)  # 晚上安排
    tips: Optional[str] = None  # 当天小贴士
    alternatives: list[dict] = Field(default_factory=list)  # 备选方案


class Itinerary(BaseModel):
    """完整行程"""

    city: str
    days: int
    days_plan: list[DayItinerary]
    map_url: Optional[str] = None  # 高德地图链接
    static_map_url: Optional[str] = None  # 静态地图图片 URL
    summary: Optional[str] = None  # LLM 生成的行程总结
    daily_budget_estimate: Optional[str] = None  # 每日预估花费
    accommodation: list[dict] = Field(default_factory=list)  # 住宿推荐
    food_guide: list[dict] = Field(default_factory=list)  # 必吃美食清单
    packing_tips: list[str] = Field(default_factory=list)
    booking_reminders: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """统一的聊天响应"""

    session_id: str
    state: SessionState
    message: str  # 给用户展示的文字

    # 根据阶段附加的结构化数据
    spots: Optional[list[Place]] = None  # SELECT_SPOTS 阶段：候选景点列表
    itinerary: Optional[Itinerary] = None  # DONE 阶段：完整行程

    # 调试 / 进度信息
    progress: Optional[str] = None  # 如 "正在匹配周边美食..."


class SessionInfo(BaseModel):
    """会话状态查询响应"""

    session_id: str
    state: SessionState
    city: Optional[str] = None
    days: Optional[int] = None
    traveler_type: Optional[str] = None
    created_at: str
    updated_at: str
