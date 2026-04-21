"""
高德地图标注 — Phase 4

功能:
  1. 读取排行后的地点数据
  2. 生成高德地图静态图 URL（所有 POI 标注在一张图上）
  3. 生成高德地图 Web 链接（可交互地图）
  4. 按天分组输出每天的地图

高德静态图 API: https://restapi.amap.com/v3/staticmap
  - markers: label + 坐标
  - 免费，无需额外申请

高德 URI 方案（打开高德 App/Web）:
  https://uri.amap.com/marker?markers=lng,lat,name|lng2,lat2,name2
"""

import logging
import urllib.parse

import config

logger = logging.getLogger(__name__)

STATIC_MAP_URL = "https://restapi.amap.com/v3/staticmap"
URI_MARKER_URL = "https://uri.amap.com/marker"

# 类型 → 标记颜色
TYPE_COLORS = {
    "景点": "0x2196F3",  # 蓝
    "餐饮": "0xFF5722",  # 红
    "购物": "0xFF9800",  # 橙
    "娱乐": "0x9C27B0",  # 紫
    "住宿": "0x4CAF50",  # 绿
}

# 类型 → 标记 label 前缀
TYPE_LABELS = {
    "景点": "J",
    "餐饮": "C",
    "购物": "G",
    "娱乐": "Y",
    "住宿": "Z",
}


def generate_static_map_url(
    places: list[dict],
    size: str = "750*500",
    zoom: int = 0,
) -> str:
    """
    生成高德静态地图 URL，标注所有有坐标的地点

    Args:
        places: 地点列表（需有 lat, lng）
        size: 图片尺寸
        zoom: 缩放级别（0=自动适配）
    Returns:
        静态地图图片 URL
    """
    if not config.AMAP_API_KEY:
        logger.warning("未配置 AMAP_API_KEY，无法生成地图")
        return ""

    markers_parts = []
    for i, p in enumerate(places):
        lat, lng = p.get("lat", ""), p.get("lng", "")
        if not lat or not lng:
            continue
        ptype = p.get("type", "景点")
        color = TYPE_COLORS.get(ptype, "0x2196F3")
        label = TYPE_LABELS.get(ptype, "P")
        # 格式: mid,color,label:lng,lat
        markers_parts.append(f"mid,{color},{label}{i + 1}:{lng},{lat}")

    if not markers_parts:
        return ""

    params = {
        "key": config.AMAP_API_KEY,
        "size": size,
        "markers": "|".join(markers_parts),
    }
    if zoom > 0:
        params["zoom"] = str(zoom)

    return f"{STATIC_MAP_URL}?{urllib.parse.urlencode(params)}"


def generate_web_map_url(places: list[dict]) -> str:
    """
    生成高德 URI 方案链接（可在浏览器/高德 App 打开的交互地图）

    格式: https://uri.amap.com/marker?markers=lng,lat,name|lng2,lat2,name2
    """
    marker_parts = []
    for p in places:
        lat, lng = p.get("lat", ""), p.get("lng", "")
        if not lat or not lng:
            continue
        name = p.get("name", "").replace("|", " ")
        marker_parts.append(f"{lng},{lat},{name}")

    if not marker_parts:
        return ""

    markers_str = "|".join(marker_parts)
    return f"{URI_MARKER_URL}?markers={urllib.parse.quote(markers_str)}"


def generate_map_report(places: list[dict], city: str) -> str:
    """生成完整的地图报告（Markdown 格式）"""
    lines = []
    lines.append(f"## {city} 地图标注\n")

    # 统计
    with_coords = [p for p in places if p.get("lat") and p.get("lng")]
    lines.append(f"共 {len(places)} 个地点，{len(with_coords)} 个有坐标标注\n")

    # 交互地图链接
    web_url = generate_web_map_url(with_coords)
    if web_url:
        lines.append(f"**[在高德地图中查看全部标注]({web_url})**\n")

    # 静态地图
    static_url = generate_static_map_url(with_coords)
    if static_url:
        lines.append(f"![静态地图]({static_url})\n")

    # 图例
    lines.append(
        "**图例**: ",
    )
    legend_parts = [f"{label}=**{ptype}**" for ptype, label in TYPE_LABELS.items()]
    lines.append(" | ".join(legend_parts))
    lines.append("")

    # 按类型分组列出
    by_type: dict[str, list] = {}
    for p in with_coords:
        ptype = p.get("type", "景点")
        by_type.setdefault(ptype, []).append(p)

    for ptype, plist in by_type.items():
        lines.append(f"\n### {ptype}（{len(plist)}个）")
        for p in plist:
            name = p["name"]
            warnings = f" ⚠️ {p['warnings']}" if p.get("warnings") else ""
            reason = f" — {p['reason']}" if p.get("reason") else ""
            lines.append(f"- **{name}**{reason}{warnings}")

    return "\n".join(lines)
