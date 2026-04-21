"""
定制版行程规划 · Step 1:入参结构定义 + 打印

目标(最小可验证):
- 把女友这次五一场景 hardcode 成一个 Python dict
- 打印出来,确认字段齐全、结构合理
- 不调 LLM、不调任何工具,就是"这份入参长什么样"

后续 Step 会在这个结构上依次加:
  Step 2 → match_pois       把 must_visit 的每个名字匹配到已爬 POI(取坐标/评分)
  Step 3 → cluster_pois     按坐标 K-means 分簇,按天分配
  Step 4 → architect loop   LLM tool-use 循环:propose → critique → revise
  Step 5 → FastAPI 端点     POST /api/plan/custom
"""

import json


# ── 女友五一清单(用户给的 21 个点,原样保留)──────────────────────────────────
MUST_VISIT_RAW = [
    "深圳湾公园",
    "世界之窗",
    "欢乐海岸",
    "老南门铜锅涮肉",
    "南山荷兰花卉小镇",
    "沅芷小筑",
    "馥树",
    "深圳人才公园",
    "车公庙",
    "c出口丰盛町婶婶泡菜粉",
    "b出口东海缤纷天地",
    "干锅牛肉黔派",
    "寿司本色",
    "香蜜公园",
    "购物公园cocopark",
    "开吉茶馆",
    "梅果.云贵川",
    "太食獸",
    "番茄食堂",
    "鸟剑居酒屋(购物公园店)",
    "岗厦B口",
]


# ── 完整入参 ────────────────────────────────────────────────────────────────
REQUEST = {
    # 1. 基本信息
    "city": "深圳",
    "dates": {
        "start": "2026-04-29",
        "end": "2026-05-04",
        "days": 6,  # 4.29-5.4 共 6 天
    },
    # 2. 同行人
    "party": {
        "people": 2,
        "relation": "情侣",
        "traveler_type": "A",  # A=情侣/闺蜜打卡型
    },
    # 3. 预算(目标预算,可以超)
    "budget": {
        "target_cny": 4000,
        "hard_limit": False,  # 超预算不阻断,但要在输出里提醒用户
        "covers": ["住宿", "餐饮", "景点门票", "市内交通"],
        "excludes": ["往返机票"],
    },
    # 4. 必去清单(女友给的)
    "must_visit": MUST_VISIT_RAW,
    # 5. 约束
    "constraints": {
        "wake_up": "10:00",  # 10 点起床,行程从 11:00 后开始
        "max_daily_steps": 10000,  # 每天步数上限(≈ 7 km)
        "transport": ["subway", "taxi"],
        "walk_radius_m": 800,  # 地铁站/目的地 800m 内走路,否则打车
    },
    # 6. 住宿偏好
    "hotel": {
        "area_prefer": ["南山", "宝安中心"],
        "booked": False,  # 还没定,后续模块可给推荐
    },
    # 7. 节假日人流风险窗口(5.1-5.3 是深圳景点最爆的三天)
    "holiday_crowd_risk_dates": ["2026-05-01", "2026-05-02", "2026-05-03"],
}


def main() -> None:
    print("=" * 60)
    print("  定制版入参 · 深圳五一行程")
    print("=" * 60)
    print(json.dumps(REQUEST, ensure_ascii=False, indent=2))
    print()
    print(f"必去清单共 {len(REQUEST['must_visit'])} 项")
    print(f"行程天数 {REQUEST['dates']['days']} 天")
    print(
        f"预算目标 ¥{REQUEST['budget']['target_cny']} / {REQUEST['party']['people']} 人(可超)"
    )


if __name__ == "__main__":
    main()
