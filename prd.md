# 智能旅行规划系统 PRD

**文档版本：** v5.2
**更新依据：** 当前仓库实现（截至 2026-06-26，commit 15da048）
**系统定位：** 面向多日城市游的智能旅行规划助手，通过自然语言意图解析、地图事实召回、时变路径求解和预算校验生成可解释行程。

---

## 1. 产品目标

本项目不是单纯的聊天式行程推荐，而是一个“LLM + 地理事实 + 运筹求解”的混合系统：

- LLM 负责自然语言意图解析和最终文案润色。
- 高德/MCP/本地 fallback 负责 POI、酒店、天气、交通矩阵等事实来源。
- 后端 Python 算法负责路线排序、时间窗约束、预算计算、预算修复与质量指标。
- 前端提供可操作工作台，展示地图、预算、路线、天气、求解事件和导出结果。

当前系统已经具备可演示的完整链路；生产级实时价格、拖拽重规划、无头 PDF/PNG 导出仍属于后续增强。

---

## 2. 当前完成度

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 自然语言意图解析 | 已实现 | 支持 55+ 个中文/英文城市、中文数字/阿拉伯数字天数、预算抽取、偏好标签归一化、时间窗抽取；LLM 优先，规则兜底。 |
| 城市与偏好词表 | 已实现 | `intent_agent.py` 的城市和关键词已同步到 `geo_fact_service.py`，高德查询使用同一套语义。 |
| Map 多 Agent | 已实现 | 并发获取 finance、hotel、attraction、weather 四类上下文；通过 `provider_adapters.py` 的 Protocol-based 注入机制，支持 Local 和 Amap 两种 Provider 实现。 |
| 高德 REST API 直连 | 已实现 | `amap_service.py` 支持 place/text、weather/weatherInfo、direction/driving/transit/walking；搜索按多个 keyword 均衡召回。 |
| 高德/MCP 接入 | 已实现 | 支持外部 MCP HTTP endpoint、官方高德工具名兜底、项目内开发 MCP in-process 或 HTTP 模式。 |
| 路线矩阵 | 已实现 | 支持按小时矩阵、Driving/Transit/Walking 成本；真实高德方向工具可用时读取距离、时长、费用和道路 polyline。 |
| 路线求解 | 已实现 | 容量感知 K-Means 风格聚类 + 日内 NSGA-II 候选序列求解（种群 32，代数 14，突变率 0.22）。 |
| 预算评估与修复 | 已实现 | 统计门票、交通、餐饮、住宿；超预算时剪除低性价比付费 POI 并重新求解；包含 POI 替代策略（同品类更便宜替换）。 |
| 天气约束 | 已实现 | 雨雪高温等天气可转成室外规避约束，并展示每日天气。 |
| WebSocket 流式事件 | 已实现 | 推送阶段完成、节点事件和最终 `TripState`；尚非真实高频 epoch 内循环流。 |
| 局部重规划 | 部分实现 | 后端支持 cheapest insertion 单点插入；前端目前是固定按钮插入示例 POI，尚无拖拽。 |
| 前端工作台 | 已实现 | 支持解析、规划、流式规划、地图、预算、路线、天气、事件、求解质量监控、任务列表（Job 轮询）和 HTML/PDF/PNG 导出。 |
| 地图渲染 | 已实现 | 前端使用高德 JSAPI 2.0，后端生成简化几何线；高德方向服务返回 polyline 时使用真实道路几何，缺失时使用插值兜底。Douglas-Peucker 简化。 |
| PDF/PNG 导出 | 已实现 | weasyprint（PDF）和 playwright（PNG）可选依赖；缺失时生成占位文件兜底。可注入前端地图快照（html2canvas）。 |
| HTML 导出 | 已实现 | 内联 SVG 预算图表 + SVG 路线地图 + 每日表格。 |
| Job 持久化 | 部分实现 | JSONL 存储已实现；当前 Windows 权限环境下 `backend/data/jobs.jsonl` 可能写入失败。 |
| 集成探测 | 已实现 | `GET /health/integrations/probe` 分别验证高德和 LLM 连通性。 |
| 工作流拓扑 | 已实现 | `GET /api/trips/workflow/topology` 返回 Map-Compute-Reduce 节点与边。 |

---

## 3. 核心用户流程

1. 用户在前端 Request 输入自然语言，例如：`去长沙玩2天，预算800元，想吃美食和泡温泉`。
2. `IntentAgent` 将文本解析为 `IntentConstraints`：
    - `destination`
    - `days`
    - `budget_limit`
    - `preferences`
    - `time_window_baseline`
