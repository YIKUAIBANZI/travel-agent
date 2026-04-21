# CLAUDE.md — 旅行规划 Agent

## 项目概述

旅行规划垂直 Agent：用户输入目的地+天数+偏好，AI 生成个性化行程。
三数据源：高德 POI（坐标/评分）+ 小红书 UGC（真实口碑）+ Exa 实时搜索（最新攻略）。

## 技术栈

- Python 3.14 + FastAPI + uvicorn
- LLM: 通义千问 qwen-plus（OpenAI 兼容 API，通过 dashscope）
- 高德 API: POI 搜索 + Geocoding + 天气
- Exa API: 实时网络攻略搜索
- XHS 爬取: DrissionPage 浏览器自动化
- 前端: 单文件 HTML + vanilla JS

## 启动方式

```bash
cd ~/Desktop/sth/travel-agent
source venv/bin/activate
PYTHONPATH=. uvicorn api.main:app --host 127.0.0.1 --port 9191
# 访问 http://127.0.0.1:9191/
```

## 目录结构

```
api/
  main.py          — FastAPI 路由
  agent.py         — 对话状态机核心（Profiler → Collector → Ranker → Architect）
  prompts.py       — 全部 LLM Prompt 模板
  session.py       — 内存 Session 管理
  models.py        — Pydantic 数据模型
  weather.py       — 高德天气 API
pipeline/
  cleaner.py       — LLM 清洗管道（XHS 文本 → 结构化地点）
  ranker.py        — 类型化景点排序
  food_matcher.py  — 景点附近美食匹配
  web_search.py    — Exa 网络搜索封装
  mapper.py        — 高德地图链接生成
scrapers/          — 高德 / XHS 爬虫
scripts/           — 预爬取 / 预清洗脚本
web/index.html     — 聊天前端界面
data/raw/          — 原始爬取数据（98城市）
data/cleaned/      — 清洗后结构化数据
```

## 对话状态机

```
INIT → COLLECT_PREFERENCES → SELECT_SPOTS → GENERATING_ITINERARY → DONE
```

- INIT: Profiler 合并提取城市信息 + 识别旅行者类型（A-F 六类）
- COLLECT_PREFERENCES: 类型化偏好追问（1-2 轮）
- SELECT_SPOTS: 类型化权重排序 → 用户选景点
- GENERATING_ITINERARY: Architect 编排（Anchor&Orbit 模型 + 节奏韵律）
- DONE: Follow-up 处理追问

## 六种旅行者类型

- A: 情侣/闺蜜打卡型 — 出片优先
- B: 家庭亲子型 — 安全、儿童友好、节奏慢
- C: 银发/孝心型 — 无障碍、文化、舒适
- D: 预算穷游型 — 免费/低价优先
- E: 深度文化型 — 历史纵深、本地沉浸
- F: 美食优先型 — 围绕餐厅排景点

## 关键约束

- .env 中的 API Key 不要提交到 git
- LLM 调用尽量合并，目标 ≤3 次/完整对话
- 端口 9191（8000 和 8080 被占用）
- XHS 爬取需新 Chrome profile + 慢速率（4-8 秒间隔）
