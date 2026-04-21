# travel-agent · 项目蓝图

> **版本**:v0.1 · 2026-04-19 · **作者**:banz
> **文档定位**:一体化的调研报告 + 架构设计 + 执行计划。
> 把所有讨论过的决定沉淀到这里,后续所有 PR、Issue、评审都以此文为基准。

---

## 零、执行摘要(TL;DR)

| 维度 | 结论 |
|---|---|
| **做什么** | 国内旅行规划 Web 产品,双模式(快捷发现 / 定制规划),AI 原生 |
| **差异化** | 高德 POI + 小红书 UGC + Exa 实时搜索 三源融合;类型化人群画像;Anchor & Orbit 地理聚类排期 |
| **技术底** | 单 Agent(LLM function-calling)+ 12 个原子工具,qwen-plus 主模型,qwen-turbo 轻量模型 |
| **栈** | FastAPI + SQLite + Redis + Docker + 阿里云 ECS,前端 SSR HTML + Alpine.js |
| **时间** | 3 周 MVP 上线。五一前**不追求产品完整**,作者本人手动用完成冷启动校准 |
| **首期不做** | 支付 / 预订 / 社交 / 海外城市 / 多语言 / 移动原生 App |
| **北极星** | 上线 1 个月内 30 人次使用,行程合理性评分 ≥ 4.0/5 |

---

## 一、项目定位

### 1.1 为什么做

现有旅行规划工具的三个痛点:

1. **小红书攻略碎片化** — 一个行程需要拼 10+ 篇笔记,散落不成体系
2. **GPT 裸问不落地** — 没有实时数据、不懂本地口碑、路线可能完全不合理
3. **传统 OTA 商业化重** — 携程/飞猪的核心是卖机酒,"规划"是引流工具,质量不是第一优先级

作者动机:

- **短期**:五一自用 + 给女友省心
- **中期**:面试/简历的差异化项目,跟"LLM + if-else 工作流"拉开距离
- **长期**:如果数据验证 OK,扩成订阅制或 affiliate 佣金

### 1.2 北极星指标(按阶段)

| 阶段 | 指标 | 时间 |
|---|---|---|
| 冷启动 | 作者用系统生成的行程顺利走下五一全程 | 2026-05-04 之前 |
| 上线首月 | 30 人次使用,行程合理性 ≥ 4.0/5(LLM-as-judge + 自评) | 2026-05-31 |
| 三个月 | 100 DAU,7 日回访率 ≥ 15% | 2026-07-31 |
| 半年 | 自然搜索流量 500/天 或 affiliate GMV 1 万/月 | 2026-10-31 |

### 1.3 明确不做的(Scope Cuts)

剪枝清单,写在这里是为了**拒绝诱惑**:

- ❌ 机票/酒店实时比价(跳转携程/Booking)
- ❌ 支付、预订、客服
- ❌ UGC 社区、评论、点赞
- ❌ 海外城市(首期只做国内 top 30 热门)
- ❌ 多语言(只做中文)
- ❌ 移动原生 App(响应式 Web 即可)
- ❌ 多人协作编辑行程
- ❌ 财务报销、发票
- ❌ 个性化推送、邮件营销

---

## 二、市场与竞品

### 2.1 玩家格局(2026 Q1 观察)

| 玩家 | 定位 | 优势 | 短板 |
|---|---|---|---|
| **携程/飞猪** | 交易型 OTA | 库存、比价、售后 | 规划弱,推销重 |
| **马蜂窝** | 内容+攻略社区 | 攻略深度 | 产品化差、广告多、AI 慢 |
| **小红书** | UGC 生活社区 | 真实口碑、达人密度 | 碎片化、凑齐一个行程费劲 |
| **GPT / 通义** | 通用 LLM | 语言灵活 | 无实时数据、路线离谱、不懂本地 |
| **旅行博主定制** | 高端服务 | 一对一、品质高 | 贵(500-2000)、慢(1-3 天)、不可 scale |
| **抖音生活服务** | 本地团购 | 价格 | 无规划能力 |
| **我们** | AI 原生规划 | 融合三源、类型化、结构化产出 | 冷启动、无流量 |

### 2.2 差异化叙事(一句话)

