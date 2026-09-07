# LIUHAO X v3.0 — Kernel 权威清单（KERNEL CANON）

> **本文件是 Kernel 的唯一权威来源。任何文档与本文件冲突时，以本文件为准。**
> 依据：`src/kernels/` 实际代码（2026-09-06 实测）
> 版本：v1.0

---

## 0. 为什么需要这份文件

仓库里存在**五套互相冲突的 Kernel 清单**：

| 来源 | 数量 | 问题 |
|---|---|---|
| `src/kernels/` 实际代码 | **14** | ← 唯一真实来源 |
| `implementation-status.UNRELIABLE.yaml` | 13（声明 14） | 漏 plugin |
| `liuhao-x-kernels-interface.md` | 12 | 漏 security、plugin；多列了 audit 进 12 个 |
| MS:§6 | 12 | 漏 audit、plugin |
| `capability-registry.yaml` | 14（2026-09-06 已同步） | ✅ 现已含 security/audit/plugin |
| `Architect-Architecture-v3.0.md` §4 | 12 | **完全不同的命名体系**（K01–K12） |

**本文件终结混乱：以代码的 14 个为准。**

---

## 1. 权威清单：14 个 Kernel

| # | Kernel | 路径 | 行数 | Definition Lock 依据 | 职责 |
|---|--------|------|------|---------------------|------|
| 1 | **identity** | `src/kernels/identity/` | 356 | §112 | Agent 身份、权限、L0-L7 作用域过滤 |
| 2 | **memory** | `src/kernels/memory/` | 498 | §112 | 多层记忆存储、L0-L7 作用域过滤、CRUD、跨层压缩整合 |
| 3 | **context** | `src/kernels/context/` | 179 | §112 | 12 类输入 → 压缩 → 模型就绪上下文 |
| 4 | **capability** | `src/kernels/capability/` | 485 | §112 | 能力注册中心、版本、追溯链、作用域检查 |
| 5 | **policy** | `src/kernels/policy/` | 502 | §112 | ABAC 策略引擎、优先级评估、人类主权覆盖 |
| 6 | **execution** | `src/kernels/execution/` | 696 | §112 | Goal→Task→Plan→Action→Verify 全链路 |
| 7 | **resource** | `src/kernels/resource/` | 482 | §112 | CPU/Mem/Storage/Token/Time/$ 六种配额 |
| 8 | **event** | `src/kernels/event/` | 341 | §112 | 统一事件总线、correlation ID、死信重试 |
| 9 | **network** | `src/kernels/network/` | 498 | §112 | 协议适配（A2A/MCP/gRPC/HTTP/WS）、路由 |
| 10 | **trust** | `src/kernels/trust/` | 629 | §112 | 信任评分、传播、衰减、撤销 |
| 11 | **evaluation** | `src/kernels/evaluation/` | 595 | §112 | 结果评估、反馈、重规划触发、升级 |
| 12 | **security** | `src/kernels/security/` | 450 | **§112** | RBAC+ABAC、Vault Transit 加密、审计 |
| 13 | **audit** | `src/kernels/audit/` | 423 | **§113** | 防篡改审计链（hash-chain）、关联查询 |
| 14 | **plugin** | `src/kernels/plugin/` | 447 | **§115** | 插件注册、热加载、版本兼容、作用域激活 |

**合计 6,391 行。**

> ⚠️ **`L0–L7` 语义锁定（2026-09-06，C11 裁决）**：在本项目中 `L0–L7` **仅指 Memory Scope**（L0=System … L7=Session），由 `identity` / `memory` kernel 已实现"L0-L7 作用域过滤"。
> 它与任何"架构分层"编号体系（如框架 #03 的 L0=Human Interface … L9）**无关**。
> **禁止**按其他文档的 L0–L7 架构分层含义修改本字段或 `src/kernels/{identity,memory}/` 的 scope 逻辑——那会直接破坏已实现的权限语义。引用冲突一律以本说明为准（详见 `UNIFIED-BLUEPRINT.md` §3 C11）。

---

## 2. 关键发现：Definition Lock 的 Kernel 结构已反推出来

每个 kernel 的模块 docstring 都写着 `依据 DL:§xxx`。据此可还原宪法的结构：

```text
DL:§112  →  十二核心 Kernel
    identity, memory, context, capability, policy, execution,
    resource, event, network, trust, evaluation, security
    （注意：security 在十二之内，代码佐证 security/__init__.py 引用 §112）

DL:§113  →  Audit Kernel（第 13 个）
    audit/__init__.py 引用 §113

DL:§115  →  Plugin Registry（第 14 个）
    plugin/__init__.py 引用 §115

12 (§112) + 1 (§113) + 1 (§115) = 14  ← 与 src/kernels/ 目录数完全一致
```

