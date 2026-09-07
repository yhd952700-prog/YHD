# Architecture v3.0 (Draft) — LIUHAO X Phase 1 调研稿

> **⚠️ DEPRECATED** — 本草稿已被 `architecture/Architect-Architecture-v3.0.md` 正式版取代。请参阅正式版。
>
> **状态**: Phase 1 调研稿（D-P1-3=A 要求三文档调研 + 用户确认）
> **Owner**: Workstream C (Architect)
> **依据**: Definition Lock §75-§90 + dependency-map.md + architecture-gap-analysis.md

---

## §1 系统图（System Diagram）

```
                       ┌─────────────────────────────────────┐
                       │      Human User / AI Engineer      │
                       └─────────────────┬───────────────────┘
                                         │ HTTPS / JWT / APIKey
                                         ▼
        ┌────────────────────────────────────────────────────────┐
        │              FastAPI Gateway (ULTRON)                  │
        │   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐    │
        │   │ CORS    │ │ GZip    │ │ OTel    │ │ RateLimit│   │
        │   └─────────┘ └─────────┘ └─────────┘ └─────────┘    │
        └─────┬───────────┬───────────┬───────────┬─────────────┘
              │           │           │           │
        ┌─────▼─────┐ ┌───▼───┐ ┌────▼────┐ ┌───▼──────┐
        │  Console  │ │Knowledge│ │Workflows│ │Plugins  │
        │  (H Front)│ │   API   │ │   API   │ │   API   │
        └─────┬─────┘ └───┬───┘ └────┬────┘ └───┬──────┘
              │           │           │           │
        ┌─────▼───────────▼───────────▼───────────▼─────────┐
        │           Application Services Layer               │
        │                                                    │
        │  AIEmployee ─► GoalTaskGraph ─► LangGraph         │
        │       │              │                │           │
        │       └────► Providers (openai/mock/self_host)    │
        │       │                                            │
        │       └────► Memory (Mem0 + 10-layer)              │
        │                                                    │
        │  RBAC + ABAC + Vault + AuditLog                    │
        └─────┬───────────┬───────────┬───────────┬─────────┘
              │           │           │           │
        ┌─────▼─────┐ ┌───▼───┐ ┌────▼────┐ ┌───▼──────┐
        │PostgreSQL │ │Mem0  │ │Qdrant/  │ │ Vault    │
        │15 (主+从) │ │Vector │ │pgvector │ │ (Transit)│
        └───────────┘ └───────┘ └─────────┘ └──────────┘
```

---

## §2 模块边界 (Module Boundaries)

### 2.1 WS-A Core Runtime
- **负责**: `src/api/`、`src/gateway/`、`src/core/`、`src/observability/`、`src/infra/`
- **接口**: FastAPI 主链 + Prometheus 指标 + OTel trace
- **不负责**: AI 业务逻辑（属于 WS-B）

### 2.2 WS-B Intelligence
- **负责**: `src/ai/`、`src/providers/`、`src/knowledge/`、`src/workflow/`
- **接口**: AIEmployee / Provider registry / LangGraph state
- **不负责**: HTTP 入口（属于 WS-A）

### 2.3 WS-C Agent Org
- **负责**: Agent Mesh（Phase 14 引入 `src/agents/`）
- **接口**: 多 Agent 协调 + 任务分发
- **不负责**: 单 Agent 内部逻辑（属于 WS-B）

### 2.4 WS-D Long-Horizon
- **负责**: Phase 13 引入 `src/planning/`
- **接口**: Planning API + Long-term scheduler
- **不负责**: 短期任务（属于 WS-B）

### 2.5 WS-E Network-World
- **负责**: `src/deployment/`、`src/distribution/`、`src/plugins/marketplace/`
- **接口**: 部署 API + 灰度/回滚
- **不负责**: 部署底层 K8s（复用基础设施）

### 2.6 WS-F Governance
- **负责**: `src/security/`、`src/audit/`
- **接口**: Auth API + RBAC + ABAC + Audit 查询
- **不负责**: 业务鉴权决策（属于各业务模块）

