# 工具与自动化域 · 只读深度体检

- 审计日期：2026-11-08
- 范围：工具注册/调用、MCP 与插件接口、任务调度、工作流恢复、权限边界、审计留痕
- 不包含：插件页 UI 列表（另有专人）、`src/desktop/**`、`packaging/**`、`installer/**` 的写操作
- 约束：未修改任何产品代码，未 commit，未 git stash；所有真实调用均在独立临时数据目录执行
- 证据目录：`%LOCALAPPDATA%\Temp\liuhao-audit`（临时探针脚本与隔离 SQLite，不在仓库内）
- 环境：Windows；`.venv` Python；`AUDIT_DB_PATH` / `MEMORY_DB_PATH` / `PERSONAL_CONTEXT_DB_PATH` / `LIUHAO_REMINDERS_DB` / `LIUHAO_LOCAL_SCHEDULER_DB` 全部指向临时目录；`LIUHAO_MCP_ADAPTER_ENABLED=0`

## 结论摘要

- P0：**0 条**
- P1：**1 条**
- P2：**4 条**
- P3：**6 条**
- P4：**3 条**
- 总计：14 条

| 编号 | 等级 | 状态 | 一句话结论 |
| --- | --- | --- | --- |
| TOOL-01 | P1 | OPEN | 工具执行本身完全不进审计库，失败调用无法回溯 |
| TOOL-02 | P2 | OPEN | 读文件无超时，Windows 设备名 `CON`/`COM1` 可永久卡死事件循环 |
| TOOL-03 | P2 | OPEN | 工具结果无全局大小上限，`python_compute` 可把 600KB 原样喂给模型 |
| TOOL-04 | P2 | OPEN | 调度器崩溃恢复是 at-least-once，非幂等 handler 会重复产生副作用 |
| TOOL-05 | P2 | OPEN | 执行引擎续跑不认 in-flight 任务，重试无幂等键，写文件/发消息可能执行两次 |
| TOOL-06 | P3 | OPEN | schema 不参与校验，`permission`/`audit_policy` 字段不生效，重名静默覆盖 |
| TOOL-07 | P3 | OPEN | 空结果无提示、非 UTF-8 报原始 codec 错误 |
| TOOL-08 | P3 | OPEN | 旧版 `PluginManager.unload_plugin` 必抛 KeyError；加载失败只给英文原文 |
| TOOL-09 | P3 | OPEN | MCP 无断线重连、调用错误原样冒泡、坏配置 KeyError、重名服务器静默覆盖 |
| TOOL-10 | P3 | OPEN | LangGraph checkpoint 模块类型错位且未接入任何生产代码 |
| TOOL-11 | P3 | OPEN | 本地时区是固定 offset，无 DST 规则；跨夏令时切换会漂移 1 小时 |
| TOOL-12 | P4 | ACCEPTED RISK | 工具返回结构不统一（str / JSON / 中文 prose / dict），靠调用方兜底 |
| TOOL-13 | P4 | ACCEPTED RISK | 硬链接可读到工作区外内容；沙箱无内存/CPU 配额 |
| TOOL-14 | P4 | ACCEPTED RISK | 插件声明版本与内核版本在激活时不再校验 |

## 验证通过项（非缺陷）

这些是本域里**实测正常**的能力，记录以免后续回归：

- **V-01 工具真的有实现、最小参数可调通**：`make_tools()` 注册 10 个智能体工具（`search_memory`、`query_audit`、`system_status`、`python_compute`、`personal_set_preference`、`personal_add_fact`、`personal_set_display_name`、`read_file`、`list_files`、`manage_reminders`），逐个最小参数调用全部返回非失败；L-Core `register_default_local_tools` 注册 6 个工具，`local_python_compute("2*3")` 返回 `6`。
- **V-02 定时任务真按时间跑**：隔离 DB 起真实 `LocalScheduler`，1s/3s/5s 三个一次性任务实测在 1.093s / 3.015s / 5.093s 触发（tick 0.2s，抖动 ≤ 0.1s）。
- **V-03 并发重复触发被去重**：同一 `job_key` 连续 enqueue 两次返回同一个 id，只执行一次；同 `job_name` 运行中不会并发起第二个。
- **V-04 路径边界有效**：工作区外绝对路径、`..` 穿越、`\\?\C:\...`、工作区内 junction 指向外部目录，读/写/预览全部被拒绝并给出中文“路径越界”。
- **V-05 无任意命令执行面**：智能体工具能力集合为 `audit.query / file.list / file.read / memory.search / personal.write / python_compute / reminder.manage / system.status`，没有 shell/command 能力；`ShellAdapter` 虽存在但未注册到工具面。
- **V-06 沙箱基础逃逸被挡**：RestrictedPython 后端对 `import os`、`open()`、`().__class__`、`getattr`、`eval`、`compile`、`str.format`、`string.Formatter` 全部拒绝或直接失败，未拿到任意类/文件/网络能力。
- **V-07 提醒投递有业务幂等**：`reminder_deliveries` 与 `reminder_dispatch` 上都有唯一索引（`src/tasks/reminders.py:147-168`），重复触发返回 `already_handled`（同文件 1430-1455）。
- **V-08 插件/ MCP 连接失败的中文可操作提示**：内核插件加载器给出“版本不兼容/目录不存在”等中文原因；MCP 连接失败分类为 `command_not_allowed` / `timeout` 并返回中文 detail（实测本机 connect timeout 2s 配置在 4.38s 内失败，分类正确）。
- **V-09 未发现可直接利用的明文密钥**：对工具、MCP、插件、调度模块做 `sk-* / AKIA* / ghp_* / api_key=` 扫描，仅命中测试桩/占位符，无真实密钥。密钥应来自配置/环境变量，本次未读取用户真实配置。

