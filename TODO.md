# TODO — 旅行规划 Agent 升级

> 基于《旅行规划Agent调研报告》(2026-04-15) 的落地计划

## Phase 1 — 核心体验升级（P0）✅

- [x] 创建 CLAUDE.md / TODO.md
- [x] Session 新增 traveler_type 字段 (`api/session.py`, `api/models.py`)
- [x] 新建 `api/prompts.py` — 五套 Prompt（Profiler/Collector/Ranker/Architect/Followup）
- [x] 重写 `api/agent.py` — Profiler 画像识别 + 类型化偏好 + Architect 行程编排
- [x] ��并 LLM 调用链（6次→3次：Profiler合并信息提取+画像、Collector自带[READY]判断、并行数据获取）

## Phase 2 — 速度 + 排序优化 ✅

- [x] `pipeline/ranker.py` 接入旅行���类型化权重（traveler_type 参数 + build_ranker_system）
- [x] 美食 + 天气并行获取（ThreadPoolExecutor in agent.py）
- [x] 删除 judge_enough 独立 LLM 调用，合并到 Collector [READY] 机制

## Phase 3 — 体验增强（部分完成）

- [x] 行程输出增加备选方案（alternatives）— models + architect prompt 已支持
- [x] 行程输出增加每日预算估算 — daily_budget_estimate 字段 + 格式化
- [x] 打包建议 + 预约提醒 — packing_tips / booking_reminders
- [ ] 餐饮���据增强（推荐菜品/人均/预约）— 需高德或大众点评额外数据
- [ ] 前端景点卡片展示增强

## Phase 4 — 部署上线

- [ ] 阿里云部署（Docker 或 uvicorn + systemd）
- [ ] 行程导出（图片/PDF）
- [ ] 用户注册 / 行程保存
- [ ] 清理 mediacrawler/ 目录
