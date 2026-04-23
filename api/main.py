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


def _render_template(filename: str):
    """读 web/ 下模板,注入高德 JS SDK key。"""
    from fastapi.responses import HTMLResponse

    html_path = os.path.join(_web_dir, filename)
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    amap_js_key = os.getenv("AMAP_WEB_JS_KEY") or os.getenv("AMAP_API_KEY") or ""
    html = html.replace("__AMAP_WEB_JS_KEY__", amap_js_key)
    return HTMLResponse(html)


@app.get("/")
def serve_index():
    """返回前端主页(对话式)。"""
    return _render_template("index.html")


@app.get("/plan/view")
def serve_plan_view():
    """返回行程可视化页(左列表 + 右地图,类似携程 TripPlanner)。"""
    return _render_template("plan.html")


@app.get("/plan/stack")
def serve_plan_stack():
    """卡片堆叠版行程视图(Anthropic design handoff 复刻,暖米色 + Instrument Serif)。"""
    return _render_template("plan_stack.html")


# ── XHS 图片代理(绕防盗链)── 本地演示用,不建议上 public
_XHS_IMG_CACHE: dict = {}


@app.get("/img-proxy")
def img_proxy(url: str):
    """通过后端带 Referer 取 XHS CDN 图,返回二进制。
    注意:本接口仅为本地演示,生产部署前请移除或加签名防滥用。
    """
    import hashlib

    import requests
    from fastapi import HTTPException
    from fastapi.responses import Response

    if not url.startswith(
        ("http://sns-webpic", "https://sns-webpic", "http://sns-img", "https://sns-img")
    ):
        raise HTTPException(400, "only xhs cdn urls allowed")
    key = hashlib.sha1(url.encode()).hexdigest()
    if key in _XHS_IMG_CACHE:
        return Response(_XHS_IMG_CACHE[key], media_type="image/webp")
    try:
        r = requests.get(
            url,
            headers={
                "Referer": "https://www.xiaohongshu.com/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            timeout=8,
        )
        if r.status_code != 200:
            raise HTTPException(502, f"upstream {r.status_code}")
    except requests.RequestException as e:
        raise HTTPException(502, f"fetch failed: {e}")
    _XHS_IMG_CACHE[key] = r.content
    return Response(r.content, media_type="image/webp")


# ── 一次性 enrich:给 POI 匹配 XHS 图片 + 静态天气 ──
_DEMO_ENRICHED: dict | None = None


def _enrich_demo(raw: dict) -> dict:
    """懒匹配:给每个 POI 找一张 XHS 图(通过 name 子串命中 note 的 title/body)+ 填深圳五一静态天气。"""
    import glob
    import json as _json

    # 1. 加载所有 XHS note,建 name → image URL 索引(简单扫一次)
    name_to_img: dict[str, str] = {}
    for path in glob.glob(os.path.join(_root, "data", "raw", "xhs_深圳*.json")):
        try:
            notes = _json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for note in notes if isinstance(notes, list) else notes.get("notes", []):
            imgs = note.get("images") or []
            if not imgs:
                continue
            (note.get("title", "") + " " + note.get("body", "")).strip()
            # 朴素切词:取高频词(4 字以上),让 POI 匹配上
            first_img = imgs[0]
            # 给 blob 里出现过的每个 2-6 字窗口都注册一下(过于激进)→ 只留 name_to_img 只记 title
            for tok in [note.get("title", "")]:
                if tok and tok not in name_to_img:
                    name_to_img[tok] = first_img

    # 2. 对每个 POI,遍历 name_to_img 找第一个子串命中
    def find_img(poi_name: str) -> str | None:
        if not poi_name:
            return None
        core = poi_name.split("(")[0].split("·")[0].strip()[:4]
        if not core:
            return None
        for title, img in name_to_img.items():
            if core in title:
                return img
        return None

    # 3. 静态天气(深圳五一典型,2-5 月平均)
    static_weather = {
        "2026-04-29": {
            "icon": "sun",
            "label": "晴",
            "tempHi": 27,
            "tempLo": 20,
            "rain": 10,
        },
        "2026-04-30": {
            "icon": "cloud",
            "label": "多云",
            "tempHi": 26,
            "tempLo": 21,
            "rain": 25,
        },
        "2026-05-01": {
            "icon": "sun",
            "label": "晴",
            "tempHi": 28,
            "tempLo": 22,
            "rain": 15,
        },
        "2026-05-02": {
            "icon": "rain",
            "label": "阵雨",
            "tempHi": 25,
            "tempLo": 21,
            "rain": 70,
        },
        "2026-05-03": {
            "icon": "cloud",
            "label": "多云",
            "tempHi": 26,
            "tempLo": 20,
            "rain": 30,
        },
        "2026-05-04": {
            "icon": "sun",
            "label": "晴",
            "tempHi": 27,
            "tempLo": 20,
            "rain": 10,
        },
    }

    for c in raw.get("day_clusters", []):
        c["weather"] = static_weather.get(c.get("date"))
        # 注:XHS CDN URL 带时效签名,离线超过数小时后 403。
        # 此处不自动注入,需要实时爬到 URL 后再填 p["image"]。
        # for p in c.get("pois", []):
        #     img = find_img(p.get("name") or p.get("query") or "")
        #     if img:
        #         p["image"] = img
    return raw


@app.post("/plan/resolve-poi")
def resolve_poi(body: dict):
    """前端添加 stop 时用:给个名字,返回坐标+地区(复用 fallback_amap_search)。"""
    from fastapi import HTTPException

    from tools.match_pois import fallback_amap_search

    query = (body.get("query") or "").strip()
    city = body.get("city") or "深圳"
    if not query:
        raise HTTPException(400, "query required")
    poi = fallback_amap_search(query, city)
    if not poi:
        raise HTTPException(404, f"no match for '{query}' in {city}")
    return poi


@app.get("/plan/demo")
def plan_demo():
    """返回深圳五一示范数据(含 XHS 封面图 + 静态天气),供 /plan/view 前端渲染。"""
    global _DEMO_ENRICHED
    if _DEMO_ENRICHED is not None:
        return _DEMO_ENRICHED
    import json as _json

    demo_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "demo",
        "shenzhen_may_day.json",
    )
    with open(demo_path, encoding="utf-8") as f:
        raw = _json.load(f)
    _DEMO_ENRICHED = _enrich_demo(raw)
    return _DEMO_ENRICHED


# 挂载 web/ 目录（/static 前缀），供 index.html 引用额外静态资源时使用
if os.path.isdir(_web_dir):
    app.mount("/static", StaticFiles(directory=_web_dir), name="static")
