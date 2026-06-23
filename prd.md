**产品需求文档 (PRD)：基于多智能体与时变图计算的智能旅行系统**

**文档版本：** v5.0 (架构细化与工程全景版)
**系统定位：** 强时空约束与财务红线双重驱动的多目标优化决策系统
**核心架构基座：** LangGraph 状态机 + Model Context Protocol (MCP) + 运筹学求解器

---

### 一、 核心定位与架构戒律

本系统并非单纯的“套壳大模型对话”，而是一个**严谨的计算机体系结构与交通工程交叉的系统**。系统严格遵循以下“计算隔离”架构戒律：

* **大模型绝对禁区：** 大语言模型（LLM）被彻底剥离空间坐标推理、路网距离测算与预算累加权限。
* **职能分离：** LLM 仅作为“自然语言编译器”和“结果渲染器”，所有的物理约束求解、多目标优化、财务对账必须由后端的纯 Python 运筹学算子和图论算法节点（Engineering Nodes）接管。

---

### 二、 运筹学与空间计算核心算法 (Core Algorithms)

系统底层的路径规划引擎完全摒弃静态 TSP 模型，全面升维至符合真实交通物理特征的运筹学体系。

#### 1. 数学模型定义

将旅行规划定义为**带预算约束的时变车辆路径问题（Budget-Constrained TD-VRPTW）**。
目标函数为求解一个有向无环图（DAG）序列 $x$，使得：


$$\max U(x) - \lambda C_{time}(x) - \gamma C_{cost}(x)$$


**约束条件：**

* **时序与时间窗约束：** 必须满足每个景点的营业时间 $[e_i, l_i]$。
* **财务红线约束：** 游玩链路的总真实财务成本 $C_{cost}(x) \le B_{max}$（$B_{max}$ 为用户设定的最大预算）。

#### 2. 两阶段空间聚类降维 (Two-Phase Heuristic)

针对多日游面临的 $\mathcal{O}(N!)$ 组合爆炸，采用先聚类后路由策略：

* **第一阶段（容量受限的 K-Means/DBSCAN）：** 依据物理坐标与预估游玩时长，将 $N$ 个候选节点划分为 $K$ 个簇（$K$ 为天数）。**硬性容量约束：** 单日簇内的（游玩时长 + 预估通勤） $\le 10$ 小时，且单日动态预算消耗占比不得引起后续天数资金断裂。
* **第二阶段（独立时变布线）：** 对每日的子簇并行执行 NSGA-II（非支配排序遗传算法）求解，时间复杂度降阶为 $\mathcal{O}(K \times (N/K)!)$。

#### 3. 时变路网与多模态交通划分 (Multiplex TD-Network)

底层路网抽象为多层网络 $\mathcal{G} = (\mathcal{V}, \mathcal{E}, \mathcal{L})$，层级 $\mathcal{L} = \{ \text{Driving}, \text{Transit}, \text{Walking} \}$。

* **时变阻抗张量：** 算法查阅三维张量 $\mathcal{T}_{N \times N \times H}$（$H$ 为离散时间片），规避早晚高峰路段。
* **动态交通方式降级（Mode Choice）：** 当两点间打车边权成本突增（$C_{driving} > \Delta C$），且时间收益 $\le 15$ 分钟时，算法强制切断 Driving 边，启用 Transit（地铁/公交）边以保护预算红线。

---

### 三、 多智能体工作流与算子编排拓扑 (Workflow Topology)

采用改良版的 Map-Compute-Reduce 架构，通过 LangGraph 进行确定性状态机流转。

#### 阶段 I：Map (多 Agent 并发数据召回)

所有前置 Agent 通过 MCP Client 调用外部 API，只负责特征提取，不进行推理。