**这证实了：代码是严格按 Definition Lock 实现的，只是宪法文件本体丢失了。**
详见 [`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md)。

### 由此解释两份文档为何各错一半

- `liuhao-x-kernels-interface.md` 把 **audit 算进十二**，漏了 security 与 plugin
- MS:§6 把 **security 算进十二**，漏了 audit 与 plugin
- 两者都漏了 **plugin**（因为 §115 在更后面，容易被忽略）

---

## 3. 与旧命名（K01–K12）的对照

`Architect-Architecture-v3.0.md` §4 使用的是 K01–K12 编号，**与本清单不兼容**。迁移时按此表：

| 旧编号 | 旧名称 | 对应新 Kernel | 说明 |
|--------|--------|--------------|------|
| K01 | Control Plane · ULTRON | — | 非 kernel，属 `apps/api` 网关层 |
| K02 | Smart Router · VISION | — | 非 kernel，属 Model Router / Model Gateway |
| K03 | LLM Adapter · ADA | — | 非 kernel，属 Model Gateway |
| K04 | Memory · EDITH | `memory` | ✅ 唯一 1:1 |
| K05 | Workflow · FRIDAY | `execution` | 部分对应 |
| K06 | Task Graph · JARVIS | `execution` | 部分对应 |
| K07 | Plugin Sandbox · JOCaSTA | `plugin` | 对应（但职责不完全相同） |
| K08 | RBAC+ABAC · KAREN | `policy` + `security` | 一拆二 |
| K09 | Audit · ENOCH | `audit` | 对应 |
| K10 | Observability · ZOON | — | 无 kernel，属 `src/observability/` 横切关注点 |
| K11 | Disaster Recovery | — | 无 kernel，属 Production Hardening |
| K12 | Scaling | — | 无 kernel，属 Production Hardening |
| — | （无） | `identity` | 新增 |
| — | （无） | `context` | 新增 |
| — | （无） | `capability` | 新增 |
| — | （无） | `resource` | 新增 |
| — | （无） | `event` | 新增 |
| — | （无） | `network` | 新增 |
| — | （无） | `trust` | 新增 |
| — | （无） | `evaluation` | 新增 |

> **DNA 标注冲突提示**：旧表把 K02 Smart Router 标为 VISION、K04 Memory 标为 EDITH；
> 而 MS:§143 规定 VISION=Perception、EDITH=WorldInterface。
> **新代码不使用十源 DNA 命名 kernel**，DNA 只用于能力溯源（见 `CAPABILITY-REGISTRY.md`）。

---

## 4. 现状评估

### 4.1 代码已落盘，但验证缺失

| 维度 | 状态 |
|---|---|
| 代码存在 | ✅ 14/14 |
| 可执行 | ✅ 模块可导入 |
| **单元测试** | ❌ **0/14** |
| **集成测试** | ❌ 0 |
| **审计覆盖** | ❌ `audited: false`（capability-registry） |

### 4.2 按 DoD 判定

按 `CODEX-CONTRACT.md` §5 的完成定义（Implemented + Tested + Observable + Permissioned + **Policy Controlled** + Audited + Documented，共 7 项）：

**14 个 kernel 中，达标数量：0。**

`capability-registry.yaml` 中原标记 `IMPLEMENTED` 的 9 个（context / capability / event / execution / resource / policy / network / trust / evaluation），
因缺 `tested` 与 `audited`，**已于 2026-09-06 在 yaml 中降级为 `PARTIALLY_IMPLEMENTED`**，与本文件判定一致。

### 4.3 高风险模块优先补测试

按"出错代价"排序，建议补测试的顺序：

1. **`security`**（450 行）— 权限漏洞代价最高
2. **`policy`**（502 行）— 策略判决错误会导致越权
3. **`execution`**（696 行）— 最大模块，逻辑复杂
4. **`audit`**（423 行）— 审计链断裂会导致合规失效
5. `identity`（356 行）
6. `trust`（629 行）— 信任传播算法易错
7. `evaluation`（595 行）
8. `network`（498 行）
9. `capability`（485 行）
10. `resource`（482 行）
11. `plugin`（447 行）
12. `event`（341 行）
13. `memory`（498 行）— 含压缩整合
14. `context`（179 行）

---

## 5. 使用规则

1. **新增 kernel**：必须先在本文件登记，并说明它不归属于现有 14 个中的任何一个。
2. **修改 kernel 接口**：同步更新 `liuhao-x-kernels-interface.md` 与 `capability-registry.yaml`。
3. **引用 kernel**：代码内一律用 `src/kernels/<name>` 路径，不要用 K01–K12 编号。
4. **文档引用**：其他文档提到 kernel 时，链接到本文件，不要各自维护清单。
