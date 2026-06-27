# Trip Planner

智能旅行规划助手。将自然语言旅行需求转成可执行的多日路线，在预算、时间窗、天气、交通方式和地图事实约束下生成可解释行程。

项目不是纯 LLM 套壳。LLM 只负责意图解析和文案渲染；坐标、距离、预算、路线顺序由后端事实服务和 Python 算法处理。

## 当前状态

按当前代码和验证结果：

- MVP 演示完成度：约 85%。
- 生产级完成度：约 65%-70%。
- 后端测试用例：`1385+` 行测试，覆盖工作流、MCP 服务器、意图解析、聚类、矩阵构建、路线求解、预算评估与修复、预算替代策略、几何生成与简化、导出多格式、Job 持久化与事件轮询、健康检查、集成探测和工作流拓扑。
- 前端类型检查通过；Vite 可在临时输出目录构建成功。

核心链路可用：

1. 意图解析：中文/英文城市、天数、预算、时间窗、偏好。LLM 失败时规则兜底。
2. Map 阶段：并行召回 POI、酒店、天气、消费上下文。
3. Compute 阶段：时变交通矩阵、容量感知多日聚类、NSGA-II 路线求解、预算评估与修复（含 POI 替代策略）。
4. Reduce 阶段：上下文压缩 + 生成用户可读行程文案。
5. 前端工作台：请求解析、路线规划、流式规划（WebSocket）、地图（高德 JSAPI）、预算仪表、天气与酒店、求解质量监控、工作流事件、任务列表（Job 轮询）和导出（HTML/PDF/PNG）。

主要限制：

- 高德价格字段已接入，但酒店房态、景点门票和餐饮实时成交价不是全量真实价格。
- 高德 REST 或 MCP 方向服务可回填道路 polyline；缺失时仍降级为后端插值连线。
- 前端重规划目前是通过固定按钮插入示例 POI，不是拖拽交互。
- WebSocket 已推送工作流阶段事件；前端展示仍偏轻量。
- HTML/PDF/PNG 导出已实现；PDF 依赖 weasyprint，PNG 依赖 playwright，缺失时使用占位文件兜底。
- `backend/data/jobs.jsonl` 在部分 Windows 权限环境下可能无法落盘；Job 会保留在内存中，重启恢复需要配置可写路径。

## 技术栈

后端：

- FastAPI（Web 框架）
- Pydantic v2（数据模型与校验）
- LangGraph（工作流编排）
- Python 运筹与图计算模块（聚类、NSGA-II、Douglas-Peucker、Haversine）
- MCP JSON-RPC client/server
- weasyprint（PDF 导出，可选）
- playwright（PNG 导出，可选）

前端：

- Vue 3（Composition API）
- Vite
- Pinia（状态管理）
- ECharts（仅内联 SVG 预算图表）
- 高德 JSAPI 2.0（地图渲染）
- html2canvas（地图快照）

## 项目结构

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
│   └── tests/                 # conftest, test_workflow (~1385 lines), test_mcp_server (~497 lines)
└── frontend/
    └── src/
        ├── api/               # trips.ts (HTTP + WebSocket API layer)
        ├── components/        # MapViewer, RouteEditor, BudgetDashboard, SolverMonitor
        ├── router/
        ├── stores/            # tripStore (Pinia)
        ├── types/             # trip.ts (TypeScript interfaces mirroring backend Pydantic models)
        └── views/             # PlannerWorkspace.vue
```

## 快速启动

### 1. 后端

```powershell
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

默认服务地址：

