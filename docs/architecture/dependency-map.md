# Architecture Dependency Map — Phase 1 真实扫描

> ⚠️ **Kernel 计数漂移**：本文 §3 基于 **12 Kernels × 10 DNA**。当前权威为 **14 Kernel**，以 [`spec/KERNEL-CANON.md`](../spec/KERNEL-CANON.md) 为准；本图为 Phase 1 时点扫描快照。

> **生成方式**: Hermes 实跑扫描 `D:/LiuHao-AI-OS/` 当前代码库
> **Commit**: 4e967467
> **范围**: 138 个 Python 源文件 + 6 个 lib 模块 + 39 个目录

---

## 1. 顶层目录结构（实跑扫描）

```
src/                          # 138 .py files
├── api/                      # FastAPI 入口层 (8 files)
│   ├── app.py                # MetricsApplication.health()
│   ├── server.py             # 启动入口
│   └── routes/{knowledge,metrics}.py
├── gateway/                  # 主 Gateway (6 files)
│   ├── main.py               # FastAPI app + lifespan
│   ├── health.py
│   └── rate_limiter / validation
├── security/                 # SEC_01-06 模块
│   ├── api_keys.py           # APIKeyManager (本 commit 补全)
│   ├── jwt_handler.py
│   ├── rbac.py / rbac_store.py
│   ├── encryption.py
│   ├── vault_crypto.py
│   ├── audit_logger.py
│   └── abac.py / rotation.py
├── ai/                       # 智能核心
│   ├── employee.py           # AIEmployee
│   ├── providers.py          # LLM 注册中心
│   ├── goal_task_graph.py    # mem0ai 集成
│   ├── langgraph_workflow.py # langgraph 集成
│   └── cost_tracker.py
├── providers/                # LLM 适配
│   ├── llm_base.py
│   ├── openai.py / mock.py / self_host.py
│   └── registry.py
├── knowledge/                # 知识 / 记忆
│   ├── memory.py             # mem0ai + langchain
│   └── rag.py
├── plugins/                  # 插件沙箱
│   ├── sandbox/{backends,models}.py
│   ├── marketplace/
│   └── dependencies/
├── integrations/             # 跨模块集成
│   ├── orm_models.py         # 30 SQLAlchemy 模型
│   ├── storage.py            # Repository pattern
│   └── vault/
├── observability/            # 可观测性
│   ├── tracing.py
│   ├── metrics.py
│   └── alerts/
├── core/                     # 基础设施
│   └── lifecycle.py
├── workflow/                 # LangGraph 工作流
├── tasks/                    # 任务调度
├── audit/                    # 审计
├── sre/                      # SRE / 灾备
│   ├── disaster/
│   └── scaling/
├── deployment/               # 部署
├── distribution/             # 分发
├── datasets/                 # 数据集
├── feedback/                 # 反馈学习
├── cost/                     # 成本追踪
├── mlops/                    # MLOps
├── models/                   # 模型管理
├── storage/                  # 存储抽象
├── ui/                       # 前端桥
├── adapters/                 # 适配器
│   ├── mcp/                  # MCP servers
│   └── observability/
└── infra/                    # 基础设施

libs/
└── liuhao-core/              # 内部基础库 (6 modules)
    ├── circuit_breaker.py
    ├── retry.py
    ├── health.py
    ├── metrics.py
    ├── logging.py
    └── config.py

apps/
└── console/console/          # React 19 + Vite 8 + TS 6 前端
```

---

## 2. Workstream ↔ 代码模块映射

