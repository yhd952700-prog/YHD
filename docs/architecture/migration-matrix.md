# LIUHAO X Migration Master Matrix

> **Date**: 2026-09-05
> **Standard**: LIUHAO X v3.0 Definition Lock §24
> **Source**: `docs/architecture/existing-codebase-audit.md`, `docs/archive/Y1_AUDIT_REPORTS.md`, `docs/architecture/existing-codebase-audit.md` §18.2-18.3
> **Owner**: Hermes / MVP Development Expert Team PM (大湾区靓仔)

---

## Migration Legend

| Action | Meaning |
|--------|---------|
| **KEEP** | Stable, correct, tested, meets v3.0 target |
| **REFACTOR** | Functionally valid, structure needs v3.0 alignment |
| **EXTEND** | Correct but incomplete; add missing v3.0 capabilities |
| **REPLACE** | Fundamental conflict; cannot reasonably adapt |
| **DEPRECATE** | Duplicate, dangerous, obsolete; formally replaced |
| **REMOVE** | Remove entirely |
| **ADD** | Not in existing system; v3.0 requires it |

---

## Migration Matrix

| # | Existing Component | Current Behavior | Target v3.0 Domain | Migration Action | Dependencies | Security Impact | Data Impact | Runtime Impact | Test Plan | Rollback Plan | Acceptance Phase | Status |
|---|-------------------|------------------|-------------------|------------------|--------------|-----------------|-------------|----------------|-----------|---------------|------------------|--------|
| **CORE IDENTITY / AUTH** |
| 1 | `src/identity/auth.py` | JWT auth, password hash, login/register | Identity Kernel | **KEEP** | None | Low | None | None | Existing tests | N/A | Phase 3 | PLANNED |
| 2 | `src/identity/rbac.py` | Role-based access, 4-level hierarchy | Identity Kernel / Capability Kernel | **EXTEND** | 1 | Medium | Role tables | None | Existing 111 tests | N/A | Phase 3 | PLANNED |
| 3 | `src/identity/abac.py` | Attribute-based access | Policy Kernel | **EXTEND** | 2 | Medium | Policy tables | None | Existing tests | N/A | Phase 7 | PLANNED |
| 4 | `src/identity/governance.py` | Approval workflows | Approval Engine | **EXTEND** | 2,3 | High | Approval tables | None | Existing tests | N/A | Phase 7 | PLANNED |
| 5 | `src/identity/audit.py` | Operation audit logs | Audit Kernel | **EXTEND** | None | High | Audit tables | Event Kernel | Existing tests | N/A | Phase 2 | PLANNED |
| 6 | `src/identity/models.py` | User/Role/Permission ORM | Identity Kernel | **REFAC** | 1,2 | Medium | Schema changes | None | Migration test | Schema rollback | Phase 3 | PLANNED |
| **AI AGENT / ORCHESTRATION** |
| 7 | `src/ai/employee.py` (+ `src/ai/agent_factory.py`) | Agent registration, routing, basic lifecycle | Agent Runtime / Agent Factory | **EXTEND** | 6 | Medium | Agent tables | Runtime Kernel | Existing tests | N/A | Phase 3 | PLANNED |
| 8 | `src/ai/orchestrator.py` | Sequential/parallel/hybrid orchestration | Execution Kernel | **EXTEND** | 7 | Medium | Execution tables | Runtime Kernel | Existing tests | N/A | Phase 8 | PLANNED |
| 9 | `src/ai/providers.py` | Model gateway (Ollama/OpenAI/Mock, 4-level fallback) | Model Gateway | **EXTEND** | 7 | Medium | Provider config | Model Gateway | Existing tests | N/A | Phase 5 | PLANNED |
| 10 | `src/ai/tools.py` | Tool registry, basic permissions | Tool Registry | **EXTEND** | 7 | Medium | Tool tables | Tool Registry | Existing tests | N/A | Phase 8 | PLANNED |
| 11 | `src/ai/planner.py` | Goal→Task Graph (keyword matching) | Execution Kernel / Capability Kernel | **REFACTOR** | 7 | Medium | Task tables | Planner | Existing tests | N/A | Phase 8 | PLANNED |
| 12 | `src/ai/workflow_bridge.py` | Plan→Workflow bridge | Execution Kernel | **EXTEND** | 8,11 | Medium | Bridge tables | Runtime | Existing tests | N/A | Phase 8 | PLANNED |
| 13 | `src/ai/cost_tracker.py` | Token usage tracking, budget | Economy Kernel | **EXTEND** | 7,9 | Medium | Cost tables | Billing | Existing tests | N/A | Phase 18 | PLANNED |
| 14 | `src/ai/command_processor.py` | Goal parsing (keyword matching) | L-Core / Context Engine | **REPLACE** | 7 | Low | None | L-Core | New tests | N/A | Phase 9 | PLANNED |
| 15 | `src/ai/workforce/employee.py` | AI Employee model | Agent Factory / Organization | **EXTEND** | 7 | Medium | Employee tables | Agent Runtime | Existing tests | N/A | Phase 12 | PLANNED |
| **WORKFLOW / TASKS** |
| 16 | `src/workflow/executor.py` | Workflow executor (state machine, events) | Execution Kernel | **EXTEND** | 8 | Medium | Workflow tables | Runtime Kernel | Existing tests | N/A | Phase 8 | PLANNED |
| 17 | `src/workflow/trade_templates.py` | Trade workflow templates (3 templates, 12 actions) | Organization / Agent Factory | **KEEP** | 16 | Low | Template data | None | Existing tests | N/A | Phase 12 | PLANNED |
| 18 | `src/tasks/` | Task CRUD, lifecycle, executor | Execution Kernel | **EXTEND** | 7 | Medium | Task tables | Runtime Kernel | Existing tests | N/A | Phase 8 | PLANNED |
| **KNOWLEDGE / MEMORY** |
| 19 | `src/knowledge/memory.py` | 10-layer memory, 10 scopes, permissions | Memory Kernel | **EXTEND** | 6 | High | Memory tables | Memory Kernel | Existing tests | N/A | Phase 6 | PLANNED |
| 20 | `src/knowledge/rag_pipeline.py` | RAG pipeline (ingest/chunk/embed/retrieve) | Memory Kernel / Perception | **EXTEND** | 19 | High | Vector data | Memory Kernel | Existing tests | N/A | Phase 6/11 | PLANNED |
| 21 | `src/knowledge/company_brain.py` | Enterprise knowledge graph | Memory Kernel / World Model | **EXTEND** | 19,20 | High | Graph data | World Model | Existing tests | N/A | Phase 11 | PLANNED |
| 22 | `src/knowledge/` (full) | Document upload/parse/chunk/embed/retrieve | Memory Kernel / VISION | **EXTEND** | 19,20 | High | Doc + vector | Memory Kernel | Existing tests | N/A | Phase 6/11 | PLANNED |
| **SECURITY / VAULT / SANDBOX** |
| 23 | `src/security/secrets.py` | Vault (PBKDF2 + AES-256 + Merkle) | Security Kernel / Trust Kernel | **EXTEND** | 6 | Critical | Secret data | Vault | Existing tests | N/A | Phase 2 | PLANNED |
| 24 | `src/security/policy.py` | Policy engine雏形 (allow/deny/approval) | Policy Kernel | **EXTEND** | 3 | Critical | Policy tables | Policy Engine | Existing tests | N/A | Phase 7 | PLANNED |
| 25 | `src/security/audit_policy.py` | Audit strategy, integrity check | Audit Kernel | **EXTEND** | 5 | Critical | Audit tables | Audit Kernel | Existing tests | N/A | Phase 2 | PLANNED |
| 26 | `src/security/sandbox/` | Sandbox execution (code/shell/browser) | Sandbox Kernel | **EXTEND** | 6 | Critical | Isolated FS | Sandbox Runtime | Existing tests | N/A | Phase 7/16 | PLANNED |
| **INTEGRATIONS / WORLD INTERFACE** |
| 27 | `src/integrations/providers.py` | WhatsApp/Facebook/LinkedIn/WeChat providers | World Interface (EDITH) | **EXTEND** | 6 | Medium | Credential tables | Network Kernel | Existing tests | N/A | Phase 16 | PLANNED |
| 28 | `src/integrations/translation.py` | Multi-language translation | World Interface | **KEEP** | 27 | Low | None | Network | Existing tests | N/A | Phase 16 | PLANNED |
| 29 | `src/integrations/orm_models.py` | 31 ORM models (old location) | Data Architecture | **DEPRECATE** | 6 | Medium | Schema migration | N/A | Migration test | Schema rollback | Phase 2 | PLANNED |
| 30 | `src/integrations/vault.py` | Vault client (HVAC) | Security Kernel | **KEEP** | 23 | Critical | Secret data | Vault | Existing tests | N/A | Phase 2 | PLANNED |
| **OBSERVABILITY / EVENTS** |
| 31 | `src/adapters/observability/` | OpenTelemetry adapter (traces/metrics/logs) | Event Kernel / Observability | **EXTEND** | 6 | Medium | Trace data | Event Kernel | Existing tests | N/A | Phase 2/14 | PLANNED |
| 32 | `src/audit/` | Audit logging | Audit Kernel | **EXTEND** | 5 | High | Audit tables | Event Kernel | Existing tests | N/A | Phase 2 | PLANNED |
| **MLOps / ANALYSIS** |
| 33 | `src/mlops/` | Experiments, training, eval, model registry, A/B | ADA Kernel / Evaluation Kernel | **EXTEND** | 6 | Medium | MLOps tables | ADA Runtime | Existing tests | N/A | Phase 11/19 | PLANNED |
| **BUSINESS / CRM** |
| 34 | `src/business/` | Lead, supplier, quote, import/export | Organization / ZOON | **KEEP** | 6 | Low | Business tables | None | Existing tests | N/A | Phase 12 | PLANNED |
| 35 | `src/crm/` | CRM engines (auto-prospecting) | Organization / ZOON | **KEEP** | 34 | Low | CRM tables | None | Existing tests | N/A | Phase 12 | PLANNED |
| **FRONTEND / API / GATEWAY** |
| 36 | `src/api/app.py` | FastAPI factory | API Gateway / L-Core | **REFACTOR** | 7 | Medium | None | API Gateway | Existing tests | N/A | Phase 2 | PLANNED |
| 37 | `src/api/routes/` | Scattered routes (dashboard, quotes, etc.) | API Gateway | **REFACTOR** | 36 | Medium | None | API Gateway | Existing tests | N/A | Phase 2 | PLANNED |
| 38 | `src/gateway/main.py` | Uvicorn startup entry | Agent Runtime | **KEEP** | 7 | Low | None | Runtime | N/A | N/A | Phase 4 | PLANNED |
| 39 | `frontend/` | React/Vite/TS console | Product / Frontend (Workstream H) | **EXTEND** | 36 | Low | None | L-Core UI | Existing vitest | N/A | Phase 9/13 | PLANNED |
| **INFRASTRUCTURE / DATA** |
| 40 | `src/models/` | New ORM location (post-migration) | Data Architecture | **KEEP** | 6 | Medium | 31+ tables | N/A | Migration test | Schema rollback | Phase 2 | PLANNED |
| 41 | `src/storage/` | Repository pattern + StorageManager | Data Architecture | **KEEP** | 40 | Medium | Repo pattern | N/A | Existing tests | N/A | Phase 2 | PLANNED |
| 42 | `alembic/` + `migrations/` | Database migrations (1 version + old) | Data Architecture | **REFAC** | 6 | High | Schema changes | N/A | Migration test | Schema rollback | Phase 2 | PLANNED |
| 43 | `docker-compose*.yml` | Infra configs (Postgres/Redis/Qdrant/Kafka/ETCD) | Infrastructure | **KEEP** | None | Medium | Volume data | All runtimes | Health checks | N/A | Phase 0 | PLANNED |
| 44 | `infra/` | Per-service infra configs | Infrastructure | **KEEP** | 43 | Medium | Config data | All runtimes | N/A | N/A | Phase 0 | PLANNED |
| 45 | `configs/` | Grafana, AlertManager, observability configs | Observability | **KEEP** | 31 | Low | Config data | Observability | N/A | N/A | Phase 14 | PLANNED |
| **EVOLUTION / EXPERIENCE** |
| 46 | `src/evolve/growth.py` | Meta-learning | Evolution Kernel | **EXTEND** | 6 | Low | Evolution data | Evolution Runtime | Existing tests | N/A | Phase 20 | PLANNED |
| 47 | `src/feedback/` | Feedback collection | Experience Kernel | **EXTEND** | 46 | Low | Feedback data | Experience | Existing tests | N/A | Phase 19 | PLANNED |
| 48 | `src/datasets/` | Dataset management | ADA / Memory | **KEEP** | 19 | Low | Dataset tables | None | Existing tests | N/A | Phase 11 | PLANNED |
| 49 | `src/deployment/` | Deployment configs | Infrastructure | **KEEP** | 43 | Low | Config data | N/A | N/A | N/A | Phase 0 | PLANNED |
| 50 | `src/distribution/` | Distribution configs | Network Kernel | **KEEP** | 43 | Low | Config data | Network | N/A | N/A | Phase 15 | PLANNED |
| **CORE / KERNEL PRIMITIVES (NEW - ADD)** |
| 51 | — | Context Engine (12 inputs → compression) | Context Kernel | **ADD** | 6,19,31 | Medium | Context data | Context Engine | New tests | N/A | Phase 1 | PLANNED |
| 52 | — | Capability Registry + Traceability | Capability Kernel | **ADD** | 6,7,8,9,10,11 | High | Capability tables | Capability Engine | New tests | N/A | Phase 2 | PLANNED |
| 53 | — | Policy Engine (ALLOW/DENY/REQUIRE_APPROVAL) | Policy Kernel | **ADD** | 3,4,24 | Critical | Policy tables | Policy Engine | New tests | N/A | Phase 7 | PLANNED |
| 54 | — | Execution Engine (Goal→Task→Plan→Action→Verify) | Execution Kernel | **ADD** | 7,8,11,12 | High | Execution tables | Runtime Kernel | New tests | N/A | Phase 8 | PLANNED |
| 55 | — | Resource Kernel (CPU/Mem/Storage/Token/Time/$ quotas) | Resource Kernel | **ADD** | 6,7,8,13 | High | Resource tables | Resource Engine | New tests | N/A | Phase 8 | PLANNED |
| 56 | — | Event Kernel (unified event bus, correlation IDs) | Event Kernel | **ADD** | 31,32 | High | Event tables | Event Bus | New tests | N/A | Phase 2 | PLANNED |
| 57 | — | Network Kernel (A2A/MCP/Discovery/Federation) | Network Kernel | **ADD** | 27,30 | High | Network tables | Network Runtime | New tests | N/A | Phase 15 | PLANNED |
| 58 | — | Trust Kernel (Trust Engine, Reputation) | Trust Kernel | **ADD** | 5,23,24,25 | Critical | Trust tables | Trust Engine | New tests | N/A | Phase 17 | PLANNED |
| 59 | — | Evaluation Kernel (Verification, Experience, L10K) | Evaluation Kernel | **ADD** | 19,33,46 | High | Eval tables | Evaluation Runtime | New tests | N/A | Phase 19/21 | PLANNED |
| 60 | — | L-Core (Intent→Goal→Plan→Agent→Exec→Verify) | L-Core | **ADD** | 14,51,53,54 | Critical | L-Core state | L-Core Runtime | New tests | N/A | Phase 9 | PLANNED |
| 61 | — | Agent Factory (Goal→AgentSpec→Identity→Activate) | Agent Factory | **ADD** | 7,8,54 | High | Agent tables | Agent Runtime | New tests | N/A | Phase 4 | PLANNED |
| 62 | — | Context Engine → Model Context compression | Context Kernel | **ADD** | 51 | Medium | Context data | Context Engine | New tests | N/A | Phase 1 | PLANNED |
| 63 | — | Scheduler (Scheduled/Event-driven/Periodic) | Scheduler | **ADD** | 8,19,31 | Medium | Schedule tables | Scheduler Runtime | New tests | N/A | Phase 13/14 | PLANNED |
| 64 | — | JOCaSTA Organization (Goals/Members/Roles/Teams/KPI) | Organization Kernel | **ADD** | 3,7,15,54 | High | Org tables | Organization Runtime | New tests | N/A | Phase 12 | PLANNED |
| 65 | — | ZOON Specialized Intelligence Framework | ZOON Kernel | **ADD** | 7,15 | Medium | Domain tables | Agent Runtime | New tests | N/A | Phase 12 | PLANNED |
| 66 | — | VISION Subsystem (Image/Video/Audio/Doc/OCR) | VISION Kernel | **ADD** | 20,22 | High | Vision data | VISION Runtime | New tests | N/A | Phase 11 | PLANNED |
| 67 | — | ADA Subsystem (SQL/Python/Stats/Anomaly/Sandbox) | ADA Kernel | **ADD** | 19,33 | High | ADA data | ADA Runtime | New tests | N/A | Phase 11 | PLANNED |
| 68 | — | EDITH World Adapters (Browser/FS/Shell/Git/Cloud/Devices) | World Interface | **ADD** | 27,57 | High | Adapter configs | Network Runtime | New tests | N/A | Phase 16 | PLANNED |
| 69 | — | FRIDAY Realtime (Monitoring/Alert/Incident) | Realtime Kernel | **ADD** | 8,31,32 | High | Realtime data | Event Kernel | New tests | N/A | Phase 14 | PLANNED |
| 70 | — | ENOCH Long-Horizon (Persistent missions/Checkpoint/Resume) | Long-Horizon Kernel | **ADD** | 8,19,54 | High | Mission tables | Runtime Kernel | New tests | N/A | Phase 13 | PLANNED |
| 71 | — | KAREN Personal Intelligence (User context/prefs) | Personal Kernel | **ADD** | 1,19 | Medium | Personal data | Context Engine | New tests | N/A | Phase 12 | PLANNED |

