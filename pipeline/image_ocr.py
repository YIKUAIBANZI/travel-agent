"""
图片 OCR / VLM 文字提取 — 用通义 qwen-vl-plus（OpenAI 兼容接口）

用法:
    from pipeline.image_ocr import ocr_images
    text = ocr_images(["https://...jpg", "https://...jpg"])

设计:
  - 一次调用传多张图（省 round-trip），最多 max_images 张（默认 4）
  - 失败兜底：全部失败返回空字符串，调用方自己决定是否继续
  - 超时 45s，够慢网也够快失败
  - Prompt 明确要求"按图片顺序编号、只输出图里的文字内容"
"""

import logging
from typing import Iterable

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

OCR_MODEL = "qwen-vl-plus"
OCR_TIMEOUT = 45
OCR_PROMPT = (
    "请按顺序识别下面这些图片中的所有文字内容（OCR），包括标题、正文、表情文字、水印标签等。\n"
    "输出要求：\n"
    "1. 每张图用 [图N] 开头，N 从 1 开始\n"
    "2. 保留原文顺序和换行，不要翻译、不要解释、不要总结\n"
    "3. 图里没有任何文字就写 [图N] (无文字)\n"
    "4. 不要输出图片内容描述，只要文字"
)


def ocr_images(urls: Iterable[str], max_images: int = 4) -> str:
    """对一组图片 URL 做 OCR，返回合并后的文本。失败返回空串。"""
    urls = [u for u in (urls or []) if u][:max_images]
    if not urls:
        return ""

    if not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY 未配置，跳过 OCR")
        return ""

    client = OpenAI(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        timeout=OCR_TIMEOUT,
    )

    content = [{"type": "text", "text": OCR_PROMPT}]
    for url in urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    try:
        resp = client.chat.completions.create(
            model=OCR_MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text
    except Exception as e:
        logger.warning(f"OCR 失败 (urls={len(urls)}): {e}")
        return ""


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("用法: python pipeline/image_ocr.py <url1> [url2] ...")
        sys.exit(1)
    result = ocr_images(sys.argv[1:])
    print(json.dumps({"ocr_text": result}, ensure_ascii=False, indent=2))
