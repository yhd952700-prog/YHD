# Plugin Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/plugin/__init__.py:76` 的 `PluginRegistry`。`lifecycle` 字段见 :78；`initialize/shutdown/pause/resume` 见 :392/:395/:398/:403。
> - **进程级入口**：`get_plugin_registry()`（`src/kernels/plugin/__init__.py:413`，构造后立刻 `initialize()`）。**注意**：本模块是 14 个内核中唯一在 import 期就急切实例化 `_global_plugin_registry` 并立即 `initialize()` 的（:479），即「存在即 READY」在导入时即发生，并非纯惰性。
> - **生命周期判定：已实现**。本类具备 `lifecycle` 字段与四个生命周期方法（存在即 READY）。
>   - **是否自动驱动**：**是**——进程级单例，首次使用时（甚至 import 时）即 READY；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；存在「import 期急切实例化」与「懒加载」双路径（文档 §6 所述矛盾属实）；版本比较用字符串 `<`（语义化版本排序错误）；与 `src/plugins` 存在重复加载路径。
>
> **性质提示**：本规范整体是「目标态契约 + 已核对现状」，不是「现状规范」。已核对的现状以上述行号为准；未标注的段落（尤其是 §9 的缺口清单）以代码为准，待实测后修订。
>

## 1. 定义

Plugin Kernel 是 Human-Sovereign Agent OS 的**插件注册与生命周期内核**，提供集中式插件
注册表：发现、加载、热激活/停用与版本兼容检查，并负责把注册表索引持久化到磁盘。它依据
Definition Lock §115，是系统扩展能力的接入点——第三方/内置 kernel 插件通过 `PluginInterface`
契约被加载并纳入 scope 受限的激活管理（`src/kernels/plugin/__init__.py:7-13`）。

## 2. 目标

- **注册**：`register_plugin(name, version, kernel_type, capabilities, scope, compatibility)`
  （`__init__.py:119`）并把 `registry_index.json` 落盘（`__init__.py:378-388`）。
- **发现/过滤**：`discover_plugins` 按 `kernel_type/scope/min_version/max_version` 过滤
  （`__init__.py:192`）。
- **加载/激活**：`activate_plugin` 经 `importlib` 或目录加载并校验 `PluginInterface`
  （`__init__.py:227-316`）。
- **生命周期**：`deactivate_plugin` / `unregister_plugin`（`__init__.py:318/164`）。
- **能力兼容**：`compatibility_check`（`__init__.py:361`）。

**当前实现与目标的差距（现状，基于代码实测，非引用外部审计文档）**：
- **单例双初始化矛盾**：先声明 `_global_plugin_registry=None`（`__init__.py:392`）走懒加载，
  又在模块导入末尾无条件 `_global_plugin_registry = PluginRegistry()`（`__init__.py:457`）
  → 懒分支永远走不到；且构造时 `mkdir("./plugins")`（`__init__.py:83`）产生隐式 cwd 副作用。
- **字符串驱动加载 + 隐式依赖**：`activate_plugin` 用 `plugin_id` 拼路径 `plugins/plugin_id`
  或 `importlib.import_module`（`__init__.py:243-283`），插件须暴露 `PluginInterface` 否则
  `ImportError`/`FileNotFoundError`。
- **版本比较用字符串比较**：`discover_plugins` 的 `min/max_version` 直接 `<`（`__init__.py:213-216`），
  注释自承 "semver-aware later"。
- **scope 集合重复**：`_VALID_SCOPES`（`__init__.py:45`）与 capability/security/resource 各自重复 L0-L7。
- **审计入口重复**：`audit_log` 包装 `src.security.audit_policy.audit_log`（`__init__.py:461-474`），
  与 `kernels/audit` 构成第三套审计入口。

## 3. 生命周期（现状已核对）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`
（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，以及 `ERROR`）。

- **当前状态（已核对 HEAD）**：`PluginRegistry`（`__init__.py:76`）**已接入 `_base`**，
  拥有 `lifecycle` 字段（`:78`，默认 `UNINITIALIZED`）与四个生命周期方法（`:392/:395/:398/:403`）。
  构造即 `_reload_index`（`__init__.py:86`）+ `mkdir`（`__init__.py:83`）副作用；`initialize()`
  置 `READY`。模块导入末尾还会急切实例化全局单例（`:479`）。
