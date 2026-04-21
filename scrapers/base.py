import json
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime

import requests

import config

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """所有爬虫的基类，提供重试、延迟、保存逻辑"""

    PLATFORM = ""  # 子类必须定义

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )

    def _sleep(self):
        """随机延迟，避免触发限流"""
        delay = random.uniform(config.SCRAPER_DELAY_MIN, config.SCRAPER_DELAY_MAX)
        logger.debug(f"等待 {delay:.1f}s...")
        time.sleep(delay)

    def _request(self, method, url, max_retries=None, **kwargs):
        """带重试的请求，处理 461/471/521 限流响应"""
        max_retries = max_retries or config.MAX_RETRIES
        for attempt in range(max_retries):
            try:
                resp = self.session.request(method, url, timeout=15, **kwargs)
                if resp.status_code in (461, 471, 521):
                    delay = 5 * (2**attempt)
                    logger.warning(
                        f"触发限流 [{resp.status_code}]，"
                        f"等待 {delay}s 后重试 ({attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                logger.error(f"请求失败 [{attempt + 1}/{max_retries}]: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(3)
        return None

    def save(self, records: list, city: str) -> str:
        """保存原始数据到 JSON 文件，返回文件路径"""
        os.makedirs(config.DATA_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"{self.PLATFORM}_{city}_{date_str}.json"
        filepath = os.path.join(config.DATA_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存 {len(records)} 条记录到 {filepath}")
        return filepath

    @abstractmethod
    def search(self, query: str, count: int = 20) -> list:
        """子类实现：搜索并返回标准化记录列表"""
        pass