## 详细条目

### TOOL-01 · 工具执行无审计留痕，失败调用无法回溯

- **编号**：TOOL-01
- **模块**：工具注册表 / 智能体工具调用 / 审计
- **文件:行号**：`src/ai/tool_registry.py:124-153`（`execute` 只有 `@observe`）；`src/ai/tools_local.py:777,801,830,855,881,902`（6 个工具声明 `audit_policy="p9.tool.execute"`）；`src/ai/liuhao.py:1029-1042,1124-1136,1139-1205`（智能体调用工具后直接回填结果，不写审计）；`src/ai/observability.py:131-235`（ENTER 的 args 只在 DEBUG，ERROR 只记异常类名，phase 固定为 `tool_registry.execute`）
- **表现**：
  - 工具的注册/审批/激活/停用/吊销都有 `@audited("p9.tool.*")`，唯独真正执行没有。
  - 声明在 `Tool.audit_policy` 上的 `p9.tool.execute` 从未被读取，纯装饰。
  - 失败调用在审计库里完全不存在；日志里也没有工具名、参数摘要、错误文本。
- **是否复现**：已复现。
  - 命令：`python probe_tools.py`（隔离 `AUDIT_DB_PATH`）。
  - 输出摘要：连续 3 次 `ToolRegistry.execute`（错类型/缺参/未知参数）后 `audit_events_before=13`、`audit_events_after=13`、`audit_delta_for_execute=0`；`audit_query` 里 13 条全是 `p9.tool.register/approve/activate`，`principal_id="capability"`，没有任何执行事件。
  - 另：一次注册失败（`AttributeError`）被审计为 failure，说明 audit 链路本身可用，缺的是 execute 这一环。
- **根因**：`execute()` 装饰器只有 `@observe`，漏加 `@audited`；执行路径也没有解析“谁在调用”，`Tool.audit_policy`/`permission` 字段没有消费者。
- **影响面**：所有智能体工具调用、目标执行中的 capability 调用、未来的插件工具调用；安全事件调查、用户申诉、合规审计全部无法回答“谁在何时用什么参数调了什么、结果如何”。
- **安全或数据风险**：中高。属于审计控制缺失，不是直接越权；但发生数据破坏/越权后无法定责、无法定位失败参数。
- **修复方案**：
  1. 给 `ToolRegistry.execute` 增加显式 `principal_id` / `correlation_id` 参数（或从执行上下文取），补 `@audited("p9.tool.execute")`。
  2. 审计 details 只写“参数摘要”：字段名 + 类型 + 长度 + sha256；敏感字段（path、query、content、token）不写明文，复用 `src/automation/audit.py:76-104` 的摘要策略。
  3. 记录 `tool_id`、`capability`、`outcome`、`error_class`、`error_summary`、`duration_ms`、`turn_id/task_id`；失败也写（当前 `ActionResult(success=False)` 要在返回前落审计）。
  4. `model` 的 `permission` 要么执行、要么删字段，避免形成“声明了管控但没人执行”的假安全。
- **需要改的文件**：`src/ai/tool_registry.py`、`src/ai/liuhao.py`、相关单测；可选 `src/ai/observability.py` 把工具名放进 phase。
- **验证方法**：隔离审计库 → 调一次成功工具、一次必失败工具、一次越权工具 → 审计库应新增 3 条 `p9.tool.execute`，含 principal、tool_id、参数摘要哈希、outcome 与错误摘要；用 `audit_query` 能按失败过滤出来。
- **等级**：P1
- **状态**：OPEN

### TOOL-02 · 读文件无超时，Windows 设备名可永久阻塞事件循环

- **编号**：TOOL-02
- **模块**：智能体文件工具 / WorldInterface
- **文件:行号**：`src/ai/world_interface.py:150-180`（`read_file` 直接 `open()` + `read_text`，无 timeout）；`src/ai/tool_registry.py:124-153`（执行层无超时）；`src/ai/liuhao.py:1029-1042`（同步调用发生在 async 生成器内）
- **表现**：`read_file(path="CON")` 或 `"COM1"` 会打开 Windows 控制台/串口设备并一直等待，不会返回；`NUL` 立即返回空串。由于调用是同步的，单个工具调用会卡住整个后端事件循环。
- **是否复现**：已复现。
  - 命令：`python probe_devices.py`（子进程 + 5s 超时保护，避免卡死审计进程）。
  - 输出摘要：`CON` → `timed_out: true, note: "child blocked >5s"`；`COM1` → 同样超时；`NUL` → `ok: true, len: 0`；`C:\Windows\win.ini` → 中文路径越界拒绝。