```text
http://127.0.0.1:8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

默认前端地址：

```text
http://127.0.0.1:5173
```

如果 PowerShell 禁止执行 `npm.ps1`，使用：

```powershell
npm.cmd run dev
```

## 环境变量

项目会读取根目录 `.env` 和 `backend/.env`。真实密钥不要提交到仓库。

### 本地 fallback 模式

不配置高德和 LLM 也能跑通 demo 链路：

```powershell
$env:TRIP_PROVIDER_MODE="local"
$env:TRIP_LLM_ENABLED="false"
```

### 高德 MCP 模式

生产/真实数据模式建议走外部 MCP endpoint：

```powershell
$env:TRIP_PROVIDER_MODE="amap"
$env:TRIP_MCP_HTTP_URL="https://your-amap-mcp-host.example/mcp"
$env:TRIP_MCP_TIMEOUT_SECONDS="20"
```

可选工具名映射：

```powershell
$env:TRIP_AMAP_MCP_POI_TOOL="amap_poi_search"
$env:TRIP_AMAP_MCP_HOTEL_TOOL="amap_hotel_anchor"
$env:TRIP_AMAP_MCP_WEATHER_TOOL="amap_weather_constraints"
$env:TRIP_AMAP_MCP_MATRIX_TOOL="amap_distance_matrix"
```

项目内开发 MCP server：

```powershell
$env:TRIP_MCP_ALLOW_INPROCESS="true"
python backend\scripts\run_mcp_server.py
```

或 HTTP 版本：

```powershell
python backend\scripts\run_mcp_http_server.py
$env:TRIP_MCP_HTTP_URL="http://127.0.0.1:8765/mcp"
```

### 高德 WebService 直连模式

项目仍保留 `TRIP_AMAP_API_KEY` 直连路径：

```powershell
$env:TRIP_PROVIDER_MODE="amap"
$env:TRIP_AMAP_API_KEY="your-amap-key"
```
注意：直连模式和 MCP 模式互斥；同时配置时优先使用 MCP。

### 前端高德地图

```powershell
$env:VITE_AMAP_JS_KEY="your-amap-js-key"
$env:VITE_AMAP_SECURITY_CODE="optional-security-code"
```

`VITE_AMAP_API_KEY` 也可用于本地兼容，但推荐使用 `VITE_AMAP_JS_KEY`，避免和后端 WebService/MCP key 混用。

### LLM
意图解析优先使用 LLM；LLM 不可用时自动降级规则解析。
```powershell
$env:TRIP_LLM_ENABLED="true"
$env:TRIP_LLM_API_KEY="your-llm-key"
$env:TRIP_LLM_BASE_URL="https://api.openai.com/v1"
$env:TRIP_LLM_MODEL="gpt-4o-mini"
```

### HTTPS/证书配置

后端访问高德、MCP、LLM 时默认使用 `certifi` CA bundle。若公司代理或本机 Conda 证书链导致 Probe 出现 `SSL` / `ASN1` 错误，可指定 CA 文件：

```powershell
$env:TRIP_SSL_CA_FILE="C:\path\to\company-ca.pem"
```

仅本地开发排障时可临时关闭校验：

```powershell
$env:TRIP_SSL_VERIFY="false"
```

LLM 失败时，意图解析会回退规则解析，行程文案会回退模板渲染。

## 常用命令

### 后端测试

```powershell
cd backend
python -m pytest tests
```

指定单个用例文件：

```powershell
python -m pytest tests\test_workflow.py
```

当前已知：完整测试在当前 Windows 权限环境下有一个 `backend/data/jobs.jsonl` 写入失败。核心算法、意图、高德、预算、工作流、MCP 服务器、几何、导出、Job、健康检查相关测试通过。

### 前端类型检查

```powershell
cd frontend
npx.cmd vue-tsc --noEmit
```

### 前端构建

默认构建：

```powershell
cd frontend
npm.cmd run build
```

如果 `frontend/dist/assets` 因权限或锁定无法创建，可临时验证构建：

```powershell
npx.cmd vite build --outDir ..\run-logs\frontend-build-check --emptyOutDir
```

## API

### 健康检查

- `GET /health`：应用存活状态。
- `GET /health/capabilities`：运行时能力（provider_mode, amap_enabled, llm_enabled 等）。
- `GET /health/integrations/probe`：对外部集成（高德/LLM）执行实时探测。

### 行程规划（前缀 `/api`）

- `POST /api/trips/intent/parse`：解析自然语言请求。
- `POST /api/trips/plan`：提交结构化意图并规划路线。
- `POST /api/trips/replan`：对某一天执行 cheapest-insertion 局部重规划。
- `GET /api/trips/demo`：返回固定 demo TripState。
- `POST /api/trips/export`：返回 HTML/PDF/PNG 导出 payload。
- `POST /api/trips/export/file`：写出导出文件到服务端磁盘。
- `GET /api/trips/workflow/topology`：工作流拓扑（Map-Compute-Reduce 节点与边）。

### Job 轮询（前缀 `/api`）

- `POST /api/trips/jobs`：创建后台规划任务。
- `GET /api/trips/jobs`：任务摘要列表。
- `GET /api/trips/jobs/{job_id}`：任务详情。
- `GET /api/trips/jobs/{job_id}/events?after=N`：增量事件轮询。

### WebSocket

- `WS /ws/solve`：流式规划。发送 JSON intent，接收一系列 `stage_complete` / 自定义事件，最后收到 `complete` 事件携带完整 TripState。

### 示例

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/trips/intent/parse `
  -ContentType "application/json; charset=utf-8" `
  -Body {"user_query":"去长沙玩2天，预算800元"}
```

## 已实现亮点

### 意图与数据层

- Intent 城市别名覆盖 55+ 个中文/英文城市。
- 偏好关键词覆盖 `museum`、`food`、`landmark`、`shopping`、`nature`、`nightlife`、`culture`、`history` 等。
- POI 搜索按多个 preference 均衡召回，避免路线全是首个偏好。
- Intent 城市和关键词已和高德事实服务同步（`geo_fact_service.py` 引用 `intent_agent.py` 的 `CITY_ALIASES` 和 `PREFERENCE_KEYWORDS`）。

### 地图与服务层