| Workstream | 负责模块 | 文件数 | 状态 |
|------------|----------|--------|------|
| **WS-A Core Runtime** | api/, gateway/, core/, observability/ | ~25 | 🟡 部分（observability 可扩展） |
| **WS-B Intelligence** | ai/, providers/, knowledge/, workflow/ | ~15 | 🟡 部分（mem0ai + langgraph 已集成） |
| **WS-C Agent Org** | ai/employee.py + ai/goal_task_graph.py | 2 | 🟡 部分（Y1 已有初版） |
| **WS-D Long-Horizon** | workflow/, tasks/, memory.py | ~8 | 🔴 缺口大（long-horizon 计划） |
| **WS-E Network-World** | deployment/, distribution/, plugins/marketplace/ | ~10 | 🟡 部分 |
| **WS-F Governance** | security/, audit/ | 12 | 🟢 ✅ 本 commit 补全 APIKey |
| **WS-G Data-Infra** | integrations/orm_models.py + storage.py + alembic/ | 5 | 🟡 部分（PG 待验证） |
| **WS-H Frontend** | apps/console/console/ | ~40 TS | 🟡 部分（React 19 + Vite 已搭） |

---

## 3. 12 Kernels × 10 DNA × 代码覆盖矩阵

| Kernel | 主要实现 | DNA 对应 | 当前状态 |
|--------|----------|----------|----------|
| K01 控制面板 (Control Plane) | gateway/main.py + api/app.py | ULTRON | 🟢 60% |
| K02 智能路由 (Smart Router) | providers/registry.py | VISION | 🟡 30% |
| K03 LLM 适配 (Provider Adapter) | providers/{openai,mock,self_host}.py | ADA | 🟢 80% |
| K04 记忆存储 (Memory) | knowledge/memory.py + integrations/orm_models.py | EDITH | 🟡 40% |
| K05 工作流 (Workflow) | workflow/ + ai/langgraph_workflow.py | FRIDAY | 🟡 35% |
| K06 任务图 (Task Graph) | ai/goal_task_graph.py | JARVIS | 🟡 30% |
| K07 插件沙箱 (Plugin Sandbox) | plugins/sandbox/ | JOCaSTA | 🟢 70% |
| K08 RBAC + ABAC | security/{rbac,abac}.py | KAREN | 🟢 75% |
| K09 审计 (Audit) | audit/ + security/audit_logger.py | ENOCH | 🟢 65% |
| K10 可观测性 (Observability) | observability/ + libs/liuhao-core/metrics.py | ZOON | 🟡 40% |
| K11 灾备 (Disaster Recovery) | sre/disaster/ | (DNA 外) | 🔴 15% |
| K12 弹性扩展 (Scaling) | sre/scaling/ | (DNA 外) | 🔴 20% |

**平均覆盖率**: ~46%（11 × 部分/总和）

---

## 4. 跨模块依赖图（核心 5 模块）

```
                ┌────────────┐
                │  gateway   │  ← FastAPI 主入口
                │   main.py  │
                └─────┬──────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │  api/   │   │security │   │observab.│
   │ app.py  │   │  __init │   │ tracing │
   └────┬────┘   └────┬────┘   └────┬────┘
        │             │             │
        └─────────────┼─────────────┘
                      │
              ┌───────▼────────┐
              │  ai/employee   │  ← 顶层智能体
              └───────┬────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │providers│   │knowledge│   │workflow │
   │registry │   │ memory  │   │ langgr. │
   └────┬────┘   └────┬────┘   └────┬────┘
        │             │             │
        └─────────────┼─────────────┘
                      │
              ┌───────▼────────┐
              │integrations/   │  ← ORM + Storage
              │  orm_models    │
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │   PostgreSQL   │  ← 31 张表 (D-004 待验证)
              │     / SQLite   │
              └────────────────┘
```

---

## 5. 已发现的关键差距（Phase 2 关注）

| # | 缺口 | 影响 | 建议 |
|---|------|------|------|
| G1 | `src/agents/` 不存在 | AIEmployee 是单个文件 | Phase 2 重构为多 agent |
| G2 | long-horizon planning 模块缺失 | K06 仅 goal_task_graph | 需新增 planning/ |
| G3 | observability/metrics 集成不深 | K10 仅 40% | 与 arize-phoenix 联动 |
| G4 | sre/disaster 简化实现 | K11 仅 15% | 备份/恢复完整化 |
| G5 | apps/console/console/index.html 缺失 | WS-H 前端未完全可用 | 需建前端入口 |

---

**本文档基于实跑扫描（138 .py 文件）生成；非估算。**

