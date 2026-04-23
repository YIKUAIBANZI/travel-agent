"""
Step 4 · Architect 反思循环(Self-Refine 范式)

流程:
    聚类数据(Step 3 输出)
         ↓
    propose  ──→ 初稿行程 JSON
         ↓
    critique ──→ issues 列表(规则 + LLM 联合检查)
         ↓
    revise   ──→ 修正行程
         ↓(如还有严重问题)
    critique ──→ revise   [最多 2 轮]
         ↓
    最终行程

LLM:qwen-plus(通过 config.LLM_* 变量,OpenAI 兼容接口)

叙事亮点(面试/博客可讲):
  - 反思循环(Reflection/Self-Refine),来自 Shinn et al. Reflexion 2023 / Madaan et al. Self-Refine 2023
  - critique 双层:硬规则(暂停营业/超预算)先抓,LLM 补抓路线/节奏
  - 收敛条件:issues 清空 或 达到 max_rounds

验收:
  - 初稿能生成合法 JSON
  - critique 能识别"暂停营业"的点并标记
  - revise 后该点从行程中消失或被标注
"""

import json
import logging
import sys
from pathlib import Path

# 允许作为脚本运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

import config  # noqa: E402

logger = logging.getLogger(__name__)

_llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
_MODEL = config.LLM_MODEL or "qwen-plus"


# ══════════════════════════════════════════════════════════════════════════
# 硬规则 critique(不花 token,先跑)
# ══════════════════════════════════════════════════════════════════════════


def _is_in_allowlist(name: str, allowed: set[str]) -> bool:
    """宽松比对:name 在白名单 或 name 包含白名单项 或 白名单项包含 name 的主体部分。"""
    if not name:
        return False
    n = name.strip()
    if n in allowed:
        return True
    # 去括号后缀再比
    core = n.split("(")[0].split("·")[0].split("（")[0].strip()
    if core and core in allowed:
        return True
    for a in allowed:
        if not a:
            continue
        if a in n or (len(core) >= 3 and core in a):
            return True
    return False


def rule_based_critique(
    itinerary: dict, request: dict, allowed_names: set[str]
) -> list[dict]:
    """规则层 critique:能用代码判断的硬问题不交给 LLM。

    allowed_names:白名单,LLM 生成的所有 name 必须能对上,否则判为 fabricated。
    """
    issues = []

    # 扁平化所有行程点的 name
    all_blocks = []
    for day in itinerary.get("days", []):
        for slot in ("morning", "lunch", "afternoon", "dinner", "evening"):
            v = day.get(slot)
            if isinstance(v, list):
                all_blocks.extend([(day.get("day"), slot, b) for b in v])
            elif isinstance(v, dict) and v:
                all_blocks.append((day.get("day"), slot, v))

    # R1. 标注"暂停营业"的点不能出现
    for day_i, slot, b in all_blocks:
        name = b.get("name", "")
        if "暂停营业" in name or "停业" in name:
            issues.append(
                {
                    "severity": "critical",
                    "rule": "closed_venue",
                    "day": day_i,
                    "slot": slot,
                    "detail": f"第{day_i}天 {slot} 的 '{name}' 已标注暂停营业,必须替换或移除",
                }
            )

    # R1.5 虚构检查(核心新增):每个 name 都必须在白名单内
    for day_i, slot, b in all_blocks:
        name = b.get("name", "")
        if not name:
            continue
        if not _is_in_allowlist(name, allowed_names):
            issues.append(
                {
                    "severity": "critical",
                    "rule": "fabricated_name",
                    "day": day_i,
                    "slot": slot,
                    "detail": (
                        f"第{day_i}天 {slot} 的 '{name}' 不在白名单,涉嫌虚构。"
                        f"必须替换为白名单内的已匹配 POI,否则用户去不了/地图标不出"
                    ),
                }
            )

    # R2. 一天主活动不能超过 4 个(Anchor & Orbit)
    for day in itinerary.get("days", []):
        total = 0
        for slot in ("morning", "afternoon", "evening"):
            v = day.get(slot)
            if isinstance(v, list):
                total += len(v)
        if total > 4:
            issues.append(
                {
                    "severity": "high",
                    "rule": "too_many_activities",
                    "day": day.get("day"),
                    "detail": f"第{day.get('day')}天主活动 {total} 个,超过上限 4 个",
                }
            )

    # R3. must_visit 覆盖检查
    must_visit = request.get("must_visit", [])
    itin_names_blob = json.dumps(itinerary, ensure_ascii=False)
    missing = []
    for name in must_visit:
        # 取关键词(去店名后缀、地铁方位)
        key = name.replace("(", "").replace(")", "").strip()
        # 至少取前 3 字符
        probe = key[-4:] if "出口" in key or "口" in key else key[:4]
        if probe and probe not in itin_names_blob:
            missing.append(name)
    if missing:
        issues.append(
            {
                "severity": "high" if len(missing) > 3 else "medium",
                "rule": "missing_must_visit",
                "detail": f"用户必去清单有 {len(missing)} 项未出现在行程中:{missing}",
            }
        )

    return issues


