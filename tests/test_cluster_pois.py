"""Step 3 · 聚类 + 日期分配 单测。"""

from tools.cluster_pois import (
    _cluster_hotness,
    _kmeans,
    _rebalance_small_clusters,
    cluster_and_assign,
)


def _match(name, lat, lng, **kw):
    return {
        "query": name,
        "poi": {"name": name, "lat": str(lat), "lng": str(lng), **kw},
    }


# ── K-means 基本可用 ──────────────────────────────────────────────────────


def test_kmeans_separates_two_clusters():
    """远离的两簇点必须被分开。"""
    points = [(22.51, 113.94), (22.52, 113.94), (22.53, 114.06), (22.54, 114.06)]
    labels = _kmeans(points, k=2, seed=0)
    # 前两个应同簇,后两个应同簇
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_kmeans_k_gt_n():
    """k > n 时不应崩。"""
    labels = _kmeans([(1.0, 1.0)], k=5)
    assert len(labels) == 5  # 补齐


# ── rebalance 小簇合并 ────────────────────────────────────────────────────


def test_rebalance_merges_singletons():
    """孤立点应合入最近邻簇,原簇置空。"""
    clusters = [
        [_match("南山A", 22.52, 113.94), _match("南山B", 22.53, 113.95)],
        [_match("孤立", 22.525, 113.945)],  # 孤立,靠近南山簇
        [_match("福田A", 22.53, 114.05), _match("福田B", 22.54, 114.06)],
    ]
    out = _rebalance_small_clusters(clusters, min_size=2)
    # 孤立簇应被清空
    sizes = [len(c) for c in out]
    assert sizes.count(0) == 1
    # 南山簇规模应扩到 3(吸收了孤立点)
    assert any(len(c) == 3 for c in out)


# ── 热门度打分 ────────────────────────────────────────────────────────────


def test_cluster_hotness_counts_high_crowd():
    cluster = [_match("世界之窗", 22.53, 113.97), _match("开吉茶馆", 22.53, 114.05)]
    assert _cluster_hotness(cluster) == 1


# ── 端到端:高热门避开风险日 ──────────────────────────────────────────────


def test_high_hotness_clusters_skip_risk_dates():
    """世界之窗这种热门 POI 不应落到风险日(5.1-5.3)。"""
    matched = [
        _match("世界之窗", 22.537, 113.975),  # 热门
        _match("欢乐海岸", 22.523, 113.991),  # 热门
        _match("开吉茶馆", 22.533, 114.054),  # 非热门
        _match("COCOPark", 22.534, 114.055),  # 非热门
    ]
    dates = ["2026-04-29", "2026-04-30", "2026-05-01", "2026-05-02"]
    risk = ["2026-05-01", "2026-05-02"]
    plan = cluster_and_assign(matched, dates, risk)

    # 含热门 POI 的那天,不应是风险日
    for day in plan:
        names = [m["poi"]["name"] for m in day["pois"]]
        if "世界之窗" in names or "欢乐海岸" in names:
            assert not day["is_risk_day"], f"Day {day['day']} 是风险日但排了热门景点"
