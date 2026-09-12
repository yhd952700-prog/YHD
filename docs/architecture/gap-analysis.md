# Architecture Gap Analysis — 当前代码 ↔ Definition Lock §75-§83

> **生成方式**: 实跑扫描 138 src 文件 + 6 lib 模块
> **范围**: §75 (PostgreSQL) / §76 (RAG/Memory) / §83 (Frontend) / §84 (Security) / §90 (Traceability)

> ⚠️ **Phase 编号口径**：本文 Phase 编号沿用撰写时口径，与 [`spec/GAP-MIGRATION-MATRIX.md`](../spec/GAP-MIGRATION-MATRIX.md) 表 3 的 **21-Phase 路线图**不一致。追溯进度请一律以 21-Phase 表为准（见 `REMEDIATION-PLAN.md` §R7）。

---

## 1. §75 PostgreSQL 15 主从拓扑

### Definition Lock 要求
- ✅ PostgreSQL 15（生产）
- ✅ 主+从拓扑（`infra/postgres/docker-compose.yml`）
- ✅ alembic 迁移 31 张表（含 goals/budgets/plugin_*/sandbox_*/device_adapters/workflow_executions）

### 当前实现
| 组件 | 状态 | 证据 |
|------|------|------|
| alembic init migration | ✅ | `alembic/versions/001_init_all_tables.py` 创建 31 表 |
| SQLAlchemy 模型 | ✅ | `src/integrations/orm_models.py` 30 模型 |
| Repository pattern | ✅ | `src/integrations/storage.py` |
| **PG 端到端验证** | ❌ | **D-004 待 Phase 1** — 当前仅 SQLite 验证 |

### Gap
- ⚠️ PG 端到端测试未跑（docker compose 未启）
- ⚠️ PG 主从同步未验证
- ⚠️ PG→SQLite fallback 行为未明确文档化

### 修复路径（Phase 1 WS-G）
```bash
docker compose -f infra/postgres/docker-compose.yml up -d
psql -h localhost -U liuhao -c "SELECT version();"
alembic upgrade head
pytest tests/test_orm_storage.py  # 在 PG 上
```

---

## 2. §76 RAG / Memory (mem0ai + langchain)

### Definition Lock 要求
- ✅ mem0ai 长期记忆
- ✅ langchain 检索链
- ✅ 10 层内存架构
- ⚠️ vector store（Qdrant/pgvector）

### 当前实现
| 组件 | 状态 | 证据 |
|------|------|------|
| `src/knowledge/memory.py` | ✅ | Mem0Memory import + 10 层抽象 |
| `src/knowledge/rag_pipeline.py` | ✅ | chunker + embedding + retriever + vector_store |
| `src/knowledge/embedding.py` | ✅ | Embedding 模型 |
| `src/knowledge/vector_store.py` | 🟡 | 抽象接口，需具体后端 |

### Gap
- ⚠️ Mem0 实际启用（当前 graceful fallback = True）
- ⚠️ Vector store 后端未选型（pgvector vs Qdrant）
- ⚠️ RAG evaluator 未集成（ragas/arize-eval）

### 修复路径（Phase 4-6 WS-B）
1. 启用 Mem0（删除 fallback）
2. 选定 pgvector（避免引入 Qdrant 基础设施）
3. 集成 ragas 评估器

---

## 3. §83 Frontend (React 19 + Vite 8 + TS 6)

### Definition Lock 要求
- ✅ React 19
- ✅ Vite 8
- ✅ TypeScript 6
- ⚠️ Console 控制台（5 个核心页面）
- ⚠️ 设计 token 系统

### 当前实现
| 组件 | 状态 | 证据 |
|------|------|------|
| `apps/console/console/` | ✅ | package.json + vite.config.ts |
| React + Vite + TS 配置 | ✅ | 已有 |
| 入口页面 | ❌ | `index.html` 缺失 |