* **AttractionSearchAgent (景点专家)：** 解析偏好，调用高德 MCP 召回高维 POI 集合 $\mathcal{V}$。
* **HotelAgent (酒店专家)：** 锁定拓扑计算的起终点锚定坐标 $v_0$。
* **WeatherQueryAgent (天气专家)：** 输出气象时段约束矩阵（如：14:00-16:00 排除室外节点）。
* **FinanceAgent (财务专家)：** 提取 $B_{max}$，调用金融 MCP 获取目标城市基准消费费率与实时外汇牌价。

#### 阶段 II：Compute (纯工程计算中枢)

接管 Map 阶段的离散坐标，执行物理与财务求解。

* **`Node_Matrix_Builder`：** 采用分块矩阵并发策略（Block Matrix Chunking），向高德批量请求生成 $\mathcal{T}$ (时间) 与 $\mathcal{D}$ (距离) 张量。
* **`Node_VRP_Solver`：** 执行上述的两阶段运筹学寻优，输出确定的拓扑路线。
* **`Node_Budget_Evaluator`：** 沿生成的有向计算图进行成本线积分运算：

$$C_{total} = \sum C_{fixed} + \sum (\mathcal{D}_{i,j} \times \text{Rate}_{mode}) + \sum C_{food}$$



若超标，抛出异常信号回流至 VRP Solver 触发局部剪枝。
* **`Node_Context_Compressor`：** 执行 JSON 树修剪，将高维数据降维为纯文本时序断言（如 `[09:00, $5, Transit] -> [09:30, Museum]`），严防大模型上下文溢出。

#### 阶段 III：Reduce (大模型语义对齐)

* **PlannerAgent (规划专家)：** 消费高度压缩的时序断言与财务明细，生成附带情绪价值、避坑提示的结构化导游词。

---

### 四、 动态交互与渲染基建 (Dynamic Interaction & Render)

#### 1. 局部增量重规划 (Incremental Re-optimization)

当用户在前端拖拽更改行程节点时，禁止触发全局图重算。

* **算法机制：** LangGraph 挂起进入 `interrupt` 状态。触发**单点插入算法（Cheapest Insertion Heuristic）**，寻找使得目标函数增量 $\Delta$ 最小的拓扑位置。
* **冲突消解：** 结合**碰撞检测（Collision Detection）**，若新节点的插入导致后续节点违背时间窗约束，仅对受波及的子图执行“破损-修复（Ruins and Recreates）”操作。

#### 2. 流式中间态监控 (Streaming Intermediate States)

消除后台求解时的黑盒等待，降低 TTFT（首字元响应时间）焦虑。

* 通过 WebSocket 建立双向通信，`Node_VRP_Solver` 在迭代计算时，高频推送当前的 Epoch 轮次与适应度成本下降曲线（Fitness Curve）至前端。

#### 3. GIS 渲染与 Headless 导出

* **GIS 抽稀：** 前端 Mapbox GL 针对后端返回的稠密路网坐标串，执行**道格拉斯-普克（Douglas-Peucker）算法**降维，保证 WebGL 渲染帧率稳定。
* **无头导出：** 后端集成 Puppeteer + Celery 异步队列，在高分辨率 Headless 浏览器环境内，将 DOM 树、ECharts 实例与底图静态化为 PDF 或图片流。

---

### 五、 系统级容错与 SLA (Fault Tolerance)

| 风险节点 | 限流与触发边界 | 容错兜底策略 (Fallback Strategy) |
| --- | --- | --- |
| **路网矩阵构建** | 高德 API 并发超过阈值或网络熔断。 | 连续 3 次带指数退避重试失败后，阻抗张量生成器退化为利用**哈弗曼公式（Haversine）**计算球面物理距离，并乘以城市曲折系数 $\rho=1.4$ 拟合通行时间，确保求解链不断裂。 |
| **运筹求解超时** | 约束过度严格或节点过多导致求解超过预设毫秒级阈值。 | 直接中断启发式搜索，返回当前种群中的**局部最优解（Local Optima）**。 |
| **数据真空** | 偏门 POI 数据源缺失部分属性。 | 容许价格或营业时间缺失（以目标城市均值/默认时间窗填充）。**严禁坐标缺失**，无经纬度节点在 Map 阶段直接硬性剔除。 |

