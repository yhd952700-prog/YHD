# LIUHAO X v3.0 — Kernel 权威清单（KERNEL CANON）

> **本文件是 Kernel 的唯一权威来源。任何文档与本文件冲突时，以本文件为准。**
> 依据：`src/kernels/` 实际代码
> **行数证据：`p36` @ `8356701e`（2026-10-04 由 `git ls-tree`/`git show HEAD:` 实测，可复现）**
> **计数证据：注册内核数取自运行时 `src.kernels._registry.KERNEL_ENTRIES`（`len()` = 14），
> 而非数目录 —— 见 §0.5。**
> 版本：v1.1（2026-10-04 修订：原v1.0 的行数列14/14 全部失真、合计数算错、且把"目录数"当作"内核数"）

---

## 0. 为什么需要这份文件

仓库里存在**五套互相冲突的 Kernel 清单**：

| 来源 | 数量 | 问题 |
|---|---|---|
| `src/kernels/` **注册表**（运行时 `KERNEL_ENTRIES`） | **14** | ← 唯一真实来源 |
| `src/kernels/` **目录数**（`ls -d src/kernels/*/`） | **15** | 多出 `retention/`（HD-06 子系统，**非注册内核**，见 §0.5） |
| `implementation-status.UNRELIABLE.yaml` | 13（声明 14） | 漏 plugin |
| `liuhao-x-kernels-interface.md` | 12 | 漏 security、plugin；多列了 audit 进 12 个 |
| MS:§6 | 12 | 漏 audit、plugin |
| `capability-registry.yaml` | 14（2026-09-06 已同步） | ✅ 现已含 security/audit/plugin |
| `Architect-Architecture-v3.0.md` §4 | 12 | **完全不同的命名体系**（K01–K12） |

**本文件终结混乱：以代码注册表的 14 个为准。**

### 0.5 为什么"14"必须由注册表给出，而不是数目录

`src/kernels/` 下有 **15 个目录**，但只有 **14 个是注册内核**。第 15 个是
`src/kernels/retention/` —— HD-06 的**留存子系统**（retention subsystem），由
`src/observability/production.py` 作为生产探针单独引用，**不在 `KERNEL_ENTRIES` 中，
没有 `lifecycle` 字段，也不出现在 `GET /v1/kernels`**。

>⚠️ **v1.0 曾在此处写下「12 (§112) + 1 (§113) + 1 (§115) = 14 ← 与 `src/kernels/`
> 目录数完全一致」。后半句按字面为假**：目录数是 15。这不是14 错了，而是"用目录数论证
> 内核数"这个**论证方式**本身不成立 —— 目录里可以放非内核的包，且本仓库已经这么做了。
> 现改为：内核数 = 注册表 `len(KERNEL_ENTRIES)`，可由
> `python -c "from src.kernels._registry import KERNEL_ENTRIES; print(len(KERNEL_ENTRIES))"`
> 直接复现。retention 的性质已在 `docs/product/KERNEL-PRODUCT-CAPABILITY.md` 中独立核实。

---

## 1. 权威清单：14 个 Kernel

> **行数如何复现**（本文档所有行数的唯一方法）：
> ```bash
> git ls-tree -r --name-only HEAD src/kernels/     # 列出受版本控制的 .py
> git show HEAD:<path> | wc -l                      # 数该版本下的行数
> ```
> 刻意**不用**工作树统计：那会随未提交改动漂移，使本文档的"证据"无法在事后被复核。
> 两列的区别必须讲清楚：`__init__.py` 行 = 主体实现；全包 `.py` 行 = 含子模块
> （`audit/` 12 个文件、`execution/` 3 个等）。**旧版只给了一个既非前者也非后者的数字。**

