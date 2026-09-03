# LiuHao AI OS (镭灏) — 开源项目集成计划

**基线**: Y1 90.3% 完成 (MASTER_BLUEPRINT_Y1_FINAL.md)  
**目标**: 引入成熟开源组件，补齐能力短板，向 JARVIS 级助手演进  
**原则**: 能用成熟库不造轮子、License 兼容、MCP/A2A 互操作优先、保持架构解耦

---

## 1. 集成优先级矩阵

| 优先级 | 项目 | 替换/增强模块 | 核心价值 | 预估工时 | 风险等级 |
|--------|------|---------------|----------|----------|----------|
| **P0-1** | **Mem0** (mem0ai/mem0) | `src/knowledge/memory.py` (10层记忆) | 多级记忆、自适应个性化、MCP原生、CLI/SDK双模 | 2-3天 | 低 (MIT, 活跃) |
| **P0-2** | **LangGraph** (langchain-ai/langgraph) | `src/ai/goal_task_graph.py` + `src/workflow/` | DAG状态机、持久化检查点、HITL、生产部署文档完善 | 3-5天 | 低 (MIT, 41K⭐) |
| **P1-1** | **Phoenix** (Arize-ai/phoenix) | `src/observability/` + 新增评估层 | OTel tracing + LLM-as-judge + drift detection 一体化 | 2-3天 | 低 (Apache-2.0) |
| **P1-2** | **MCP** (modelcontextprotocol) | `src/ai/tools.py` (Tool System) | 标准化Agent↔Tool协议、100+现成工具、生态互操作 | 2-3天 | 低 (MIT, 标准化) |
| **P2-1** | **Ragas** / **DeepEval** | CI/CD 质量门控 | RAG/通用评估、回归测试、CI集成 | 1-2天 | 低 |
| **P2-2** | **CrewAI** / **AutoGen** | `src/ai/employee.py` (多Agent编排) | 角色制协作、复杂拓扑、代码执行Agent | 3-4天 | 中 (参考而非直接替换) |
| **P3** | **Composio** / **E2B** | 工具生态扩展 | 100+集成、沙箱执行、浏览器自动化 | 2-3天 | 中 |

---

## 2. 详细集成映射

### 2.1 Mem0 → 记忆层 (P0-1)

**现有**: `src/knowledge/memory.py` - 10层记忆、权限控制、过期清理、审计  
**目标**: 增加向量检索、跨会话持久化、自适应个性化、MCP接口

| 现有能力 | Mem0 增强 | 集成点 |
|----------|-----------|--------|
| 10层内存结构 | 向量语义检索 + 图谱关系 | `Memory.search()` → 语义召回 |
| 权限/过期/审计 | 用户/会话/Agent三级隔离 | `Memory.add(user_id, agent_id, metadata)` |
| 模型解耦 | 多LLM提供商支持 (OpenAI/Anthropic/Ollama) | `Memory(llm_client=...)` |
| 无 | MCP Server 原生支持 | `mem0 init --agent` 零配置接入 |

**迁移路径**:
```python
# 当前
from src.knowledge.memory import Memory
mem = Memory()

# 目标 (保持接口兼容)
from src.knowledge.memory import Memory  # 内部委托 Mem0
mem = Memory()  # 自动使用 Mem0 作为后端
```

**验收标准**:
- [ ] 现有单测 100% 通过 (接口不变)
- [ ] 新增: 跨会话记忆召回准确率 > 85%
- [ ] 新增: MCP Client 可连接 Mem0 Server
- [ ] 性能: 检索延迟 < 100ms (p99)

---

### 2.2 LangGraph → Goal→Task Graph + Workflow Engine (P0-2)

**现有**: `src/ai/goal_task_graph.py` + `src/workflow/planner.py` + `executor.py`  
**目标**: 获得生产级 DAG 引擎、状态持久化、Human-in-the-loop、可视化调试

| 现有能力 | LangGraph 增强 | 集成点 |
|----------|----------------|--------|
| Goal→Plan→Task Graph | 编译型 StateGraph、类型安全 State | `GoalTaskGraph` 内部委托 `StateGraph` |
| 任务依赖拓扑 | 条件边、循环、并行分支、Map-Reduce | `add_conditional_edges()` |
| 执行器 | 检查点持久化 (PostgreSQL/SQLite/Redis) | `checkpointer=PostgresSaver()` |
| 无 | Human-in-the-loop 断点 | `interrupt_before=["review"]` |
| 无 | LangGraph Studio 可视化调试 | 开发期生产力提升 |

