# LIUHAO X v3.0 — Codex 执行契约

> **这是你在写任何一行代码之前，唯一必读的文件。**
> 版本：v1.0 · 建立日期：2026-09-06 · 适用对象：Hermes / Codex / 任何 AI 编码代理

---

## 0. 三十秒版本

1. 这是一个 **Human-Sovereign Agent Operating System**，不是聊天机器人，不是十个 AI 角色扮演。
2. 代码主体在 `src/`，其中 `src/kernels/` 有 **14 个 kernel，6,391 行**——这是系统的地基。
3. **这 14 个 kernel 目前零单元测试。** 你的首要任务不是加功能，是补测试。
4. **不要相信 `implementation-status.UNRELIABLE.yaml` 里的绿色标记**，它是错的（见 §3）。
5. 完成定义见 §5。没过 DoD 的功能，状态只能写 `PARTIALLY_IMPLEMENTED`。

---

## 1. 文档地图（按顺序读）

> **宪法层单一入口**：[`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md) —— 所有冲突裁决（C1–C12）、命名空间规则、章节映射的唯一来源。下表中的 spec 文件均受其裁决约束。

| 顺序 | 文件 | 什么时候读 | 篇幅 |
|---|---|---|---|
| 1 | **本文件** | 每次开工前 | 一页 |
| 2 | [`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md) | 动 kernel 之前**必读** | 中 |
| 3 | [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md) | 新增/修改能力时 | 中 |
| 4 | [`spec/GAP-MIGRATION-MATRIX.md`](spec/GAP-MIGRATION-MATRIX.md) | 做大范围重构时 | 中 |
| 5 | [`architecture/Architect-Architecture-v3.0.md`](architecture/Architect-Architecture-v3.0.md) | 需要理解全局结构 | 长 |
| 6 | [`spec/MASTER-SPEC-v3.0.md`](spec/MASTER-SPEC-v3.0.md) | 查目标规格条款：先用第一部分提炼版速查，有疑义查第二部分 221 节原文 | 长 |
| 7 | [`spec/DEFINITION-LOCK-STATUS.md`](spec/DEFINITION-LOCK-STATUS.md) | 看到 `DL:§xx` 引用时 | 短 |

> **不要一上来就读 Master Spec 的 221 节。** 那是目标规格，不是现状。先读 KERNEL-CANON 知道代码里现在有什么。

---

## 2. 硬规则（违反即 Reject，无例外）

### 2.1 真实性红线

```text
1.  不把概念当实现。
2.  不把 TODO 当完成。
3.  不把 Mock 当生产能力。
4.  不把硬编码 Demo 当智能系统。
5.  不把模型输出默认当事实。
6.  TODO 没做完，状态就写 PARTIALLY_IMPLEMENTED，不许写 IMPLEMENTED。
7.  没写测试的功能，不许标 IMPLEMENTED。
```

### 2.2 安全红线

```text
8.   不把 Tool Call 默认当授权。
9.   不把 Agent 默认当可信。
10.  不把 Memory 默认公开。
11.  不允许 Agent 自动扩大权限。
12.  不允许无限 Agent Spawn。
13.  不允许未经验证的生产自修改。
14.  不允许隐藏 Tool Execution / Network Access / Side Effect。
15.  Critical Action 必须 Audit + Policy。
16.  高风险 Action 必须 Human Approval。
17.  Untrusted Code 必须 Sandbox。
18.  External Agent 必须 Trust Boundary。
```

### 2.3 归属红线

```text
19.  Agent 必须拥有 Identity。
20.  Tool 必须拥有 Capability。
21.  Memory 必须拥有 Owner。
22.  Release 必须通过 Test + Security Gate + Evaluation。
23.  不得创建第二套互相冲突的 Identity / Policy / Capability / Audit 系统。
     （仓库已有 src/identity/ + src/security/ + src/kernels/{identity,security,policy}/，
       加新东西前先确认用哪一套，不要另起炉灶。）
```

### 2.4 反假货五连（最重要）