- **根因**：`resolve_in_workspace` 只做路径归一化与包含判断，不识别 Windows 保留设备名；`open()` 没有超时；注册表与智能体层都没有 per-call watchdog。
- **影响面**：单条工具调用即可让整个本地服务无法响应（聊天、SSE、定时任务同进程）；LLM 幻觉或恶意提示都可能触发。
- **安全或数据风险**：可用性风险高；不是路径越界读写，但属于拒绝服务面。审计也无法记录（调用根本没返回）。
- **修复方案**：
  1. 文件工具入口拒绝 Windows 设备名：`CON, PRN, AUX, NUL, COM1-9, LPT1-9`（大小写不敏感，含 `CON.txt` 这类变体）。
  2. `ToolRegistry.execute` 增加受控超时（例如 30s）并在线程池执行：`asyncio.to_thread` + `wait_for`，避免阻塞事件循环。
  3. 对文件读取使用 `os.stat` 上限 + 分块读取；命名管道/FIFO 直接拒绝。
  4. 同时给 `python_compute` 的 `timeout` 参数 clamp（如 1–60s），禁止模型传 3600。
- **需要改的文件**：`src/ai/world_interface.py`、`src/ai/tool_registry.py`、`src/ai/liuhao.py`、`src/ai/tools_local.py`（timeout clamp）。
- **验证方法**：`read_file("CON")` / `read_file("COM1")` 在 1s 内返回中文“不支持的设备路径”；并发发起一个聊天请求，确认不被阻塞；`python_compute(timeout=3600)` 被 clamp 或直接拒绝。
- **等级**：P2
- **状态**：OPEN

### TOOL-03 · 工具结果无全局大小上限

- **编号**：TOOL-03
- **模块**：工具调用结果 / 智能体上下文
- **文件:行号**：`src/ai/tools_local.py:84-140`（python_compute 直接返回完整 stdout/result，无截断）；`src/ai/liuhao.py:394-423`（`_tool_output_text` 不做长度限制）；对照 `src/ai/tools/kernel_tools.py:296-318`（`read_file` 有 20000 字符截断）
- **表现**：`python_compute` 打印 20 万字符时，工具结果 `result_chars=200002`、`stdout_chars=400004`、序列化后 `600075` 字符，全部原样拼进模型消息。
- **是否复现**：已复现。
  - 命令：`python probe_tools.py`。
  - 输出摘要：`{"status":"executed","result_chars":200002,"stdout_chars":400004,"serialized_chars":600075}`。
  - 对照：`read_file` 读 50 万字符文件只回 20020 字符。
- **根因**：结果大小策略只存在于个别工具（read_file），注册表/智能体层没有统一截断；`_tool_output_text` 只做 JSON 美化，不做长度控制。
- **影响面**：模型上下文被打爆（provider 报 context length、费用飙升、回合失败）；本地 SSE/前端收到超大 payload；极端情况下阻塞序列化。
- **安全或数据风险**：可用性与成本风险；大输出可能把敏感文件内容/日志整段送进第三方模型。
- **修复方案**：
  1. 在 `ToolRegistry.execute` 或 `_tool_output_text` 增加统一上限（建议 32k–64k 字符），超出部分截断并加“已截断，完整结果见文件”提示。
  2. 大结果落盘到工作区临时文件，工具结果只回路径 + 摘要 + 前 N 行。
  3. 对 `python_compute` 限制 stdout/result 总量，超限直接返回“输出过大”而不是继续。
- **需要改的文件**：`src/ai/tool_registry.py`（统一策略）、`src/ai/liuhao.py`、`src/ai/tools_local.py`。
- **验证方法**：同一输出 20 万字符，工具反馈长度 ≤ 64k；模型消息里出现截断标记；完整内容可在工作区文件找到。
- **等级**：P2
- **状态**：OPEN

### TOOL-04 · 调度器崩溃恢复为 at-least-once，非幂等 handler 会重复副作用

- **编号**：TOOL-04
- **模块**：本地任务调度
- **文件:行号**：`src/tasks/local_scheduler.py:485-534`（启动 `force=True` 把 running 任务重新入队）；`src/tasks/local_scheduler.py:563-600`（`_execute` 直接调用 handler）；`src/tasks/local_scheduler.py:346-396`（`_claim_due` 只按 job_name 互斥，不按副作用幂等）
- **表现**：进程在“handler 已产生副作用、但还没写 completed”时崩溃，重启后任务被重新执行；系统没有把 job id 作为幂等键传给 handler。
- **是否复现**：已复现。
  - 命令：`python probe_scheduler.py`（隔离调度 DB；先手工产生一次副作用并把行置为 `running/attempts=1/lease` 过期，再启动调度器）。
  - 输出摘要：`side_effect_lines=["pre-crash","side"]`（同一副作用被执行两次）；job 最终 `attempts=2`、`last_error="lease expired (worker crashed)"`。
  - 同时验证：普通 1s/3s/5s 任务准时触发（1.093/3.015/5.093s）；同 `job_key` 重复 enqueue 返回同 id 且只跑一次。
- **根因**：调度器只保证“不丢任务”，没有 exactly-once 语义；恢复逻辑不区分“副作用是否已经发生”。`_execute` 没有把 `job.id` 作为幂等键或事务边界传给 handler。
- **影响面**：所有非幂等 handler：发消息、写业务文件、下单/下单类外部调用、外部 webhook；提醒投递本身有业务幂等（见 V-07），但通用 handler 没有。
- **安全或数据风险**：重复下单/重复通知/重复写文件；数据一致性风险。
- **修复方案**：
  1. 明确 SLA：at-least-once；要求 handler 接收 `idempotency_key = f"{job_name}:{job_key}:{occurrence}"` 并自行 claim。
  2. 或引入 outbox：handler 只写 outbox（与业务同一事务），由 dispatcher 保证 exactly-once 投递。
  3. 对内置的 reminder handler 保持现有 `reminder_dispatch` 唯一索引；对其他 handler 在注册时强制声明 `idempotent`/`side_effect` 元数据。
  4. `recover_expired_leases` 对“attempts 已产生副作用但状态未完成”的行，优先标记 `needs_review`，不要自动重跑。