---

### 六、 全局状态模型 (Global State Schema)

严格采用 Pydantic 进行静态类型校验（Single Source of Truth 原则）：

* **`Intent_Constraints`**：`user_query`, `destination`, `time_window_baseline`, `budget_limit` ($B_{max}$).
* **`Financial_Context`**：`exchange_rate`, `base_transit_fare`, `avg_meal_cost`.
* **`Spatial_Graph_Data`**：`poi_candidates` (含固定成本 $C_{fixed}$), `time_dependent_tensor`.
* **`Routing_Solution`**：`optimized_route` (包含时序、交通方式、动态花销), `budget_breakdown`.
* **`Graph_Controls`**：状态机游标 `current_status` 与阻断器 `edit_trigger`。

---

### 七、 全栈项目工程目录树 (Project Structure)

```text
trip-planner/
├── backend/                             # Python 核心后端
│   ├── app/
│   │   ├── agents/                      # LLM 智能体层 (Map / Reduce 职能)
│   │   │   ├── attraction_agent.py
│   │   │   ├── hotel_agent.py
│   │   │   ├── weather_agent.py
│   │   │   ├── finance_agent.py
│   │   │   └── planner_agent.py
│   │   ├── algorithms/                  # 纯工程运筹学与图计算算法层 (Compute 职能)
│   │   │   ├── clustering.py            # 容量受限 K-Means/DBSCAN 实现
│   │   │   ├── vrp_solver.py            # TD-VRPTW 求解器 (NSGA-II)
│   │   │   └── budget_evaluator.py      # 有向计算图的财务积分算子
│   │   ├── graph/                       # LangGraph 状态机编排
│   │   │   ├── state.py                 # Pydantic Global State Schema
│   │   │   ├── nodes.py                 # 工程算子节点封装
│   │   │   ├── edges.py                 # 路由规则、冲突回退与中断条件边
│   │   │   └── workflow.py              # 图实例化、编译与挂起配置
│   │   ├── mcp_server/                  # Model Context Protocol 工具注册中心
│   │   │   ├── amap_tools.py            # 封装高德 POI 与 Distance Matrix API
│   │   │   └── finance_tools.py         # 封装汇率与区域消费基准 API
│   │   ├── api/                         # FastAPI 路由通信层
│   │   │   ├── ws_router.py             # WebSocket (流式算法中间态推送)
│   │   │   └── http_router.py           # RESTful 接口 (任务提交、导出触发)
│   │   ├── services/                    # 基础设施中间件层
│   │   │   ├── export_service.py        # 基于 Headless Chrome 的渲染分发
│   │   │   └── cache_service.py         # Redis 缓存层 (路网距离重用与限流控制)
│   │   └── core/
│   │       ├── config.py
│   │       └── exceptions.py            # 定义运筹求解崩溃、预算超标等特定异常
│   ├── tests/
│   └── requirements.txt
│
└── frontend/                            # 前端交互与地理信息渲染
    ├── src/
    │   ├── api/                         # 统筹 HTTP/WS 通信
    │   ├── components/
    │   │   ├── MapViewer.vue            # WebGL 底图加载与道格拉斯-普克抽稀轨迹渲染
    │   │   ├── RouteEditor.vue          # 拖拽排序面板 (触发局部 Cheapest Insertion)
    │   │   └── BudgetDashboard.vue      # ECharts 数据双向绑定面板
    │   ├── stores/                      # 严密同步后端的 Global State
    │   │   └── tripStore.ts
    │   ├── views/
    │   │   └── PlannerWorkspace.vue
    │   ├── types/                       # TypeScript Interface (硬对齐 Pydantic Schema)
    │   └── router/
    ├── tailwind.config.js
    └── package.json

```