> **"把小红书的真实口碑、高德的精准坐标、大模型的行程智能,编排成一张可以照着走的日程表。"**

支撑点(都可以展开讲):

- **Anchor & Orbit 排期模型** — 每天一个锚点景点,周边串联
- **六类人群画像** — 情侣/亲子/银发/穷游/文化/美食,类型化权重
- **真实口碑融合** — 不是空想,每个推荐能点到 XHS 原帖
- **一天一块地理聚类** — K-means 分簇,拒绝来回折返

### 2.3 为什么现在做

- LLM function-calling 在 2025 年成熟,成本下降到做消费级应用可行
- 国内 XHS 成为旅游决策第一信息源(超过马蜂窝/携程攻略),数据价值最高
- 五一、暑期、十一三波强周期性需求,验证节奏清晰

---

## 三、用户画像与核心场景

### 3.1 六类旅行者(沿用)

| 代号 | 人群 | 核心偏好 | 关键约束 |
|---|---|---|---|
| A | 情侣/闺蜜打卡 | 出片、网红地、氛围感 | 餐厅排队可忍,丑景点不去 |
| B | 家庭亲子 | 安全、儿童友好、节奏慢 | 一天 3 点上限、午休时间 |
| C | 银发/孝心 | 无障碍、文化、舒适 | 走路少、早睡、医院近 |
| D | 预算穷游 | 免费/低价、性价比 | 严格预算、青旅接受度高 |
| E | 深度文化 | 历史、博物馆、本地沉浸 | 愿意坐车远、宁精不博 |
| F | 美食优先 | 围绕餐厅排景点 | 排队 1h 以内、预订可提前 |

### 3.2 首要三个场景(产品为此而设计)

**场景 1 · 说走就走(快捷版主要场景)**
- 画像:A / F,25-35 岁
- 触发:"周五突发奇想,周六就想出发"
- 核心需求:**2 分钟内给我一个靠谱清单**,不要问太多,能直接跟着走
- 产出:卡片流(8-12 张) + 一键生成 1-2 日行程

**场景 2 · 清单定制(定制版主要场景)**
- 画像:全类型,尤其 A / B
- 触发:"小红书刷了几十篇,有 20 个想去的点,怎么排?"
- 核心需求:**按区域分簇,一天一块不折返**
- 产出:Day 1~N 时间轴 + 地图 + 酒店推荐

**场景 3 · 深度定制(重度用户,P1)**
- 画像:E / B
- 触发:"3-7 天跨城市行,必须把每个点都研究透"
- 核心需求:**细节充分,可编辑、可回滚**
- 产出:富文本行程 + 可拖拽编辑 + 导出 PDF

### 3.3 非场景(不服务)

- 商务差旅(订机酒+会议,是 OTA 强项)
- 单日 Citywalk(时间尺度太小)
- 团队游(20 人以上,需要组织、拼车、集合)

---

## 四、产品设计

### 4.1 双模式架构

```
首页(Landing)
├─┐
│ └─ [快捷版 · 说走就走]      ← 主要触达场景 1
│     ├─ 输入:城市 + 可选(天数、同行人)
│     ├─ 展示:8-12 张景点/餐厅卡片,真实口碑摘要
│     ├─ 交互:滑动收藏 ❤,攒 3-5 个触发"一键生成行程"
│     └─ 出口:跳转到定制版的行程生成
│
├─ [定制版 · 我心里有数]      ← 主要触达场景 2
│   ├─ 表单:城市 / 日期 / 人数 / 预算 / 必去清单(自由输入或粘贴)
│   ├─ Profiler:识别人群类型(可选追问 1 轮)
│   ├─ Agent:Tool-use loop 生成行程初稿
│   ├─ 展示:Day 1-N 时间轴 + 高德地图 + 酒店推荐
│   └─ 交互:每个卡片可替换/删除,局部 regen
│
└─ [我的](P2)
    ├─ 历史行程
    ├─ 收藏
    └─ 设置
```

### 4.2 关键用户流

**快捷版(首次访问):**

```
输入"深圳" → 2s 加载(命中预爬缓存) → 卡片流
 → 用户左滑/右滑 → 收藏 3 个 → 弹窗"生成行程?"
 → 跳定制版(带上收藏项作为必去清单)
```

