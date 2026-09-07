# LIUHAO X v3.0 — PM PRD (Product Requirements Document)

> **Version**: v3.0
> **Date**: 2026-09-05
> **Status**: AUTO-GENERATED from Definition Lock + Blueprint Requirements
> **Source**: Definition Lock §75-§90, Blueprint Requirements (122 reqs), Architecture v3.0

---

## 1. Product Vision

**LIUHAO X / 鎏灏 X** = Human-Sovereign Agent Operating System

**Core**: Open-Ended Capability + Bounded Autonomous Authority

**Long-term**: The Operating System for the Agent World

**Highest Principle**: Human Sovereignty Above All

---

## 2. Target Users

| User Type | Description | Key Needs |
|-----------|-------------|-----------|
| AI Engineers | Build, deploy, manage agent systems | Reliable runtime, observability, debuggability |
| Enterprise Teams | Run agent fleets in production | Security, compliance, scaling, governance |
| Researchers | Experiment with novel agent architectures | Extensibility, provider mesh, sandbox isolation |
| End Users | Interact with agents via Console | Usable UI, real-time feedback, control |

---

## 3. Core Requirements (from Definition Lock §75-§90)

### 3.1 §75 PostgreSQL 15 主从拓扑
- **REQ-PG-001**: PostgreSQL 15 production deployment with master+replica topology
- **REQ-PG-002**: Alembic migrations for 31 tables (goals, budgets, plugin_*, sandbox_*, device_adapters, workflow_executions)
- **REQ-PG-003**: SQLAlchemy 2.0 ORM models (30 models) with Repository pattern
- **REQ-PG-004**: PG end-to-end validation (D-004) — docker compose, alembic upgrade, pytest on PG

### 3.2 §76 RAG / Memory (mem0ai + langchain)
- **REQ-RAG-001**: mem0ai long-term memory integration (currently graceful fallback=true, must enable)
- **REQ-RAG-002**: langchain retrieval chain (chunker + embedding + retriever + vector_store)
- **REQ-RAG-003**: 10-layer memory architecture
- **REQ-RAG-004**: Vector store backend selection (pgvector recommended per OD-G1)
- **REQ-RAG-005**: RAG evaluator integration (ragas/arize-eval)

### 3.3 §77 Provider Mesh (LLM Adapters)
- **REQ-PROV-001**: Provider registry with openai/mock/self_host adapters
- **REQ-PROV-002**: LLM base abstraction with circuit breaker + retry
- **REQ-PROV-003**: Cost tracking per provider/call

### 3.4 §78 AI Employee / Goal-Task Graph
- **REQ-AI-001**: AIEmployee top-level agent with GoalTaskGraph integration
- **REQ-AI-002**: LangGraph workflow orchestration
- **REQ-AI-003**: mem0ai integration for long-term context

### 3.5 §79 Plugins / Sandbox
- **REQ-PLUG-001**: Plugin sandbox with multiple backends (subprocess, firecracker, gvisor)
- **REQ-PLUG-002**: Plugin marketplace with dependency resolution
- **REQ-PLUG-003**: Sandbox isolation and resource limits

### 3.6 §80 Observability (OTel + Metrics + Alerts)
- **REQ-OBS-001**: Full distributed tracing (OTel)
- **REQ-OBS-002**: RED metrics (Rate, Errors, Duration) + USE metrics (Utilization, Saturation, Errors)
- **REQ-OBS-003**: Alerting with threshold-based and anomaly-based rules

### 3.7 §81 Security (RBAC + ABAC + Vault + Audit)
- **REQ-SEC-001**: RBAC roles + permissions (verified SEC_01)
- **REQ-SEC-002**: ABAC policies with default-deny (verified SEC_02)
- **REQ-SEC-003**: Vault Transit encryption, keys never in DB (verified SEC_03)
- **REQ-SEC-004**: CryptoAuditLogger with hash-chain integrity (verified SEC_04)
- **REQ-SEC-005**: API Key management with scopes (verified SEC_05)
- **REQ-SEC-006**: Key rotation automation (verified SEC_06)

### 3.8 §82 SRE / Disaster Recovery
- **REQ-SRE-001**: Backup/restore with RPO < 5min, RTO < 30min
- **REQ-SRE-002**: Multi-AZ deployment, PostgreSQL master+replica
- **REQ-SRE-003**: Horizontal scaling via K8s HPA

### 3.9 §83 Frontend (React 19 + Vite 8 + TS 6)
- **REQ-FE-001**: Console with 5 core pages (Dashboard, Workflows, Plugins, Memory, Settings)
- **REQ-FE-002**: Design token system (colors, typography, spacing)
- **REQ-FE-003**: REST API + WebSocket integration with FastAPI backend