**迁移路径**:
```python
# 当前: 手写拓扑排序
from src.ai.goal_task_graph import GoalTaskGraph
graph = GoalTaskGraph()
tasks = graph.decompose_goal(goal)

# 目标: LangGraph 编译图
from langgraph.graph import StateGraph
from src.ai.goal_task_graph import GoalTaskGraph  # 适配器

class GraphState(TypedDict):
    goal: GoalDefinition
    tasks: List[Task]
    current_task: Optional[Task]
    results: Dict[str, Any]
    review_feedback: Optional[str]

graph = GoalTaskGraph()  # 内部构建 StateGraph
result = graph.invoke({"goal": goal})  # 自动执行+检查点
```

**验收标准**:
- [ ] 现有 4/4 Integration Tests 通过
- [ ] 新增: 检查点恢复测试 (中断→恢复→完成)
- [ ] 新增: HITL 断点测试 (人工介入→继续)
- [ ] 新增: 并行分支执行正确性
- [ ] 可视化: LangGraph Studio 可加载图结构

---

### 2.3 Phoenix → Observability + Evaluation (P1-1)

**现有**: `src/observability/` (Logging + Metrics + Tracing) + `docker-compose.observability.yml`  
**目标**: 统一 tracing+eval 平台、自托管、Agent 原生仪表板

| 现有能力 | Phoenix 增强 | 集成点 |
|----------|--------------|--------|
| 结构化日志 + Correlation ID | 统一 OTel Collector 接收 | `otel-collector` → Phoenix OTLP endpoint |
| Prometheus Metrics (40+) | LLM 指标自动采集 (token、latency、cost) | `Phoenix.trace()` 自动插桩 |
| Tempo Traces | 评估数据集、实验对比、回归检测 | `phoenix.evals.run()` |
| Grafana 21面板 | Agent 原生仪表板 (traces、spans、evals) | 替代/补充 Grafana |
| AlertManager 3规则 | Drift detection、性能退化告警 | Phoenix alerts → AlertManager |

**架构调整**:
```
当前: App → OTel Collector → Tempo + Prometheus + Loki → Grafana
目标: App → OTel Collector → Phoenix (traces+evals) + Prometheus (metrics) + Loki (logs) → Grafana
```

**验收标准**:
- [ ] 现有 CI Observability Check 通过
- [ ] 新增: Phoenix UI 可看到完整 Agent trace (planner→agents→tools)
- [ ] 新增: 创建 1 个评估数据集 + 1 个回归实验
- [ ] 新增: Drift 告警在 AlertManager 触发

---

### 2.4 MCP → Tool System 标准化 (P1-2)

**现有**: `src/ai/tools.py` - ToolRegistry、ToolDiscovery、ToolInvocation、权限/风险/审计  
**目标**: 对外暴露 MCP Server、接入 MCP 生态工具、双向互操作

| 现有能力 | MCP 增强 | 集成点 |
|----------|----------|--------|
| Tool Schema (JSON Schema) | MCP Tool 定义兼容 | `Tool.to_mcp()` / `MCPTool.to_internal()` |
| 权限/风险/超时/重试 | MCP 权限模型映射 | `ToolPermission` ↔ `MCPAuthorization` |
| 审计日志 | MCP 调用链路追踪 | Correlation ID 透传 |
| 无 | 100+ 现成 MCP Server (文件系统、GitHub、数据库、浏览器...) | `MCPClient.discover_tools()` |
| 无 | 作为 MCP Server 暴露给外部 Agent | `MCPServer.from_tool_registry()` |

**双向模式**:
```
外部 Agent (Claude Code, Cursor) → MCP Client → LiuHao MCP Server → 内部 Tool Registry
LiuHao Agent → MCP Client → 外部 MCP Server (GitHub, PostgreSQL, Browser) → 结果
```

**验收标准**:
- [ ] 现有 Tool 测试 100% 通过
- [ ] 新增: 作为 MCP Server 启动，Claude Code 可调用内部工具
- [ ] 新增: 作为 MCP Client 调用 filesystem/github server
- [ ] 权限/审计/超时在 MCP 边界生效

