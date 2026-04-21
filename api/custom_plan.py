"""
Step 5 · 定制版行程 API 端点

POST /plan/custom

输入:用户完整场景(必去清单 + 约束)
输出:经过"match → cluster → reflect × N"完整 agent 链路的行程 JSON
"""

import logging
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from agent.architect_toolloop import LLMCallError, reflect_and_plan
from tools.cluster_pois import cluster_and_assign
from tools.match_pois import match_must_visit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["custom-plan"])

# 限流:同一 IP 每小时最多 5 次 /plan/custom(一次请求 3-5 次 LLM 调用,防成本攻击)
# 在 api/main.py 里需挂 state.limiter = limiter 和 exception_handler
limiter = Limiter(key_func=get_remote_address)


# ── Pydantic models ────────────────────────────────────────────────────────


class DateRange(BaseModel):
    start: str  # YYYY-MM-DD
    end: str


class Party(BaseModel):
    people: int = 2
    relation: str = "情侣"
    traveler_type: Literal["A", "B", "C", "D", "E", "F"] = "A"


class Budget(BaseModel):
    target_cny: int = 4000
    hard_limit: bool = False
    covers: list[str] = Field(
        default_factory=lambda: ["住宿", "餐饮", "景点门票", "市内交通"]
    )
    excludes: list[str] = Field(default_factory=lambda: ["往返机票"])


class Constraints(BaseModel):
    wake_up: str = "10:00"
    max_daily_steps: int = 10000
    transport: list[str] = Field(default_factory=lambda: ["subway", "taxi"])
    walk_radius_m: int = 800


class Hotel(BaseModel):
    area_prefer: list[str] = Field(default_factory=list)
    booked: bool = False


class CustomPlanRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=20)
    dates: DateRange
    party: Party
    budget: Budget
    must_visit: list[str] = Field(..., min_length=1, max_length=50)
    constraints: Constraints = Field(default_factory=Constraints)
    hotel: Hotel = Field(default_factory=Hotel)
    holiday_crowd_risk_dates: list[str] = Field(default_factory=list, max_length=30)
    max_rounds: int = Field(2, ge=1, le=3)  # 反思循环最多几轮(卡上限防成本飞)


class CustomPlanResponse(BaseModel):
    city: str
    days: int
    match_summary: dict  # 匹配成功率
    day_clusters: list[dict]  # Step 3 的输出(给前端画地图用)
    itinerary: dict  # Step 4 的最终行程
    trace: list[dict]  # propose / critique / revise 过程
    stats: dict  # LLM 调用次数、是否收敛等


# ── 路由 ───────────────────────────────────────────────────────────────────


@router.post("/custom", response_model=CustomPlanResponse)
@limiter.limit("5/hour")  # 每 IP 每小时 5 次,防成本攻击
async def plan_custom(request: Request, body: CustomPlanRequest) -> CustomPlanResponse:
    """定制版:给清单和约束,一口气跑 match → cluster → reflect 全链路。

    耗时 3-5 min(LLM 反思循环)。单 worker 下同步阻塞,用 asyncio.to_thread 解放 event loop。
    """
    import asyncio

    logger.info(
        f"[plan/custom] city={body.city} days={body.dates.start}~{body.dates.end} "
        f"must_visit={len(body.must_visit)} 项"
    )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_plan_sync, body),
            timeout=360.0,  # 6 分钟硬上限,超时返回 504
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "规划超时(>6min),请重试或减少 must_visit/max_rounds")
    except LLMCallError as e:
        logger.exception("LLM 调用失败")
        raise HTTPException(502, f"LLM 上游失败:{e}")


def _run_plan_sync(body: CustomPlanRequest) -> CustomPlanResponse:
    """同步实现(在 threadpool 里跑),主体逻辑。"""

    # 1. Match POIs
    match_result = match_must_visit(body.must_visit, city=body.city)
    matched = match_result["matched"]
    if not matched:
        raise HTTPException(
            400, f"0 个清单项匹配到 POI,无法规划({body.city} 数据可能缺失)"
        )

    # 2. 生成日期序列
    try:
        start = date.fromisoformat(body.dates.start)
        end = date.fromisoformat(body.dates.end)
    except ValueError as e:
        raise HTTPException(400, f"日期格式错误: {e}")
    if end < start:
        raise HTTPException(400, "end < start")
    dates = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]
    if len(dates) > 14:
        raise HTTPException(400, "单次最多规划 14 天")

    # 3. Cluster + 日期分配
    day_clusters = cluster_and_assign(
        matched=matched,
        dates=dates,
        risk_dates=body.holiday_crowd_risk_dates,
    )

    # 4. Reflect & plan
    request_dict = body.model_dump()
    result = reflect_and_plan(
        request=request_dict,
        days_plan=day_clusters,
        max_rounds=body.max_rounds,
    )

    # 5. 统计
    trace = result["trace"]
    n_propose = sum(1 for t in trace if t["step"] == "propose")
    n_revise = sum(1 for t in trace if t["step"].startswith("revise"))
    n_critique = sum(1 for t in trace if t["step"].startswith("critique"))
    last_critique = next(
        (t for t in reversed(trace) if t["step"].startswith("critique")), None
    )
    converged = last_critique is not None and not any(
        i.get("severity") in ("critical", "high")
        for i in last_critique.get("issues", [])
    )

    return CustomPlanResponse(
        city=body.city,
        days=len(dates),
        match_summary={
            "matched": len(matched),
            "total": len(body.must_visit),
            "pool_size": match_result["pool_size"],
            "unmatched": match_result.get("unmatched", []),
        },
        day_clusters=[
            {
                "day": d["day"],
                "date": d["date"],
                "is_risk_day": d["is_risk_day"],
                "is_free_day": d.get("is_free_day", False),
                "hotness": d["hotness"],
                "pois": [
                    {
                        "name": (m.get("poi") or {}).get("name"),
                        "lat": (m.get("poi") or {}).get("lat"),
                        "lng": (m.get("poi") or {}).get("lng"),
                        "district": (m.get("poi") or {}).get("district"),
                        "query": m.get("query"),
                    }
                    for m in d["pois"]
                ],
            }
            for d in day_clusters
        ],
        itinerary=result["itinerary"],
        trace=trace,
        stats={
            "llm_calls": n_propose + n_revise + n_critique,
            "propose": n_propose,
            "critique": n_critique,
            "revise": n_revise,
            "converged": converged,
        },
    )