```text
24.  NO FAKE FRONTEND   — 禁止 setTimeout / fake progress / fake agent count 冒充执行
25.  NO FAKE AI         — 能力没实现就 return NOT_IMPLEMENTED，不许生成看起来成功的假答案
26.  NO FAKE TOOL       — 只有 JSON Schema 不算 Tool 已实现，必须 Registered+Executable+Auditable
27.  NO FAKE AGENT      — Agent 必须有 Identity/RuntimeState/Memory/Capabilities/Budget/Lifecycle
28.  NO FAKE ORGANIZATION — Organization 必须有真实 Members/Roles/Budget/KPI/Audit
```

---

## 3. 当前真实状态（2026-09-06 实测）

### 3.1 代码是真的

| 项 | 实测值 |
|---|---|
| `src/kernels/` 模块数 | **14** |
| 总代码行数 | **6,391** |
| 最大模块 | execution 696 行 |
| 最小模块 | context 179 行 |

### 3.2 但验证是空的

| 项 | 实测值 |
|---|---|
| kernel 层单元测试 | **0 个** |
| `tests/` 下相关文件 | 仅 `test_jwt_kernel.py`、`test_rbac_kernel.py`（安全测试，非 kernel 测试） |

### 3.3 ⚠️ 状态文件不可信

| 文件 | 说什么 | 真实性 |
|---|---|---|
| `capability-registry.yaml` | `audited: false`、`tested: false`、`0/0 pending` | ✅ **可信** |
| `implementation-status.UNRELIABLE.yaml` | `audited: 14`、`tested: 14` | ❌ **假的，不要用** |
| `implementation-status.UNRELIABLE.yaml` by_kernel | `audit: "minimal"`、`test: "basic"` | ⚠️ 模糊，不可验证 |
| `l10k-baseline.yaml` §27-29 | 已记录上述矛盾，但选择相信假数据 | ❌ **决策错误，待修正** |

**结论：判断完成度时，以 `capability-registry.yaml` 为准，或直接看 `tests/` 里有没有对应测试文件。**

### 3.4 数字本身也是错的

> **2026-09-06 更新**：`capability-registry.yaml` 已于本轮同步为 **14 项**（含 security/audit/plugin），状态统一为 `PARTIALLY_IMPLEMENTED`，id 改为 `LHX-C-NNN` 并保留 `legacy_id`。下方前两条关于它的旧指控已结案；**唯一仍不可信的状态文件是 `implementation-status.UNRELIABLE.yaml`**（见 R9，已重命名为 `implementation-status.UNRELIABLE.yaml`）。

- `~~capability-registry.yaml~~`：`~~total_capabilities: 12，实际只列 11 项（缺 security、audit、plugin）~~` —— ✅ **已修正为 14/14**
- `implementation-status.UNRELIABLE.yaml`：`total_modules: 14`，实际 `by_kernel` 只列 **13** 项（缺 plugin）
- `implementation-status.UNRELIABLE.yaml`：声明 `partial: 3`，实际有 **4** 个 PARTIAL；声明 `missing: 2`，实际 **0** 个

---

## 4. 代码在哪

```text
src/
├── kernels/          ← 系统地基，14 个 kernel，改这里务必先读 KERNEL-CANON
│   ├── identity/  memory/  context/  capability/  policy/
│   ├── execution/ resource/  event/  network/  trust/
│   ├── evaluation/  security/  audit/  plugin/
├── identity/         ← 人类账号体系（RBAC/ABAC、主子账号、data_scope）
├── security/         ← 安全实现（RBAC/ABAC/Vault）
├── audit/            ← 审计
├── ai/               ← AIEmployee、goal_task_graph、langgraph_workflow
├── providers/        ← LLM provider 适配
├── knowledge/        ← memory.py（旧版记忆，注意与 kernels/memory 的关系）
├── api/  gateway/    ← FastAPI 入口
├── workflow/  tasks/  plugins/  observability/  infra/
└── ...（共 31 个一级目录）

apps/console/         ← 前端（L-Core）
```

### 关键入口

| 用途 | 入口 |
|---|---|
| API 入口 | `src/api/app.py` |
| 服务启动 | `src/gateway/main.py` |
| 顶层智能体 | `src/ai/employee.py` |
| 目标-任务图 | `src/ai/goal_task_graph.py` |
| 权限检查 | `src/identity/rbac.py`、`src/security/` |
| 数据范围过滤 | `src/identity/visibility.py` |