---

### 2.5 Ragas/DeepEval → CI 质量门 (P2-1)

**现有**: `scripts/ci_observability_check.py` + 11 单元/集成测试  
**目标**: 自动化 LLM 质量回归防护

| 场景 | 工具 | 指标 | CI 集成 |
|------|------|------|---------|
| RAG 问答 | Ragas | Faithfulness、Context Precision、Answer Relevance | `pytest --ragas` |
| Agent 轨迹 | DeepEval | Task Completion、Tool Correctness、Hallucination | `pytest --deepeval` |
| 多 Agent 协作 | 自定义 | Coordination Score、Aggregation Quality | `pytest --agent-eval` |

**CI Pipeline 扩展**:
```yaml
# .github/workflows/ci.yml 新增
- name: RAG Evaluation
  run: python -m pytest tests/eval_rag.py --ragas --fail-under=0.8
- name: Agent Trajectory Evaluation  
  run: python -m pytest tests/eval_agent.py --deepeval --fail-under=0.75
```

---

## 3. 依赖版本锁定策略

```toml
# pyproject.toml 新增
[tool.uv.sources]
mem0 = { git = "https://github.com/mem0ai/mem0", tag = "v1.2.0" }
langgraph = "==0.2.34"
arize-phoenix = "==4.12.0"
mcp = { git = "https://github.com/modelcontextprotocol/python-sdk", tag = "v1.0.0" }
ragas = "==0.1.23"
deepeval = "==1.0.15"

# 约束: 仅锁定主版本，允许补丁自动更新
# 定期 (月度) 运行: uv lock --upgrade-package <pkg>
```

---

## 4. License & 合规清单

| 项目 | License | 商用友好 | 依赖传递风险 | 结论 |
|------|---------|----------|--------------|------|
| Mem0 | MIT | ✅ | 低 | 可直接集成 |
| LangGraph | MIT | ✅ | 低 (LangChain 核心) | 可直接集成 |
| Phoenix | Apache-2.0 | ✅ | 低 | 可直接集成 |
| MCP SDK | MIT | ✅ | 无 | 可直接集成 |
| Ragas | Apache-2.0 | ✅ | 中 (LangChain) | 需验证传递依赖 |
| DeepEval | MIT | ✅ | 低 | 可直接集成 |
| CrewAI | MIT | ✅ | 中 | 仅参考不强依赖 |
| AutoGen | MIT | ✅ | 低 | 仅参考不强依赖 |

**红线**: 无 GPL/AGPL/SSPL 依赖进入核心链路

---

## 5. 迁移风险评估与缓解

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|----------|
| LangGraph API 破坏性变更 | 高 | 中 | 锁定版本、编写适配器层、集成测试覆盖核心路径 |
| Mem0 托管版与开源版功能差异 | 中 | 高 | 优先自托管、仅用开源 SDK 功能、避免托管专有特性 |
| Phoenix 资源占用 (Java/ClickHouse) | 中 | 中 | 提供 Lite 模式 (SQLite backend)、资源限制配置 |
| MCP 协议演进不稳定 | 中 | 低 | 锁定协议版本、抽象传输层、保持向后兼容 |
| 依赖链膨胀 | 中 | 高 | 定期 `uv tree --depth=2` 审计、最小化传递依赖 |

---

## 6. 实施里程碑

| 里程碑 | 交付物 | 截止 | 验收标准 |
|--------|--------|------|----------|
| **M1 (Week 1)** | Mem0 集成 + 单测通过 | Day 3 | 现有测试 100% 过、新增跨会话记忆测试过 |
| **M2 (Week 2)** | LangGraph 替换 GoalTaskGraph | Day 8 | 4/4 测试过、检查点恢复测试过、HITL 测试过 |
| **M3 (Week 3)** | Phoenix 部署 + 评估数据集 | Day 12 | UI 可见完整 trace、1 个回归实验跑通 |
| **M4 (Week 4)** | MCP Server/Client 双向互通 | Day 16 | Claude Code 调用内部工具、内部调用外部 MCP |
| **M5 (Week 5)** | CI 质量门 + 文档收尾 | Day 20 | PR 自动跑评估、失败阻断合并 |

