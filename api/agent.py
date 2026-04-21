"""
旅游规划 Agent — 对话状态机核心逻辑（v2: 类型化 Prompt + 合并 LLM 调用）

状态转移:
  INIT
    → Profiler: 合并提取城市/天数 + 识别旅行者类型(A-F)
    → 转 COLLECT_PREFERENCES

  COLLECT_PREFERENCES
    → 类型化追问（1-2 轮），Collector 自带 [READY] 判断
    → 偏好收集完毕后，调 pipeline 获取候选景点
    → 转 SELECT_SPOTS

  SELECT_SPOTS
    → 等待用户从候选列表中选择景点
    → 转 GENERATING_ITINERARY

  GENERATING_ITINERARY
    → 并行: 美食匹配 + 天气 API + Exa 搜索
    → Architect: 编排行程 (Anchor & Orbit + 节奏韵律)
    → 转 DONE

  DONE
    → Follow-up Handler 处理追问
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

import config
from api.models import (
    ChatResponse,
    DayItinerary,
    Itinerary,
    Place,
    SessionState,
)
from api.prompts import (
    FOLLOWUP_SYSTEM,
    PROFILER_SYSTEM,
    build_architect_system,
    build_collector_system,
)
from api.session import Session
from api.weather import format_weather_summary, get_weather_forecast
from pipeline.mapper import generate_static_map_url, generate_web_map_url
from pipeline.web_search import (
    search_food_guides,
    search_hotel_guides,
    search_travel_guides,
)

logger = logging.getLogger(__name__)

# ── LLM 客户端（全局复用）──────────────────────────────────────────────────
_llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
_model = config.LLM_MODEL


def _chat(
    messages: list[dict], temperature: float = 0.7, max_tokens: int = 2000
) -> str:
    resp = _llm.chat.completions.create(
        model=_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content
    return (content or "").strip()


def _parse_json_from_llm(text: str):
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── 阶段处理函数 ─────────────────��─────────────────────────────────────────


def handle_init(session: Session, user_message: str) -> ChatResponse:
    """
    INIT: Profiler 合并提取城市信息 + 识别旅行者类型
    """
    session.add_message("user", user_message)

    try:
        raw = _chat(
            [
                {"role": "system", "content": PROFILER_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        info = _parse_json_from_llm(raw)
        city = info.get("city", "").strip()
        if not city:
            raise ValueError("未能识别城市")

        session.city = city
        session.days = int(info.get("days", 3))
        session.people = str(info.get("people", "2人"))
        session.transport = str(info.get("transport", "公共交通"))
        session.traveler_type = str(info.get("traveler_type", "A"))
        session.traveler_type_confidence = float(info.get("type_confidence", 0.3))
        session.traveler_type_secondary = info.get("type_secondary")

        logger.info(
            f"Profiler: city={city}, days={session.days}, "
            f"type={session.traveler_type}({session.traveler_type_confidence:.1f})"
        )

    except Exception as e:
        logger.warning(f"Profiler 解析失败: {e}")
        reply = "请告诉我你想去哪个城市，计划几天，几个人，大概用什么方式出行？"
        session.add_message("assistant", reply)
        return ChatResponse(
            session_id=session.session_id, state=session.state, message=reply
        )

    # 进入偏好收集
    session.state = SessionState.COLLECT_PREFERENCES
    pref_question = _ask_preferences(session)
    session.add_message("assistant", pref_question)

    return ChatResponse(
        session_id=session.session_id, state=session.state, message=pref_question
    )


def _ask_preferences(session: Session) -> str:
    """类型化偏好追问"""
    system = build_collector_system(session.traveler_type)
    prompt = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"目的地：{session.city}，{session.days}天，{session.travel_info}。"
                "请问我几个问题了解我的偏好。"
            ),
        },
    ]
    reply = _chat(prompt, temperature=0.8, max_tokens=200)
    # 去掉 [READY] 标记（第一轮不可能 ready）
    return reply.replace("[READY]", "").strip()


def handle_collect_preferences(session: Session, user_message: str) -> ChatResponse:
    """
    COLLECT_PREFERENCES: 类型化偏好收集
    Collector 自带 [READY] 判断，替代独立的 judge_enough LLM 调用
    """
    session.add_message("user", user_message)
    session.pref_rounds += 1

    if session.preferences:
        session.preferences += f"；{user_message}"
    else:
        session.preferences = user_message

    # 强制上限：2 轮后直接进入景点获取
    if session.pref_rounds >= 2:
        return _fetch_spots_and_respond(session)

    # 让 Collector 追问并自带判断
    system = build_collector_system(session.traveler_type)
    followup_prompt = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"目的地：{session.city}，��知偏好：{session.preferences}，"
                "请继续追问或判断是否足够。"
            ),
        },
    ]
    reply = _chat(followup_prompt, temperature=0.8, max_tokens=200)

    # 检查 [READY] 标记
    if "[READY]" in reply:
        reply_clean = reply.replace("[READY]", "").strip()
        if reply_clean:
            session.add_message("assistant", reply_clean)
        return _fetch_spots_and_respond(session)

    session.add_message("assistant", reply)
    return ChatResponse(
        session_id=session.session_id, state=session.state, message=reply
    )


def _fetch_spots_and_respond(session: Session) -> ChatResponse:
    """
    调用 pipeline 获取候选景点 → 类型化排序 → 转入 SELECT_SPOTS
    并行获取: Exa 搜索 + 清洗数据加载
    """
    import glob

    # ── 加载清洗��据 ──
    cleaned_dir = config.CLEANED_DIR
    pattern = os.path.join(cleaned_dir, f"{session.city}*.json")
    files = sorted(glob.glob(pattern), reverse=True)

    if files:
        with open(files[0], encoding="utf-8") as f:
            places_raw = json.load(f)
        logger.info(f"命中清洗缓存: {files[0]}，共 {len(places_raw)} 条")
    else:
        logger.info(f"无清��缓存，运行 Cleaner for {session.city}")
        try:
            from pipeline.cleaner import Cleaner

            cleaner = Cleaner()
            places_raw = cleaner.clean(city=session.city)
        except Exception as e:
            logger.error(f"Cleaner 失败: {e}")
            places_raw = []

    if not places_raw:
        reply = (
            f"抱歉，暂时没有找到「{session.city}」的景点数据。"
            "请换一个城市试试，或者告诉我你对哪些地方感兴趣，我来帮你规划。"
        )
        session.add_message("assistant", reply)
        return ChatResponse(
            session_id=session.session_id, state=session.state, message=reply
        )

    # ── 并行: Exa 网络攻略 ──
    web_guides: list[str] = []
    try:
        web_guides = search_travel_guides(
            city=session.city,
            preferences=session.preferences or "",
            num_results=5,
        )
    except Exception as e:
        logger.warning(f"Exa 搜索异常: {e}")
    session.web_guides = web_guides

    # ── 类型化 LLM 排序 ──
    enriched_preferences = session.preferences or "综合体验"
    if web_guides:
        guide_hints = "\n".join(g[:200] for g in web_guides[:2])
        enriched_preferences = (
            f"{enriched_preferences}\n\n[网络攻略参考]\n{guide_hints}"
        )

    try:
        from pipeline.ranker import Ranker

        ranker = Ranker()
        ranked = ranker.rank(
            city=session.city,
            preferences=enriched_preferences,
            days=session.days,
            travel_info=session.travel_info,
            traveler_type=session.traveler_type,
        )
    except Exception as e:
        logger.warning(f"Ranker 失败，使用原始顺序: {e}")
        ranked = places_raw

    # 取前 20 个景点展示（过滤餐饮，景点优先）
    spots_to_show = [p for p in ranked if p.get("type") != "餐饮"][:20]
    if len(spots_to_show) < 5:
        spots_to_show = ranked[:20]

    session.candidate_spots = spots_to_show
    session.state = SessionState.SELECT_SPOTS

    # 生成展示文字
    lines = [
        f"好的！根据你的偏好，我为你筛选了 {session.city} 的推荐景点，"
        f"共 {len(spots_to_show)} 个：",
        "",
    ]
    for i, p in enumerate(spots_to_show):
        reason = f" — {p.get('reason', '')[:30]}" if p.get("reason") else ""
        rating = f" ⭐{p['rating']}" if p.get("rating") else ""
        warning = f" ⚠️{p.get('warnings', '')[:20]}" if p.get("warnings") else ""
        best_time = f" 🕐{p.get('best_time', '')[:15]}" if p.get("best_time") else ""
        ptype = p.get("type", "")
        lines.append(
            f"{i + 1}. 【{ptype}】**{p['name']}**{rating}{reason}{warning}{best_time}"
        )

    lines.append("")
    suggest_count = session.days * 3
    lines.append(
        f'你可以直接说序号（如"1 3 5"）或景点名称，'
        f'也可以说"全选"。建议选 {suggest_count} 个左右。'
    )

    reply = "\n".join(lines)
    session.add_message("assistant", reply)

    spots_models = [
        Place(**{k: v for k, v in p.items() if k in Place.model_fields})
        for p in spots_to_show
    ]

    return ChatResponse(
        session_id=session.session_id,
        state=session.state,
        message=reply,
        spots=spots_models,
    )


def handle_select_spots(session: Session, user_message: str) -> ChatResponse:
    """
    SELECT_SPOTS: 解析用户选择的景点
    """
    session.add_message("user", user_message)
    candidates = session.candidate_spots
    msg_lower = user_message.strip()

    # "全选"
    if "全选" in msg_lower or "都要" in msg_lower or "全部" in msg_lower:
        selected = candidates
    else:
        selected = []

        # 尝试提取数字序号
        numbers = re.findall(r"\d+", user_message)
        if numbers:
            for n in numbers:
                idx = int(n) - 1
                if 0 <= idx < len(candidates):
                    selected.append(candidates[idx])

        # 尝试匹配景点名
        if not selected:
            for p in candidates:
                if p["name"] in user_message:
                    selected.append(p)

        # LLM 兜底解析
        if not selected:
            parse_prompt = [
                {
                    "role": "system",
                    "content": (
                        "用户在选择旅游景点。给定候选列表和用户回复，"
                        "返回��户选择的景点名称列表（JSON 数组）。"
                        "如果用户表达不明确，返回空数组 []。只返回 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"候选景点：{[p['name'] for p in candidates]}\n"
                        f"用户回复：{user_message}"
                    ),
                },
            ]
            try:
                raw = _chat(parse_prompt, temperature=0.1, max_tokens=200)
                names = _parse_json_from_llm(raw)
                if isinstance(names, list):
                    for name in names:
                        for p in candidates:
                            if p["name"] == name or name in p["name"]:
                                if p not in selected:
                                    selected.append(p)
            except Exception:
                pass

    if not selected:
        reply = "没有识别到你的选择��请用序号（如 1 3 5）或景点名告诉我你想去哪些地方。"
        session.add_message("assistant", reply)
        return ChatResponse(
            session_id=session.session_id, state=session.state, message=reply
        )

    session.selected_spots = selected
    session.state = SessionState.GENERATING_ITINERARY

    names_str = "、".join(p["name"] for p in selected)
    progress_msg = (
        f"已选定 {len(selected)} 个景点：{names_str}。\n"
        "正在匹配周边美食、查询天气并编排行程，稍等片刻..."
    )
    session.add_message("assistant", progress_msg)

    return _generate_itinerary(session)


def _generate_itinerary(session: Session) -> ChatResponse:
    """
    GENERATING_ITINERARY → DONE
    并行: 美食匹配 + 天气
    然后 Architect LLM 编排
    """
    spots = session.selected_spots

    # ── 并行获取: 美食匹配 + 天气 + 住宿Exa + 美食Exa ──

    def _match_food():
        try:
            from pipeline.food_matcher import FoodMatcher

            matcher = FoodMatcher()
            return matcher.match(spots, top_n=len(spots), food_per_place=3, radius=1000)
        except Exception as e:
            logger.warning(f"美食匹配失败: {e}")
            return spots

    def _get_weather():
        try:
            return get_weather_forecast(session.city, days=min(session.days, 3))
        except Exception as e:
            logger.warning(f"天气获取失败: {e}")
            return []

    def _search_hotels():
        try:
            return search_hotel_guides(
                city=session.city,
                traveler_type=session.traveler_type,
                num_results=3,
            )
        except Exception as e:
            logger.warning(f"住宿Exa搜索失败: {e}")
            return []

    def _search_food():
        try:
            return search_food_guides(
                city=session.city,
                preferences=session.preferences or "",
                num_results=3,
            )
        except Exception as e:
            logger.warning(f"美食Exa搜索失败: {e}")
            return []

    with ThreadPoolExecutor(max_workers=4) as executor:
        food_future = executor.submit(_match_food)
        weather_future = executor.submit(_get_weather)
        hotel_future = executor.submit(_search_hotels)
        food_exa_future = executor.submit(_search_food)
        spots = food_future.result()
        weathers = weather_future.result()
        hotel_guides = hotel_future.result()
        food_exa_guides = food_exa_future.result()

    logger.info(
        f"并行获取完成: 美食POI={len([s for s in spots if s.get('nearby_food')])}个, "
        f"天气={len(weathers)}天, 住宿攻略={len(hotel_guides)}条, 美食攻略={len(food_exa_guides)}条"
    )

    weather_summary = format_weather_summary(weathers)

    # ── Architect LLM 编排 ──
    itinerary_data = _llm_arrange_itinerary(
        session, spots, weather_summary, hotel_guides, food_exa_guides
    )

    # ── 生成地图链接 ──
    spots_with_coords = [p for p in spots if p.get("lat") and p.get("lng")]
    map_url = generate_web_map_url(spots_with_coords) if spots_with_coords else None
    static_map_url = (
        generate_static_map_url(spots_with_coords) if spots_with_coords else None
    )

    # ── 组装 Itinerary model ──
    days_plan = []
    for day_data in itinerary_data.get("days", []):
        day_num = day_data.get("day", 1)
        weather_for_day = weathers[day_num - 1] if day_num - 1 < len(weathers) else None
        days_plan.append(
            DayItinerary(
                day=day_num,
                date=day_data.get("date"),
                theme=day_data.get("theme"),
                rhythm=day_data.get("rhythm"),
                district=day_data.get("district"),
                weather=weather_for_day,
                morning=day_data.get("morning", []),
                lunch=day_data.get("lunch"),
                afternoon=day_data.get("afternoon", []),
                dinner=day_data.get("dinner"),
                evening=day_data.get("evening", []),
                tips=day_data.get("tips"),
                alternatives=day_data.get("alternatives", []),
            )
        )

    itinerary = Itinerary(
        city=session.city,
        days=session.days,
        days_plan=days_plan,
        map_url=map_url,
        static_map_url=static_map_url,
        summary=itinerary_data.get("summary"),
        daily_budget_estimate=str(itinerary_data.get("daily_budget_estimate", ""))
        or None,
        accommodation=itinerary_data.get("accommodation", []),
        food_guide=itinerary_data.get("food_guide", []),
        packing_tips=itinerary_data.get("packing_tips", []),
        booking_reminders=itinerary_data.get("booking_reminders", []),
    )

    session.state = SessionState.DONE

    reply = _format_itinerary_text(itinerary, session)
    session.add_message("assistant", reply)

    return ChatResponse(
        session_id=session.session_id,
        state=session.state,
        message=reply,
        itinerary=itinerary,
    )


def _llm_arrange_itinerary(
    session: Session,
    spots: list[dict],
    weather_summary: str,
    hotel_guides: list[str] = None,
    food_exa_guides: list[str] = None,
) -> dict:
    """Architect Prompt 编排行程（含住宿+美食推荐）"""
    spots_info = []
    for p in spots:
        food_names = [f["name"] for f in p.get("nearby_food", [])]
        spots_info.append(
            {
                "name": p.get("name"),
                "type": p.get("type"),
                "district": p.get("district", ""),
                "reason": p.get("reason", ""),
                "warnings": p.get("warnings", ""),
                "best_time": p.get("best_time", ""),
                "opentime": p.get("opentime", ""),
                "rating": p.get("rating"),
                "nearby_food": food_names,
            }
        )

    system_prompt = build_architect_system(session.traveler_type)

    # Exa 网络攻略
    web_guide_section = ""
    if getattr(session, "web_guides", None):
        guide_texts = "\n---\n".join(g[:400] for g in session.web_guides[:3])
        web_guide_section = f"\n\n[网络攻略参考]：\n{guide_texts}\n"

    # 住宿攻略参考
    hotel_section = ""
    if hotel_guides:
        hotel_texts = "\n---\n".join(g[:300] for g in hotel_guides[:3])
        hotel_section = (
            f"\n\n[住宿攻略参考（请据此推荐 accommodation）]：\n{hotel_texts}\n"
        )

    # 美食攻略参考
    food_section = ""
    if food_exa_guides:
        food_texts = "\n---\n".join(g[:300] for g in food_exa_guides[:3])
        food_section = f"\n\n[美食攻略参考（请据此推荐 food_guide + lunch/dinner）]：\n{food_texts}\n"

    user_msg = (
        f"城市：{session.city}\n"
        f"天数：{session.days}天\n"
        f"出行方式：{session.travel_info}\n"
        f"偏好：{session.preferences or '综合体验'}\n"
        f"天气预报：{weather_summary}\n\n"
        f"选定景点（共{len(spots_info)}个）：\n"
        f"{json.dumps(spots_info, ensure_ascii=False, indent=2)}"
        f"{web_guide_section}"
        f"{hotel_section}"
        f"{food_section}\n\n"
        "请编排详细行程（包含住宿推荐和美食清单）："
    )

    try:
        raw = _chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.5,
            max_tokens=4000,
        )
        return _parse_json_from_llm(raw)
    except Exception as e:
        logger.error(f"Architect 编排失败: {e}")
        return _fallback_arrange(session, spots)


def _fallback_arrange(session: Session, spots: list[dict]) -> dict:
    """LLM 失败时的降级行程"""
    days = []
    per_day = max(1, len(spots) // session.days)
    for d in range(1, session.days + 1):
        day_spots = spots[(d - 1) * per_day : d * per_day]
        morning = [
            {
                "time": "09:00",
                "name": p["name"],
                "duration": "2小时",
                "activity": p.get("reason", "游览"),
                "warning": p.get("warnings", ""),
                "transport": session.transport,
            }
            for p in day_spots[:2]
        ]
        afternoon = [
            {
                "time": "14:00",
                "name": p["name"],
                "duration": "2小时",
                "activity": p.get("reason", "游览"),
                "warning": p.get("warnings", ""),
                "transport": session.transport,
            }
            for p in day_spots[2:4]
        ]
        evening = []
        if day_spots:
            foods = day_spots[0].get("nearby_food", [])
            if foods:
                evening = [
                    {
                        "time": "18:30",
                        "name": foods[0]["name"],
                        "duration": "1.5小时",
                        "activity": "晚餐",
                        "warning": "",
                        "transport": "步行",
                    }
                ]
        days.append(
            {
                "day": d,
                "theme": f"第{d}天",
                "rhythm": "缓启动" if d == 1 else "满日",
                "tips": "",
                "morning": morning,
                "afternoon": afternoon,
                "evening": evening,
            }
        )
    return {"summary": f"{session.city}{session.days}天行程", "days": days}


def _format_itinerary_text(itinerary: Itinerary, session: Session) -> str:
    """将结构化行程转为 Markdown 文字"""
    lines = [f"# {session.city} {session.days}天行程安排", ""]

    if itinerary.summary:
        lines.append(f"> {itinerary.summary}")
        lines.append("")

    if itinerary.daily_budget_estimate:
        lines.append(f"💰 **预估日均花费**：{itinerary.daily_budget_estimate}")
        lines.append("")

    for day in itinerary.days_plan:
        theme_str = f" · {day.theme}" if day.theme else ""
        rhythm_str = f" [{day.rhythm}]" if day.rhythm else ""
        lines.append(f"## 第{day.day}天{theme_str}{rhythm_str}")

        if day.district:
            lines.append(f"📍 主要区域：{day.district}")

        if day.weather:
            w = day.weather
            temp = f" {w.temperature_night}~{w.temperature}°C" if w.temperature else ""
            lines.append(f"天气：{w.weather or ''}{temp}")

        if day.tips:
            lines.append(f"💡 {day.tips}")
        lines.append("")

        for period, label in [
            (day.morning, "上午"),
            (day.afternoon, "下午"),
            (day.evening, "晚上"),
        ]:
            if not period:
                continue
            lines.append(f"**{label}**")
            for item in period:
                time_str = item.get("time", "")
                name = item.get("name", "")
                duration = item.get("duration", "")
                activity = item.get("activity", "")
                highlight = item.get("highlight", "")
                warning = item.get("warning", "")
                transport = item.get("transport", "")
                cost = item.get("cost", "")

                line = f"- {time_str} **{name}**（{duration}）"
                if activity:
                    line += f"：{activity}"
                if highlight:
                    line += f" ✨ {highlight}"
                if cost:
                    line += f" 💰{cost}"
                if warning:
                    line += f" ⚠️ {warning}"
                if transport:
                    line += f" 🚇 {transport}"
                lines.append(line)
            lines.append("")

        # 午餐
        if day.lunch:
            lunch = day.lunch
            l_line = (
                f"🍜 **午餐** {lunch.get('time', '12:00')} **{lunch.get('name', '')}**"
            )
            if lunch.get("recommend_dishes"):
                l_line += f"（推荐：{'、'.join(lunch['recommend_dishes'])}）"
            if lunch.get("per_person"):
                l_line += f" 人均{lunch['per_person']}"
            lines.append(l_line)
            lines.append("")

        # 晚餐
        if day.dinner:
            dinner = day.dinner
            d_line = (
                f"🍽️ **晚餐** {dinner.get('time', '18:00')} **{dinner.get('name', '')}**"
            )
            if dinner.get("recommend_dishes"):
                d_line += f"（推荐：{'、'.join(dinner['recommend_dishes'])}）"
            if dinner.get("per_person"):
                d_line += f" 人均{dinner['per_person']}"
            lines.append(d_line)
            lines.append("")

        # 备选方案
        if day.alternatives:
            lines.append("**备选方案**")
            for alt in day.alternatives:
                lines.append(
                    f"- {alt.get('condition', '备选')}：**{alt.get('name', '')}** — {alt.get('reason', '')}"
                )
            lines.append("")

    # 住宿推荐
    if itinerary.accommodation:
        lines.append("---")
        lines.append("## 🏨 住宿推荐")
        lines.append("")
        for h in itinerary.accommodation:
            h_line = f"**{h.get('name', '')}**"
            if h.get("type"):
                h_line += f"（{h['type']}）"
            if h.get("price_range"):
                h_line += f" 💰{h['price_range']}"
            lines.append(f"- {h_line}")
            if h.get("area"):
                lines.append(f"  📍 {h['area']}")
            if h.get("why"):
                lines.append(f"  → {h['why']}")
            if h.get("tips"):
                lines.append(f"  💡 {h['tips']}")
        lines.append("")

    # 美食清单
    if itinerary.food_guide:
        lines.append("## 🍜 必吃美食清单")
        lines.append("")
        for f in itinerary.food_guide:
            f_line = f"**{f.get('name', '')}**"
            if f.get("type"):
                f_line += f"（{f['type']}）"
            if f.get("per_person"):
                f_line += f" 人均{f['per_person']}"
            lines.append(f"- {f_line}")
            if f.get("signature"):
                lines.append(f"  招牌：{'、'.join(f['signature'])}")
            if f.get("area"):
                lines.append(f"  📍 {f['area']}")
            if f.get("tips"):
                lines.append(f"  💡 {f['tips']}")
        lines.append("")

    # 打包建议
    if itinerary.packing_tips:
        lines.append("---")
        lines.append("🎒 **打包建议**：" + "、".join(itinerary.packing_tips))
        lines.append("")

    # 预约提醒
    if itinerary.booking_reminders:
        lines.append("📋 **需提前预约**：" + "、".join(itinerary.booking_reminders))
        lines.append("")

    if itinerary.map_url:
        lines.append(f"[📍 在高德地图查看全部景点]({itinerary.map_url})")

    return "\n".join(lines)


def handle_done(session: Session, user_message: str) -> ChatResponse:
    """DONE: Follow-up 处理追问"""
    session.add_message("user", user_message)

    system = FOLLOWUP_SYSTEM.format(city=session.city, days=session.days)
    context_msgs = [{"role": "system", "content": system}]
    context_msgs.extend(session.messages[-6:])

    try:
        reply = _chat(context_msgs, temperature=0.7, max_tokens=1000)
    except Exception as e:
        logger.error(f"追问处理失败: {e}")
        reply = "抱歉，处理你的问题时出了点问题，请重试。"

    session.add_message("assistant", reply)

    return ChatResponse(
        session_id=session.session_id, state=session.state, message=reply
    )


# ── 主入口 ───────────────���───────────────────────────────���─────────────────


def process_message(session: Session, user_message: str) -> ChatResponse:
    state = session.state

    if state == SessionState.INIT:
        return handle_init(session, user_message)
    elif state == SessionState.COLLECT_PREFERENCES:
        return handle_collect_preferences(session, user_message)
    elif state == SessionState.SELECT_SPOTS:
        return handle_select_spots(session, user_message)
    elif state == SessionState.GENERATING_ITINERARY:
        return handle_done(session, user_message)
    elif state == SessionState.DONE:
        return handle_done(session, user_message)
    else:
        return ChatResponse(
            session_id=session.session_id,
            state=state,
            message="未知状态，请刷新页面重新开始。",
        )
