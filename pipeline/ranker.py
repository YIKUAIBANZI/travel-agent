"""
推荐排行 + Markdown 输出 — Phase 3

功能:
  1. 读取 data/cleaned/{city}_{date}.json
  2. 收集用户偏好（交互式问答或命令行参数）
  3. LLM 根据用户偏好对地点重排序 + 补充推荐理由
  4. 输出 Markdown 排行表到终端 + 文件

输出格式:
  | # | 名称 | 类型 | 推荐理由 | 评分 | 注意事项 | 来源 |
  |---|------|------|----------|------|----------|------|
"""

import glob
import json
import logging
import os
from datetime import datetime

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

# 旧版通用 Prompt 已移至 api/prompts.py (build_ranker_system)
# 这里保留一份 fallback，当不传 traveler_type 时使用（CLI 模式）
RANK_SYSTEM_PROMPT = """你是一个旅行规划专家。
用户会提供一组旅游地点数据和他们的偏好信息。
请根据用户偏好对这些地点排序，输出一个 JSON 数组。

排序原则：
1. 用户偏好的类型优先（如偏好美食，餐饮类排前面）
2. 有真实用户推荐理由的优先
3. 有评分的按评分高到低
4. 有注意事项的保留原文

对每个地点，你可以：
- 补充或改写 reason（更简洁、更口语化）
- 添加 warnings（如果你知道该地点五一期间会人多）
- 添加 best_time（最佳到访时段，如"日落前1小时""上午人少"）
- 不要改动 name、lat、lng、source_url 等事实字段

返回 JSON 数组，按推荐顺序排列，不要加其他文字。每个对象保留原有全部字段。"""

RANK_USER_TEMPLATE = """城市：{city}
用户偏好：{preferences}
出行天数：{days}天
出行人数和方式：{travel_info}

以下是可用的地点数据（JSON）：
{places_json}

请排序并返回完整 JSON 数组："""


class Ranker:
    """推荐排行器"""

    def __init__(self):
        if not config.LLM_API_KEY:
            raise ValueError("未配置 LLM_API_KEY，请在 .env 中设置")
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.model = config.LLM_MODEL

    def rank(
        self,
        city: str,
        preferences: str = "综合体验",
        days: int = 3,
        travel_info: str = "2人，公共交通",
        cleaned_dir: str = None,
        traveler_type: str = None,
    ) -> list[dict]:
        """
        对清洗后的地点数据排序

        Args:
            city: 城市名
            preferences: 用户偏好描述
            days: 出行天数
            travel_info: 出行方式/人数
            cleaned_dir: 清洗数据目录
            traveler_type: 旅行者类型 A-F（可选，传入则使用类型化 Prompt）
        Returns:
            排序后的地点列表
        """
        cleaned_dir = cleaned_dir or config.CLEANED_DIR
        places = self._load_cleaned(city, cleaned_dir)

        if not places:
            logger.warning("没有可用的地点数据，请先运行清洗管道")
            return []

        logger.info(f"读取到 {len(places)} 个地点，开始排序...")

        ranked = self._llm_rank(
            city, places, preferences, days, travel_info, traveler_type
        )
        return ranked

    def _load_cleaned(self, city: str, cleaned_dir: str) -> list[dict]:
        pattern = os.path.join(cleaned_dir, f"{city}*.json")
        files = sorted(glob.glob(pattern), reverse=True)  # 最新的在前
        if not files:
            return []
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)

    def _llm_rank(
        self,
        city: str,
        places: list[dict],
        preferences: str,
        days: int,
        travel_info: str,
        traveler_type: str = None,
    ) -> list[dict]:
        # 截断到前50个，避免 token 过长
        places_subset = places[:50]
        places_json = json.dumps(places_subset, ensure_ascii=False, indent=2)

        user_msg = RANK_USER_TEMPLATE.format(
            city=city,
            preferences=preferences,
            days=days,
            travel_info=travel_info,
            places_json=places_json,
        )

        # 如果传了 traveler_type，使用类型化 Prompt
        if traveler_type:
            from api.prompts import build_ranker_system

            system_prompt = build_ranker_system(traveler_type)
        else:
            system_prompt = RANK_SYSTEM_PROMPT

        try:
            resp = self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=8000,
            )
            text = resp.choices[0].message.content.strip()

            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            result = json.loads(text)
            if isinstance(result, list):
                return result
            return places_subset

        except Exception as e:
            logger.error(f"LLM 排序失败: {e}，返回原始顺序")
            return places_subset