3. Map 阶段并行召回：
    - 候选 POI（按多个 preference 均衡召回）
    - 酒店锚点
    - 天气预报与约束
    - 城市消费/交通/住宿预算上下文
4. Compute 阶段构建时变矩阵并求解：
    - 按天聚类（容量感知 + 日均固定成本约束）
    - 每日路线优化（NSGA-II）
    - 时间窗/天气约束校验
    - 预算评估与必要时修复（含 POI 替代策略）
5. Reduce 阶段：
    - 上下文压缩节点将路线转为文字断言
    - Planner 节点生成叙述性行程
6. 前端展示地图、日程、交通费用、预算、天气、质量指标、fitness curve 和事件流。
7. 用户可选择导出为 HTML/PDF/PNG，支持地图快照注入。

---

## 4. 架构原则

### 4.1 LLM 边界

LLM 不直接产生坐标、距离、路线矩阵或预算总额。系统允许 LLM 做两件事：

- 将自然语言编译成结构化意图。
- 将已验证的路线和预算事实渲染成用户可读文案。

当 LLM 不可用、返回非 JSON、字段不合法或超时时，系统必须回退到规则解析或模板叙述，避免阻断主链路。

### 4.2 事实来源边界

POI、酒店、天气、交通矩阵优先来自高德/MCP；不可用时使用本地 fallback。

价格字段采用“事实优先，估算兜底”的策略：

- POI/酒店读取 `biz_ext.cost`、`price`、`avg_cost` 等字段。
- 驾车读取 `taxi_cost`、`tolls` 等字段。
- 公交换乘读取 `cost`、上下车站点和线路片段。
- 高德缺失或不覆盖的数据使用城市均值或本地默认值。

注意：高德 POI 消费字段不等于所有景点门票、酒店房态和实时成交价。生产级价格仍需接入专门票务/酒店/餐饮数据源。

### 4.3 Provider Adapter 模式

系统使用 Protocol-based 依赖注入（`provider_adapters.py`），将每个外部接口抽象为 Provider：

- `AttractionProvider` - POI 搜索
- `HotelProvider` - 酒店锚点
- `WeatherProvider` - 天气报告
- `FinanceProvider` - 消费上下文

根据 `TRIP_PROVIDER_MODE` 环境变量，自动选择 `Local*Provider` 或 `Amap*Provider` 实现。

### 4.4 错误处理边界

所有 API 端点继承统一的 JSON 错误响应（`error_handlers.py`）：

- `validation_error`（422）- 请求校验失败
- `trip_planner_error`（400）- 领域错误（MatrixBuildError, SolverTimeoutError, BudgetExceededError, InvalidPOIError）
- `internal_error`（500）- 未预期异常

---

## 5. 后端功能规格

### 5.1 IntentAgent

输入：`user_query`
输出：`IntentConstraints`

已实现能力：

- 55+ 个中文城市和英文城市别名。
- 中文数字/阿拉伯数字天数。
- 预算抽取（支持中英文模式）。
- 偏好标签归一化（museum, food, landmark, shopping, nature, nightlife, culture, history）。
- 时间窗抽取（支持中英文时间格式）。
- LLM 优先，规则兜底。

### 5.2 AttractionAgent

输入：`IntentConstraints.preferences` 和 `destination`
输出：`POICandidate[]`

要求：

- POI 候选必须包含坐标；无坐标节点不得进入求解。
- 多个 preference 需均衡召回，避免首个偏好填满候选集。
- intent 中的偏好关键词需同步到高德查询层。

当前实现：

- Amap MCP 工具优先。
- 高德 REST API 直连兜底。
- 本地 demo POI 兜底。

### 5.3 HotelAgent

输出路线起终点酒店锚点。

当前实现：

- 高德 REST API / MCP 酒店优先。
- 如果返回酒店消费字段，则作为住宿预算参考，反写 `FinancialContext.avg_hotel_nightly_cost`。
- 不可用时使用本地中心酒店锚点。

### 5.4 WeatherAgent

输出：

- `WeatherConstraint[]`
- `DailyWeatherForecast[]`

当前实现：

- 支持高德 REST API 和 MCP 天气。
- 雨雪雷暴、高温等会影响室外 POI。
- 不可用时生成 fallback 预报。

### 5.5 FinanceAgent

输出 `FinancialContext`：

- `currency`
- `exchange_rate`
- `base_transit_fare`
- `driving_rate_per_km`
- `avg_meal_cost`
- `avg_hotel_nightly_cost`

当前默认值：CNY, 1.0, 4.0, 2.6, 45.0, 80.0。

### 5.6 Health / Capabilities / Probe

