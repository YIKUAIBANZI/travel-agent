"""Step 2 · POI 匹配的核心单测(不调网络)。"""

from tools.match_pois import (
    _DISTRICT_STOPWORDS,
    normalize,
)
from tools.match_pois import match_one


# ── normalize ─────────────────────────────────────────────────────────────


def test_normalize_strips_metro_prefix():
    assert normalize("c出口丰盛町") == "丰盛町"
    assert normalize("B口岗厦") == "岗厦"
    # 末尾的 "B口" 不在正则覆盖范围(只去开头前缀),保持原样 lowercase
    assert normalize("岗厦B口") == "岗厦b口"


def test_normalize_strips_shop_suffix():
    # (购物公园店) 应被整段去掉
    assert normalize("鸟剑居酒屋(购物公园店)") == "鸟剑居酒屋"


def test_normalize_strips_punct_and_case():
    assert normalize("梅果.云贵川") == "梅果云贵川"
    assert normalize("COCOPark") == "cocopark"


# ── contain / fuzzy 匹配 ──────────────────────────────────────────────────


def _make_poi(name, lat="22.5", lng="114.0", **kw):
    return {"name": name, "lat": lat, "lng": lng, **kw}


def test_exact_match_wins():
    pool = [_make_poi("世界之窗"), _make_poi("深圳湾公园")]
    poi, score, how = match_one("世界之窗", pool)
    assert poi is not None and how == "exact" and score == 1.0


def test_contain_rejects_district_stopword():
    """'南山荷兰花卉小镇' 不能被 contain 命中到区名 '南山'。"""
    pool = [_make_poi("南山"), _make_poi("深圳湾公园")]
    assert "南山" in _DISTRICT_STOPWORDS  # 前置保证:区名在黑名单
    poi, score, how = match_one("南山荷兰花卉小镇", pool)
    assert poi is None or how != "contain"


def test_contain_min_length():
    """短 query(2 字)不触发 contain,避免乱匹。"""
    pool = [_make_poi("深圳湾公园")]
    poi, _, how = match_one("深圳", pool)  # 2 字,<_MIN_MATCH_LEN=3
    assert how != "contain"


def test_fuzzy_rejects_loose_similarity():
    """'香蜜公园' 不能被 fuzzy 匹到 '荔香公园'(旧版阈值 0.7 会误匹)。"""
    pool = [_make_poi("荔香公园")]
    poi, score, how = match_one("香蜜公园", pool)
    # 现在阈值 0.85,两者相似度 0.75,应该拒绝
    assert poi is None