### 3.10 §84-§90 Governance + Traceability
- **REQ-GOV-001**: Human checkpoints on 100% high-risk decisions
- **REQ-GOV-002**: Decision explainability via KAREN + ENOCH
- **REQ-TRACE-001**: Full traceability: Kernel ↔ DNA ↔ Phase ↔ Workstream
- **REQ-TRACE-002**: Capability Registry with traceability matrix

---

## 4. Non-Functional Requirements (from Architecture §4)

| Category | Metric | Target | Priority |
|----------|--------|--------|----------|
| Performance | P95 API latency | < 200ms | MUST |
| Performance | P99 API latency | < 1s | MUST |
| Performance | Workflow P95 | < 5s (incl LLM) | MUST |
| Availability | System uptime | ≥ 99.95% | MUST |
| Scalability | Horizontal scaling | K8s HPA auto | MUST |
| Scalability | DB connection pool | 20-100 dynamic | SHOULD |
| Security | TLS 1.3 enforced | End-to-end | MUST |
| Security | Vault keys | Never in DB | MUST |
| Audit | Operation traceability | 100% | MUST |
| Audit | Tamper-proof | Hash chain | MUST |
| Observability | Trace completeness | Full chain | MUST |
| Observability | Metrics completeness | RED + USE | MUST |
| Disaster Recovery | RPO | < 5min | MUST |
| Disaster Recovery | RTO | < 30min | MUST |
| Compliance | Human checkpoints | 100% high-risk | MUST |
| Compliance | Decision explainable | KAREN + ENOCH | MUST |

---

## 5. Milestones (22 Phases per Definition Lock)

| Milestone | Phases | Description | Target |
|-----------|--------|-------------|--------|
| M1 Foundation | 0-5 | Core runtime, security, kernels, PG, basic observability | Phase 5 exit |
| M2 Intelligence | 6-11 | AI Employee, planning, long-horizon, agent org | Phase 11 exit |
| M3 World | 12-17 | Network, deployment, plugins, frontend console | Phase 17 exit |
| M4 Sovereign | 18-21 | Human sovereignty, federated, mobile, production hardening | Phase 21 exit |

---

## 6. Acceptance Criteria (Per Phase)

### Phase 2 Exit (Definition Lock §122)
- [ ] One Identity Authority
- [ ] One Capability Authority
- [ ] One Policy Authority
- [ ] One Audit Authority
- [ ] Critical Audit Coverage = 100%

### Phase 5 Exit (M1 Complete)
- [ ] 12 Kernels IMPLEMENTED (not SCAFFOLD)
- [ ] PG end-to-end validated
- [ ] Security 100% green
- [ ] Observability baseline operational

### Phase 11 Exit (M2 Complete)
- [ ] AI Employee with planning operational
- [ ] Multi-agent coordination working
- [ ] Long-horizon planning functional

### Phase 17 Exit (M3 Complete)
- [ ] Console 5 pages + design tokens live
- [ ] Plugin marketplace operational
- [ ] Deployment/gray release working

### Phase 21 Exit (M4 Complete = FINAL ACCEPTANCE)
- [ ] LIUHAO X v3.0 FINAL ACCEPTANCE = PASS
- [ ] Human sovereignty verified
- [ ] Production ready

---

## 7. Risks & Mitigations (from Architecture §5)

| Risk | Level | Mitigation |
|------|-------|------------|
| Single point of failure | Medium | PG master+replica + Redis cluster + multi-AZ |
| Model unavailable | Medium | Provider mesh + mock fallback |
| Long-term memory loss | Medium | Mem0 + periodic backup |
| Audit chain break | Low | Hash chain + remote notarization |
| Workflow state loss | Low | Checkpoint every step |
| Permission misconfig | Medium | ABAC default-deny + audit |

---

## 8. Open Decisions (from Gap Analysis)

| ID | Decision | Recommendation |
|----|----------|----------------|
| OD-G1 | Vector store: pgvector vs Qdrant | pgvector (avoid extra infra) |
| OD-G2 | Mem0 enable vs keep fallback | Enable (remove fallback) |
| OD-G3 | Frontend priority | 5 pages MoSCoW (Must: Dashboard + Workflows) |
| OD-G4 | Traceability matrix structure | 12K × 10DNA × 22Phase (adopted) |

---

## 9. Success Metrics

- **Technical**: All Phase exit gates PASS, 0 CRITICAL security findings, 99.95% uptime
- **Product**: Console usable by AI engineers, agent fleets deployable in < 10 min
- **Compliance**: 100% audit coverage, all human checkpoints enforced

---

*Auto-generated from Definition Lock v3.0 + Blueprint Requirements + Architecture v3.0 evidence.*