**定制版(明确需求):**

```
填表单 → "生成中"进度条(拆步骤显示)
  1. 理解你的偏好...(Profiler)
  2. 搜集景点和口碑...(parallel tools)
  3. 按区域分组...(cluster)
  4. 编排日程...(propose + critique)
 → 行程展示 → 用户点"第 2 天咖啡馆换一个" → 局部 regen(保留其它)
```

### 4.3 关键页面清单

| 页面 | 路由 | 核心组件 |
|---|---|---|
| 首页 | `/` | 城市输入框、两个模式按钮、3 张 demo 截图 |
| 快捷版 | `/explore?city=xxx` | 卡片流、收藏栏、悬浮 CTA |
| 定制表单 | `/plan/new` | 多步表单、偏好识别、必去清单粘贴框 |
| 生成中 | `/plan/:id/generating` | 流式进度、tool 调用可视化 |
| 行程详情 | `/plan/:id` | 时间轴、地图、酒店、导出按钮 |
| 我的(P2) | `/me` | 历史 + 收藏 |

---

## 五、技术架构

### 5.1 分层总图

```
┌──────────────────────────────────────────────────────────┐
│  Frontend (web/)                                         │
│  • 模板:Jinja2 SSR(首屏快)+ Alpine.js 交互              │
│  • 地图:高德 JS SDK                                       │
│  • 导出:html2canvas(前端截图即可,不搞 PDF 后端)         │
└──────────────────────────────────────────────────────────┘
                          ↓ HTTP / SSE
┌──────────────────────────────────────────────────────────┐
│  BFF — FastAPI (api/)                                    │
│  • 路由:/explore /plan /chat /hotel /export               │
│  • 鉴权:phone + code(阿里云短信,P1)或 anon cookie        │
│  • 限流:slowapi(10 req/min 无登录;100 有登录)             │
│  • 日志:loguru + 结构化 JSON                              │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│  Agent Orchestrator (agent/)                             │
│  • 主循环:LLM → tool_calls → observations → ...            │
│  • 上下文管理:摘要压缩(>6k token 触发)                    │
│  • 错误处理:单 tool 3 次重试,失败告诉 LLM 绕路             │
│  • 观测:每次 tool 调用落盘 tool_call_log                  │
└──────────────────────────────────────────────────────────┘
     ↓                ↓                ↓              ↓
┌──────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐
│ Tools 12 │  │ Data Layer │  │ LLM Layer  │  │ Storage  │
│ 见 §6.2  │  │ AMap API   │  │ qwen-plus  │  │ SQLite   │
│          │  │ XHS 预爬   │  │ qwen-turbo │  │ Redis    │
│          │  │ Exa        │  │            │  │ FS(JSON) │
│          │  │ 高德天气   │  │            │  │          │
└──────────┘  └────────────┘  └────────────┘  └──────────┘
```

### 5.2 技术栈选型

| 层 | 选型 | 版本 | 替代 | 理由 |
|---|---|---|---|---|
| Python | CPython | 3.12+ | — | 3.14 有些 lib 不兼容,回 3.12 稳 |
| Web | FastAPI | 0.115+ | Flask/Django | 现有、异步、OpenAPI 自动 |
| Template | Jinja2 | — | React | MVP 不搞 SPA,SSR 首屏快 |
| 前端交互 | Alpine.js | 3.x | Vue/React | 零构建、单文件友好 |
| DB | SQLite | 3.40+ | PG | 100 DAU 之前够用 |
| 缓存 | Redis | 7 | memcached | 支持复杂结构 |
| LLM 主 | qwen-plus | — | GPT-4/Claude | 国内合规,function-calling 稳 |
| LLM 轻 | qwen-turbo | — | — | 清洗等轻任务 1/10 价 |
| 爬虫 | DrissionPage | 4.1+ | Playwright | 现有,反爬稍弱 |
| 容器 | Docker | 24+ | — | 标配 |
| 部署 | 阿里云 ECS 2C4G | — | 腾讯云 | 国内备案方便 |
| 反代 | Nginx | 1.24 | Caddy | 熟悉度 |
| HTTPS | Certbot | — | — | 免费 |
| 监控 | loguru + 自写看板 | — | Sentry/LangFuse | 先简单,后面再接 |

