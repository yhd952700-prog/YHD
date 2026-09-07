# OPEN-DECISIONS Register — LIUHAO X v3.0 演进悬而未决登记

> **依据**：DL:§悬而未决登记册规范
> **日期**：2026-09-05
> **状态**：INITIALIZED（Phase 1 启动后持续维护）

---

## 0. 规范

按 Definition Lock：
> 出现「定不下来/先放一放/等外部条件」时，**立即**落条
> 三类固定 slug：
>   - `waiting-on-external-condition`：等外部条件（用户确认/第三方审批）
>   - `design-decision-to-evaluate`：设计待评估（需做 POC 对比）
>   - `existing-design-boundary`：现有设计边界约束
> 铁律：
>   - 只追加 + 就地关闭（OPEN → RESOLVED，补 Resolution 字段）
>   - **每次 Phase 开始时，把未决项自动复现到工作上下文最前面**（带「N 未决 + M 已决」汇总）

---

## 1. 开放决策（OPEN）

### OD-001 — 演进范围（22 Phase 全做 vs Convergence Point 收敛）

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 |
| Open Item | LIUHAO X v3.0 是否按 22 Phase 全量执行，还是按 3 个 Convergence Point 收敛（M1+M2 先做，M3 增量） |
| Related Constraints | DL:§95-§115 Convergence Point、DL:§22 Ready/Blocked、DL:§147 Global Final DoD |
| Current Leaning | **倾向 B：按 Convergence Point 收敛**（M1+M2 完成后即获得"可演进的 v3.0 内核"，M3 增量推进，避免 9-13 个月单线风险） |
| Blocked By | 等用户确认 |
| Resolves When | 用户答复 |
| Status | **OPEN** |
| Slug | `waiting-on-external-condition` |

### OD-002 — Y1 Sprint 与 v3.0 Phase 协调

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 + docs/SPRINT_PLAN_3M_V2.md |
| Open Item | Y1 3M/6Sprint 计划（S1-S6, 9/1-11/30）是否与 v3.0 22 Phase 并行？ |
| Related Constraints | DL:§22 Ready（必须不冲突）、Sprint Plan 截止 11/30 |
| Current Leaning | **倾向 B：并行**（Y1 S1-S3 完成的子模块直接为 v3.0 提供模块基础；Y1 S4-S6 不阻塞 v3.0 Phase 1-2 工作） |
| Blocked By | 等用户确认 |
| Resolves When | 用户答复 |
| Status | **OPEN** |
| Slug | `waiting-on-external-condition` |

### OD-003 — 工作区未提交代码处理

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | git status（14 modified + 15+ untracked） |
| Open Item | `git status` 显示大量未提交改动（包括 ci.yml / observability 配置 / AI employee / langgraph workflow / knowledge/memory），处理策略？ |
| Related Constraints | DL:§98 Code Parallelism（未提交共享变更需先 Impact Analysis）、DL:§23 KEEP 原则（不删除工作代码） |
| Current Leaning | **倾向 A：先 commit 到 wip/liuhao-x-evolve 分支**（保留 Y1 当前主线 main/develop 不变，v3.0 演进开新分支 wip/liuhao-x-evolve，避免影响 Y1 Sprint） |
| Blocked By | 等用户确认分支策略 |
| Resolves When | 用户答复 + git 操作执行 |
| Status | **OPEN** |
| Slug | `waiting-on-external-condition` |

### OD-004 — Definition Lock 与 Y1 文档关系

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 |
| Open Item | Definition Lock v3.0（spec-as-contract）是否覆盖/取代 Y1 既有 docs/PHASE*/SEC_* 文档？ |
| Related Constraints | DL:§0 SINGLE SOURCE OF TRUTH、DL:§13 v3.0 后续修改只能以 Amendment 形式记录 |
| Current Leaning | **倾向 B：Y1 文档保留为历史档案**（Phase 2-8 ACCEPTANCE_REPORT + SEC_01-SEC_05_06 是 v3.0 Phase Entry 的实证依据），Definition Lock 为 v3.0 演进依据 |
| Blocked By | 等用户确认 |
| Resolves When | 用户答复 |
| Status | **OPEN** |
| Slug | `design-decision-to-evaluate` |

### OD-005 — 路径错误目录清理

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 |
| Open Item | 仓库根目录有 3 个路径错误创建的目录（`D:LiuHao-AI-OSsrcintegrations`、`D:LiuHao-AI-OSsrcobservability`、`D:LiuHao-AI-OSsrcperformance`）+ `AppData/Local` |
| Related Constraints | DL:§23 KEEP 原则（不无故删除）、DL:§23 REMOVE 原则（重复/废弃/正式替代） |
| Current Leaning | **倾向清理**：这些是误创建（路径前缀 `D:\LiuHao-AI-OS` 被当成目录名），应在 Phase 1 启动前 `git rm` 并 commit |
| Blocked By | 等用户确认 |
| Resolves When | 用户答复 + git 操作执行 |
| Status | **OPEN** |
| Slug | `existing-design-boundary` |

