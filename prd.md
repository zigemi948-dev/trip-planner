# 智能旅行规划系统 PRD

**文档版本：** v5.1  
**更新依据：** 当前仓库实现、端到端体验与测试结果  
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
| 自然语言意图解析 | 已实现 | 支持中文/英文城市、天数、预算、偏好、时间窗；LLM 失败时回退规则解析。 |
| 城市与偏好词表 | 已实现 | `intent_agent.py` 的城市和关键词已同步到 `geo_fact_service.py`，高德查询使用同一套语义。 |
| Map 多 Agent | 已实现 | 并发获取 finance、hotel、attraction、weather 四类上下文。 |
| 高德/MCP 接入 | 部分实现 | 支持外部 MCP、官方高德工具名兜底、项目内开发 MCP；价格字段尽力读取，缺失时 fallback。 |
| 路线矩阵 | 部分实现 | 支持按小时矩阵、Driving/Transit/Walking 成本；真实高德方向工具可用时读取距离、时长、费用。 |
| 路线求解 | 已实现 | 容量感知聚类 + 日内 NSGA-II 候选序列求解。 |
| 预算评估与修复 | 已实现 | 统计门票、交通、餐饮、住宿；超预算时剪除低性价比付费 POI 并重新求解。 |
| 天气约束 | 已实现 | 雨雪高温等天气可转成室外规避约束，并展示每日天气。 |
| WebSocket 流式事件 | 已实现 | 推送阶段完成、节点事件和最终 `TripState`；尚非真实高频 epoch 内循环流。 |
| 局部重规划 | 部分实现 | 后端支持单点 cheapest insertion；前端目前是固定按钮插入示例 POI，尚无拖拽。 |
| 前端工作台 | 已实现 | 支持解析、规划、流式规划、地图、预算、路线、天气、事件、任务列表和 HTML 导出。 |
| 地图渲染 | 部分实现 | 前端使用高德 JSAPI；后端生成简化几何线，尚非真实道路 polyline。 |
| 导出 | 部分实现 | 支持 HTML payload 和文件写入；PDF/PNG/Headless Chrome/Celery 未实现。 |
| Job 持久化 | 部分实现 | JSONL 存储已实现；当前 Windows 权限环境下 `backend/data/jobs.jsonl` 可能写入失败。 |

---

## 3. 核心用户流程

1. 用户在前端 Request 输入自然语言，例如：`去长沙玩2天，预算800元，想吃美食和泡温泉`。
2. `IntentAgent` 将文本解析为 `IntentConstraints`：
   - `destination`
   - `days`
   - `budget_limit`
   - `preferences`
   - `time_window_baseline`
3. Map 阶段并发召回：
   - 候选 POI
   - 酒店锚点
   - 天气预报与约束
   - 城市消费/交通/住宿预算上下文
4. Compute 阶段构建时变矩阵并求解：
   - 按天聚类
   - 每日路线优化
   - 时间窗/天气约束校验
   - 预算评估与必要时修复
5. Reduce 阶段生成叙述性行程。
6. 前端展示地图、日程、交通费用、预算、天气、质量指标和事件流。

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

---

## 5. 后端功能规格

### 5.1 IntentAgent

输入：`user_query`  
输出：`IntentConstraints`

已实现能力：

- 中文城市和英文城市别名。
- 中文数字/阿拉伯数字天数。
- 预算抽取。
- 偏好标签归一化。
- 时间窗抽取。
- LLM 优先，规则兜底。

### 5.2 AttractionAgent

输入：`IntentConstraints.preferences` 和 `destination`  
输出：`POICandidate[]`

要求：

- POI 候选必须包含坐标；无坐标节点不得进入求解。
- 多个 preference 需均衡召回，避免首个偏好填满候选集。
- intent 中的偏好关键词需同步到高德查询层。

当前实现：

- 自定义 MCP 工具优先。
- 官方高德 MCP 工具兜底。
- 本地 demo POI 兜底。

### 5.3 HotelAgent

输出路线起终点酒店锚点。

当前实现：

- 高德/MCP 酒店优先。
- 如果返回酒店消费字段，则作为住宿预算参考。
- 不可用时使用本地中心酒店锚点。

### 5.4 WeatherAgent

输出：

- `WeatherConstraint[]`
- `DailyWeatherForecast[]`

当前实现：

- 支持高德天气。
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

当前实现：

- 通过 MCP `finance_context` 获取上下文。
- 本地 fallback 为 CNY 和固定城市均值。
- 餐饮 POI 人均价会反写到餐费均值，酒店价格会反写到住宿均值。

后续目标：

- 接入真实汇率、城市消费、酒店均价或票务数据源。

---

## 6. 求解与预算规格

### 6.1 空间聚类

按天数将候选 POI 划分为多日簇，考虑：

- 经纬度距离。
- 游玩时长。
- 单日最大时长。
- POI utility。

当前实现为容量感知的 K-Means 风格启发式。

### 6.2 日内路线求解

每个日簇通过候选序列 + NSGA-II 评估，目标函数近似为：

