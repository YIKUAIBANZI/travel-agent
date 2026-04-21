"""
Step 3 · POI 聚类 + 日期分配

输入:
  - pois:Step 2 match_must_visit() 返回的 matched 列表,每项含 poi.lat/lng
  - dates:出行日期序列,如 ['2026-04-29', ..., '2026-05-04']
  - risk_dates:节假日人流爆炸的日期,如 ['2026-05-01', '2026-05-02', '2026-05-03']
  - k:簇数(=天数)

算法:
  1. K-means 按经纬度聚类 → k 个簇,每簇对应"一天在同一片区走完"
  2. 热门度打分:簇内含"世界之窗/欢乐海岸"等高人流 POI 得高分
  3. 日期分配(greedy):
     - 高热门簇 → 优先排到非节假日(4.29/4.30/5.4)
     - 低热门簇 → 排到节假日(5.1-5.3 商场/公园/餐厅 不怕挤)

输出:
  [
    {"day": 1, "date": "2026-04-29", "is_risk_day": False, "hotness": 1, "pois": [...] },
    ...
  ]

验收:
  - 每簇内部点距离紧凑(avg pairwise distance 小)
  - 世界之窗 / 欢乐海岸 这种热门景点不落在 5.1-5.3
"""

import math
import random


# 深圳已知高人流 POI(5.1-5.3 爆炸档位)
_HIGH_CROWD = {
    "世界之窗",
    "欢乐海岸",
    "深圳湾公园",  # 节假日人也多,但比世界之窗好
    "荷兰花卉小镇",
    "南山荷兰花卉小镇",
}


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    """二维欧氏距离,深圳范围小够用。"""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _kmeans(
    points: list[tuple[float, float]], k: int, max_iter: int = 30, seed: int = 42
) -> list[int]:
    """返回每个点的 cluster 索引 [0, k)。"""
    n = len(points)
    if n <= k:
        return list(range(n)) + [0] * max(0, k - n)

    random.seed(seed)
    # k-means++ 初始化
    centers = [points[random.randrange(n)]]
    while len(centers) < k:
        d2 = [min(_dist(p, c) ** 2 for c in centers) for p in points]
        total = sum(d2)
        if total == 0:
            centers.append(points[random.randrange(n)])
            continue
        r = random.uniform(0, total)
        cum = 0.0
        for i, v in enumerate(d2):
            cum += v
            if cum >= r:
                centers.append(points[i])
                break

    labels = [0] * n
    for _ in range(max_iter):
        # 分配
        new_labels = []
        for p in points:
            best = min(range(k), key=lambda j: _dist(p, centers[j]))
            new_labels.append(best)
        if new_labels == labels:
            break
        labels = new_labels
        # 重计中心
        for j in range(k):
            members = [points[i] for i, lb in enumerate(labels) if lb == j]
            if members:
                cx = sum(p[0] for p in members) / len(members)
                cy = sum(p[1] for p in members) / len(members)
                centers[j] = (cx, cy)
    return labels


def _cluster_hotness(cluster_pois: list[dict]) -> int:
    """簇热门度 = 含 _HIGH_CROWD 的点数。"""
    count = 0
    for m in cluster_pois:
        name = (m.get("poi") or {}).get("name", "")
        query = m.get("query", "")
        if any(h in name or h in query for h in _HIGH_CROWD):
            count += 1
    return count


def _cluster_center(cluster_pois: list[dict]) -> tuple[float, float] | None:
    """簇质心。空簇返回 None。"""
    xs, ys = [], []
    for m in cluster_pois:
        p = m.get("poi") or {}
        lat, lng = _to_float(p.get("lat")), _to_float(p.get("lng"))
        if lat and lng:
            xs.append(lat)
            ys.append(lng)
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _rebalance_small_clusters(
    clusters: list[list[dict]], min_size: int = 2
) -> list[list[dict]]:
    """把 size < min_size 的小簇并入最近邻簇。腾空的 slot 保留为空列表。"""
    # 先算每个非小簇的中心
    centers = [_cluster_center(c) for c in clusters]

    # 反复处理,直到没有可合并的小簇
    changed = True
    while changed:
        changed = False
        sizes = [len(c) for c in clusters]
        non_empty_big = [i for i, s in enumerate(sizes) if s >= min_size]
        if not non_empty_big:
            break
        for i, c in enumerate(clusters):
            if 0 < sizes[i] < min_size:
                # 找最近的"非自身且足够大"的邻簇
                my_center = centers[i]
                if my_center is None:
                    continue
                best_j = min(
                    non_empty_big,
                    key=lambda j: _dist(my_center, centers[j]) if centers[j] else 1e9,
                )
                # 迁移
                clusters[best_j].extend(c)
                clusters[i] = []
                # 更新目标簇中心
                centers[best_j] = _cluster_center(clusters[best_j])
                centers[i] = None
                changed = True
                break
    return clusters