### OD-006 — P1/P3/P4/P6 实跑验证执行方式

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 |
| Open Item | P1（Baseline Build）/ P3（Environment Readiness）/ P4（Data Safety）/ P6（Security Baseline）当前是 NEEDS VERIFICATION，由谁执行、如何执行？ |
| Related Constraints | DL:§13-§22 P0-P8 Preconditions、DL:§120 Phase 0 Acceptance |
| Current Leaning | **倾向：Hermes 直接执行**（在 Phase 1 设计阶段同时跑 `python main.py health` + `docker compose up` + `pytest tests/` + `alembic upgrade head` + `tests/security/`） |
| Blocked By | 等用户确认（是否允许 Hermes 直接执行命令？还是仅做调研？） |
| Resolves When | 用户答复 |
| Status | **OPEN** |
| Slug | `waiting-on-external-condition` |

### OD-007 — Definition Lock 22 Phase 执行计划形式

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 |
| Open Item | 22 Phase 是按"线性 38-58 周一次性完成"还是"按 Convergence Point 分阶段交付"？ |
| Related Constraints | DL:§22 Ready、DL:§147 Global Final DoD |
| Current Leaning | **倾向：按 Convergence Point 分 4 个里程碑（M1+M2+M3+M4）**，每完成一个里程碑提交用户验收 |
| Blocked By | 等用户确认 |
| Resolves When | 用户答复 |
| Status | **OPEN** |
| Slug | `design-decision-to-evaluate` |

### OD-008 — Security Kernel ABAC 请求属性可覆盖存储安全属性（S3）

| 字段 | 值 |
|------|----|
| Date | 2026-09-06 |
| Source | 后端预审（贝洛奇）+ QA 证据测试 tests/kernels/security/test_defect_design_gaps.py::TestDesignGapS3 及 test_security.py 修订版 |
| Open Item | check_abac 的 attributes 参数与 set_principal_attributes 存储值直接 merge，调用方传 clearance=0.99 可覆盖存储的 0.0 而通过 >0.5 规则（属性注入越权面） |
| Related Constraints | CODEX-CONTRACT §2.2 规则 11（不允许 Agent 自动扩大权限）；DL:§112 |
| Current Leaning | 存储属性必须优先于请求属性；请求属性仅允许补充非安全属性或经显式白名单 |
| Blocked By | 总监裁决属性合并策略（白名单 / 完全禁止覆盖 / 仅补充缺失键） |
| Resolves When | 裁决后按测试证据修复，TestDesignGapS3 相关红灯转绿 |
| Status | **OPEN**（证据测试已落盘，修复排第二轮） |
| Slug | `design-decision-to-evaluate` |

### OD-009 — Security Kernel scope 参数不参与判决（S4）

| 字段 | 值 |
|------|----|
| Date | 2026-09-06 |
| Source | 后端预审（贝洛奇）+ QA 证据测试 tests/kernels/security/test_defect_design_gaps.py::TestDesignGapS4 |
| Open Item | docstring 声明 Scope enforcement L0-L7，SecurityPrincipal 有 scope 字段，但 check_rbac/check_abac/decide_access 从不校验 scope 合法性与层级，无效值（L9/global/空串）与有效值同权 |
| Related Constraints | CODEX-CONTRACT §2.1 规则 1（不把概念当实现）；DL:§112（scope enforcement） |
| Current Leaning | 至少校验合法 scope 枚举（L0-L7）拒绝无效值；进一步需定义 principal.scope 与请求 scope 的层级比较语义（与 policy kernel scope_enforcement 对齐） |
| Blocked By | 总监裁决 scope 比较语义（严格相等 / 允许升级 / 允许降级） |
| Resolves When | 裁决后按测试证据修复，TestDesignGapS4 相关红灯转绿 |
| Status | **OPEN**（证据测试已落盘，修复排第二轮） |
| Slug | `design-decision-to-evaluate` |

### OD-010 — Policy Kernel 人类主权覆盖可被伪造（P10）

| 字段 | 值 |
|------|----|
| Date | 2026-09-06 |
| Source | 后端预审（贝洛奇）+ QA 证据测试 tests/kernels/policy/test_defect_design_gaps.py::TestDesignGapP10 |
| Open Item | human_sovereignty 内建规则仅凭 context 中 actor.type=="human" 即放行 HIGH/CRITICAL：actor.type 是调用方自填、无任何鉴权的属性，Agent 上下文自称 human 即获得人类主权覆盖 |
| Related Constraints | CODEX-CONTRACT §2.2 规则 9（不把 Agent 默认当可信）、规则 16（高风险 Action 必须 Human Approval）；DL:§112 |
| Current Leaning | human 声明必须经身份核验（联动 identity kernel 已验证身份），未经核验的 actor.type=="human" 不得触发主权覆盖 |
| Blocked By | 总监裁决核验机制挂接点（identity kernel 的哪个校验接口、核验失败时 DENY 还是 DEFER） |
| Resolves When | 裁决后按测试证据修复，TestDesignGapP10 相关红灯转绿 |
| Status | **OPEN**（证据测试已落盘，修复排第二轮） |
| Slug | `design-decision-to-evaluate` |