| # | Kernel | 路径 | `__init__.py` 行 | 全包 `.py` 行 | Definition Lock 依据 | 职责 |
|---|--------|------|------|------|---------------------|------|
| 1 | **identity** | `src/kernels/identity/` | 1327 | 2219 | §112 | Agent 身份、权限、L0-L7 作用域过滤 |
| 2 | **memory** | `src/kernels/memory/` | 638 | 1318 | §112 | 多层记忆存储、L0-L7 作用域过滤、CRUD、跨层压缩整合 |
| 3 | **context** | `src/kernels/context/` | 264 | 264 | §112 | 12 类输入 → 压缩 → 模型就绪上下文 |
| 4 | **capability** | `src/kernels/capability/` | 558 | 558 | §112 | 能力注册中心、版本、追溯链、作用域检查 |
| 5 | **policy** | `src/kernels/policy/` | 1033 | 1033 | §112 | ABAC 策略引擎、优先级评估、人类主权覆盖 |
| 6 | **execution** | `src/kernels/execution/` | 1414 | 3044 | §112 | Goal→Task→Plan→Action→Verify 全链路 |
| 7 | **resource** | `src/kernels/resource/` | 634 | 634 | §112 | CPU/Mem/Storage/Token/Time/$ 六种配额 |
| 8 | **event** | `src/kernels/event/` | 401 | 401 | §112 | 统一事件总线、correlation ID、死信重试 |
| 9 | **network** | `src/kernels/network/` | 834 | 834 | §112 | 协议适配（A2A/MCP/gRPC/HTTP/WS）、路由 |
| 10 | **trust** | `src/kernels/trust/` | 765 | 765 | §112 | 信任评分、传播、衰减、撤销 |
| 11 | **evaluation** | `src/kernels/evaluation/` | 732 | 732 | §112 | 结果评估、反馈、重规划触发、升级 |
| 12 | **security** | `src/kernels/security/` | 795 | 1065 | **§112** | RBAC+ABAC、Vault Transit 加密、审计 |
| 13 | **audit** | `src/kernels/audit/` | 2288 | 6694 | **§113** | 审计链（hash-chain，设计为防篡改；运行时完整性当前 UNVERIFIED：链存在分叉，待 F1–F6 修复+独立验证）、关联查询 |
| 14 | **plugin** | `src/kernels/plugin/` | 590 | 590 | **§115** | 插件注册、热加载、版本兼容、作用域激活 |
| — | *（非注册）* **retention** | `src/kernels/retention/` | *134* | *1131* | — | **HD-06 留存子系统**，非注册内核（见 §0.5） |

**14 个注册内核合计：12,273 行（`__init__.py`）/ 20,151 行（全包 `.py`）。**
连同非注册的 `retention/`：`src/kernels/` 全树 = 12,407 / 21,282 行。

> ⚠️ **v1.0 此处写的是「合计 6,391 行」，该数字有三个问题，且没有一个是对的**：
> 1. **它与它自己上方的表格不符** —— 表内 14 个行数相加为 **6,581**，与 6,391 差 190。
>    一个在同一页里与自己矛盾的"权威清单"，其数字不能再被任何文档引用。
> 2. **口径不明** —— 既不是 `__init__.py` 之和，也不是全包 `.py` 之和，两者都不是6,391。
> 3. **全部低估** —— 14 个内核 **14/14** 的行数都比表中所列**多**，`audit` 一项就
>    从 423 实际为 2,288（`__init__.py`）/ 6,694（全包），低估 5倍以上。
>
> 本次修订一次性改为**双列 + 明确测量方法 + 锁定 commit**，使每个数字都可被第三方复算。

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