- **需要改的文件**：`src/tasks/local_scheduler.py`、`src/tasks/local_handlers.py`、`src/tasks/reminders.py`（key 传递）、相关单测。
- **验证方法**：重复上述崩溃场景，外部副作用计数应保持 1（或任务进入 `needs_review` 而非自动重跑）；正常定时与并发去重仍通过。
- **等级**：P2
- **状态**：OPEN

### TOOL-05 · 执行引擎断点续跑不认 in-flight 任务，重试无幂等键

- **编号**：TOOL-05
- **模块**：Execution Kernel / 工作流恢复
- **文件:行号**：`src/kernels/execution/__init__.py:1662-1700`（`_adopt_journaled_progress` 只采纳 `task_completed`）；`src/kernels/execution/__init__.py:1928-2005`（`_execute_task` 重试循环，`self.executor.execute(action)` 无幂等键）；`src/kernels/execution/_journal.py:149-248`（journal 只有 started/completed/failed）
- **表现**：任务发出 `task_started` 后进程崩溃，恢复时该任务不在 completed 集合里，会被当作 PENDING 重新执行；重试同一 action 也不复用任何副作用键。
- **是否复现**：已复现（journal 语义级）。
  - 命令：`python probe_journal.py`。
  - 输出摘要：`completed=["write_file"]`，`send_message` 只有 `task_started`、不在 completed；`place_order` 是 `task_failed`，同样会被重试。结论行：`send_message/place_order are absent from completed => engine re-executes them`。
  - 结合 TOOL-04 的真实调度器复现，证明“副作用已发生但未标记完成 → 重跑”在本机是可达状态。
- **根因**：恢复只区分“完成/未完成”，不区分“已开始且可能已产生副作用”；执行层没有 idempotency claim，也没有“中断任务需人工确认”的状态。
- **影响面**：目标执行中的所有写文件/消息/外部调用任务；进程崩溃、kill、断电、升级重启都会触发。
- **安全或数据风险**：重复写文件、重复发消息、重复下单；不可逆外部副作用。
- **修复方案**：
  1. 在执行前 claim `idempotency_key = f"{execution_id}:{task_id}:{attempt_group}"`；重试复用同一 key；副作用端按 key 去重。
  2. 恢复时对“有 `task_started`、无 `task_completed/failed`”的任务标记 `needs_review`，不自动重跑；由用户/上层确认后再执行。
  3. 对文件写入使用内容哈希 + 目标存在性校验（当前 `file_write` 已有 sha256/verified，可扩展为幂等依据）。
  4. 若短期不做 exactly-once，至少在 UI 明确提示“中断任务可能已执行，请先核对”。
- **需要改的文件**：`src/kernels/execution/__init__.py`、`src/kernels/execution/_journal.py`、`src/ai/tools_local.py`（file_write 幂等键）、相关状态机与 UI 提示。
- **验证方法**：
  1. 构造 `task_started` → kill → 重启，任务应进入 `needs_review` 而不是自动执行。
  2. 构造副作用后 `task_failed` 重试，外部副作用计数保持 1（使用幂等 key 测试桩）。
- **等级**：P2
- **状态**：OPEN

### TOOL-06 · schema 不校验、permission/audit_policy 不生效、重名静默覆盖

- **编号**：TOOL-06
- **模块**：工具注册表
- **文件:行号**：`src/ai/tool_registry.py:43-55`（`permission`/`sandbox_policy`/`audit_policy` 字段）；`src/ai/tool_registry.py:68-71`（register 直接覆盖同 id）；`src/ai/tool_registry.py:124-137`（execute 不校验 schema，直接 `tool.fn(**inputs)`）；`src/ai/tool_registry.py:162-164`（router 取第一个匹配能力）；`src/ai/tools/kernel_tools.py:341-345`（schema 声明 required，但无人执行）
- **表现**：
  - `path: 123` 被 `str()` 成 `"123"` 去读文件，而不是参数校验失败。
  - `value: "not-an-int"` 对声明 `integer` 的参数仍然执行成功。
  - 缺参/未知参数把 Python `TypeError` 原文回给用户。
  - 声明 `permission="ADMIN_ONLY"` 的工具照样执行。
  - 同 `tool_id` 二次注册静默覆盖；同 capability 多工具时 router 永远只选第一个，第二个不可达。
- **是否复现**：已复现。
  - 命令：`python probe_tools.py`、`python probe_registry_collision.py`。
  - 输出摘要：
    - `wrong_type: {"success": true, "value": "not-an-int"}`
    - `missing: {"success": false, "error": "main.<locals>.<lambda>() missing 1 required positional argument: 'value'"}`
    - `unknown_arg: {"error": "... got an unexpected keyword argument 'bogus'"}`
    - `permission_field: {"success": true, "output": "ran", "declared_permission": "ADMIN_ONLY"}`
    - `same_id_overwrite: "second"`；`router_pick: "cap.first"`
