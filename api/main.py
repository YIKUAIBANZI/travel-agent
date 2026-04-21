"""
FastAPI 应用主入口

启动方式（开发）:
  cd ~/Desktop/sth/travel-agent
  uvicorn api.main:app --reload --port 8000

启动方式（生产，阿里云）:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

API 文档: http://localhost:8000/docs
"""

import logging
import os
import sys

# 确保项目根目录在 sys.path（直接运行 uvicorn api.main:app 时需要）
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware


from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.agent import process_message
from api.custom_plan import limiter as custom_plan_limiter
from api.custom_plan import router as custom_plan_router
from api.models import (
    ChatRequest,
    ChatResponse,
    NewSessionRequest,
    SessionInfo,
    SessionState,
    SpotSelectionRequest,
)
from api.session import store

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── FastAPI 应用 ───────────────────────────────────────────────────────────

app = FastAPI(
    title="旅游规划 Agent API",
    description="多轮对话式旅游行程规划，基于高德 POI + 小红书攻略 + 通义千问",
    version="1.0.0",
)

# CORS — 允许前端任意域名调用（生产环境请改为具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SlowAPI 限流 — 需把 limiter 挂到 app.state + 注册 429 handler + 加中间件
app.state.limiter = custom_plan_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── 路由 ───────────────────────────────────────────────────────────────────


# 定制版路由(Step 5):POST /plan/custom
app.include_router(custom_plan_router)


@app.get("/health")
def health():
    """健康检查"""
    return {
        "status": "ok",
        "sessions": store.count(),
    }


@app.post("/session/new")
def new_session(body: NewSessionRequest = None) -> dict:
    """
    显式创建新会话，返回 session_id。
    前端也可以跳过这步，直接 POST /chat（不传 session_id 时自动创建）。
    """
    session = store.create()
    logger.info(f"新建会话: {session.session_id}")
    return {
        "session_id": session.session_id,
        "state": session.state,
        "message": (
            "你好！我是旅游规划助手。请告诉我你想去哪个城市，"
            '计划几天，几个人出行？例如："我想去成都玩4天，2个人，公共交通"'
        ),
    }


@app.get("/session/{session_id}")
def get_session(session_id: str) -> SessionInfo:
    """查询会话状态"""
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return SessionInfo(**session.to_dict())


@app.post("/chat")
def chat(body: ChatRequest) -> ChatResponse:
    """
    核心对话接口

    - 首次调用不传 session_id（或传空字符串）时自动创建会话
    - 后续调用传之前返回的 session_id
    - 根据当前会话状态自动路由到对应处理逻辑

    状态流转:
      INIT → COLLECT_PREFERENCES → SELECT_SPOTS → DONE
    """
    session, is_new = store.get_or_create(body.session_id)

    if is_new:
        logger.info(f"自动创建会话: {session.session_id}")
    else:
        logger.info(
            f"会话 {session.session_id[:8]}... 状态={session.state} 消息={body.message[:50]}"
        )

    try:
        response = process_message(session, body.message)
    except Exception as e:
        logger.exception(f"处理消息时发生未知错误: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误，请稍后重试")

    return response


@app.post("/chat/select-spots")
def select_spots(body: SpotSelectionRequest) -> ChatResponse:
    """
    景点选择专用接口（SELECT_SPOTS 阶段）
    前端可以渲染复选框后通过此接口提交，比自然语言更精确。

    body.selected_indices: 景点序号列表（0-based，对应 /chat 返回的 spots 数组下标）
    """
    session = store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.state != SessionState.SELECT_SPOTS:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {session.state} 不支持景点选择，请先通过 /chat 进入景点选择阶段",
        )

    candidates = session.candidate_spots
    selected = []
    for idx in body.selected_indices:
        if 0 <= idx < len(candidates):
            selected.append(candidates[idx])

    if not selected:
        raise HTTPException(status_code=400, detail="未选中任何有效景点")

    session.selected_spots = selected
    session.state = SessionState.GENERATING_ITINERARY

    names_str = "、".join(p["name"] for p in selected)
    logger.info(f"景点选择: {session.session_id[:8]} → {names_str}")

    # 直接进入行程生成
    from api.agent import _generate_itinerary

    try:
        return _generate_itinerary(session)
    except Exception as e:
        logger.exception(f"行程生成失败: {e}")
        raise HTTPException(status_code=500, detail="行程生成失败，请重试")


@app.delete("/session/{session_id}")
def delete_session(session_id: str) -> dict:
    """删除会话（可选清理接口）"""
    deleted = store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "deleted", "session_id": session_id}


# ── 静态前端 ────────────────────────────────────────────────────────────────

_web_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)


@app.get("/")
def serve_index():
    """返回前端页面"""
    return FileResponse(os.path.join(_web_dir, "index.html"))


# 挂载 web/ 目录（/static 前缀），供 index.html 引用额外静态资源时使用
if os.path.isdir(_web_dir):
    app.mount("/static", StaticFiles(directory=_web_dir), name="static")
