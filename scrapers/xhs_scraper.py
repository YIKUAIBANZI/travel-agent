"""
小红书爬虫 — 三种模式

模式 A (XHS_MODE=api):
  用 xhs 库 XhsClient.get_note_by_keyword()
  需要 .env 中配置 XHS_COOKIE_A1 / XHS_COOKIE_WEB_SESSION / XHS_COOKIE_WEB_ID
  若签名失效(406)，自动切换到模式 C

模式 B (XHS_MODE=connected，推荐个人使用):
  连接用户已登录的 Chrome 浏览器，复用已有 session，无需手动配置 Cookie
  使用方法:
    1. 先在终端运行:
       open -a "Google Chrome" --args --remote-debugging-port=9222 --no-first-run
    2. 在打开的 Chrome 里登录小红书
    3. 运行爬虫: python scrapers/xhs_scraper.py --query "深圳攻略" --mode connected

模式 C (XHS_MODE=browser):
  启动新的 Chrome 窗口（非 headless），用于测试和调试
  XHS 对 headless 有安全限制，此模式窗口可见
"""

import logging
import time
from datetime import datetime

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


def _extract_img_url(img: dict) -> str:
    """XHS image_list 每项的 url 抽取，兼容多种字段顺序"""
    if not isinstance(img, dict):
        return ""
    for key in ("url_default", "url_pre", "url"):
        val = img.get(key)
        if val:
            return val
    info_list = img.get("info_list") or []
    for info in info_list:
        val = (info or {}).get("url")
        if val:
            return val
    return ""


def _extract_images(note: dict) -> list:
    return [
        u for u in (_extract_img_url(i) for i in (note.get("image_list") or [])) if u
    ]


XHS_NOTE_BASE_URL = "https://www.xiaohongshu.com/explore/"
XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={query}&source=web_search_result_notes"