---

## 六、Agent 设计

### 6.1 架构决策:单 Agent vs Multi-Agent

**首期用单 Agent + 原子工具集**,理由:

- Multi-agent(Researcher/Planner/Critic)虽然有叙事价值,但工程复杂度 3x,调试难 5x
- 单 Agent 在 12 个工具的规模下完全能胜任旅行规划
- 上线后如果行程质量分 < 4.0,再升级为 Planner+Critic 的 dual-agent

### 6.2 工具清单(12 个,按职能分组)

| # | 工具 | 入参 | 出参 | 底层 | 缓存 TTL |
|---|---|---|---|---|---|
| **检索类** |
| 1 | `search_poi` | city, category, keyword? | POI[] | 高德 | 7d |
| 2 | `search_hotel` | city, area, price_range | Hotel[] | 高德 + XHS | 3d |
| 3 | `search_xhs_notes` | query, topk | Note[] | 预爬 JSON | 不缓存(已是离线) |
| 4 | `web_search` | query | Snippet[] | Exa | 24h |
| 5 | `get_weather` | city, date_range | Forecast | 高德天气 | 6h |
| **计算类** |
| 6 | `cluster_pois` | POI[], k | 分簇 POI[][] | K-means(纯 Python) | 不缓存 |
| 7 | `route_matrix` | POI[] | 距离/时间矩阵 | 高德路径规划 | 永久 |
| 8 | `detect_crowd` | POI, date | 1-5 级 | 规则+节假日表 | 1d |
| **规划类** |
| 9 | `propose_itinerary` | POI[], days, constraints | Itinerary | LLM | 不缓存 |
| 10 | `critique_itinerary` | Itinerary | Issue[] | 规则 + LLM | 不缓存 |
| 11 | `revise_itinerary` | Itinerary, Issue[] | Itinerary | LLM | 不缓存 |
| **交付类** |
| 12 | `generate_map_link` | POI[] | 高德 URL | — | 不缓存 |

### 6.3 工具设计三原则

1. **原子化** — 一个工具只做一件事。不要写 `plan_trip(everything)` 这种上帝工具。
2. **幂等 + 可缓存** — 入参相同就命中缓存。所有工具返回结构固定的 `{ok, data, error}`。
3. **失败友好** — 工具失败不抛异常,返回 `{ok: false, error: "..."}`,由 Agent 决定重试或绕路。

### 6.4 决策循环

```python
# 伪代码
while True:
    response = llm.complete(messages, tools=TOOLS)
    if response.tool_calls:
        results = parallel_execute(response.tool_calls)  # 并行
        messages.append(response)
        messages.extend(results)
    else:
        return response.content  # 最终回答
    if len(messages) > CONTEXT_LIMIT:
        messages = summarize_and_compress(messages)
```

### 6.5 记忆分层

| 层 | 存储 | 内容 | 生命周期 |
|---|---|---|---|
| 会话 | Redis | 当前对话的 messages | 24h |
| 偏好 | SQLite `users.preferences` | 识别出的类型、常用约束 | 永久,可更新 |
| 行程历史 | SQLite `trips` | 过往生成的所有行程 | 永久 |
| 工具缓存 | Redis + 磁盘 | 见 6.2 TTL 列 | 见上 |

### 6.6 Prompt 策略

- **主 System Prompt**:500 token 以内,分段放置。包含:角色、可用工具摘要、输出格式、错误处理原则
- **Tool 描述标准化**:每个工具 description 不超过 2 行,重点说"什么场景用、不该用在什么场景"
- **输出结构化**:最终行程输出用 Pydantic 校验,LLM 不按格式就强制重试
- **一次只说一种语言**:所有 Prompt 中文,降低混语噪声

---

## 七、数据源方案

### 7.1 高德 POI / 酒店 / 天气(已接)

- **Key**:Web 服务 API,免费 3000/日,升级到 30w/日 ~¥500/月
- **POI 类型码**:景点 `110000`,餐饮 `050000`,住宿 `100000`
- **坐标系**:高德 GCJ-02(火星坐标),自己用 OK,跟 Google Maps 对接要转
- **限速**:5 req/s,业务层自己限