# ══════════════════════════════════════════════════════════════════════════
# LLM 调用层
# ══════════════════════════════════════════════════════════════════════════


class LLMCallError(RuntimeError):
    """LLM 调用失败(超时 / 非 JSON / API 错误),用户层捕获转 5xx。"""


def _chat(
    system: str, user: str, temperature: float = 0.3, timeout: float = 60.0
) -> str:
    """单次 LLM 调用。带超时 + 1 次指数退避 retry + 错误边界。"""
    import time as _time

    last_err: Exception | None = None
    for attempt in range(2):  # 最多 2 次(1 次原始 + 1 次退避)
        try:
            resp = _llm.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
                timeout=timeout,
                # Qwen3 系列默认开 thinking,长 prompt 下单次 60s+ 超时。
                # 规划任务不需要长链思考,关掉后速度提升 5x。
                extra_body={"enable_thinking": False},
            )
            content = resp.choices[0].message.content
            if not content:
                raise LLMCallError("LLM returned empty content")
            # 快速校验返回的是合法 JSON 字符串
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                raise LLMCallError(f"LLM response is not valid JSON: {e}") from e
            return content
        except LLMCallError:
            raise
        except Exception as e:  # openai.APITimeoutError / RateLimitError / 网络错
            last_err = e
            if attempt == 0:
                backoff = 2.0 + attempt * 2
                logger.warning(
                    f"[llm] 调用失败 attempt={attempt + 1}: {e},{backoff}s 后重试"
                )
                _time.sleep(backoff)
                continue
    raise LLMCallError(f"LLM 调用失败(已重试): {last_err}") from last_err


# ══════════════════════════════════════════════════════════════════════════
# 三个核心 prompt
# ══════════════════════════════════════════════════════════════════════════


_PROPOSE_SYSTEM = """你是资深旅行规划师。根据【已聚类】的每日 POI 清单,编排成一份"一天一片区、不折返"的行程。

# 绝对铁律(违反即作废)
**你只能使用 allowed_names 列表中的精确名称作为 name 字段**。
- 不得虚构任何不在列表里的店名/景点/装置/餐厅
- 不得给已有名字加后缀(如 "XX·旗舰店"、"XX·顶楼观景台") —— 用户的地图要能一一标出
- 所有 morning/afternoon/evening/lunch/dinner 的 name 字段必须是 allowed_names 里的一个
- 如果你很想推荐某个不在列表里的地方,跳过它,不要编造
- activity / highlight / tips 字段里可以写体验细节,但 name 必须是精确匹配的白名单名

# 其他硬约束
1. 同一天的点已由算法按地理聚类好,不要跨天搬动(除非是机动日)
2. 热门景点(世界之窗/欢乐海岸等)已避开节假日高峰,请保留这个安排
3. 每天活动 11:00 开始(用户 10 点起床),不超过 22:00
4. 一天主活动不超过 4 个(Anchor & Orbit 模型:1 锚点 + 2-3 卫星 + 留白)
5. 两餐之间间隔 ≥ 3 小时,不一顿接一顿
6. 餐厅可以作为 lunch/dinner 槽位,小吃/咖啡作为下午茶
7. 标注"暂停营业"的店绝对不排进去
8. 每天预估走路 ≤ 10000 步(≈ 7km),同片区内走路,跨片区打车

# 节奏
- Day 1 (到达日):轻启动,最多 2-3 个活动
- 中间几天:按聚类结果
- 最后一天:收尾,午前结束主要活动
- **机动日(is_free_day=true):必须规划好! 从相邻天挪 2-3 个 optional_recommendations(标记 optional:true),或从 unvisited_must 列表里补。不能留空。用户可选执行,但你必须给出建议**

# 输出 JSON 格式
{
  "summary": "一句话概括整个行程基调",
  "total_budget_cny": 5000,
  "days": [
    {
      "day": 1,
      "date": "2026-04-29",
      "theme": "8 字内标题",
      "district": "主要片区",
      "rhythm": "缓启动/满日/轻松/自由",
      "morning":  [{"time":"11:30","name":"xxx","activity":"具体做什么","transport":"打车 15 分钟","cost":"免费"}],
      "lunch":     {"time":"13:00","name":"xxx","dishes":["招牌菜"],"per_person":"80"},
      "afternoon":[{"time":"15:00","name":"xxx","activity":"...","transport":"步行","cost":"0"}],
      "dinner":    {"time":"19:00","name":"xxx","dishes":["..."],"per_person":"100"},
      "evening":  [{"time":"21:00","name":"xxx","activity":"...","transport":"...","cost":"0"}],
      "est_steps": 8000,
      "est_cost_cny": 400,
      "tips": "当天小贴士"
    }
  ]
}

只输出 JSON,不要其他文字。
"""