---

## 5. 完成定义（Definition of Done）

一个功能**只有同时满足以下全部 7 项条件**，才可以标记 `IMPLEMENTED`：

```text
✅ Implemented      代码存在且可执行（不是空函数、不是 raise NotImplementedError）
✅ Tested           有对应测试且通过（unit + 至少一项 integration/e2e）
✅ Observable       有 OTel trace / 结构化日志 / 可查询状态
✅ Permissioned     经过 Identity + Capability + Policy 检查
✅ Policy Controlled 通过 policy engine 判决（Every Action needs Policy，最高原则）
✅ Audited          关键操作写入 audit log（含 correlation_id）
✅ Documented       更新了对应文档
```

> **第 5 项 `Policy Controlled` 为 2026-09-06 依据 C3 裁决新增**（统一蓝图 §3 / §7）。它意味着一个能力**即使通过了权限检查，也必须有明确的策略判决记录**，否则不得标记完成。这项把"权限"与"策略"拆开：Permissioned 是"有没有资格"，Policy Controlled 是"这次动作是否被策略允许"。

**缺任何一项 → 状态写 `PARTIALLY_IMPLEMENTED`。**

每个功能还必须能回答这 12 个问题（答不出就是没做完）：

```text
Domain?  State?  Identity?  Permission?  Capability?  Runtime?
Event?   Failure Mode?  Security Model?  Test?  Metric?  Rollback?
```

---

## 6. 当前任务优先级

### P0 — 立即（阻塞其他一切）

| # | 任务 | 为什么 |
|---|---|---|
| P0-1 | 把 `implementation-status.UNRELIABLE.yaml` 的 `audited: 14` / `tested: 14` 改为真实值并加注 | 假数据会诱导 AI 跳过验证 |
| P0-2 | 修正 `l10k-baseline.yaml` §27-29 的错误决策（改用 capability-registry 数据） | 同上 |
| P0-3 | 处理 Definition Lock 缺失（见 `spec/DEFINITION-LOCK-STATUS.md`） | 16+ 处引用的依据不可查 |

### P1 — 本周

| # | 任务 |
|---|---|
| P1-1 | 为 14 个 kernel 补单元测试（优先 identity / policy / execution / audit 四个高风险模块） |
| P1-2 | 以 `src/kernels/` 14 个模块为准，同步更新 `capability-registry.yaml`（补齐 security/audit/plugin，修正 total 数字） |
| P1-3 | 建立 `tests/kernels/` 目录与 conftest 基础设施 |

### P2 — 下一步

| # | 任务 |
|---|---|
| P2-1 | 按 `spec/GAP-MIGRATION-MATRIX.md` 推进模块化拆分（当前是 `src/` 单体，目标是 apps/ + packages/） |
| P2-2 | 补齐 Agent Runtime（目前 `src/agents/` 不存在，WS-D 处于 BLOCKED） |
| P2-3 | 建立 Capability Registry 的可执行版本（当前只有 yaml 声明） |

---

## 7. 常见陷阱

| 陷阱 | 正确做法 |
|---|---|
| 看到 `workstreams.yaml` 说 `completed: 100` 就以为做完了 | 它自己写的是 "All 9 **SCAFFOLD** kernels implemented"，脚手架 ≠ 完成 |
| 想新建一套权限/审计机制 | 先查 `src/identity/` + `src/kernels/{identity,policy,security,audit}/`，用现成的 |
| 按 `Architect-Architecture-v3.0.md` §4 的 K01-K12 去找 kernel | 那是旧命名，实际 kernel 在 `src/kernels/`，对照表见 `spec/KERNEL-CANON.md` |
| 想改 `src/knowledge/memory.py` | 先确认该用 `src/kernels/memory/`，别维护两套 |
| 引用 `DL:§xx` | 该文件已缺失，见 `spec/DEFINITION-LOCK-STATUS.md` 的条款推断表 |
| 用 `admin`/`root` 之类硬编码凭证跑通就算完成 | 违反 §2.2，必须走 Identity + Vault |

---

## 8. 一句话

**代码是真的，测试是空的，状态文件在撒谎。先补测试，再谈功能。**