- **根因**：registry 把 schema 当元数据，没有接 JSON Schema 校验；生命周期只校验“非空 + 可调用”；`ToolRouter` 没有冲突检测。
- **影响面**：所有工具；参数错误被误当成真实业务输入（如把 `123` 当路径），插件/MCP 工具重名时行为不确定。
- **安全或数据风险**：中低。`permission` 不生效会形成假安全；“首个匹配”可能让高权限工具被低权限同名能力遮蔽（或反之）。
- **修复方案**：
  1. 在 `execute` 前用 JSON Schema（`jsonschema` 或轻量校验器）校验 `required/type/enum/additionalProperties=false`；失败返回中文可操作错误。
  2. `register` 对已存在的 `tool_id` 默认拒绝，显式 `replace=True` 才允许；启动时检测 capability 冲突并告警/报错。
  3. `permission` 要么接 PolicyEngine，要么删除；`audit_policy` 与 TOOL-01 一起接审计。
- **需要改的文件**：`src/ai/tool_registry.py`、`src/ai/tools/kernel_tools.py`（schema 归一）、相关单测。
- **验证方法**：契约测试：每个工具传错类型/缺参/未知参数都应被拒绝且错误信息为中文；重复 id 注册失败；capability 冲突启动失败。
- **等级**：P3
- **状态**：OPEN

### TOOL-07 · 空结果无提示，非 UTF-8 文件报原始 codec 错误

- **编号**：TOOL-07
- **模块**：智能体工具结果呈现
- **文件:行号**：`src/ai/liuhao.py:394-423`（空串直接返回）；`src/ai/world_interface.py:172-180`（`read_text` 固定 UTF-8，异常原样上抛）；`src/ai/liuhao.py:1139-1205`（异常 `str(exc)` 直接进 tool result）
- **表现**：
  - 读 0 字节文件 → 工具反馈长度 0，模型只看到 `[工具 read_file 结果]` 后没有内容。
  - 读二进制/GBK 文件 → `读取失败：'utf-8' codec can't decode byte 0xff in position 0: invalid start byte`。
- **是否复现**：已复现。
  - 命令：`python probe_output.py`。
  - 输出摘要：`empty.txt → feedback_len: 0`；`binary.bin → 读取失败：'utf-8' codec can't decode byte 0xff ...`；`long.txt` 正常截断到 20020 字符。
- **根因**：没有“空结果”分支；文件读取不探测编码/类型；异常翻译只包了中文前缀，原因仍是 Python 原文。
- **影响面**：LLM 面对空结果容易编造内容；用户看到技术英文错误，无法判断是编码问题还是文件损坏。
- **安全或数据风险**：低；主要是体验与误导。
- **修复方案**：
  1. 空结果统一回“工具返回空结果（文件 0 字节）”，并附文件大小/类型。
  2. 读取前用 `chardet`/字节嗅探判断文本/二进制；二进制拒绝并提示“这不是文本文件”。
  3. 非 UTF-8 中文可试 `GB18030`，失败再报“文件编码不支持，请转成 UTF-8”。
- **需要改的文件**：`src/ai/world_interface.py`、`src/ai/liuhao.py`、`src/ai/tools/kernel_tools.py`。
- **验证方法**：同一组探针文件：空文件有明确提示；二进制返回中文“非文本”；GBK 正常读出或给出可操作建议。
- **等级**：P3
- **状态**：OPEN

### TOOL-08 · 旧版 PluginManager：卸载必崩，加载失败只给英文原文

- **编号**：TOOL-08
- **模块**：插件接口（旧版 manager）
- **文件:行号**：`src/plugins/manager.py:388-411`（`del self._errors[plugin_name]` 在 404）；`src/plugins/manager.py:213-220,296,331,360`（错误存 `str(e)` 原文）
- **表现**：
  - 成功加载的插件，`unload_plugin()` 在 `stop_plugin` 成功后把 `_errors` 键 pop 掉，随后第 404 行无条件 `del`，必抛 `KeyError`。
  - 加载不存在的插件，界面/CLI 得到 `No module named 'plugins.xxx'` 英文原文，无中文可操作原因。
  - 注：网关插件页使用的是内核 `PluginRegistry`（错误已是中文可操作），本条只影响仍在用旧 `PluginManager` 的 CLI/旧入口。
- **是否复现**：已复现。
  - 命令：`python probe_plugin.py`。
  - 输出摘要：`{"unload_raised": true, "type": "KeyError", "detail": "'fake'"}`；`{"load_missing_ok": false, "error_text": "No module named 'plugins.definitely_missing_plugin_xyz'"}`。
- **根因**：`stop_plugin` 成功路径 `pop` 了错误键，`unload_plugin` 又按“必有错误键”删除；错误没有做异常分类与中文映射。
- **影响面**：旧 CLI 无法卸载插件；排障信息不可读。
- **安全或数据风险**：低；可能留下加载中的插件资源未释放。
- **修复方案**：
  1. 改为 `self._errors.pop(plugin_name, None)`。
  2. 把 `ModuleNotFoundError/ImportError/AttributeError/版本不兼容` 映射成中文“插件包不存在/入口类缺失/依赖缺失/版本不兼容”，并附路径。
  3. 统一到内核 `PluginRegistry` 的错误模型，旧 manager 直接复用。