- 高德 REST API 直连：`place/text`、`weather/weatherInfo`、`direction/driving`、`direction/transit`、`direction/walking`。
- 高德/MCP 路径读取方向工具中的距离、时长、公交费用、出租车估价等字段。
- 酒店/餐饮消费字段可反写预算上下文，避免部分重复计费。
- 矩阵缓存（MemoryCache + 1h TTL），减少重复计算。
- `http_client.py` 封装共享 SSL 策略（certifi / 自定义 CA / 跳过校验）。

### 算法层

- 容量感知 K-Means 风格多日聚类，支持日均固定成本预算约束。
- 日内 NSGA-II 候选序列求解（种群 32，代数 14，突变率 0.22）。
- 时变交通矩阵（24 小时离散切片 + 拥堵系数）。
- 交通方式自动选择：短距离步行、高成本时降级公交。
- 预算超限会进入剪枝和重算循环。
- POI 替代策略：预算超标时优先用更便宜的同类 POI 替换，而不是只删除。
- 道路 polyline 回填：高德方向服务返回时使用真实道路几何，缺失时 Haversine 插值兜底。
- Douglas-Peucker 几何简化，减少前端渲染点密度。
- 质量指标聚合（`observability.py`）：总停靠点、总距离、活跃时长、预算利用率、交通方式占比。

### 导出层

- HTML 导出：内联 SVG 预算图表 + 路线地图 SVG + 每日行程表格。
- PDF 导出：weasyprint（可选），失败时生成最小占位 PDF。
- PNG 导出：playwright（可选），截取全页面长图，失败时生成占位图。
- 地图快照注入：前端 html2canvas → base64 data URI → 嵌入导出文件。

### 前端

- 高德 JSAPI 地图渲染，按天分色路线 + 酒店标记 + InfoWindow 交互。
- 路线编辑器展示每日停靠点、到达/离开时间、交通方式和费用、公交上下车站点。
- 预算仪表展示固定成本、交通、餐饮、住宿和剩余预算分项。
- 求解监控：总停靠点、距离、时间、预算利用率、fitness curve 柱状图、交通方式分布。
- 天气与酒店信息面板。
- 工作流事件流。
- 后台 Job 提交 + 间隔 1s 自动轮询（`setInterval`）。
- 导出格式选择（HTML / PDF / PNG）。
- 运行时能力检测与集成探测。
- Vite 代理：`/api` → FastAPI，`/ws` → WebSocket。

## 当前验证记录

最近一次检查结果：

- 后端测试文件：`test_workflow.py`（1385 行）+ `test_mcp_server.py`（497 行）。
- 功能覆盖：意图解析、Haversine、矩阵构建、聚类、路线求解、预算评估、预算替代策略（pruner）、几何生成与简化、导出（HTML/PDF/PNG）、Job 持久化与事件轮询、工作流运行与重规划、健康检查、运行时能力、集成探测、工作流拓扑、FastAPI 端点集成测试。
- 前端：`npx.cmd vue-tsc --noEmit` 通过。
- 前端：`npx.cmd vite build --outDir ..\run-logs\frontend-build-check --emptyOutDir` 通过。
- 前端默认 `npm.cmd run build` 可能因 `frontend/dist/assets` 权限失败。

## 后续更新计划

### P0：稳定性与可交付修复

- 修复 JobStore 默认写入路径权限问题，支持无法落盘时降级内存态或使用可配置 runtime 目录。
- 清理 `frontend/dist` 权限/锁定问题，保证默认 `npm run build` 可重复成功。
- 为 `.env` 增加示例文件，避免密钥和运行配置混乱。
- 增加端到端 smoke test：parse → plan → budget → export → verify file on disk。

### P1：真实数据质量

- 完善高德 live probe，分别验证 POI、酒店、天气、驾车、公交、步行工具。
- 标记每个价格字段来源：实时、参考、估算。
- 接入更可靠的酒店/门票/餐饮价格源，避免把高德 POI cost 当作所有场景的真实成交价。
- 对高德失败增加重试、限流和熔断日志。

### P2：交互体验

- 实现前端拖拽排序与真实局部重规划。
- 支持地图点击 POI 插入路线。
- 增强加载、错误、空状态和移动端布局。
- 在路线卡片中展示价格来源、天气影响和预算修复原因。

### P3：导出增强

- 导出中加入更详细的地图截图（高德截图而非 HTML Canvas 快照）。
- 支持导出文件历史列表和管理。
- 移除 PDF/PNG 对 weasyprint/playwright 的可选依赖提为强依赖或提供安装脚本。

### P4：算法增强

- WebSocket 推送 NSGA-II 每代最佳 fitness（当前为工作流节点级推送，非 solver epoch 内高频流）。
- 按多日预算压力分配 POI（当前初步实现日均 cost 约束）。
- 引入更丰富的 POI 属性（评分、评论数、开放时间季节性）。

## 文档

- 产品与架构说明：[`prd.md`](./prd.md)
- 后端入口：[`backend/app/main.py`](./backend/app/main.py)
- 前端入口：[`frontend/src/views/PlannerWorkspace.vue`](./frontend/src/views/PlannerWorkspace.vue)