12 (§112) + 1 (§113) + 1 (§115) = 14
```

这 14 个名字与 §1 表、与运行时注册表 `KERNEL_ENTRIES` **三处一致**。

> ⚠️ **v1.0 在此处写过一句需要收回的强断言**：「这证实了：代码是严格按 Definition Lock
> 实现的，只是宪法文件本体丢失了。」——这是**循环论证**：被"证实"的 DL 正文已经不在
> 仓库中（见 [`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md)），无法与代码比对；
> 而这里唯一的证据是代码 docstring 里**自己写的** `DL:§xxx` 标注。用代码去"证实"代码的
> 自我声明，只能证明标注一致，不能证明实现符合一份已不可见的宪法。
> **可说的只有**：14 个内核各自声明了一个 DL 条款号，且这些声明与本表一致。
> **不可说**：代码"严格按"DL 实现，或 DL 与代码"完全一致"。宪法缺失仍是缺失。
>
> 同理，v1.0 的「← 与 `src/kernels/` 目录数完全一致」已在 §0.5 纠正（目录数是 15）。
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

> ⚠️ **本节是 2026-09-06 的历史快照，已过时，勿引用为当前状态。**
> 当时 14 个内核只落盘了代码、没有任何测试。此后内核测试与七维 DoD 已全部补齐：
> **14 个内核 DoD 达标 14/14**，本机内核回归集 602 passed，`src/` 全部模块可 import。
> 当前权威结论见 [`KERNEL-DOD-AUDIT.md`](KERNEL-DOD-AUDIT.md) 与
> [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md)。下面的"达标数量：0"只代表
> 2026-09-06 当天，保留作演进对照。
>
> ⚠️ **「602 passed」是一个无法被复现的数字，故不复述为当前事实。** 它描述的是
> 2026-09-06 那天的一次本机运行，既无 commit锁定、也无命令记录；仓库此后新增了大量
> 测试与内核（`retention/`），该计数早已不成立，且本项目历史上多次出现
> "`|| echo` 吞退出码"导致的假绿基线。**要引用内核测试数，请先跑出来并记录命令与commit。**

### 4.1 代码已落盘，但验证缺失（2026-09-06 快照）

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

按"出错代价"排序。**行数为 `p36@2bac346b` 实测（全包 `.py`），已按真值重排**——
v1.0 用的是那组失真数字，导致"`execution` 是最大模块"这一结论本身就是错的
（真实排名：audit 6,694 > execution 2,889 > identity 2,219> …）。

1. **`security`**（1,065 行）— 权限漏洞代价最高
2. **`policy`**（1,033 行）— 策略判决错误会导致越权
3. **`execution`**（3,044 行）— 逻辑复杂，且是 Goal→Action 的执行面
4. **`audit`**（6,694 行）— 审计链断裂会导致合规失效（**代码量最大**）
5. `identity`（2,219 行）
6. `trust`（765 行）— 信任传播算法易错
7. `evaluation`（678 行）
8. `network`（834 行）
9. `capability`（558 行）
10. `resource`（634 行）
11. `plugin`（590 行）
12. `event`（401 行）
13. `memory`（1,318 行）— 含压缩整合
14. `context`（264 行）

---

## 5. 使用规则

1. **新增 kernel**：必须先在本文件登记，并说明它不归属于现有 14 个中的任何一个。
   登记的同时必须把它加进 `src/kernels/_registry.py` 的 `KERNEL_ENTRIES`；**只加目录
   不加注册表**就会重演`retention/` 造成的"目录数 ≠ 内核数"歧义（本文件 §0.5）。
2. **修改 kernel 接口**：同步更新 `liuhao-x-kernels-interface.md` 与 `capability-registry.yaml`。
3. **引用 kernel**：代码内一律用 `src/kernels/<name>` 路径，不要用 K01–K12 编号。
4. **文档引用**：其他文档提到 kernel 时，链接到本文件，不要各自维护清单。
5. **行数不是身份**：本文件 §1 的行数列是**证据**，不是定义，且会随代码变动过时。
   任何"共 N 行 /共 M 个内核"的说法，在被复算之前都应视为未经验证。**计数必须来自
   运行时或 `git show HEAD:`，不能来自记忆、不能来自本文档上一版。**
   本文档 v1.0→ v1.1 的修订正是这条规则缺失所导致的事故：一份自称"唯一权威来源"的
   文件，其14 个行数 14/14 失真、合计数与自身表格矛盾、并把目录数当作内核数。