- **需要改的文件**：`src/plugins/manager.py`、相关测试。
- **验证方法**：加载成功后卸载返回 True 且无异常；加载不存在插件返回中文原因；卸载后 `_plugins/_status/_errors` 均无残留。
- **等级**：P3
- **状态**：OPEN

### TOOL-09 · MCP 无断线重连、调用错误原样冒泡、坏配置/重名处理不足

- **编号**：TOOL-09
- **模块**：MCP 适配器
- **文件:行号**：`src/adapters/mcp/mcp_adapter.py:385-400`（`call_tool` 无 try/分类/重连）；`src/adapters/mcp/mcp_adapter.py:153-176`（`server_data["name"]` 硬取，重复名覆盖）；`src/adapters/mcp/mcp_adapter.py:218-257`（连接错误对象，仅 connect 阶段使用）；`src/adapters/mcp/mcp_adapter.py:455-530`（`_classify_failure` 只在 connect 用）
- **表现**：
  - 连接阶段：缺 `name` 直接 `KeyError`，整个配置加载失败；同名服务器后者覆盖前者（`server_count=1`，保留第二组 args）。
  - 已连接后上游断连：`call_tool` 直接把 SDK 异常抛给调用方，session 仍留在 `self.sessions`，不会自动重连；错误是英文。
  - 工具缓存只在“服务器不存在”时刷新，服务器端工具列表变更后仍是旧缓存。
  - 调用参数不按 `inputSchema` 校验，错误由上游返回原文。
- **是否复现**：已复现（连接/配置部分）。
  - 命令：`python probe_mcp.py`、`python probe_mcp_timeout.py`。
  - 输出摘要：`missing_name → KeyError('name')`；`duplicate_name → server_count: 1, args_kept: ["-c","print(2)"]`；`call_unconnected → ValueError: Not connected to server 'deadhttp'`；连接超时分类为 `timeout` 且返回中文 detail（这部分正常）。
  - 断线后自动重连为静态判断（代码里没有 reconnect 分支），未做真实 kill 测试。
- **根因**：MCP 适配器只覆盖“连接/列举”，没有把 `_classify_failure` 与重连策略用到 `call_tool`；配置校验缺省；缓存无失效策略。
- **影响面**：MCP 工具调用（当前默认关闭、未接入智能体工具面，故影响有限）；启用后会出现“一次断连后永久不可用，只能手动重启”。
- **安全或数据风险**：中低；工具名跨服务器已由 `(server_name, tool_name)` 限定，无直接冲突；配置错误可能导致启动失败。
- **修复方案**：
  1. `call_tool` 包 try，按 `_classify_failure` 分类；对幂等工具重连一次并重试，非幂等只返回中文“上游断开，请重试”。
  2. 连接失败时 `disconnect_server` 清理死 session；增加可选的 health-check 重连循环。
  3. `load_config` 校验必填 `name` 唯一，重复/缺失给中文错误并跳过该条而不是整体崩溃。
  4. 工具缓存加 TTL 或每次 `list_tools` 后刷新。
- **需要改的文件**：`src/adapters/mcp/mcp_adapter.py`、MCP 配置校验与测试。
- **验证方法**：起一个本地 mini MCP server → 连接 → kill → `call_tool` 返回中文“上游断开”并自动重连；重复名配置启动失败并指出冲突项；缺 name 配置给中文错误不崩溃。
- **等级**：P3
- **状态**：OPEN

### TOOL-10 · LangGraph checkpoint 模块类型错位且未接入生产代码

- **编号**：TOOL-10
- **模块**：工作流恢复（checkpoint 子模块）
- **文件:行号**：`src/workflow/checkpoint.py:213-215`（把 `node_id`/`edge_id` 直接 `json.dumps` 成字符串）；`src/workflow/checkpoint.py:296-307`（按 `List[str]` 反序列化）；`src/workflow/checkpoint.py:240`（`done` 恒为 False）；`src/workflow/checkpoint.py:360-?`（`restore_checkpoint` 但无 mark_done）
- **表现**：
  - `create_checkpoint(node_id="node-1")` 存成 JSON 字符串 `"node-1"`，`get_checkpoint` 返回 `str`，而类型标注/调用方预期是 `list[str]`；迭代会逐字符。
  - `done` 永远是 False，且没有 API 能把 checkpoint 标记为完成。
  - 全仓库 `src/` 没有任何生产代码引用该模块（只有自身定义），实际恢复走 ExecutionEngine journal。
- **是否复现**：已复现。
  - 命令：`python probe_checkpoint.py`。
  - 输出摘要：`node_stack_type: "str", node_stack_value: "node-1"`, `remaining_edges_type: "str"`, `done: false`, `has_mark_done_api: false`。
- **根因**：`create_checkpoint` 把单值当列表序列化；`done` 没有更新路径；模块是遗留/半成品，没有接入调用方。
- **影响面**：当前无影响（死代码）；一旦有人按注释接入 LangGraph 恢复，节点栈会按字符遍历、checkpoint 永远不完成。
- **安全或数据风险**：低；属于潜在恢复错误。
- **修复方案**：
  1. 存 `[node_id]`/`[edge_id]`，或改成单值列并修正读取类型。
  2. 增加 `mark_done(checkpoint_id)`/`update_checkpoint(...)`。
  3. 若短期不用，删除模块或标注 `experimental` 并在 CI 禁止引用；如计划接入，补 restore→resume 的集成测试。