def cluster_and_assign(
    matched: list[dict],
    dates: list[str],
    risk_dates: list[str],
) -> list[dict]:
    """聚类 + 日期分配。返回 [{day, date, is_risk_day, hotness, pois}]。"""
    k = len(dates)
    points = []
    valid = []
    for m in matched:
        poi = m.get("poi") or {}
        lat = _to_float(poi.get("lat"))
        lng = _to_float(poi.get("lng"))
        if lat == 0.0 or lng == 0.0:
            continue
        points.append((lat, lng))
        valid.append(m)

    labels = _kmeans(points, k)

    # 聚合
    clusters: list[list[dict]] = [[] for _ in range(k)]
    for i, lb in enumerate(labels):
        clusters[lb].append(valid[i])

    # 合并孤立小簇(size < 2)到最近邻簇,腾空的 slot 作为"自由日"
    clusters = _rebalance_small_clusters(clusters, min_size=2)

    # 热门度排序(高 → 低,空簇自然排最后)
    ordered = sorted(
        enumerate(clusters),
        key=lambda ic: _cluster_hotness(ic[1]),
        reverse=True,
    )

    # 日期分组:非风险日优先,风险日次之
    non_risk = [d for d in dates if d not in risk_dates]
    risk = [d for d in dates if d in risk_dates]
    ordered_dates = non_risk + risk  # 高热门塞非风险,低热门塞风险

    assigned = []
    for day_idx, ((_, cluster), date) in enumerate(
        zip(ordered, ordered_dates), start=1
    ):
        assigned.append(
            {
                "day": day_idx,
                "date": date,
                "is_risk_day": date in risk_dates,
                "hotness": _cluster_hotness(cluster),
                "pois": cluster,
                "is_free_day": len(cluster) == 0,  # 被合并走的空 slot,机动日
            }
        )

    # 按日期排序输出(Day 1 = 最早日)
    assigned.sort(key=lambda x: x["date"])
    for i, a in enumerate(assigned, start=1):
        a["day"] = i
    return assigned


# ── CLI 调试入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from examples.shenzhen_may_day import REQUEST
    from tools.match_pois import match_must_visit

    match_result = match_must_visit(REQUEST["must_visit"], city=REQUEST["city"])
    matched = match_result["matched"]

    # 生成日期序列
    from datetime import date, timedelta

    start = date.fromisoformat(REQUEST["dates"]["start"])
    end = date.fromisoformat(REQUEST["dates"]["end"])
    dates = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]

    plan = cluster_and_assign(
        matched=matched,
        dates=dates,
        risk_dates=REQUEST["holiday_crowd_risk_dates"],
    )

    print("=" * 60)
    print(
        f"  聚类 + 日期分配 · {REQUEST['city']} · {len(dates)} 天 / {len(matched)} 点"
    )
    print("=" * 60)
    for day in plan:
        mark = "🚨高峰日" if day["is_risk_day"] else "✅平日  "
        tag = " [机动/自由日]" if day.get("is_free_day") else ""
        print(
            f"\nDay {day['day']} · {day['date']} {mark} · 热门度={day['hotness']} · {len(day['pois'])} 点{tag}"
        )
        for m in day["pois"]:
            p = m["poi"]
            crowd = (
                " 🔥"
                if any(
                    h in (p.get("name", "") + m.get("query", "")) for h in _HIGH_CROWD
                )
                else ""
            )
            print(
                f"  · {m['query']:30s} → {p['name']}  [{p.get('district', '?')}]{crowd}"
            )
