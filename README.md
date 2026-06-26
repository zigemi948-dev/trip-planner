# Trip Planner

智能旅行规划助手。项目目标是把自然语言旅行需求转成可执行的多日路线，并在预算、时间窗、天气、交通方式和地图事实约束下生成可解释行程。

当前仓库已经具备可演示的端到端链路：

- 意图解析：中文/英文城市、天数、预算、时间窗、偏好。
- Map 阶段：POI、酒店、天气、消费上下文。
- Compute 阶段：时变交通矩阵、多日聚类、NSGA-II 路线求解、预算评估与修复。
- Reduce 阶段：生成用户可读行程文案。
- 前端工作台：请求解析、路线规划、流式规划、地图、预算、天气、事件、任务、导出。

项目不是纯 LLM 套壳。LLM 只负责意图解析和文案渲染；坐标、距离、预算、路线顺序由后端事实服务和 Python 算法处理。

## 当前状态

按当前代码和验证结果：

- MVP 演示完成度：约 80%。
- 生产级完成度：约 60%-65%。
- 后端测试通过：`55 passed`。
- 前端类型检查通过；Vite 可在临时输出目录构建成功。

主要限制：

- 高德价格字段已接入，但酒店房态、景点门票和餐饮实时成交价不是全量真实价格。
- 高德 REST 或 MCP 方向服务可回填道路 polyline；缺失时仍降级为后端插值连线。
- 前端重规划目前是固定按钮插入示例 POI，不是拖拽交互。
- WebSocket 已推送 solver 代际 fitness；前端展示仍偏轻量。
- HTML 导出已实现，PDF/PNG/Headless Chrome 导出未实现。
- `backend/data/jobs.jsonl` 在部分 Windows 权限环境下可能无法落盘；Job 会保留在内存中，重启恢复需要配置可写路径。

## 技术栈

后端：

- FastAPI
- Pydantic v2
- LangGraph
- Python 运筹与图计算模块
- MCP JSON-RPC client/server

前端：

- Vue 3
- Vite
- Pinia
- ECharts
- 高德 JSAPI 2.0

## 项目结构

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
$env:TRIP_AMAP_API_KEY="your-amap-webservice-key"
```

### 前端高德地图

```powershell
$env:VITE_AMAP_JS_KEY="your-amap-js-key"
$env:VITE_AMAP_SECURITY_CODE="optional-security-code"
```

`VITE_AMAP_API_KEY` 也可用于本地兼容，但推荐使用 `VITE_AMAP_JS_KEY`，避免和后端 WebService/MCP key 混用。

### LLM

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
C:\Users\lenovo\anaconda3\envs\open_ai\python.exe -m pytest tests\test_workflow.py
```

当前已知：完整测试在当前 Windows 权限环境下有一个 `backend/data/jobs.jsonl` 写入失败。核心算法、意图、高德、预算、工作流相关测试通过。

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

核心接口：

- `POST /api/trips/intent/parse`：解析自然语言请求。
- `POST /api/trips/plan`：提交结构化意图并规划路线。
- `POST /api/trips/replan`：对某一天执行局部插入重规划。
- `GET /api/trips/demo`：返回 demo TripState。
- `POST /api/trips/export`：返回 HTML 导出 payload。
- `POST /api/trips/export/file`：写出 HTML 文件。
- `POST /api/trips/jobs`：创建规划任务。
- `GET /api/trips/jobs`：任务摘要列表。
- `GET /api/trips/jobs/{job_id}`：任务详情。
- `GET /api/trips/jobs/{job_id}/events`：增量事件。
- `GET /api/trips/workflow/topology`：工作流拓扑。
- `GET /health`：健康检查。
- `GET /health/capabilities`：运行时能力。
- `GET /health/integrations/probe`：高德/LLM 探测。
- `WS /ws/solve`：流式规划。

示例：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/trips/intent/parse `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"user_query":"去长沙玩2天，预算800元"}'
```

## 已实现亮点

- Intent 城市和偏好关键词已和高德事实服务同步。
- POI 搜索按多个 preference 均衡召回，避免路线全是首个偏好。
- 高德/MCP 路径读取方向工具中的距离、时长、公交费用、出租车估价等字段。
- 酒店/餐饮消费字段可反写预算上下文，避免部分重复计费。
- 预算超限会进入剪枝和重算。
- 前端可展示交通方式、上下车站点、每日预算、天气、酒店、事件和质量指标。

## 当前验证记录

最近一次检查结果：

- 后端：`55 passed`。
- 前端：`npx.cmd vue-tsc --noEmit` 通过。
- 前端：`npx.cmd vite build --outDir ..\run-logs\frontend-build-check --emptyOutDir` 通过。
- 前端默认 `npm.cmd run build` 可能因 `frontend/dist/assets` 权限失败。

## 后续更新计划

### P0：稳定性与可交付修复

- 修复 JobStore 默认写入路径权限问题，支持无法落盘时降级内存态或使用可配置 runtime 目录。
- 清理 `frontend/dist` 权限/锁定问题，保证默认 `npm run build` 可重复成功。
- 为 `.env` 增加示例文件，避免密钥和运行配置混乱。
- 增加端到端 smoke test：parse → plan → budget → export。

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

### P3：导出与展示

- 接入 Headless Chrome，将 HTML 导出升级为 PDF/PNG。
- 导出中加入地图截图、预算图表和每日路线摘要。
- 支持导出文件历史列表。

### P4：算法增强（已落地）

- 已使用高德 REST 或 MCP 方向服务返回的道路 polyline 填充路线几何，缺失时继续使用插值兜底。
- 已让 WebSocket 推送 NSGA-II 每代最佳 fitness，前端可观察求解收敛过程。
- 已增加 POI 替代策略：预算超标时优先用更便宜的同类 POI 替换，而不是只删除。
- 已按多日预算压力分配 POI，降低前几天过度消耗固定预算的概率。

## 文档

- 产品与架构说明：[`prd.md`](./prd.md)
- 后端入口：[`backend/app/main.py`](./backend/app/main.py)
- 前端入口：[`frontend/src/views/PlannerWorkspace.vue`](./frontend/src/views/PlannerWorkspace.vue)