- **需要改的文件**：`src/workflow/checkpoint.py`（或删除）、相关测试/文档。
- **验证方法**：`create_checkpoint` → `get_checkpoint` 的 `node_stack/remaining_edges` 是 list；`mark_done` 后 `done=True`；恢复调用方按 list 正常遍历。
- **等级**：P3
- **状态**：OPEN

### TOOL-11 · 本地时区是固定 offset，无夏令时规则

- **编号**：TOOL-11
- **模块**：提醒/定时任务时间计算
- **文件:行号**：`src/tasks/reminder_time.py:140-156`（`local_timezone()` 用 `datetime.now().astimezone().tzinfo`）；`src/tasks/reminder_time.py:398-419`（`_next_run_at` 用该 tz 做 `replace(hour=...)` 与 UTC 转换）
- **表现**：
  - Windows 上 `datetime.now().astimezone().tzinfo` 是固定偏移的 `datetime.timezone`，不具备 DST 规则。
  - 当前 offset 下的换算是对的（每天 10:00 → UTC 18:00），但服务启动时把 tz 固定下来；跨夏令时切换后，重复提醒会相对本地墙钟漂移 1 小时。
- **是否复现**：已复现（类型与换算）。
  - 命令：`python probe_reminder_tz.py`、`python probe_scheduler.py`。
  - 输出摘要：`tz_type: "timezone"`, `tz: "太平洋标准时间(墨西哥)"`；`每天十点 → run_at_utc: 2026-11-08T18:00:00`, `local_render: 10:00:00-08:00`；调度器输出 `utcoffset_jan` 与 `utcoffset_jul` 都是 `-1 day, 16:00:00`（同一固定偏移）。
  - DST 切换本身未在真实时钟上等待验证；由“固定 offset 类型”静态推断。
- **根因**：用 `astimezone()` 的当前偏移代替 IANA 时区；没有 `zoneinfo`/Windows 时区映射。
- **影响面**：非中国时区用户的重复提醒/日程在 DST 切换后偏 1 小时；中国无夏令时，当前影响为 0；跨时区/出国用户受影响。
- **安全或数据风险**：低；业务准时性风险。
- **修复方案**：
  1. 配置 IANA 时区（如 `Asia/Shanghai`），用 `zoneinfo.ZoneInfo`；Windows 上做 `tzlocal`/注册表映射。
  2. 每次计算下一次触发时按“本地墙钟 + 时区规则”重新推导，处理不存在/重复的 DST 时刻（跳过或取后者）。
  3. 提供 UI 时区设置，默认取 OS 时区但允许覆盖。
- **需要改的文件**：`src/tasks/reminder_time.py`、`src/tasks/reminders.py`（tz 生命周期）、设置项/前端。
- **验证方法**：用 `America/New_York` 单测跨 3 月/11 月切换，`每天 09:00` 在切换后仍是本地 09:00；中国时区回归不变。
- **等级**：P3
- **状态**：OPEN

### TOOL-12 · 工具返回结构与 schema 格式不统一

- **编号**：TOOL-12
- **模块**：工具接口契约
- **文件:行号**：`src/ai/tools_local.py:762-780`（L-Core schema 是 `{"path":"str"}` 旧格式）；`src/ai/tools/kernel_tools.py:341-345`（智能体 schema 是标准 JSON Schema）；`src/ai/liuhao.py:394-423`（返回 str/JSON/中文 prose 混合）
- **表现**：
  - 两套工具面 schema 格式不同：一套是 JSON Schema，一套是“字段名→类型字符串”。
  - 返回值有的为原始文本（`read_file`），有的为 JSON 字符串（`python_compute/system_status/manage_reminders`），有的为中文 prose（`search_memory` 缺参提示）。
  - 调用方靠 `coerce_tool_output` / 字符串兜底，没有统一 envelope。
- **是否复现**：已复现（静态 + 探针 `output_type` 全为 `str`，内容形态不同）。
- **根因**：两套工具注册路径（kernel_tools vs tools_local）历史分叉，没有统一 Tool 契约。
- **影响面**：新增消费者（UI、SDK、外部 MCP 暴露）需要理解多种返回形态；错误处理容易漏。
- **安全或数据风险**：低；契约清晰度问题。
- **修复方案**：统一返回 `{ok, status, data, error, truncated, meta}`；schema 统一为 JSON Schema；对旧工具加适配层。
- **需要改的文件**：`src/ai/tools_local.py`、`src/ai/tools/kernel_tools.py`、`src/ai/tool_registry.py`、消费端适配。
- **验证方法**：所有工具返回值通过同一 envelope 校验；schema 列表能被同一校验器消费。
- **等级**：P4
- **状态**：ACCEPTED RISK

### TOOL-13 · 硬链接可读工作区外内容；沙箱无内存/CPU 配额

- **编号**：TOOL-13
- **模块**：文件边界 / 受限 Python 沙箱
- **文件:行号**：`src/ai/world_interface.py:150-180`（读路径只做 resolve，不识别硬链接）；`src/plugins/sandbox/backends/restricted_python_backend.py`（能力白名单，无内存/CPU 配额）
- **表现**：
  - 工作区内放一个指向外部文件的硬链接，`read_file` 会读到外部文件内容（写入因 file_write 采用原子替换，外部目标未被改写：实测 `outside_target_after="original"`）。
  - 受限 Python 允许 `'a' * 10**8` 这类内存放大与长循环，只有进程超时，没有内存/CPU 上限；格式串功能被整体禁用（`NotImplementedError`）。