### 2.7 WS-G Data-Infra
- **负责**: `src/integrations/orm_models.py` + `src/storage/` + `infra/` + Alembic
- **接口**: ORM 模型 + 迁移 + 备份/恢复
- **不负责**: 业务查询（属于各业务模块）

### 2.8 WS-H Frontend
- **负责**: `apps/console/console/` + 设计 token
- **接口**: REST API + WebSocket
- **不负责**: 后端实现

---

## §3 数据流 (Data Flow)

### 3.1 写路径

```
User → Console → FastAPI Gateway → Business API → 
  → RBAC check → Service Logic → 
    → ORM (PostgreSQL via SQLAlchemy 2.0)
    → Mem0 (长期记忆)
    → Vault (密钥 / Transit 加密)
    → Audit Logger (不可篡改)
  → Response → Audit Trail → User
```

### 3.2 读路径（带缓存）

```
User → Console → Gateway → Cache (Redis) → ORM → 
  → Response (with trace_id) → ZOON (metrics)
```

### 3.3 异步流（workflow / event）

```
Trigger → LangGraph StateGraph →
  → Worker Pool (asyncio) →
    → External API (with retry / circuit breaker)
  → Checkpoint (PostgreSQL/SQLite)
  → Result + Audit
```

---

## §4 NFR (Non-Functional Requirements)

| 类别 | 指标 | 目标 |
|------|------|------|
| **性能** | P95 API 延迟 | < 200ms |
| **性能** | P99 API 延迟 | < 1s |
| **性能** | Workflow P95 | < 5s（含 LLM） |
| **可用性** | 系统 uptime | ≥ 99.95% |
| **扩展** | 水平扩缩容 | K8s HPA 自动 |
| **扩展** | DB 连接池 | 20-100 动态 |
| **安全** | TLS 1.3 强制 | 全链路 |
| **安全** | Vault 密钥 | 永不入库 |
| **审计** | 操作可追溯 | 100% |
| **审计** | 不可篡改 | hash chain |
| **可观测** | trace 完整 | 全链路 |
| **可观测** | metrics 完整 | RED + USE |
| **灾备** | RPO | < 5min |
| **灾备** | RTO | < 30min |
| **合规** | Human 检查点 | 100% 高风险 |
| **合规** | 决策可解释 | KAREN + ENOCH |

---

## §5 风险与缓解 (Risks & Mitigations)

| 风险 | 等级 | 缓解策略 |
|------|------|----------|
| 单点故障 | 中 | PG 主从 + Redis 集群 + 多 AZ |
| 模型不可用 | 中 | Provider Mesh + 降级 mock |
| 长期记忆丢失 | 中 | Mem0 + 定期备份 |
| 审计链断裂 | 低 | hash chain + 异地公证 |
| 工作流状态丢失 | 低 | Checkpoint 每步 |
| 权限误配置 | 中 | ABAC 默认拒绝 + 审计 |

---

## §6 演进路径 (Evolution Path)

```
v3.0 (当前)              v3.0 Amendment     v4.0
─────────────            ──────────────     ─────────────
22 Phases                小修订             重大演进
M1-M4                    Patch releases     新 Kernels
4 Milestones             (4-8 周/次)        (12+ 月)
Human-Sovereign          同上               Federated + Mobile
```

---

## §7 验收 (Definition of Done for Architecture)

- [ ] 8 Workstream 模块边界清晰（无循环依赖）
- [ ] 全部 12 Kernels 有具体模块映射
- [ ] 全部 10 DNA 在至少 1 个 WS 中体现
- [ ] 31 张表全部 PG 端到端通过
- [ ] 5 Console 页面与后端 API 联调通过
- [ ] NFR 全 KPI 达标
- [ ] 用户（项目总监）签字

---

**本文档为 Phase 1 调研稿；用户确认后作为正式 v3.0 Architecture。**

