# Trip Planner

基于 `prd.md` 的智能旅行规划系统工程骨架。

当前版本先实现一条可运行的本地计算链路：

- Map: 解析请求并生成候选 POI、酒店锚点、天气约束、预算上下文。
- Compute: 构建距离/时间矩阵，执行容量感知的多日聚类、路线启发式求解、预算评估。
- Reduce: 将结构化求解结果渲染为用户可读的行程说明。
- GIS: 为每日路线生成抽稀后的几何轨迹与边界框，前端用 SVG 呈现坐标化路线。
- Jobs: 规划任务会写入 `backend/data/jobs.jsonl`，可在服务重启后恢复。
- Observability: 返回路线质量指标、预算使用率、交通方式占比和适应度曲线，供前端监控面板展示。
- Streaming: 前端可通过 WebSocket 执行 `Stream Solve`，实时接收阶段事件并在完成后更新完整 TripState。

## Backend

```powershell
cd backend
python -m pip install -r requirements.txt
python -m pytest
uvicorn app.main:app --reload
```

`FastAPI` 和 `LangGraph` 属于运行依赖；核心算法与测试不依赖外部地图服务。

可选外部能力通过环境变量开启；未开启或调用失败时会自动回退到本地 demo/fallback：

```powershell
# 高德 POI / 酒店 / 距离矩阵
$env:TRIP_PROVIDER_MODE="amap"
$env:TRIP_AMAP_API_KEY="your-amap-key"

# OpenAI-compatible LLM 意图解析与行程文案渲染
$env:TRIP_LLM_ENABLED="true"
$env:TRIP_LLM_API_KEY="your-llm-key"
$env:TRIP_LLM_MODEL="gpt-4o-mini"
```

无需启动服务也可以运行本地演示：

```powershell
python backend\scripts\demo_workflow.py
python backend\scripts\demo_replan.py
python backend\scripts\demo_job_export.py
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

前端当前提供工作台骨架、路线编辑列表、预算面板和地图占位组件。

## API

- `POST /api/trips/plan`: 提交旅行规划请求。
- `POST /api/trips/intent/parse`: 将自然语言请求解析为结构化 `IntentConstraints`。
- `GET /api/trips/demo`: 获取演示规划结果。
- `POST /api/trips/replan`: 对某一天执行局部插入重规划。
- `POST /api/trips/export`: 返回 HTML 导出载荷。
- `POST /api/trips/export/file`: 将 HTML 导出文件写入 `backend/exports/`。
- `POST /api/trips/jobs`: 创建可查询的规划任务。
- `GET /api/trips/jobs`: 获取任务摘要列表。
- `GET /api/trips/jobs/{job_id}`: 获取任务状态、事件和最终状态。
- `WS /ws/solve`: 接收求解中间态事件，最终 `complete` 事件返回完整 `TripState`。
- `GET /health`: 服务健康检查。

## Runtime Data

- `backend/data/`: JSONL 任务存储。
- `backend/exports/`: 导出的 HTML 文件。
- `backend/.test-exports/` 和 `backend/.test-jobs/`: 测试临时输出。
## Amap MCP Runtime

Production Amap mode now requires a real external MCP endpoint. The backend no
longer silently falls back to its in-process demo MCP server when
`TRIP_PROVIDER_MODE=amap`.

```powershell
$env:TRIP_PROVIDER_MODE="amap"
$env:TRIP_MCP_HTTP_URL="https://your-amap-mcp-host.example/mcp"
$env:TRIP_MCP_TIMEOUT_SECONDS="20"
# Optional: map this project to the real Amap MCP server's tool names.
$env:TRIP_AMAP_MCP_POI_TOOL="amap_poi_search"
$env:TRIP_AMAP_MCP_HOTEL_TOOL="amap_hotel_anchor"
$env:TRIP_AMAP_MCP_WEATHER_TOOL="amap_weather_constraints"
$env:TRIP_AMAP_MCP_MATRIX_TOOL="amap_distance_matrix"
```

The project-local MCP server remains available only for development:

```powershell
$env:TRIP_MCP_ALLOW_INPROCESS="true"
python backend\scripts\run_mcp_server.py
```

Use the in-process server only for local testing. Real POI, hotel, weather, and
distance-matrix facts should come through `TRIP_MCP_HTTP_URL`.

## Amap JS Frontend

The frontend route map uses Amap JSAPI 2.0. Set a Gaode Web JS API key before
starting Vite:

```powershell
$env:VITE_AMAP_JS_KEY="your-amap-js-key"
# Optional, when your Amap Web JSAPI app has a security code enabled:
$env:VITE_AMAP_SECURITY_CODE="your-amap-security-code"
npm run dev
```

`VITE_AMAP_API_KEY` is also accepted for local convenience, but
`VITE_AMAP_JS_KEY` is preferred so the browser key is distinct from backend MCP
or Web Service keys.