- **契约（目标态）**：
  - `initialize()`：从 `registry_index.json` 恢复索引，进入 `READY`；**不得**在 import 期
    急切实例化全局单例（`__init__.py:457` 应删除，统一走 `get_plugin_registry` 懒加载
    `__init__.py:395-400`）。
  - `shutdown()`：刷新并关闭索引文件，进入 `STOPPED`。
  - `pause()/resume()`：挂起/恢复加载活动（如压测/维护窗口）；当前仅置位 `lifecycle`。
- **错误转入 ERROR**：`activate_plugin` 加载抛错（`ImportError`/`FileNotFoundError`，
  `__init__.py:273/278`）应置 `ERROR` 并保留 `info.error`，而非仅静默标记 `FAILED`。

**插件态机（内嵌）**：`PluginStatus`（`__init__.py:48`）`DISCOVERED → REGISTERED → LOADING
→ ACTIVE` 与 `FAILED / UNLOADED`，由 `register_plugin/activate_plugin/deactivate_plugin/
unregister_plugin` 驱动（`__init__.py:119/227/318/164`）。

## 4. 输入模型

- `register_plugin(name: str, version: str, kernel_type: str, capabilities: List[str],
  scope: str, compatibility: Optional[Dict]=None, metadata: Optional[Dict]=None)
  → PluginInfo`（`__init__.py:119-129`）。
- `PluginInfo`（`__init__.py:58`）：`plugin_id / name / version / kernel_type / capabilities /
  scope / compatibility / status / loaded_at / unloaded_at / error / metadata`。
- `discover_plugins(kernel_type?, scope?, min_version?, max_version?)`（`__init__.py:192`）。
- `activate_plugin(plugin_id: str) → Optional[PluginInfo]`（`__init__.py:227`）。
- `deactivate_plugin(plugin_id, reason="")`（`__init__.py:318`）、
  `unregister_plugin(plugin_id, reason="")`（`__init__.py:164`）。
- `compatibility_check(plugin_id, required_capabilities)`（`__init__.py:361`）。

## 5. 输出模型

- `PluginInfo`（`__init__.py:58`）：注册/发现/激活的统一返回单元，`status` 反映生命周期。
- **持久化**：`_save_index`（`__init__.py:378-388`）写 `./plugins/registry_index.json`
  （经 `_json_default` 处理 `datetime`，`__init__.py:29-41`）。
- **审计事件**：`audit_log`（`__init__.py:461-474`）发 `plugin_register/unregister/activate/
  deactivate/activate_failed`，但 `scope` 硬编码 `"L1"`（`__init__.py:153-312`）。
- `_loaded: Dict[str, Any]`（`__init__.py:81`）：已激活插件模块的内存缓存。

## 6. 错误处理（现状已核对）

对齐 `src/kernels/_base.py` 的异常族：
`KernelError`(基类) / `KernelNotInitializedError` / `KernelStateError` /
`KernelPermissionError` / `KernelCapabilityError` / `KernelConfigurationError`。

- **当前实际异常**：`register_plugin` 对非法 `scope` 抛 `ValueError`（`__init__.py:131-132`），
  应改为 `KernelConfigurationError`；加载失败抛 `ImportError`/`FileNotFoundError`
  （`__init__.py:273/278`），应改为 `KernelCapabilityError`（缺 `PluginInterface` 即能力契约不满足）。
- **应抛异常的点**：
  - 磁盘不可写 / `registry_index.json` 损坏（`_reload_index` `__init__.py:88`、
    `_save_index` `__init__.py:387`）→ `KernelConfigurationError`。
  - 重复 `plugin_id` 注册（`plugin_id = f"{kernel_type}:{name}:{version}"` `__init__.py:134`）
    应抛 `KernelStateError` 而非静默覆盖（`__init__.py:150`）。
  - `activate_plugin` 在 `info` 不存在时返回 `None`（`__init__.py:232-233`），应抛
    `KernelNotInitializedError`。
- **当前缺失/错误（现状实测，非引用外部审计）**：
  - **单例双初始化**：`__init__.py:457` 在 import 期急切实例化，导致 `get_plugin_registry`
    的懒加载分支（`__init__.py:398-399`）永不可达，且 `mkdir("./plugins")` 副作用早于任何调用。
  - **字符串版本比较**：`__init__.py:213-216` 的 `<` 对语义化版本排序错误（`"1.10"<"1.9"`）。

## 7. 权限边界（现状已核对）

- **所需 scope**：`register_plugin` 校验 `scope ∈ _VALID_SCOPES`（`__init__.py:45/131`），
  但仅校验格式，未做 scope 授权裁决。