- `GET /health`：返回 `{"status": "ok", "app": "Trip Planner", "version": "0.1.0"}`。
- `GET /health/capabilities`：返回运行时集成开关（不暴露密钥）。
- `GET /health/integrations/probe`：执行实时 smoke test，分别探测高德和 LLM 可用性。

### 5.7 HTTP Client

统一的 HTTP 客户端（`http_client.py`），支持：

- certifi CA bundle 默认校验。
- `TRIP_SSL_CA_FILE` 自定义 CA 文件。
- `TRIP_SSL_VERIFY=false` 本地开发跳过校验。
- 网络错误提示（ASN1/SSL/Certificate 友好消息）。

---

## 6. 算法规格

### 6.1 多日聚类

当前实现为容量感知的 K-Means 风格启发式聚类（`clustering.py`）：

- 每天有硬容量限制（`max_day_minutes`，默认 600 分钟 + 30 分钟通勤缓冲）。
- 支持日均固定成本预算约束（`max_day_fixed_cost` = budget_limit / days）。
- K-Means 迭代 6 轮，初始中心点在 POI 列表中均匀采样。
- 排序分配策略：耗时/高价值 POI 优先分配。

### 6.2 日内路线求解

每个日簇通过 NSGA-II 评估候选序列（`vrp_solver.py`）：

```text
fitness = utility - time_penalty - cost_penalty - skipped_penalty
```

参数：种群 32，代数 14，突变率 0.22。

约束：

- POI 营业时间。
- 每日开始/结束时间。
- 天气约束（avoid_outdoor）。
- 交通矩阵边（时变 24 小时离散切片）。

小规模（<=5 POI）使用穷举排列保证最优。

### 6.3 交通方式选择

支持：

- Walking
- Transit
- Driving

当前策略（`matrix_builder.py`、`amap_service.py`）：

- 短距离（<=1.2 km）优先步行。
- Driving 成本 > Transit 成本 × 2.2 且节省时间 <= 15 分钟时降级 Transit。
- 高德方向工具可用时读取真实距离、时长、公交费用、出租车估价等字段。
- 拥堵系数：早高峰 1.35×，晚高峰 1.45×，深夜 0.85×。

### 6.4 预算评估

预算包含（`budget_evaluator.py`）：

- 景点/活动固定成本。
- 每段交通成本。
- 每日餐饮成本（avg_meal_cost × 2）。
- 多日住宿成本（最后一晚不计算）。

若总成本超过用户预算：

1. 优先选择同品类更便宜的 POI 替换（`budget_pruner.py`）。
2. 无可替换时剪除最低 utility/cost 的付费 POI。
3. 回到矩阵与求解阶段重算。
4. 直到预算满足或无法再剪除。

### 6.5 几何生成与简化

路线几何生成（`geometry.py`）：

- 高德方向服务返回 `polyline` 时使用真实道路坐标。
- 缺失时 Haversine 插值（根据坐标距离动态调整步数 3-18）。
- Douglas-Peucker 简化（tolerance=0.0004），provider polyline 不简化。
- 计算包围盒（BoundingBox）供地图自动缩放。

### 6.6 质量指标

`observability.py` 聚合以下指标：

- `total_stops`
- `total_distance_km`
- `total_minutes`
- `total_transport_cost`
- `budget_usage_ratio`
- `average_fitness`
- `mode_share`
- `fitness_curve`（每个路线分数 → 运行平均）

---

## 7. 前端功能规格

当前前端是一个工作台（`PlannerWorkspace.vue`），而不是营销页。已实现：

- 请求输入与 Parse Request。
- 结构化参数编辑。
- Plan Trip（HTTP POST）。
- Stream Solve（WebSocket）。
- Submit Job（后台任务 + 1s 间隔轮询）。
- Insert Library 示例重规划。
- Export HTML / PDF / PNG（支持格式选择和地图快照注入）。
- 高德 JSAPI 2.0 地图路线展示（按天分色 + 酒店标记 + InfoWindow）。
- 每日路线、交通方式、上下车站点。
- 预算仪表。
- 天气与酒店。
- 质量指标与 fitness curve。
- 工作流事件。
- 任务列表。
- 运行时能力检测与集成探测。
- Vite 代理：/api → FastAPI, /ws → WebSocket。

待增强：

- 拖拽排序与真实前端重规划交互。
- 地图上 POI 点击插入。
- 更细的 loading/error 空状态。
- 更完整的移动端交互优化。
- 在每个路线卡片中展示价格来源标记和天气影响。

---

## 8. API 规格

### 健康检查（无前缀）

- `GET /health`
- `GET /health/capabilities`
- `GET /health/integrations/probe`