_CRITIQUE_SYSTEM = """你是旅行行程的审查员。读用户约束 + 行程初稿 + 硬规则已发现的问题,补充发现 LLM 才能判断的软问题。

重点看:
1. 跨景点交通是否合理(同片区不应打车,跨片区不应步行)
2. 节奏是否合理(一天太满/太空、两餐间距、晚归过晚)
3. Day 1 到达日/Day N 离开日是否轻量
4. 机动日(is_free_day)是否合理利用
5. 单点餐厅日均 3 次以上是否吃不消
6. 是否有明显不适合"情侣类型 A"的安排

不要重复硬规则已列出的问题。

输出 JSON:
{
  "issues": [
    {"severity": "critical|high|medium|low", "day": 3, "detail": "..."}
  ],
  "verdict": "pass | needs_revision"
}

如果没有软问题,issues 空列表,verdict=pass。
只输出 JSON。
"""


_REVISE_SYSTEM = """你是旅行行程修订员。读原行程 + issues 列表 + allowed_names 白名单,输出修正后的完整行程。

# 铁律
**所有 name 字段必须是 allowed_names 里的精确名称**。被标 fabricated_name 的点必须替换为白名单项,不得保留/改写/加后缀。

# 其他规则
- 保留无问题的部分不动
- 被标 closed_venue 的点:从行程中移除,替换成白名单里同片区的其他项或留空
- 被标 fabricated_name:必须替换为 allowed_names 中的项;如果找不到合适的替换,删除该 block(宁可空不要虚构)
- 被标 too_many_activities 的天:挪 1-2 个活动到 is_free_day(机动日)
- 被标 missing_must_visit 的:安排到 is_free_day 或晚上时段
- 其他软问题按描述改

输出格式与原行程完全一致(JSON),不要额外解释。只输出 JSON。
"""


# ══════════════════════════════════════════════════════════════════════════
# 主入口:反思循环
# ══════════════════════════════════════════════════════════════════════════


def _compact_days_for_prompt(days_plan: list[dict]) -> list[dict]:
    """压缩 Step 3 的输出,只留 LLM 需要的字段(节省 token)。"""
    compact = []
    for d in days_plan:
        pois_brief = []
        for m in d["pois"]:
            p = m.get("poi") or {}
            pois_brief.append(
                {
                    "name": p.get("name", ""),
                    "district": p.get("district", ""),
                    "type": p.get("type", ""),
                    "lat": p.get("lat"),
                    "lng": p.get("lng"),
                    "reason": p.get("reason", ""),
                    "closed": ("暂停营业" in p.get("name", ""))
                    or ("停业" in p.get("name", "")),
                }
            )
        compact.append(
            {
                "day": d["day"],
                "date": d["date"],
                "is_risk_day": d.get("is_risk_day", False),
                "is_free_day": d.get("is_free_day", False),
                "pois": pois_brief,
            }
        )
    return compact


def build_allowed_names(days_plan: list[dict], must_visit: list[str]) -> list[str]:
    """从已聚类 POI + 必去清单构造白名单(LLM 只能从这里面选 name)。"""
    names = set()
    for d in days_plan:
        for m in d.get("pois", []):
            nm = (m.get("poi") or {}).get("name")
            if nm:
                names.add(nm)
    for n in must_visit:
        if n:
            names.add(n)
    return sorted(names)