---

## 2. 已决决策（RESOLVED）

### OD-001 — 演进范围 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计 + 用户答复 |
| Open Item | LIUHAO X v3.0 是否按 22 Phase 全量执行 |
| Resolution | **按 Convergence Point 收敛**：M1 (Phase 0-2+3+7, Secure Control Foundation) + M2 (Phase 4+5+6+8, Executable Intelligence Core) 优先；M3 (Phase 9-20, Agent World OS) + M4 (Phase 21-22, L10K + Hardening) 增量推进 |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05** |
| 影响范围 | DL:§95-§115 Convergence Point 仍是依据；execution 模型变为分里程碑交付 |

### OD-002 — Y1 Sprint 与 v3.0 Phase 协调 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | 用户答复 |
| Resolution | **Y1 与 v3.0 并行**：Y1 3M/6Sprint 计划与 v3.0 Phase 1-2 并行；Y1 S1-S3 已交付的子模块（identity/RBAC/agents/workflow/knowledge/security）直接为 v3.0 提供模块基础 |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05** |
| 影响范围 | docs/SPRINT_PLAN_3M_V2.md 保留；v3.0 不阻塞 Y1 S4-S6 |

### OD-003 — 工作区未提交代码处理 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | 用户答复 |
| Resolution | **创建 wip/liuhao-x-evolve 分支并提交**：保留 main/develop 不变；v3.0 演进开新分支 wip/liuhao-x-evolve |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05 — 等待执行** |
| 影响范围 | git 操作执行中：git checkout -b wip/liuhao-x-evolve + git add + git commit |

### OD-005 — 路径错误目录清理 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | 用户答复（合并 OD-003 决策） |
| Resolution | **随 wip/liuhao-x-evolve 分支一并处理**：4 个路径错误目录（`D:LiuHao-AI-OSsrcintegrations`、`D:LiuHao-AI-OSsrcobservability`、`D:LiuHao-AI-OSsrcperformance`、`AppData/Local`）用 `git rm` 删除（如果已 track）或确认 untracked |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05 — 等待执行** |

### OD-006 — P1/P3/P4/P6 实跑验证执行方式 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | 用户答复 |
| Resolution | **Hermes 直接实跑**：在 Phase 1 启动同时，跑 `python main.py health` + `pytest tests/` + `docker compose up` + `alembic upgrade head` + `tests/security/`，交付完整验证证据 |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05 — 等待执行** |
| 影响范围 | Phase 2 启动门禁将根据实跑结果决定 PASS/FAIL |

### OD-007 — Definition Lock 22 Phase 执行计划形式 ✅ RESOLVED

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | 用户答复（合并 OD-001 决策） |
| Resolution | **按 Convergence Point 分 4 个里程碑**（M1+M2+M3+M4），每完成一个里程碑提交用户验收 |
| Resolved By | 用户（采纳推荐方案） |
| Status | **RESOLVED 2026-09-05** |

### OD-004 — Definition Lock 与 Y1 文档关系（默认采纳"共存"倾向）

| 字段 | 值 |
|------|----|
| Date | 2026-09-05 |
| Source | Phase 0 审计默认倾向 |
| Resolution | **Y1 文档保留为历史档案**：Phase 2-8 ACCEPTANCE_REPORT + SEC_01-SEC_05_06 是 v3.0 Phase Entry 的实证依据；Definition Lock 为 v3.0 演进依据 |
| Resolved By | 默认倾向（用户未明确答复，但无相反意见） |
| Status | **RESOLVED 2026-09-05（默认）** |
| 影响范围 | Y1 文档不动；新增 Definition Lock 演进文档到 docs/architecture/、docs/execution/、docs/capabilities/、docs/security/、docs/risk/ |

---

## 3. 汇总

- **OPEN 总数**：3（OD-008/009/010，kernel 测试冲刺第一批设计级缺陷，2026-09-06 登记）
- **RESOLVED 总数**：6（OD-001/002/003/005/006/007 + OD-004 默认）

**Phase 0 决策已全部收敛；kernel 冲刺新增 3 项待裁决。**

---

## 4. 签字

**Owner**：Hermes / MVP 开发专家团项目总监（大湾区靓仔）
**日期**：2026-09-05
**下一步**：git 创建 wip/liuhao-x-evolve + 实跑 P1/P3/P4/P6 → 启动 Phase 1

---

*END OF OPEN-DECISIONS REGISTER*