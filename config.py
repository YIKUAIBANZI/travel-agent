import os
from dotenv import load_dotenv

load_dotenv()

XHS_COOKIES = {
    "a1": os.getenv("XHS_COOKIE_A1", ""),
    "web_session": os.getenv("XHS_COOKIE_WEB_SESSION", ""),
    "webId": os.getenv("XHS_COOKIE_WEB_ID", ""),
}

SCRAPER_DELAY_MIN = float(os.getenv("SCRAPER_DELAY_MIN", "1.5"))
SCRAPER_DELAY_MAX = float(os.getenv("SCRAPER_DELAY_MAX", "3.5"))
DATA_DIR = os.getenv("DATA_DIR", "data/raw")
MAX_RETRIES = 3

# "api" 用 xhs 库签名方式；"browser" 用 DrissionPage 真实浏览器（无需 Cookie）
XHS_MODE = os.getenv("XHS_MODE", "browser")

AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

# LLM 配置（支持 OpenAI / Claude / Deepseek / 通义 等任何 OpenAI 兼容 API）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# 清洗后数据目录
CLEANED_DIR = os.getenv("CLEANED_DIR", "data/cleaned")

# Exa 网络搜索（可选，不配置则跳过网络攻略增强）
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