---

## 7. 代码仓库结构调整

```
D:\LiuHao-AI-OS├── src/
│   ├── ai/
│   │   ├── providers.py          # 保持 (Provider 抽象)
│   │   ├── employee.py           # 增强: 委托 LangGraph/CrewAI
│   │   ├── goal_task_graph.py    # 重写: LangGraph 适配器
│   │   └── tools.py              # 增强: MCP 双向适配
│   ├── knowledge/
│   │   └── memory.py             # 重写: Mem0 后端适配器
│   ├── observability/
│   │   ├── logging_utils.py      # 保持
│   │   ├── metrics.py            # 保持 + Phoenix 指标桥接
│   │   ├── tracing.py            # 重写: Phoenix OTel 导出
│   │   └── evaluation.py         # 新增: Ragas/DeepEval 封装
│   └── adapters/                 # 新增: 统一适配器层
│       ├── mem0_adapter.py
│       ├── langgraph_adapter.py
│       ├── phoenix_adapter.py
│       └── mcp_adapter.py
├── tests/
│   ├── integration/              # 新增集成测试
│   │   ├── test_mem0_integration.py
│   │   ├── test_langgraph_checkpoint.py
│   │   ├── test_phoenix_eval.py
│   │   └── test_mcp_bidirectional.py
│   └── eval/                     # 新增评估测试
│       ├── test_rag_faithfulness.py
│       └── test_agent_trajectory.py
├── configs/
│   ├── phoenix/                  # Phoenix 配置
│   ├── mcp/                      # MCP Server 配置
│   └── observability/            # 现有 + Phoenix 集成
├── docker-compose.observability.yml  # 更新: 加入 Phoenix
├── docker-compose.mcp.yml        # 新增: MCP Server 栈
├── scripts/
│   ├── ci_observability_check.py # 现有
│   └── ci_quality_gates.py       # 新增: 评估门控
└── docs/
    ├── OPENSOURCE_INTEGRATION_PLAN.md  # 本文档
    └── ARCHITECTURE_DECISIONS.md       # ADR 记录
```

---

## 8. 首周行动清单 (Day 1-3: Mem0 集成)

- [ ] `uv add mem0ai` 安装依赖
- [ ] 创建 `src/adapters/mem0_adapter.py` 实现 `MemoryBackend` 协议
- [ ] 修改 `src/knowledge/memory.py` 注入适配器 (保持对外接口不变)
- [ ] 编写 `tests/integration/test_mem0_integration.py` (跨会话、MCP Client、性能)
- [ ] 运行全测试套件确保无回归
- [ ] 更新 `docs/Y1_REQUIREMENT_TRACEABILITY.md` 记录集成状态

---

## 9. 决策日志 (ADR 模板)

每个集成决策记录一条 ADR：

```markdown
# ADR-001: Adopt Mem0 as Memory Backend
## Status: Accepted
## Context: Y1 10层记忆缺乏语义检索、跨会话持久化、MCP互操作
## Decision: 引入 Mem0 开源版作为底层存储/检索引擎，保持上层接口不变
## Consequences: +语义检索 +MCP +个性化; -新增依赖 -需维护适配器
## Alternatives: Letta(重、耦合LangChain), MemGPT(学术原型), 自研向量检索(工时大)
```

---

## 10. 成功度量 (North Star Metrics)

| 指标 | Y1 Baseline | Target (Post-Integration) | 测量方式 |
|------|-------------|---------------------------|----------|
| **记忆召回准确率** | N/A | > 85% | Mem0 eval dataset |
| **工作流执行成功率** | ~90% (手写) | > 99% (检查点恢复) | LangGraph checkpoint test |
| **可观测性覆盖率** | Traces only | Traces + Evals + Drift | Phoenix dashboard |
| **工具生态覆盖** | ~20 内置 | 100+ (MCP) | MCP server registry count |
| **CI 回归检出率** | 0% (无评估) | > 80% | Ragas/DeepEval gate stats |
| **端到端延迟 (p99)** | ~2s | < 3s (含评估) | Phoenix latency panel |

---

*文档版本: 1.0 | 创建: 2026-09-02 | 依据: GitHub 搜索结果 + Y1 实证基线*