```text
utility - time_penalty - cost_penalty - skipped_penalty
```

约束：

- POI 营业时间。
- 每日开始/结束时间。
- 天气约束。
- 交通矩阵边。

### 6.3 交通方式选择

支持：

- Walking
- Transit
- Driving

当前策略：

- 短距离优先步行。
- Driving 成本明显高且节省时间有限时降级 Transit。
- 高德方向工具可用时读取真实距离、时长、公交费用、出租车估价等字段。

### 6.4 预算评估

预算包含：

- 景点/活动固定成本。
- 每段交通成本。
- 每日餐饮成本。
- 多日住宿成本。

若总成本超过用户预算：

- 选择最低 utility/cost 的付费 POI 剪除。
- 回到矩阵与求解阶段重算。
- 直到预算满足或无法再剪除。

---

## 7. 前端功能规格

当前前端是一个工作台，而不是营销页。已实现：

- 请求输入与 Parse Request。
- 结构化参数编辑。
- Plan Trip。
- Stream Solve。
- Submit Job。
- Insert Library 示例重规划。
- Export HTML / Export File。
- 高德地图路线展示。
- 每日路线、交通方式、上下车站点。
- 预算仪表。
- 天气与酒店。
- 质量指标与 fitness curve。
- 工作流事件。
- 任务列表。

待增强：

- 拖拽排序与真实前端重规划交互。
- 地图上 POI 点击插入。
- 更细的 loading/error 空状态。
- 更完整的移动端交互优化。

---

## 8. API 规格

主要接口：

- `POST /api/trips/intent/parse`
- `POST /api/trips/plan`
- `POST /api/trips/replan`
- `GET /api/trips/demo`
- `POST /api/trips/export`
- `POST /api/trips/export/file`
- `POST /api/trips/jobs`
- `GET /api/trips/jobs`
- `GET /api/trips/jobs/{job_id}`
- `GET /api/trips/jobs/{job_id}/events`
- `GET /api/trips/workflow/topology`
- `GET /health`
- `GET /health/capabilities`
- `GET /health/integrations/probe`
- `WS /ws/solve`

---

## 9. 容错策略

| 风险 | 当前策略 | 后续增强 |
| --- | --- | --- |
| LLM 不可用或输出非法 | 规则解析/模板文案兜底 | 增加结构化重试和质量评分 |
| 高德/MCP 不可用 | 本地 POI/酒店/天气/矩阵 fallback | 指数退避、熔断、详细错误观测 |
| POI 无坐标 | 丢弃或无法转为候选 | 增加地理编码补全 |
| 价格缺失 | 使用估算值 | 接入票务/酒店/餐饮价格源 |
| 预算超标 | 剪除低性价比付费 POI 并重算 | 更细粒度替代 POI 和交通策略 |
| Job 文件写失败 | 当前会导致任务接口失败 | 改为可配置写目录、异常降级内存态 |
| 前端构建 dist 权限 | 可改 outDir 临时构建 | 清理目录权限或改默认构建目录 |

---

## 10. 验收标准

### MVP 验收

- 能解析中文自然语言请求。
- 能生成多日路线。
- 路线包含多种偏好类型，不被首个 preference 垄断。
- 能展示预算拆分、天气、酒店、地图、事件。
- 高德不可用时仍可本地 fallback 跑通。
- 后端核心测试通过。

### 生产级验收

- 高德/MCP live probe 稳定。
- Job 持久化无权限问题。
- 真实拖拽重规划可用。
- PDF/PNG 导出可用。
- 价格数据来源可解释，区分实时价、参考价、估算价。
- 前后端 CI 构建稳定。
- 密钥与运行时配置安全隔离。

---

## 11. 当前已知限制

- 酒店和景点价格不是全量实时成交价。
- 后端生成的路线几何主要是简化连线，不是完整道路 polyline。
- WebSocket 推送是节点级/事件级，不是 solver 内部每代高频流。
- FinanceAgent 尚未接入真实外汇或城市消费服务。
- 前端重规划仍是固定按钮示例，不是完整拖拽体验。
- HTML 导出已可用，PDF/PNG 尚未实现。
- `backend/data/jobs.jsonl` 在部分 Windows 权限环境下会写入失败。

---

## 12. 目录结构

```text
trip-planner/
├── backend/
│   ├── app/
│   │   ├── agents/          # intent / attraction / hotel / weather / finance / planner
│   │   ├── algorithms/      # clustering / matrix / VRP / budget / geometry
│   │   ├── api/             # HTTP / WebSocket / health
│   │   ├── core/            # config / exceptions
│   │   ├── graph/           # LangGraph state, nodes, edges, workflow
│   │   ├── mcp_server/      # project-local MCP tools
│   │   └── services/        # amap, geo facts, matrix, cache, jobs, export, LLM
│   ├── scripts/
│   └── tests/
└── frontend/
    ├── src/
    │   ├── api/
    │   ├── components/
    │   ├── router/
    │   ├── stores/
    │   ├── types/
    │   └── views/
    └── package.json
```