### 7.2 小红书 UGC(已接)

- **方案**:DrissionPage 浏览器自动化,非官方 API
- **频率**:4-8 秒/条,绝对不能加速,不然封号秒
- **预爬策略**:top 30 城市预先爬好放 JSON,首次查询走预爬,不足再增量
- **清洗**:qwen-turbo LLM 清洗,提取"景点名 + 核心评价",缓存到 `data/cleaned/`
- **法律**:仅用于聚合摘要,不直接呈现原文,点击跳转原帖

### 7.3 Exa(已接)

- **用途**:兜底搜索,当高德 + XHS 都找不到时补实时攻略
- **价格**:$0.0065/次,控制调用,放到最后

### 7.4 酒店(新增)

- **不做美团 API**(个人搞不到,企业账号要对接繁琐)
- **方案**:高德 POI(类型=100000) + XHS 口碑交叉 → 生成携程跳转链接
- **跳转 URL 模板**:`https://hotels.ctrip.com/hotels/list?city={cityid}&checkin={}&checkout={}&keyword={}`
- **P2 考虑**:申请 Booking.com Affiliate(境外为主)或 Amadeus Sandbox

### 7.5 节假日人流表(自维护)

```json
{
  "世界之窗_深圳": {
    "weekday": 2,
    "weekend": 4,
    "holiday_5.1": 5,
    "holiday_5.2": 5,
    "holiday_5.3": 5
  }
}
```

- 手动维护 top 30 城市 top 20 景点的基础表
- 基于历史数据 + 官方公告 + XHS 舆情更新
- 季度更新一次

---

## 八、数据模型

### 8.1 核心表(SQLite DDL 摘要)

```sql
-- 用户(P1 才启用)
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  phone TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  preferences JSON  -- {traveler_type, constraints, ...}
);

-- 会话(一次聊天)
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,  -- uuid
  user_id INTEGER NULL,  -- 允许匿名
  city TEXT,
  state TEXT,  -- INIT/COLLECTING/GENERATING/DONE
  messages JSON,  -- 完整对话历史
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- 行程(最终产物)
CREATE TABLE trips (
  id TEXT PRIMARY KEY,
  user_id INTEGER NULL,
  session_id TEXT,
  city TEXT,
  start_date DATE,
  end_date DATE,
  traveler_type CHAR(1),
  itinerary JSON,  -- 完整结构化行程
  created_at TIMESTAMP
);

-- 收藏(快捷版用)
CREATE TABLE favorites (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NULL,
  anon_key TEXT,  -- 未登录用 cookie key
  poi_id TEXT,
  poi_snapshot JSON,
  created_at TIMESTAMP
);

-- 工具调用日志(观测 + 调试)
CREATE TABLE tool_call_log (
  id INTEGER PRIMARY KEY,
  session_id TEXT,
  tool_name TEXT,
  args JSON,
  result_status TEXT,  -- ok/error
  duration_ms INTEGER,
  cost_tokens INTEGER,
  created_at TIMESTAMP
);

-- 工具结果缓存(持久层,Redis 之下的 fallback)
CREATE TABLE tool_cache (
  key TEXT PRIMARY KEY,  -- sha256(tool_name + args_json)
  tool_name TEXT,
  result JSON,
  expires_at TIMESTAMP
);
```

### 8.2 行程 JSON 结构(关键输出格式)

```json
{
  "trip_id": "uuid",
  "city": "深圳",
  "days": [
    {
      "day": 1,
      "date": "2026-04-29",
      "theme": "到达 + 海边慢启动",
      "blocks": [
        {
          "time": "14:00-17:00",
          "poi": {"name": "深圳湾公园", "lat": ..., "lng": ...},
          "activity": "骑行/散步",
          "duration_min": 180,
          "xhs_quotes": ["笔记摘要1", "笔记摘要2"],
          "warnings": []
        }
      ],
      "est_walking_meters": 8000,
      "est_cost_cny": 300
    }
  ],
  "hotel_recommendations": [...],
  "packing_tips": [...],
  "booking_reminders": [...],
  "total_budget_est": 5040,
  "weather_note": "..."
}
```