def propose(request: dict, days_plan: list[dict], allowed_names: list[str]) -> dict:
    """第一步:基于聚类数据编排初稿。"""
    # 找出 must_visit 里在白名单但还没被分到任何天的项(供机动日补位)
    assigned = set()
    for d in days_plan:
        for m in d.get("pois", []):
            nm = (m.get("poi") or {}).get("name", "")
            if nm:
                assigned.add(nm)
    unvisited_must = [q for q in request.get("must_visit", []) if q not in assigned]

    user_msg = json.dumps(
        {
            "request": {
                "city": request["city"],
                "dates": request["dates"],
                "party": request["party"],
                "budget": request["budget"],
                "constraints": request["constraints"],
            },
            "allowed_names": allowed_names,
            "unvisited_must": unvisited_must,
            "days_with_pois": _compact_days_for_prompt(days_plan),
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = _chat(_PROPOSE_SYSTEM, user_msg, temperature=0.4)
    return json.loads(raw)


def critique(itinerary: dict, request: dict, rule_issues: list[dict]) -> dict:
    """第二步:软问题 critique(硬规则已先跑)。"""
    user_msg = json.dumps(
        {
            "constraints": request["constraints"],
            "party": request["party"],
            "rule_issues_already_found": rule_issues,
            "itinerary": itinerary,
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = _chat(_CRITIQUE_SYSTEM, user_msg, temperature=0.2)
    return json.loads(raw)


def revise(itinerary: dict, all_issues: list[dict], allowed_names: list[str]) -> dict:
    """第三步:根据 issues 修订。"""
    user_msg = json.dumps(
        {
            "issues": all_issues,
            "allowed_names": allowed_names,
            "itinerary_to_fix": itinerary,
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = _chat(_REVISE_SYSTEM, user_msg, temperature=0.3)
    return json.loads(raw)


def reflect_and_plan(request: dict, days_plan: list[dict], max_rounds: int = 2) -> dict:
    """主入口:propose → [critique → revise] × max_rounds。"""
    trace = []
    allowed_names = build_allowed_names(days_plan, request.get("must_visit", []))
    allowed_set = set(allowed_names)
    logger.info(f"📋 allowed_names 白名单:{len(allowed_names)} 项")

    logger.info("📝 [propose] 生成初稿...")
    itinerary = propose(request, days_plan, allowed_names)
    trace.append({"step": "propose", "itinerary": itinerary})

    for r in range(max_rounds):
        logger.info(f"🔍 [critique] 第 {r + 1} 轮审查...")
        rule_issues = rule_based_critique(itinerary, request, allowed_set)
        logger.info(f"   规则层:{len(rule_issues)} 个硬问题")
        for iss in rule_issues:
            logger.info(f"     · [{iss['severity']}] {iss.get('detail', '')}")

        llm_critique = critique(itinerary, request, rule_issues)
        llm_issues = llm_critique.get("issues", [])
        logger.info(
            f"   LLM层:{len(llm_issues)} 个软问题 · verdict={llm_critique.get('verdict')}"
        )
        for iss in llm_issues:
            logger.info(
                f"     · [{iss.get('severity')}] day {iss.get('day', '?')}: {iss.get('detail', '')}"
            )

        all_issues = rule_issues + llm_issues
        trace.append({"step": f"critique_r{r + 1}", "issues": all_issues})

        # 收敛:没有 critical/high 问题就停
        blocking = [i for i in all_issues if i.get("severity") in ("critical", "high")]
        if not blocking:
            logger.info("✅ 无阻塞问题,停止反思。")
            break

        logger.info(
            f"🔧 [revise] 修订(处理 {len(blocking)} 个阻塞问题 + {len(all_issues) - len(blocking)} 个次要问题)..."
        )
        itinerary = revise(itinerary, all_issues, allowed_names)
        trace.append({"step": f"revise_r{r + 1}", "itinerary": itinerary})

    return {
        "itinerary": itinerary,
        "trace": trace,
    }


# ══════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    from datetime import date, timedelta

    from examples.shenzhen_may_day import REQUEST
    from tools.cluster_pois import cluster_and_assign
    from tools.match_pois import match_must_visit

    # 组装上游数据
    matched = match_must_visit(REQUEST["must_visit"], city=REQUEST["city"])["matched"]
    start = date.fromisoformat(REQUEST["dates"]["start"])
    end = date.fromisoformat(REQUEST["dates"]["end"])
    dates = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]
    days_plan = cluster_and_assign(
        matched=matched,
        dates=dates,
        risk_dates=REQUEST["holiday_crowd_risk_dates"],
    )

    print("=" * 60)
    print(f"  Architect 反思循环 · {REQUEST['city']} · {len(dates)} 天")
    print("=" * 60)
    result = reflect_and_plan(REQUEST, days_plan, max_rounds=2)

    print("\n" + "=" * 60)
    print("  最终行程")
    print("=" * 60)
    print(json.dumps(result["itinerary"], ensure_ascii=False, indent=2))