# ------------------------------------------------------------------
# Markdown 输出
# ------------------------------------------------------------------


def to_markdown_table(places: list[dict], city: str, preferences: str = "") -> str:
    """生成 Markdown 排行表"""
    lines = []
    lines.append(f"# {city} 推荐排行")
    if preferences:
        lines.append(f"> 偏好: {preferences}")
    lines.append("")
    lines.append("| # | 名称 | 类型 | 推荐理由 | 评分 | 注意事项 | 来源 |")
    lines.append("|---|------|------|----------|------|----------|------|")

    for i, p in enumerate(places, 1):
        name = p.get("name", "")
        ptype = p.get("type", "")
        reason = p.get("reason", "")[:40]
        rating = f"{p['rating']}" if p.get("rating") else "-"
        warnings = p.get("warnings", "")
        if warnings:
            warnings = f"⚠️ {warnings}"
        source = p.get("source_platform", "")
        source_url = p.get("source_url", "")
        if source_url:
            source = f"[{source}]({source_url})"

        lines.append(
            f"| {i} | {name} | {ptype} | {reason} | {rating} | {warnings} | {source} |"
        )

    lines.append("")
    lines.append(
        f"*共 {len(places)} 个推荐地点 | 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
    )
    return "\n".join(lines)


def to_coordinate_table(places: list[dict]) -> str:
    """生成坐标表（供地图标注使用）"""
    lines = []
    lines.append("| 名称 | 纬度 | 经度 | 类型 | 区域 |")
    lines.append("|------|------|------|------|------|")
    for p in places:
        if p.get("lat") and p.get("lng"):
            lines.append(
                f"| {p['name']} | {p['lat']} | {p['lng']} | {p['type']} | {p.get('district', '')} |"
            )
    return "\n".join(lines)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="推荐排行器: 对清洗后的地点数据排序并输出 Markdown 表格"
    )
    parser.add_argument("--city", required=True, help="城市名")
    parser.add_argument(
        "--preferences",
        default="综合体验",
        help="偏好描述，如 '美食为主，顺便看看景点'",
    )
    parser.add_argument("--days", type=int, default=3, help="出行天数")
    parser.add_argument("--travel-info", default="2人，公共交通", help="出行方式/人数")
    parser.add_argument(
        "--no-llm", action="store_true", help="跳过 LLM 排序，直接输出原始顺序"
    )
    parser.add_argument("--output", default=None, help="输出文件路径（默认仅终端打印）")
    args = parser.parse_args()

    if args.no_llm:
        # 不用 LLM，直接读数据输出
        cleaned_dir = config.CLEANED_DIR
        pattern = os.path.join(cleaned_dir, f"{args.city}*.json")
        files = sorted(glob.glob(pattern), reverse=True)
        if not files:
            print(
                f"[ERROR] 未找到 {args.city} 的清洗数据，请先运行 pipeline/cleaner.py"
            )
            sys.exit(1)
        with open(files[0], encoding="utf-8") as f:
            places = json.load(f)
    else:
        try:
            ranker = Ranker()
        except ValueError as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        places = ranker.rank(
            city=args.city,
            preferences=args.preferences,
            days=args.days,
            travel_info=args.travel_info,
        )

    if not places:
        print("[WARN] 没有地点数据")
        sys.exit(1)

    md = to_markdown_table(places, args.city, args.preferences)
    print(md)

    coord_md = to_coordinate_table(places)
    print("\n## 坐标表")
    print(coord_md)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
            f.write("\n\n## 坐标表\n")
            f.write(coord_md)
        print(f"\n[SUCCESS] 已保存到 {args.output}")