---

## 九、API 契约(核心 5 个端点)

### 9.1 Endpoints

```
POST  /api/explore            快捷版:给城市,回卡片流
POST  /api/plan/new           定制版:启动一次规划会话
POST  /api/plan/{id}/chat     聊天接口(SSE)
GET   /api/plan/{id}          取行程
POST  /api/plan/{id}/regen    局部重新生成
GET   /api/hotel              酒店推荐
POST  /api/favorite           收藏 POI
GET   /api/trips              我的历史(P1)
```

### 9.2 `/plan/new` schema 示例

**Request**
```json
{
  "city": "深圳",
  "start_date": "2026-04-29",
  "end_date": "2026-05-04",
  "people": 2,
  "traveler_type_hint": "A",
  "budget_cny": 4000,
  "must_visit": ["深圳湾公园", "世界之窗", "..."],
  "constraints": {
    "wake_up_time": "10:00",
    "max_daily_walk_meters": 10000,
    "transport": ["subway", "taxi"],
    "hotel_area": "南山|宝安"
  }
}
```

**Response (SSE stream)**
```
event: stage
data: {"stage": "profiling", "msg": "识别你的偏好..."}

event: stage
data: {"stage": "searching", "msg": "搜集景点和口碑..."}

event: tool_call
data: {"tool": "search_poi", "args": {...}}

event: itinerary
data: { 完整 JSON 同 §8.2 }

event: done
data: {"trip_id": "uuid"}
```

---

## 十、前端设计

### 10.1 目录结构

```
web/
├── static/
│   ├── css/       (tailwind 单文件)
│   ├── js/        (Alpine 组件)
│   └── img/
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── explore.html
│   ├── plan_new.html
│   ├── plan_detail.html
│   └── components/
│       ├── poi_card.html
│       ├── day_timeline.html
│       └── map_embed.html
└── README.md
```

### 10.2 关键组件

- **POI 卡片**:封面图 + 名称 + 评分 + XHS 口碑 1 句 + 收藏按钮
- **日程时间轴**:左时间轴 + 右活动块 + 块内可替换/删除
- **地图嵌入**:高德 JS SDK,标记 + 路线,点击标记跳到对应 day
- **进度流**:SSE 驱动的步骤指示器,显示当前在做什么
- **导出**:html2canvas 截图 PNG,右键可存

### 10.3 状态管理

Alpine.js `x-data` 局部状态 + `fetch` + `EventSource`(SSE),不引入 Redux/Zustand。

---

## 十一、部署运维

### 11.1 环境

| 环境 | 用途 | 域名 | 成本 |
|---|---|---|---|
| local | 开发 | `localhost:9191` | 0 |
| staging | 内测 | `staging.yourdomain.xyz` | ECS 2C4G ¥65/月 |
| prod | 生产 | `yourdomain.xyz` | 同上或升 4C8G |

**单服务器部署够用到 500 DAU,不要过早上 k8s**。

### 11.2 Docker Compose

```yaml
services:
  app:
    build: .
    ports: ["9191:9191"]
    env_file: .env
    volumes: ["./data:/app/data"]
    depends_on: [redis]
  redis:
    image: redis:7-alpine
    volumes: ["redis-data:/data"]
  nginx:
    image: nginx:alpine
    ports: ["80:80", "443:443"]
    volumes: ["./nginx.conf:/etc/nginx/nginx.conf"]
volumes:
  redis-data:
```

### 11.3 CI/CD(简单版)

- GitHub Actions:push 到 main 自动跑 pytest + ruff
- 发布:人工 ssh 到 ECS `git pull && docker compose up -d --build`
- **先不搞全自动部署**,上线频率低,人工可控

### 11.4 监控(MVP)

- loguru 写 `logs/app.log`,loglevel INFO
- 每 5 分钟统计一次"今日 plan 数 / 错误数 / LLM 成本",落 SQLite `stats_daily`
- 简单 `/admin/dashboard` 页展示曲线(自己看)
- **P1 再接 Sentry**

### 11.5 成本预估