- **是否复现**：已复现（硬链接写不穿透；读未单独断言，静态成立）；沙箱逃逸尝试全部被挡（见 V-06），资源放大为静态判断。
- **根因**：文件边界基于路径字符串 + realpath，硬链接不改变路径；沙箱目标是“能力隔离”，不是“资源隔离”。
- **影响面**：能创建硬链接的本地攻击者本来就有文件系统权限，提权意义有限；沙箱资源放大可被恶意提示利用造成 OOM/CPU 占满。
- **安全或数据风险**：低–中；接受为已知风险，但建议补资源配额。
- **修复方案**：
  1. 读文件前比较 `os.stat` 的 `st_nlink`/卷序列号，或对高敏感目录禁用硬链接读取（Windows 上可用 `GetFileInformationByHandle`）。
  2. 沙箱子进程加内存上限（Windows Job Object）与 CPU 时间上限；`timeout` 参数 clamp。
  3. 恢复受限的 `str.format` 仅用于无属性访问的安全子集（可选）。
- **需要改的文件**：`src/ai/world_interface.py`、`src/plugins/sandbox/backends/restricted_python_backend.py`。
- **验证方法**：硬链接读取被拒绝或告警；`'a'*10**9` 被内存限制拒绝；长循环在 CPU 上限内被杀。
- **等级**：P4
- **状态**：ACCEPTED RISK

### TOOL-14 · 插件版本兼容在激活阶段不再校验

- **编号**：TOOL-14
- **模块**：插件接口（内核 PluginRegistry）
- **文件:行号**：`src/kernels/plugin/__init__.py:330-430`（激活/加载路径）；发现阶段只做元数据读取
- **表现**：插件清单里的 `api_version`/`min_kernel_version` 只在发现阶段用于展示或过滤，`activate_plugin` 不再校验；版本不兼容的插件仍可被激活并 import。
- **是否复现**：静态判断（未构造不兼容插件，避免写入仓库）。
- **根因**：发现与激活两套路径，兼容校验只在发现侧。
- **影响面**：插件升级/内核升级后可能出现运行时 AttributeError；错误发生在激活之后而非激活时。
- **安全或数据风险**：低；可用性/可维护性。
- **修复方案**：激活前复用同一版本校验函数，失败返回中文“插件要求内核 ≥ x.y，当前 z”；在 UI 插件页展示兼容状态。
- **需要改的文件**：`src/kernels/plugin/__init__.py`、插件清单 schema、测试。
- **验证方法**：构造 `min_kernel_version` 高于当前的插件，激活应失败并给出中文原因。
- **等级**：P4
- **状态**：ACCEPTED RISK

## 未覆盖 / 边界说明

1. **外部模型工具未真调**：`local_llm_research`、`local_llm_synthesis` 会按用户配置访问外部模型；为避免真实外网请求与费用，只做静态检查（无 provider 时返回中文“需要配置模型”，有 provider 时正常）。未把它们判为“注册即失败”。
2. **MCP 已连接后断线未做 kill 实测**：`call_tool` 无重连已由代码静态确认（`mcp_adapter.py:385-400` 只有 `raise ValueError`，无 try/reconnect）；受限于 MCP 默认关闭且未接入智能体，未再搭 mini server 做 kill 测试。
3. **自动化观察/点击工具未真调**：三重闸门默认关闭，非 human principal 下 `build_observation_tools` 返回空；强行开启会触碰真实桌面/窗口，超出“只读隔离”边界。已通过能力清单确认智能体面无自动化工具。
4. **`src/desktop/**`、`packaging/**`、`installer/**` 未读写**，插件页 UI 列表未查。
5. **DST 未真实跨时钟验证**：仅验证本地 tz 为固定 offset（`datetime.timezone`）且 1 月/7 月偏移相同，据此判断无 DST 规则；未改系统时钟。
6. **密钥扫描范围**：工具/MCP/插件/调度相关源码；未读取用户真实配置文件（`~/.liuhao/config` 等），因此“无明文密钥”结论只覆盖仓库代码与测试桩。

## 最该先修的 5 条

1. **TOOL-01（P1）**：给 `ToolRegistry.execute` 补审计（principal、tool_id、参数摘要哈希、outcome、错误摘要、correlation_id）。这是安全可追溯性的底座，也决定 TOOL-06 的 `audit_policy` 是否有意义。
2. **TOOL-02（P2）**：文件工具拒绝 Windows 设备名 + 工具执行加超时/线程池。单条 `read_file("CON")` 就能卡死整个服务，修复成本低、收益直接。
3. **TOOL-03（P2）**：统一工具结果大小上限与落盘策略。防止 600KB 输出打爆上下文/费用/前端。
4. **TOOL-04（P2）**：调度器恢复引入幂等键或 outbox；至少对非幂等 handler 标记 `needs_review`。防止重复下单/通知。
5. **TOOL-05（P2）**：执行引擎对 in-flight 任务不自动重跑，重试复用幂等键。与 TOOL-04 一起构成“不可重复任务不执行两次”的闭环。