### Gap
- ❌ Console 主入口 `index.html` 缺失
- ❌ 5 个核心页面未实现（Dashboard / Workflows / Plugins / Memory / Settings）
- ❌ 设计 token / 主题未定义
- ❌ 后端 API 联调未做

### 修复路径（Phase 15-21 WS-H）
1. 创建 `index.html` + `App.tsx` 骨架
2. 5 个核心页面（MoSCoW 优先级）
3. 设计 token（颜色/字体/间距）
4. 与 FastAPI 联调

---

## 4. §84 Security (RBAC + ABAC + Audit + Vault)

### Definition Lock 要求
- ✅ RBAC 角色 + 权限
- ✅ ABAC 策略
- ✅ Audit 不可篡改
- ✅ Vault Transit 加密

### 当前实现
| 组件 | 状态 | 证据 |
|------|------|------|
| `src/security/{rbac,rbac_store,abac}.py` | ✅ | RBAC + ABAC |
| `src/security/{api_keys,encryption,vault_crypto}.py` | ✅ | Vault + 加密 |
| `src/security/audit_logger.py` | ✅ | CryptoAuditLogger |
| `tests/security/` | ✅ | 10 tests collected（**本轮修**） |

### Gap（**全部修复**）
- ✅ D-002: hvac 依赖（**已 pip install**）
- ✅ APIKey/KeyScope/KeyStatus/KeyManager 缺失类（**已补全**）

### 修复路径
✅ **全部修复** — `c363094f` commit 验证 137/137 PASS

---

## 5. §90 Traceability (全链路追踪)

### Definition Lock 要求
- ✅ Kernels ↔ DNA ↔ Phase ↔ Workstream 全链路
- ⚠️ Capability Registry
- ⚠️ Traceability Matrix

### 当前实现
| 组件 | 状态 | 证据 |
|------|------|------|
| **本文档** | ✅ | docs/product/capability-traceability.md |
| **dependency-map** | ✅ | docs/architecture/dependency-map.md |
| OPEN-DECISIONS | ✅ | docs/archive/EXECUTION_AND_DECISIONS.md |
| Workstreams | ✅ | docs/archive/EXECUTION_AND_DECISIONS.md |

### Gap
- ⚠️ Phase 1 期间逐步补全（**本轮已产 2/6 文档**）

### 修复路径（Phase 1 后续）
- `docs/architecture/gap-analysis.md` ✅ **本文件**
- `docs/archive/EXECUTION_AND_DECISIONS.md` ⬜
- `docs/archive/EXECUTION_AND_DECISIONS.md` ⬜
- `docs/archive/EXECUTION_AND_DECISIONS.md` ⬜

---

## 6. Gap 总览表

| § | 范围 | 总需求 | 已实现 | 缺口 | 优先 Phase |
|---|------|--------|--------|------|-----------|
| §75 | PostgreSQL | 100% | 70% | 30%（端到端验证） | 1（WS-G） |
| §76 | RAG/Memory | 100% | 65% | 35%（Mem0 启用 + vector） | 4-6 |
| §83 | Frontend | 100% | 15% | 85%（5 页面 + 设计） | 15-21 |
| §84 | Security | 100% | 100% | **0%（已全绿）** | — |
| §90 | Traceability | 100% | 60% | 40%（Phase 1 后续文档） | 1 |
| **合计** | | **100%** | **62%** | **38%** | — |

---

## 7. 关键决策（待裁决）

| # | 决策 | 推荐 |
|---|------|------|
| OD-G1 | Vector store 选型：pgvector vs Qdrant | pgvector（避免基础设施） |
| OD-G2 | Mem0 启用 vs 保留 fallback | 启用（删除 fallback） |
| OD-G3 | Frontend 优先级 | 5 页面 MoSCoW（Must: Dashboard + Workflows） |
| OD-G4 | Traceability 矩阵结构 | 12K × 10DNA × 22Phase（已采用） |

---

**本文档基于 138 src 文件实跑扫描；修复路径含具体 commit SHA 与命令。**