| 项 | 月成本 |
|---|---|
| ECS 2C4G | ¥65 |
| 域名 .xyz | ¥10 |
| 短信(P1) | ¥50(按量,100 注册) |
| qwen-plus(20k token/plan × 100 plan) | ¥30 |
| qwen-turbo(清洗) | ¥10 |
| Exa | $5 ≈ ¥35 |
| 高德(免费额度够) | 0 |
| **合计** | **~¥200/月** |

100 DAU 以内月成本 ¥200 以内,可接受。

---

## 十二、执行计划(3 周 MVP)

### Phase 0 · 准备(Day 0-1)

- [ ] 合并 `main.py` 和 `api/`:只留 FastAPI 入口
- [ ] 写 `README.md`(一张架构图 + 启动方式)
- [ ] 补 `requirements.txt`(pydantic, loguru, slowapi, redis, sqlalchemy)
- [ ] 建 SQLite schema,跑一次 migration 脚本
- [ ] Git 提交整理,分支 `main`(稳定) / `dev`(开发)

### Phase 1 · Agent 核心重构(Week 1,Day 2-7)

- [ ] `agent/tools/` 目录:12 个工具独立文件
- [ ] `agent/core.py`:tool-use loop,支持并行、重试、摘要压缩
- [ ] `agent/schemas.py`:Pydantic 定义所有工具入参、出参
- [ ] 替换现有 `api/agent.py` 状态机
- [ ] 单测:每个工具都有 `test_<tool>.py`,pytest 跑通
- [ ] 本地能完整跑通"深圳 4.29-5.4"的完整定制版 → 出合理 JSON

**验收**:用 curl 打 `/api/plan/new`,5-15 秒内返回结构化 JSON,路线不折返

### Phase 2 · 前端 + 快捷版(Week 2,Day 8-14)

- [ ] 首页、快捷版、定制表单、行程详情四个模板
- [ ] 高德 JS SDK 地图嵌入组件
- [ ] SSE 流式生成 UI(stage 指示器)
- [ ] 局部 regen("第 2 天咖啡馆换一个")
- [ ] html2canvas 导出
- [ ] 酒店模块接入(高德 + XHS + 携程跳转)
- [ ] 深圳、上海、成都、杭州 4 城预爬 + 预清洗

**验收**:手机浏览器打开,完整走完"快捷版 → 收藏 → 生成定制行程 → 导出图" 全流程

### Phase 3 · 上线(Week 3,Day 15-21)

- [ ] Dockerfile + docker-compose.yml
- [ ] Nginx conf + Certbot HTTPS
- [ ] 阿里云 ECS 买、域名买、备案启动(备案慢,提前做)
- [ ] prod 部署,健康检查通过
- [ ] 基础监控 dashboard(plan 数 / 错误率 / 成本)
- [ ] 5 个朋友内测,收 bug,修关键的 3 个
- [ ] 作者本人五一实际用(两个目的:自测 + 内容产出素材)

**验收**:域名可访问 HTTPS,内测用户完整走完不崩

### Phase 4 · 五一后优化(Week 4+)

- [ ] 根据五一真实使用反馈,排 P0 问题
- [ ] 补 README + demo GIF(给面试/社交媒体用)
- [ ] 写一篇技术博客:Anchor & Orbit 模型 + tool-use 工程实践
- [ ] P1 特性:用户登录、历史行程
- [ ] P2 特性:多 agent Critic、向量化 XHS、更多城市

---

## 十三、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| XHS 爬虫被封 | 高 | 中 | 降频 + 多账号 + 预爬缓存厚度 |
| 五一前 ICP 备案下不来 | 中 | 高 | 先上 IP 地址 + 境外 VPS 测试 |
| LLM 成本跑飞 | 低 | 中 | 工具缓存 + 成本看板 + 每会话上限 |
| 行程质量不稳定 | 中 | 高 | 单测每工具 + 手工 20 query eval 集 |
| 高德免费额度用完 | 中 | 低 | 付费升级 ¥500/月,或换腾讯地图 |
| 作者五一实际用得不爽 | 中 | 高 | **这是最重要的信号,马上迭代** |
| 竞品突然发力(携程做 AI 规划) | 低 | 中 | 差异化叙事(UGC 融合+本地人群画像) |

---

## 十四、成功指标(如何知道做成了)