class XhsScraper(BaseScraper):
    PLATFORM = "xhs"

    def search(self, query: str, count: int = 20) -> list:
        mode = config.XHS_MODE

        if mode == "api":
            if not any(config.XHS_COOKIES.values()):
                logger.warning("XHS Cookie 未配置，切换到 connected 模式")
                return self._search_connected(query, count)
            logger.info("使用 API 模式（xhs 库）")
            try:
                return self._search_api(query, count)
            except Exception as e:
                logger.warning(f"API 模式失败 ({e})，切换到 connected 模式")
                return self._search_connected(query, count)

        if mode == "connected":
            return self._search_connected(query, count)

        # mode == "browser": 启动新窗口（调试用）
        return self._search_new_browser(query, count)

    # ------------------------------------------------------------------
    # 模式 A: xhs 库 API（需 Cookie）
    # ------------------------------------------------------------------
    def _search_api(self, query: str, count: int) -> list:
        from xhs import XhsClient
        from xhs.exception import DataFetchError, SignError

        cookie_str = "; ".join(f"{k}={v}" for k, v in config.XHS_COOKIES.items() if v)
        client = XhsClient(cookie=cookie_str)
        records = []
        page = 1

        while len(records) < count:
            try:
                result = client.get_note_by_keyword(
                    keyword=query, page=page, page_size=20
                )
            except (SignError, DataFetchError) as e:
                raise RuntimeError(f"XHS API 错误: {e}") from e

            items = result.get("items", []) if isinstance(result, dict) else []
            if not items:
                logger.info("没有更多结果")
                break

            for item in items:
                note = item.get("note_card") or item.get("note") or item
                note_id = note.get("note_id") or note.get("id", "")
                records.append(
                    {
                        "id": note_id,
                        "platform": "xhs",
                        "title": note.get("title", ""),
                        "body": note.get("desc", ""),
                        "author": (note.get("user") or {}).get("nickname", ""),
                        "source_url": f"{XHS_NOTE_BASE_URL}{note_id}"
                        if note_id
                        else "",
                        "images": _extract_images(note),
                        "likes": (note.get("interact_info") or {}).get(
                            "liked_count", 0
                        ),
                        "created_at": "",
                        "city": query,
                        "scraped_at": datetime.now().isoformat(),
                    }
                )
                if len(records) >= count:
                    break

            logger.info(f"第 {page} 页: +{len(items)} 条，累计 {len(records)}")
            if not result.get("has_more", False):
                break
            page += 1
            self._sleep()

        return records[:count]

    # ------------------------------------------------------------------
    # 模式 B: 连接已登录 Chrome（推荐个人使用）
    # ------------------------------------------------------------------
    def _search_connected(self, query: str, count: int) -> list:
        """
        连接用户已启动的 Chrome（带 --remote-debugging-port=9222）
        需要提前在终端运行:
          open -a "Google Chrome" --args --remote-debugging-port=9222 --no-first-run
        然后在 Chrome 里登录小红书，再运行此爬虫
        """
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            raise RuntimeError("DrissionPage 未安装，请运行: pip install DrissionPage")

        logger.info(f"Connected 模式搜索小红书: {query}")
        logger.info(
            "（确保已运行: open -a 'Google Chrome' --args --remote-debugging-port=9222）"
        )

        try:
            opts = ChromiumOptions().set_local_port(9222)
            page = ChromiumPage(addr_or_opts=opts)
        except Exception as e:
            raise RuntimeError(
                f"连接 Chrome 失败: {e}\n"
                "请先在终端运行:\n"
                "  open -a 'Google Chrome' --args --remote-debugging-port=9222 --no-first-run\n"
                "然后在 Chrome 里登录小红书，再重新运行爬虫"
            ) from e

        records = []
        seen_ids = set()
        try:
            url = XHS_SEARCH_URL.format(query=query)
            page.get(url)
            time.sleep(6)

            # 开始监听后刷新，拦截 search/notes API 响应
            page.listen.start("v1/search/notes")
            page.refresh()
            time.sleep(8)

            # 收集搜索结果
            self._collect_from_packets(page, records, seen_ids, query, timeout=10)

            # 滚动加载更多
            scroll = 0
            while len(records) < count and scroll < 8:
                page.scroll.down(1000)
                time.sleep(5)
                self._collect_from_packets(page, records, seen_ids, query, timeout=5)
                scroll += 1
                logger.info(f"滚动加载: 累计 {len(records)} 条")
                if len(records) == 0 and scroll >= 2:
                    break

            if not records:
                logger.warning(
                    "未拦截到搜索结果 — 请确认已在 Chrome 里登录小红书，并能正常看到搜索结果"
                )

        finally:
            page.listen.stop()

        records = records[:count]

        # 逐条抓取笔记详情页拿正文
        if records:
            logger.info(f"开始抓取 {len(records)} 条笔记详情...")
            self._fetch_note_details(page, records)

        logger.info(f"Connected 模式完成，共获取 {len(records)} 条")
        return records

    def _collect_from_packets(
        self, page, records: list, seen_ids: set, query: str, timeout: int = 5
    ):
        """从拦截的网络包中提取搜索结果"""
        packets = list(page.listen.steps(count=5, timeout=timeout))
        for packet in packets:
            try:
                body = packet.response.body
                if not isinstance(body, dict):
                    continue
                items = body.get("data", {}).get("items", [])
                for item in items:
                    note = item.get("note_card") or item.get("note") or {}
                    # note_id 在 item 层级
                    note_id = (
                        item.get("id") or note.get("note_id") or note.get("id", "")
                    )
                    if not note_id or note_id in seen_ids:
                        continue
                    seen_ids.add(note_id)
                    # xsec_token 用于详情页访问
                    xsec_token = item.get("xsec_token", "")
                    records.append(
                        {
                            "id": note_id,
                            "platform": "xhs",
                            "title": note.get("display_title", "")
                            or note.get("title", ""),
                            "body": note.get("desc", ""),
                            "author": (note.get("user") or {}).get("nickname", ""),
                            "source_url": f"{XHS_NOTE_BASE_URL}{note_id}",
                            "xsec_token": xsec_token,
                            "images": _extract_images(note),
                            "ocr_text": "",
                            "likes": (note.get("interact_info") or {}).get(
                                "liked_count", 0
                            ),
                            "created_at": "",
                            "city": query,
                            "scraped_at": datetime.now().isoformat(),
                        }
                    )
                if items:
                    logger.info(f"拦截到 {len(items)} 条搜索结果")
            except Exception as e:
                logger.debug(f"解析响应失败: {e}")

    def _fetch_note_details(self, page, records: list):
        """在搜索结果页逐个点击笔记卡片，通过 v1/feed API 拿正文

        流程: 点击 note-item → 拦截 v1/feed → page.back() 回搜索页 → 下一个
        """
        fetched = 0

        for i, rec in enumerate(records):
            try:
                # 每轮重新获取 DOM 引用，避免 stale
                current_items = page.eles(".note-item")
                if i >= len(current_items):
                    logger.debug(f"note-item 不足，跳过第 {i + 1} 条")
                    break

                page.listen.start("v1/feed")
                current_items[i].click()
                time.sleep(6)

                got_body = False
                packets = list(page.listen.steps(count=3, timeout=8))
                for packet in packets:
                    try:
                        body = packet.response.body
                        if not isinstance(body, dict):
                            continue
                        feed_items = body.get("data", {}).get("items", [])
                        for fi in feed_items:
                            nc = fi.get("note_card") or {}
                            desc = nc.get("desc", "")
                            if desc:
                                rec["body"] = desc
                                got_body = True
                            title = nc.get("title", "") or nc.get("display_title", "")
                            if title:
                                rec["title"] = title
                            hi_imgs = _extract_images(nc)
                            if hi_imgs:
                                rec["images"] = hi_imgs
                    except Exception:
                        pass

                try:
                    page.listen.stop()
                except Exception:
                    pass

                # OCR 前 4 张图（失败不中断）
                ocr_text = ""
                if rec.get("images"):
                    try:
                        from pipeline.image_ocr import ocr_images

                        ocr_text = ocr_images(rec["images"], max_images=4)
                        rec["ocr_text"] = ocr_text
                    except Exception as e:
                        logger.debug(f"OCR 调用异常: {e}")
                        rec["ocr_text"] = ""

                if got_body:
                    fetched += 1
                logger.info(
                    f"[{i + 1}/{len(records)}] "
                    f"'{rec['title'][:30]}' — "
                    f"{'%d 字' % len(rec.get('body', '')) if got_body else '无正文'} "
                    f"| OCR {len(ocr_text)} 字"
                )

                # back() 回到搜索结果页（关闭弹窗）
                page.back()
                time.sleep(4)
                self._sleep()

            except Exception as e:
                try:
                    page.listen.stop()
                except Exception:
                    pass
                logger.debug(f"详情抓取失败: {e}")
                # 尝试恢复到搜索页
                try:
                    page.back()
                    time.sleep(2)
                except Exception:
                    pass

        logger.info(f"详情抓取完成: {fetched}/{len(records)} 条有正文")

    # ------------------------------------------------------------------
    # 模式 C: 启动新 Chrome 窗口（调试用）
    # ------------------------------------------------------------------
    def _search_new_browser(self, query: str, count: int) -> list:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            raise RuntimeError("DrissionPage 未安装，请运行: pip install DrissionPage")

        logger.info(f"Browser 模式（新窗口）搜索小红书: {query}")
        logger.warning(
            "此模式需要手动在弹出的 Chrome 窗口中登录小红书，然后等待自动搜索"
        )

        opts = ChromiumOptions()
        opts.headless(False)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-blink-features=AutomationControlled")

        page = ChromiumPage(addr_or_opts=opts)
        records = []

        try:
            page.get("https://www.xiaohongshu.com")
            logger.info("请在弹出的 Chrome 窗口中登录小红书，等待30秒...")
            time.sleep(30)  # 给用户时间登录

            url = XHS_SEARCH_URL.format(query=query)
            page.listen.start("search/notes")
            page.get(url)
            time.sleep(5)

            packets = page.listen.steps(count=10, timeout=5)
            for packet in packets:
                try:
                    body = packet.response.body
                    if not isinstance(body, dict):
                        continue
                    items = body.get("data", {}).get("items", [])
                    for item in items:
                        note = item.get("note_card") or item.get("note") or {}
                        note_id = note.get("note_id") or note.get("id", "")
                        if not note_id:
                            continue
                        records.append(
                            {
                                "id": note_id,
                                "platform": "xhs",
                                "title": note.get("title", ""),
                                "body": note.get("desc", ""),
                                "author": (note.get("user") or {}).get("nickname", ""),
                                "source_url": f"{XHS_NOTE_BASE_URL}{note_id}",
                                "images": _extract_images(note),
                                "likes": (note.get("interact_info") or {}).get(
                                    "liked_count", 0
                                ),
                                "created_at": "",
                                "city": query,
                                "scraped_at": datetime.now().isoformat(),
                            }
                        )
                except Exception as e:
                    logger.debug(f"解析响应失败: {e}")

        finally:
            page.listen.stop()
            page.quit()

        logger.info(f"Browser 模式完成，共获取 {len(records)} 条")
        return records[:count]


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="小红书攻略爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 模式 A: API（需在 .env 配置 Cookie）
  python scrapers/xhs_scraper.py --query "深圳攻略" --mode api

  # 模式 B: 连接已登录 Chrome（推荐）
  # 先运行: open -a "Google Chrome" --args --remote-debugging-port=9222 --no-first-run
  # 在 Chrome 里登录小红书，然后:
  python scrapers/xhs_scraper.py --query "深圳攻略" --mode connected

  # 模式 C: 启动新窗口（调试用，会弹出 Chrome 窗口让你登录）
  python scrapers/xhs_scraper.py --query "深圳攻略" --mode browser
        """,
    )
    parser.add_argument("--query", required=True, help="搜索关键词，如 '深圳攻略'")
    parser.add_argument("--count", type=int, default=20, help="目标条数（默认20）")
    parser.add_argument(
        "--mode",
        choices=["api", "connected", "browser"],
        default=None,
        help="爬取模式（不填则读 .env XHS_MODE，默认 connected）",
    )
    args = parser.parse_args()

    if args.mode:
        config.XHS_MODE = args.mode

    scraper = XhsScraper()
    try:
        results = scraper.search(query=args.query, count=args.count)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if not results:
        print("[WARN] 未获取到任何结果")
        sys.exit(1)

    filepath = scraper.save(results, city=args.query)
    print(f"\n[SUCCESS] {len(results)} 条记录已保存到 {filepath}")
