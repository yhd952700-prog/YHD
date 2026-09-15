# Context Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **真实实现**：`src/kernels/context/__init__.py:71` 的 `ContextKernel`。`lifecycle` 字段见 :81；`initialize/shutdown/pause/resume` 见 :222/:225/:228/:233。
> - **进程级入口**：工厂 `create_context_kernel(...)`（`src/kernels/context/__init__.py:240`）。**无进程级单例**，也没有 `get_context_kernel()`。
> - **生命周期判定：已实现协议，但不自动驱动**。`ContextKernel` 已实现 `lifecycle` 字段与四个方法；**但它只有工厂，构造后停留在 `UNINITIALIZED`，不会被自动驱动到 READY**——`src/kernels/_registry.py` 的 `snapshot()` 将其报告为「无规范实例」（factory-only）。把它写成「启动即就绪 / 构造即 READY」即属不实。
> - **本文档性质**：目标态契约 + 已核对现状。标注「目标态（尚未实现）」者为设计意图；未标注者以代码为准。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。

## 1. 定义

Context Kernel 负责把 12 类原始输入流（goal / task / memory / identity / capability / resource / event / network / trust / evaluation / policy / time）经注意力机制压缩成"模型就绪上下文包"。它在 Human-Sovereign 架构中位于执行链上游，为模型推理与规划提供带 scope 标记、带 correlation_id 的可追溯上下文。纯内存、无持久化。

## 2. 目标（现状可实现的能力）

- 接受 12 类类型化输入（`ContextInputType`，`src/kernels/context/__init__.py:24`）。
- 应用注意力/压缩机制产出 `ContextCompression`（`compress:108`、`ContextCompression:52`）。
- 输出模型就绪包，并维护 correlation_id 端到端可追溯（`ContextInput.correlation_id:46`）——**2026-09-15 整改**：`ContextCompression` 新增 `correlation_ids`（从输入去重按序携带）；`compressed[type]` 新增 `data`（该类输入的全部 payload）。此前二者都被丢弃：产出只有 `{count,scope,mechanism}`。
- **scope 是标签，不是过滤器**：`set_scope:94` 校验并存 `self.scope`，`compress` 用它给产出打标；**不按输入自带的 `scope` 筛输入**——按 §7「Context 不做访问裁决」，筛选应委托 Policy/Identity。（原稿「支持 L0–L7 scope 过滤」的措辞与此不符，已更正。）
- 确定性、与输入顺序无关的输出（重要修复见 `compress` 注释，:124-184）。UNIFORM/RECENCY「全保留」行为由 `tests/kernels/context/test_context_retention.py` 守卫。

## 3. 生命周期（现状）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- `ContextKernel`（`src/kernels/context/__init__.py:71`）已实现 `lifecycle` 字段（:81）与四个方法（:222-233）。
- **工厂，不自动驱动**：`create_context_kernel(mechanism, scope)`（:240）构造实例；工厂**不调用 `initialize()`**，实例停留在 `UNINITIALIZED`。本内核无进程级单例，注册表 `snapshot()` 如实报告为「无规范实例」。
- `initialize()`：契约 READY（幂等）；当前无额外资源需初始化。
- `shutdown()`：契约 STOPPED；当前无持久状态需释放（纯内存）。
- `pause()/resume()`：当前仅置位状态，PAUSED 语义「拒绝 compress/process」属目标态（见第 8 节）。
- 错误转入 `ERROR`：当 `set_scope` 收到非法 scope（当前抛 `ValueError`，:98）应转 `KernelConfigurationError` 并置 ERROR。

## 4. 输入模型