- **所需 capability**：审计使用 `permission="plugin:manage"`（`__init__.py:156-312`），应经
  `policy` 内核 `capability_required` 规则裁决。注意：`capability_required` 规则**已激活**
  （L1，precedence=200，DENY 缺能力），并非失活；`_adjudicate` 调用 `evaluate_policy_simple`
  时传 `scope=None`，是否真正拦截 `plugin:manage` 需以代码实测确认，不可断言"失活"。
- **与 policy/security 边界**：插件的加载/激活不直接调用 `policy`/`security`，审计经
  `src.security.audit_policy` 而非 `kernels/audit`，存在**审计权威分裂**（以代码实测为准）。
- **当前越界/重复（现状实测）**：
  - **与 `src/plugins` 重复**：`kernels/plugin` 的 `importlib` 加载（`__init__.py:248-283`）
    与 `src/plugins/manager.py:149-201` 功能重叠，两套插件加载路径并存。
  - **三套审计 store**：`kernels/plugin` 的 `audit_log`（`__init__.py:461`）→ `src/security/
    audit_policy` 与 `kernels/audit` 及 `src/audit`（shim）构成三条审计入口。
  - **scope 集合重复**：`_VALID_SCOPES`（`__init__.py:45`）应在 `_crosscutting` 共享。

## 8. 测试要求

通过 DoD 必须满足：

- **单元**：`register_plugin` 写盘后 `_reload_index` 可恢复；`discover_plugins` 各过滤维度；
  `compatibility_check` 缺能力返回缺失列表（`__init__.py:361-376`）。
- **边界**：`register_plugin` 非法 `scope` 抛 `KernelConfigurationError`（替换当前 `ValueError`，
  `__init__.py:131`）；重复 `plugin_id` 必须报错而非覆盖（`__init__.py:150`）。
- **失败路径测试**：
  - **单例双初始化**：断言 `get_plugin_registry()` 首次调用才实例化且 `import` 阶段不触达
    磁盘副作用（删除 `__init__.py:457` 后）；验证 `./plugins` 不会被 import 创建。
  - **字符串版本比较**：`discover_plugins(min_version="1.9", max_version="1.10")` 必须包含
    `"1.10"`（当前 `info.version < min_version` 用 `<` 会误判，需改 `packaging.version`）。
  - `activate_plugin` 加载无 `PluginInterface` 的模块必须抛 `KernelCapabilityError`
    而非 `ImportError`；`info` 不存在应抛 `KernelNotInitializedError`。
  - 审计入口必须统一到 `kernels/audit`：断言 `audit_log` 不再绕道 `src.security.audit_policy`。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**，并非全部已在 HEAD 实测确认；落地前须以当前代码重新核实，不可照抄：

- ✅ **已完成（2026-09-15）**：删除 import 期急切实例化（原 `__init__.py:476` 的 `_global_plugin_registry = PluginRegistry()` + 手工 `initialize()`），统一走 `get_plugin_registry` 懒加载。实测证实该急切实例化会在 **import 时**执行 `PluginRegistry.__init__` 的 `mkdir("./plugins")`（相对 cwd）⇒ cwd 副作用；且与 `_registry.py` 把本内核列为「12 个**惰性**单例」的契约冲突。回归测试 `TestImportIsSideEffectFree`（子进程 + tmp cwd 断言：`_global_plugin_registry is None` 且未新建 `plugins/`）。
- ✅ **已完成（2026-09-15）**：`discover_plugins` 的 `min_version`/`max_version` 由字符串 `<`/`>` 改为 `packaging.version` 语义比较（新增 `_version_ge`；不可解析的版本串回退字符串比较）。实测旧行为：`"1.10.0" < "1.9.0"` 为真 ⇒ 合法的插件会被静默漏掉。回归测试 `TestVersionBoundsAreSemantic`。
- 加载/激活失败应抛标准 `Kernel*` 异常（而非 `ImportError`/`FileNotFoundError`/`None`）。**（未动；属错误语义改造，需决策）**
- 审计入口应统一收敛到 `kernels/audit`，消除与 `src/security.audit_policy` 的双/三套权威分裂。**（未动；属架构收敛）**
- `_VALID_SCOPES` 等 scope 集合应在 `_crosscutting` 统一共享，消除跨内核重复定义。**（未动；属重构）**
- `capability_required` 对 `plugin:manage` 的真实拦截效果需以代码实测确认（规则已激活）。**（本次未核实）**
