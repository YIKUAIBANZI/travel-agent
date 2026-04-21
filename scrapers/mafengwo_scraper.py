"""
马蜂窝游记爬虫（DrissionPage 浏览器模式）

马蜂窝已启用 JS 反爬（HTTP 202 probe），必须用真实浏览器访问。
本脚本使用 DrissionPage 控制 Chrome，无需 Cookie 和签名。

依赖: DrissionPage（pip install DrissionPage），本地需安装 Chrome
"""

import logging
import time
from datetime import datetime

from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

MFW_SEARCH_URL = "https://www.mafengwo.cn/search/q.php?q={query}&t=travels"


class MafengwoScraper(BaseScraper):
    PLATFORM = "mafengwo"

    def search(self, query: str, count: int = 20) -> list:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            raise RuntimeError("DrissionPage 未安装，请运行: pip install DrissionPage")

        logger.info(f"正在爬取马蜂窝游记: {query}（目标={count}条）")

        opts = ChromiumOptions()
        opts.headless(True)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-dev-shm-usage")

        page = ChromiumPage(addr_or_opts=opts)
        records: list[dict] = []

        try:
            url = MFW_SEARCH_URL.format(query=query)
            page.get(url)
            time.sleep(3)

            seen_ids: set[str] = set()
            scroll_attempts = 0
            max_scrolls = max(count // 4, 6)

            while len(records) < count and scroll_attempts < max_scrolls:
                # 游记搜索结果选择器（马蜂窝搜索结果页）
                items = (
                    page.eles("css:.post-list .item")
                    or page.eles("css:.travels-list li")
                    or page.eles("css:.search-list .item")
                    or page.eles("css:article.item")
                )

                for item in items:
                    try:
                        link_el = (
                            item.ele("css:h3 a", timeout=0)
                            or item.ele("css:.title a", timeout=0)
                            or item.ele("css:a.post-title", timeout=0)
                            or item.ele("css:a[href*='/i/']", timeout=0)
                        )
                        if not link_el:
                            continue

                        href = link_el.attr("href") or ""
                        title = link_el.text.strip()

                        if not href or not title:
                            continue

                        # 构建完整 URL 和 ID
                        if href.startswith("/"):
                            source_url = f"https://www.mafengwo.cn{href}"
                        elif href.startswith("http"):
                            source_url = href
                        else:
                            continue

                        note_id = href.rstrip("/").split("/")[-1].replace(".html", "")
                        if note_id in seen_ids:
                            continue
                        seen_ids.add(note_id)

                        # 摘要
                        body_el = (
                            item.ele("css:.excerpt", timeout=0)
                            or item.ele("css:.desc", timeout=0)
                            or item.ele("css:p", timeout=0)
                        )
                        body = body_el.text.strip() if body_el else ""

                        # 作者
                        author_el = (
                            item.ele("css:.author", timeout=0)
                            or item.ele("css:.username", timeout=0)
                            or item.ele("css:.user-name", timeout=0)
                        )
                        author = author_el.text.strip() if author_el else ""

                        # 点赞/阅读数
                        likes_el = item.ele(
                            "css:.like, css:.count, css:.num", timeout=0
                        )
                        try:
                            likes = (
                                int("".join(filter(str.isdigit, likes_el.text)) or "0")
                                if likes_el
                                else 0
                            )
                        except (ValueError, AttributeError):
                            likes = 0

                        records.append(
                            {
                                "id": note_id,
                                "platform": "mafengwo",
                                "title": title,
                                "body": body,
                                "author": author,
                                "source_url": source_url,
                                "images": [],
                                "likes": likes,
                                "created_at": "",
                                "city": query,
                                "scraped_at": datetime.now().isoformat(),
                            }
                        )

                        if len(records) >= count:
                            break

                    except Exception as e:
                        logger.debug(f"解析条目失败，跳过: {e}")
                        continue

                logger.info(
                    f"滚动 {scroll_attempts + 1}/{max_scrolls}: 已收集 {len(records)} 条"
                )

                if len(records) >= count:
                    break

                # 尝试点击"下一页"或滚动加载
                next_btn = page.ele("css:.next", timeout=0) or page.ele(
                    "css:a[rel='next']", timeout=0
                )
                if next_btn:
                    next_btn.click()
                    time.sleep(3)
                else:
                    page.scroll.down(1000)
                    time.sleep(2.5)

                scroll_attempts += 1

        finally:
            page.quit()

        logger.info(f"马蜂窝爬取完成，共获取 {len(records)} 条")
        return records[:count]


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="马蜂窝游记爬虫（浏览器模式）")
    parser.add_argument("--city", required=True, help="城市名，如 '深圳'")
    parser.add_argument("--count", type=int, default=20, help="目标条数（默认20）")
    args = parser.parse_args()

    scraper = MafengwoScraper()
    results = scraper.search(query=args.city, count=args.count)

    if not results:
        print("[WARN] 未获取到任何结果，请检查网络连接或搜索词")
        sys.exit(1)

    filepath = scraper.save(results, city=args.city)
    print(f"\n[SUCCESS] {len(results)} 条记录已保存到 {filepath}")