- `ContextInput(type: ContextInputType, source: str, timestamp: float, data: Any=None, correlation_id: Optional[str]=None, scope: str="L0")`（:39）。
- `set_scope(scope: str) -> None`（:94，非法抛 `ValueError`）。
- `add_input(input_: ContextInput) -> None`（:101，未知类型抛 `ValueError`）。
- `compress(inputs: Optional[List[ContextInput]]=None) -> ContextCompression`（:108）。
- `process(inputs) -> ContextCompression`（:212，全流水线）。
- 枚举：`ContextInputType`（12 类，:24）、`AttentionMechanism`（UNIFORM/IMPORTANCE/RECENCY/HYBRID，:63）。
- 注意：`compress` 接收的 `inputs` 参数才是真实数据源，`add_input` 写入的 `_input_counts` 不参与 `compress` 产出（待核实，见第 8 节）。

## 5. 输出模型

- `ContextCompression`（:52）：`compressed: Dict[str,Any]`、`attention_weights: Dict[str,float]`、`retained_keys: List[str]`、`discarded_keys: List[str]`、`compression_ratio: float`、`scope: str`、`timestamp: float`。
- **不持久化**，纯函数式返回。

## 6. 错误处理（对齐 `src/kernels/_base.py`）

- `KernelConfigurationError`：`set_scope` 收到非 L0–L7 的 scope（:97-98，当前抛 `ValueError`）应改抛此异常。
- `KernelStateError`：在 PAUSED 态调用 `compress`/`process`。
- `KernelNotInitializedError`：READY 之前调用 `compress`。

## 7. 权限边界

- 本 kernel 需要的 capability / scope：内置 `context_compression`（owner=`context_kernel`，scope=L3，:307-316）。`context.compress`/`context.process` 被 Policy 预批准（`internal_service_allowed_actions`，policy `:85-86`）；`context.set_scope` 属权威变更被 `internal_service_denied_actions` 拦截（policy `:124`）。
- 权限名（冒号形式，authority A）见 `src/kernels/security/_permission_map.py`：`context:read`（viewer）、`context:write`（admin，对应 authority B 的 `context.set_scope`）。访问控制收敛设计见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`。
- Context **只负责组装上下文**，不做访问裁决；scope 过滤应委托 Policy/Identity 判定。

## 8. 目标态契约与已知缺口（尚未实现，待代码实测）

以下为设计契约要求与当前实现的**待核实差距**，非以 HEAD 验证过的事实；不得引用任何已删除的审计文档。

- ✅ **已整改（2026-09-15，实测证实）**：`compress()` 此前只记类型计数、`input_.data` 从未读取（`compressed[type]` = `{count, scope, mechanism}`）——现已加入 `data`（该类型全部输入的 payload，按输入顺序）。回归测试 `TestBundleCarriesPayloadAndCorrelation`。
- ✅ **已核实＝设计如此（非缺陷）**：scope 仅用 `self.scope` 打标、不筛输入——按 §7，Context 不做访问裁决，筛选委托 Policy/Identity；输入自带的 `scope` 不回写（产出条目的 `scope` 恒为内核 scope，已被 `test_compressed_bundle_carries_scope_and_mechanism` 固定）。§2 措辞已据此更正。
- **误报记录**：原稿「UNIFORM 策略把内容全丢弃」**已在更早修复**（绝对阈值 `weight>0.5` → 相对保留，见 `compress` 注释 :124-184；守卫 `tests/kernels/context/test_context_retention.py`），本次复核实测 UNIFORM/RECENCY 全保留 ⇒ 判为误报。
- `compression_ratio` 实为 `len(retained_types)/12` 的保留比（:198），命名误导，应为 `retention_ratio`（改名是破坏性 API 变更，未动；测试已固定该字段名）。
- `add_input`/`_input_counts` 是否为被覆盖的死状态（:208-217）——需代码实测。实测结论：`compress` 只读 `inputs` 参数、并重置 `_input_counts`，故 `_input_counts` 对产出无影响（`add_input` 计数仅在被 `process`/`compress` 立刻清零前短暂可见）。但 `test_add_input_increments_count` 固定了该行为，删除属破坏性变更，未动。
- `__import__("time")` 反模式（:45/:59）应改为顶层 `import time`（未动；纯风格，非静默缺陷）。
