# 内核规范索引（docs/kernel-spec）

本目录收录 14 个内核的「生命周期 / 错误处理 / 权限边界」规范。原稿来自
`D:\WorkBuddyFiles\parallel-track-archive\kernel-spec\`，合入时逐篇核对当前代码、
追加了统一的**诚实状态头**，并清除了原稿对一份仓库外审计文档的全部引用。

> ⚠ 阅读前必读下方「本规范的诚实边界」。这些文档整体是**设计意图 / 契约（目标态）**，
> **不是**对当前代码状态的忠实描述。每篇文首的「诚实状态头」已标明该篇哪些段落是
> 已核对现状、哪些是目标态待修订。

## 一、14 篇清单与一句话职责

| 文档 | 内核 | 真实实现类（路径:行号）¹ | 进程级入口 | 自动驱动到 READY |
|------|------|------------------------|------------|------------------|
| [audit.md](./audit.md) | 防篡改审计（哈希链） | `src/kernels/audit/__init__.py:104` `AuditStore` | `get_audit_store()` | 是（单例） |
| [capability.md](./capability.md) | 能力注册中心 | `src/kernels/capability/__init__.py:105` `CapabilityRegistry` | `get_capability_registry()` | 是（单例） |
| [context.md](./context.md) | 上下文压缩 | `src/kernels/context/__init__.py:71` `ContextKernel` | `create_context_kernel()` **工厂** | **否** |
| [evaluation.md](./evaluation.md) | 结果评估与反馈闭环 | `src/kernels/evaluation/__init__.py:141` `Evaluator` | `get_evaluator()` | 是（单例） |
| [event.md](./event.md) | 统一事件总线 | `src/kernels/event/__init__.py:137` `EventBus` | `get_event_bus()` | 是（单例） |
| [execution.md](./execution.md) | 编排内核 | `src/kernels/execution/__init__.py:572` `ExecutionEngine` | `create_execution_engine()` **工厂** | **否** |
| [identity.md](./identity.md) | 身份与权限基座 | `src/kernels/identity/__init__.py:141` `IdentityManager` | `get_identity_manager()` | 是（单例） |
| [memory.md](./memory.md) | 多层记忆存储 | `src/kernels/memory/__init__.py:268` `MemoryKernel` | `get_memory_kernel()` | 是（单例） |
| [network.md](./network.md) | 协议适配与路由 | `src/kernels/network/__init__.py:348` `NetworkBus` | `get_network_bus()` | 是（单例） |
| [plugin.md](./plugin.md) | 插件注册与生命周期 | `src/kernels/plugin/__init__.py:76` `PluginRegistry` | `get_plugin_registry()` | 是（单例，且 import 期即急切 READY） |
| [policy.md](./policy.md) | ABAC 策略裁决 | `src/kernels/policy/__init__.py:357` `PolicyEngine` | `get_policy_engine()` | 是（单例） |
| [resource.md](./resource.md) | 资源配额 | `src/kernels/resource/__init__.py:146` `ResourceQuotaManager` | `get_resource_manager()` | 是（单例） |
| [security.md](./security.md) | RBAC+ABAC 访问权威 | `src/kernels/security/__init__.py:177` `SecurityEngine` | `get_security_engine()` | 是（单例） |
| [trust.md](./trust.md) | 信任评分与信任链 | `src/kernels/trust/__init__.py:170` `TrustManager` | `get_trust_manager()` | 是（单例） |

¹ 表中类名行号已由本次合入**逐篇脚本校验**（读该文件第 N 行确认含 `class <名>`）；12 个
`get_*()` 入口行号同样校验（确认第 N 行含 `def <名>`）；`context` / `execution` 的工厂行号
亦已校验（`:240` / `:913`）。

每篇文首的「诚实状态头」给出该内核的精确行号、`lifecycle` 与四个方法的位置、未接线部分，
以及其对外部审计文档引用的处理结论。

## 二、本规范的诚实边界（务必照此理解）

1. **生命周期协议已定义**：`src/kernels/_base.py` 定义了 `KernelLifecycle` 枚举（UNINITIALIZED /
   INITIALIZING / READY / PAUSED / STOPPED / ERROR）、`KernelError` 异常族、`KernelInterface`
   Protocol 与可选 `Kernel` 基类。14 个内核的目标类**现在都具备** `lifecycle` 字段与
   `initialize()` / `shutdown()` / `pause()` / `resume()` 方法。

2. **12/12 单例内核在构造后即为 READY**：`audit` / `capability` / `evaluation` / `event` /
   `identity` / `memory` / `network` / `plugin` / `policy` / `resource` / `security` / `trust`
   这 12 个内核都有进程级惰性单例（`get_*()`）。其不变量是「**存在即 READY**」——`get_*()`
   在构造完成后立刻调用 `initialize()`（见各 `get_*()` 内的注释）。
   首次真正使用（而非进程启动）时才惰性构造。

3. **`context` / `execution` 无进程级单例、不被自动驱动**：二者只有工厂
   `create_context_kernel(...)` / `create_execution_engine(...)`，**没有** `get_*()` 单例，
   工厂**不会**把它们驱动到 READY。把它们写成「启动即就绪 / 构造即 READY」是**不实陈述**。
   `src/kernels/_registry.py` 的 `snapshot()` 将二者诚实报告为「无规范实例」（factory-only）。

4. **`shutdown` / `pause` / `resume` 目前没有后台驱动方**：这四个生命周期方法已存在，但
   **只由显式调用者触发**，没有任何定时器 / 守护协程 / 启动钩子自动流转状态。注册表
   `src/kernels/_registry.py` 提供 `initialize_all()` / `shutdown_all()` / `snapshot()` /
   `uninitialized_but_present()` 作为**显式**驱动入口（且不创建任何实例）。文档中凡声称某内核
   「状态会自动流转 / 后台自动 pause / 定时校验」的，均属虚构。

5. **权限名（冒号形式）只被 authority A 真正裁决**：权限映射见
   `src/kernels/security/_permission_map.py`（authority A，colon 权限如 `context:read`）。
   该表明确说明——多数 A 侧权限**没有** authority B（`src/kernels/policy` 的 point-dot 动作）
   对应项；只有 `context:write` / `capability:manage` / `execution:trigger` / `resource:allocate` /
   `evaluation:run` 等少数有 B 对应。`_permission_map.py` 刻意「不改任何裁决」，未知/未播种权限
   仍按 fail-closed 处理。文档中凡声称「某冒号权限已被统一强制执行」的，需以该表与 `_adjudicate`
   实参为准重测。

6. **14 篇已全部清除对仓库外审计文档的引用**：原稿普遍以「审计 §x.y」形式引用
   `ARCHITECTURE-AUDIT.md`——该文档**不在本仓库中**，其结论已被项目纪律认定不准。
   `policy.md` 原第 69 行曾直接引用被禁的「审计 P0」（安全双权威）结论，该段已删除。
   本次已用脚本逐篇清除全部此类引用（14/14 篇复查均无残留），并在每篇头部「外部审计引用」
   一条中声明「无」。**目录内不含、也不得引入**该审计文档及其结论。
   文档中凡源自该审计、尚未以代码重新实测的「差距」断言，**一律以代码为准**。

## 三、可观测与核对入口

- 生命周期协议基座：`src/kernels/_base.py`
- 14 内核注册表（只读探测 + 显式驱动，零行为变更）：`src/kernels/_registry.py`
  - `snapshot()`：返回每个内核实时 `lifecycle`（只读，不创建实例；context/execution 报「无规范实例」）
  - `initialize_all()` / `shutdown_all()`：对已存在的实例做显式驱动
  - `uninitialized_but_present()`：报告「实例存在却仍 UNINITIALIZED」的真实缺口
- 受人类闸门保护的只读端点：`GET /v1/kernels`（位于 `src/gateway/kernels.py`，挂在带
  `require_human_principal` 闸门的 router 上，返回 `snapshot()` 内容）
- 相关测试：`tests/kernels/test_kernel_lifecycle.py`、`tests/kernels/test_kernel_registry.py`

## 四、合入纪律提醒

- 本目录**未**合入 `ARCHITECTURE-AUDIT.md`（34KB）与 `PROJECT-BASELINE.md`（18KB）——这两份
  不要进仓库；其结论不得被本目录任何规范引用。
- 本目录文档为**只读规范**，本次合入未改动任何 `.py` 代码。
- 这 14 篇应被理解为「**设计意图 / 目标态契约**」而非「现状规范」；在依照它们改代码前，
  先以当前 HEAD 实测核实每篇「差距」段落。