### 14.1 工程指标

- [ ] API P95 延迟 < 20s(定制版生成)
- [ ] API P95 延迟 < 3s(快捷版)
- [ ] 工具调用平均数 < 8(单次 plan)
- [ ] 工具缓存命中率 > 60%
- [ ] 月故障时间 < 2h

### 14.2 产品指标

- [ ] 周活用户 ≥ 30(首月)
- [ ] 行程完成率(生成 → 查看 → 导出) ≥ 50%
- [ ] 局部 regen 使用率 ≥ 20%(表示行程不够好,用户在调)
- [ ] 7 日回访率 ≥ 15%(三个月目标)

### 14.3 质量指标(eval)

维护一个 20 条典型 query 的 eval 集,每发布前跑:

```
examples = [
  "情侣 3 天成都 3000 预算",
  "亲子 4 天苏州 7000 预算 含 2 名儿童",
  "银发 5 天杭州 5000 预算 爸妈 70 岁膝盖不好",
  "穷游 7 天大理 2500 预算 青旅",
  "美食 2 天广州 2000 预算",
  ...
]
```

- **路线合理性**(LLM-as-judge 1-5 分) ≥ 4.0
- **偏好匹配度** ≥ 4.2
- **预算符合度** ≥ 4.3
- **行程信息完整度** ≥ 4.0

---

## 十五、附录

### 15.1 环境变量

```bash
# .env
AMAP_API_KEY=xxx
AMAP_WEB_JS_KEY=xxx   # 前端用,安全域白名单
DASHSCOPE_API_KEY=sk-xxx
EXA_API_KEY=xxx

REDIS_URL=redis://localhost:6379/0
DATABASE_URL=sqlite:///./data/app.db

APP_ENV=local|staging|prod
APP_PORT=9191
LOG_LEVEL=INFO

# 限流(每 IP)
RATE_LIMIT_ANON=10/min
RATE_LIMIT_USER=100/min

# 成本控制
MAX_TOKENS_PER_SESSION=30000
MAX_TOOL_CALLS_PER_SESSION=25
```

### 15.2 常用命令

```bash
# 开发
source venv/bin/activate
PYTHONPATH=. uvicorn api.main:app --reload --port 9191

# 测试
pytest -v
pytest --cov=. --cov-report=html

# 预爬
python scripts/precrawl.py --cities 深圳,上海,成都,杭州

# Docker 本地
docker compose up --build

# 部署
ssh prod "cd /srv/travel-agent && git pull && docker compose up -d --build"
```

### 15.3 参考资源

- 高德 Web 服务 API:https://lbs.amap.com/api/webservice/summary
- 高德 JS SDK:https://lbs.amap.com/api/jsapi-v2/summary
- 通义千问 API:https://help.aliyun.com/zh/model-studio/
- Exa:https://docs.exa.ai/
- FastAPI:https://fastapi.tiangolo.com/
- Anthropic Tool-use 设计(通用可参考):https://docs.anthropic.com/en/docs/build-with-claude/tool-use

### 15.4 人群类型到工具权重映射(参考 `pipeline/ranker.py`)

| 类型 | POI 类型权重 | 节奏 | 典型约束 |
|---|---|---|---|
| A 情侣 | 景点 1.0, 餐厅 0.8, 网红 1.2 | 中等 | 晚起、不赶路 |
| B 亲子 | 景点 1.0, 公园 1.2, 儿童 1.5 | 慢 | 2-3 点/天、午休 |
| C 银发 | 景点 1.0, 文化 1.3, 医疗近 1.1 | 慢 | 走路少、早回酒店 |
| D 穷游 | 景点 0.7, 免费 1.5, 青旅 1.0 | 快 | 严控预算 |
| E 文化 | 博物馆 1.5, 历史 1.3, 景点 0.8 | 深 | 愿意远、宁精不博 |
| F 美食 | 餐厅 1.5, 景点 0.6 | 中等 | 围绕餐厅排 |

### 15.5 Changelog

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-04-19 | v0.1 | 初版,基于多轮规划讨论整合 |

---

**文档维护**:每次重大决策变更在这里更新,不要让 BLUEPRINT 和代码/现实脱节。
