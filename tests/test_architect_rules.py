"""Step 4 · 规则层 critique(不调 LLM,纯 Python 逻辑)。"""

from agent.architect_toolloop import (
    _is_in_allowlist,
    build_allowed_names,
    rule_based_critique,
)


# ── 白名单宽松匹配 ────────────────────────────────────────────────────────


def test_is_in_allowlist_exact():
    assert _is_in_allowlist("深圳湾公园", {"深圳湾公园"})


def test_is_in_allowlist_with_shop_suffix():
    """候选 name 含 '(xxx店)' 后缀,仍应匹到白名单(正向词序)。"""
    # 词序一致的 case:白名单里有完整子串
    assert _is_in_allowlist("鸟剑居酒屋(购物公园店)", {"鸟剑居酒屋"})


def test_is_in_allowlist_rejects_fabricated():
    """完全虚构的名字应被拒。"""
    assert not _is_in_allowlist("椰林椰子鸡·南山店", {"深圳湾公园", "世界之窗"})


# ── 白名单构造 ────────────────────────────────────────────────────────────


def test_build_allowed_names_merges_pois_and_must_visit():
    days = [
        {"pois": [{"poi": {"name": "深圳湾公园"}}, {"poi": {"name": "世界之窗"}}]},
    ]
    must = ["深圳人才公园", "世界之窗"]  # 有重复
    allowed = build_allowed_names(days, must)
    assert set(allowed) == {"深圳湾公园", "世界之窗", "深圳人才公园"}


# ── 规则层 critique ──────────────────────────────────────────────────────


def _make_itin(day_blocks):
    return {"days": [{"day": i + 1, **d} for i, d in enumerate(day_blocks)]}


def test_rule_detects_closed_venue():
    itin = _make_itin([{"lunch": {"name": "太食獸(暂停营业)"}}])
    issues = rule_based_critique(itin, {}, allowed_names={"太食獸(暂停营业)"})
    assert any(i["rule"] == "closed_venue" for i in issues)


def test_rule_detects_fabricated_name():
    """行程里有白名单外的 name,必须标 fabricated。"""
    itin = _make_itin([{"lunch": {"name": "椰林椰子鸡"}}])
    allowed = {"深圳湾公园", "世界之窗"}
    issues = rule_based_critique(itin, {"must_visit": []}, allowed)
    assert any(i["rule"] == "fabricated_name" for i in issues)


def test_rule_accepts_white_listed_name():
    """白名单内的 name 不应触发 fabricated。"""
    itin = _make_itin([{"lunch": {"name": "深圳湾公园"}}])
    issues = rule_based_critique(itin, {"must_visit": []}, {"深圳湾公园"})
    assert not any(i["rule"] == "fabricated_name" for i in issues)


def test_rule_too_many_activities():
    """主活动(morning/afternoon/evening)加起来超过 4 应告警。"""
    day = {
        "morning": [{"name": "深圳湾公园"}, {"name": "人才公园"}],
        "afternoon": [{"name": "欢乐海岸"}, {"name": "世界之窗"}],
        "evening": [{"name": "CoCoPark"}],
    }
    issues = rule_based_critique(
        _make_itin([day]),
        {"must_visit": []},
        {"深圳湾公园", "人才公园", "欢乐海岸", "世界之窗", "CoCoPark"},
    )
    assert any(i["rule"] == "too_many_activities" for i in issues)