---

## Migration Summary

| Action | Count | Percentage |
|--------|-------|------------|
| KEEP | 12 | 17% |
| EXTEND | 32 | 46% |
| REFACTOR | 4 | 6% |
| REPLACE | 1 | 1% |
| DEPRECATE | 1 | 1% |
| ADD | 21 | 30% |
| **TOTAL** | **71** | **100%** |

---

## Critical Path Migration Order

| Wave | Components | Rationale |
|------|------------|-----------|
| **Wave 1** (Phase 1-2) | 6, 42, 51, 52, 56, 54, 62 | Foundation: Data, Context, Capability, Event, Execution kernels |
| **Wave 2** (Phase 3-4) | 1-7, 15, 19, 23, 24, 25, 31, 53, 61 | Identity, Memory, Security, Agent Runtime, Policy, Agent Factory |
| **Wave 3** (Phase 5-8) | 8-14, 16-18, 20-22, 33, 46, 54, 55, 57, 58 | Model Gateway, Workflow, Tools, Execution, Resource, Network, Trust |
| **Wave 4** (Phase 9-12) | 14, 15, 27, 28, 34, 35, 47, 50, 57-61, 63-65 | L-Core, World Interface, Organization, ZOON, Scheduler |
| **Wave 5** (Phase 13-16) | 21, 22, 26, 29, 30, 48, 49, 66, 67, 68 | VISION, ADA, EDITH Adapters, Long-Horizon |
| **Wave 6** (Phase 17-22) | 58, 59, 69, 70, 71 | Trust, Evaluation, Realtime, ENOCH, KAREN, L10K |

---

## Rollback Strategy (Per Component)

| Component | Rollback Trigger | Rollback Action | Owner |
|-----------|------------------|-----------------|-------|
| Schema migrations | Migration test fails | `alembic downgrade -1` | DBA |
| Runtime kernel | Integration test fails | Feature flag disable | Architect |
| Data migration | Data integrity check fails | Point-in-time recovery | DBA |
| API contract | Consumer test fails | Version rollback | API Team |
| Frontend | Build/TypeScript errors | `git revert` | Frontend Lead |

---

## Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Migration Lead | Hermes / MVP Dev Expert Team PM (大湾区靓仔) | 2026-09-05 | PLANNED |

---

*END OF LIUHAO X MIGRATION MASTER MATRIX*