### 行程规划（前缀 `/api`）

- `POST /api/trips/intent/parse`
- `POST /api/trips/plan`
- `POST /api/trips/replan`
- `GET /api/trips/demo`
- `POST /api/trips/export`（支持 export_format: html/pdf/png + map_snapshot_base64）
- `POST /api/trips/export/file`
- `GET /api/trips/workflow/topology`

### Job 轮询（前缀 `/api`）

- `POST /api/trips/jobs`
- `GET /api/trips/jobs`
- `GET /api/trips/jobs/{job_id}`
- `GET /api/trips/jobs/{job_id}/events?after=N`

### WebSocket

- `WS /ws/solve`

---

## 9. 容错策略

| 风险 | 当前策略 | 后续增强 |
| --- | --- | --- |
| LLM 不可用或输出非法 | 规则解析/模板文案兜底；`complete_json` 尝试提取 JSON 子串 | 增加结构化重试和质量评分 |
| 高德/MCP 不可用 | 本地 POI/酒店/天气/矩阵 fallback；Adapter 模式自动降级 | 指数退避、熔断、详细错误观测 |
| 高德 REST API 失败 | 在 `amap_service.py` 中逐级降级 | 重试与限流 |
| POI 无坐标 | 丢弃或无法转为候选 | 增加地理编码补全 |
| 价格缺失 | 使用估算值（城市均值） | 接入票务/酒店/餐饮价格源 |
| 预算超标 | 优先同品类替代，其次剪除低性价比付费 POI 并重算 | 更细粒度替代 POI 和交通策略 |
| 导出工具缺失 | weasyprint/playwright 缺失时生成占位 PDF/PNG | 提供安装指引或强依赖 |
| Job 文件写失败 | 当前会导致任务接口失败 | 改为可配置写目录、异常降级内存态 |
| 前端构建 dist 权限 | 可改 outDir 临时构建 | 清理目录权限或改默认构建目录 |
| HTTPS/SSL 错误 | `http_client.py` 支持自定义 CA 和跳过校验 | 自动检测代理环境 |

---

## 10. 验收标准

### MVP 验收

- 能解析中文自然语言请求。
- 能生成多日路线。
- 路线包含多种偏好类型，不被首个 preference 垄断。
- 能展示预算拆分、天气、酒店、地图、事件。
- 高德不可用时仍可本地 fallback 跑通。
- 后端核心测试通过。
- 前端类型检查通过。

### 生产级验收

- 高德/MCP live probe 稳定。
- Job 持久化无权限问题。
- 真实拖拽重规划可用。
- PDF/PNG 导出可用（weasyprint/playwright 已安装）。
- 价格数据来源可解释，区分实时价、参考价、估算价。
- 前后端 CI 构建稳定。
- 密钥与运行时配置安全隔离。

---

## 11. 当前已知限制

- 酒店和景点价格不是全量实时成交价。
- 后端生成的路线几何当高德 polyline 缺失时仍是简化连线。
- WebSocket 推送是节点级/事件级，不是 solver 内部每代高频流。
- FinanceAgent 尚未接入真实外汇或城市消费服务。
- 前端重规划仍是固定按钮示例，不是完整拖拽体验。
- HTML/PDF/PNG 导出已可用；PDF 和 PNG 分别依赖 weasyprint 和 playwright。
- `backend/data/jobs.jsonl` 在部分 Windows 权限环境下会写入失败。
- 前端 `npm run build` 可能因 `frontend/dist/assets` 权限失败。

---

## 12. 目录结构

```text
trip-planner/
├── backend/
│   └── app/
│       ├── agents/            # intent / attraction / hotel / weather / finance / planner
│       ├── algorithms/        # clustering / matrix / VRP solver / budget / geometry / geo / observability
│       ├── api/               # http_router / ws_router / health_router / error_handlers
│       ├── core/              # config / exceptions
│       ├── graph/             # LangGraph state, nodes, edges, workflow
│       ├── mcp_server/        # project-local MCP tools
│       └── services/          # amap, geo_facts, matrix, cache, jobs, export, LLM, MCP client,
│                              # http_client, integration_probe, provider_adapters
│   ├── scripts/               # demo / run_mcp / run_mcp_http
│   └── tests/                 # conftest, test_workflow, test_mcp_server
└── frontend/
    └── src/
        ├── api/               # trips.ts
        ├── components/        # MapViewer, RouteEditor, BudgetDashboard, SolverMonitor
        ├── router/
        ├── stores/            # tripStore (Pinia)
        ├── types/             # trip.ts
        └── views/             # PlannerWorkspace.vue
```
