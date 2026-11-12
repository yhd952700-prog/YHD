# 部署运维与灾难恢复域 · 只读深度体检（OPS）

> 注（2026-11-08）：文中 `python -m src.upgrade` 的 check/rollback 合并命令已拆成
> 两条显式命令（各自单独写）；除此以外正文未改，归属线已知悉。

- 审计对象：`d:\LiuHao-AI-OS`（当前工作树）
- 审计日期：2026-11-08
- 审计范围：启动/停止/重启、健康检查、配置管理、日志告警、数据备份、灾难恢复、升级回滚、文档操作一致性
- 明确排除：安装器 Inno 脚本细节（安装器本体另有专人）；`src/desktop/**`、`packaging/**`、`installer/**` 只读，未写
- 边界承诺：未改任何代码、未 commit、未 `git stash`

## 0. 执行边界与污染声明（必须先读）

1. **真实安装未被触碰**：`D:\LiuHao` 真实安装与真实数据目录全程未启停、未写入。仅对 `127.0.0.1:8080` 做过两次只读 `GET /v1/health`（返回 `service=liuhao-gateway, version=1.0.0, ai_provider=ollama/real`），用于确认本次实验未影响真实实例。
2. **隔离方式**：独立数据目录 + 独立实例根 + 独立日志目录 + 独立端口 `18808`，全部位于
   `%TEMP%\liuhao-ops-audit-20261108`（实测为 `D:\cache\temp\...`）。
   变量：`LIUHAO_DATA_DIR`、`LIUHAO_LOG_DIR`、`LIUHAO_INSTANCE_ROOT`、`LIUHAO_WORKSPACE_ROOT`、`CONVERSATION_DB_PATH`、`AUDIT_DB_PATH`。
3. **污染声明（诚实披露）**：第一轮按“只设 `LIUHAO_DATA_DIR`”的方式启动（23:56），验证了源码模式的真实缺陷 OPS-001：`conversation_store.db` 仍打开仓库根、员工/目标状态仍落仓库根。该轮对仓库根开发态数据文件只构成“打开/读取”，未删除、未改写既有业务内容；从 00:02 起全部改用 `LIUHAO_WORKSPACE_ROOT` + `CONVERSATION_DB_PATH` 完整隔离。**该现象本身就是 OPS-001 的现场证据。**
4. 本机同一工作树有多个并发会话在编辑，运行期捕获的两条异常（OPS-025/OPS-026）已按“疑似并发瞬时态”单独标注，未当作稳定结论。

## 1. 方法与结论口径

- 静态：读启动链、健康探针、日志/配置、备份/升级实现，逐条给出 `文件:行号`。
- 动态：在隔离目录与隔离端口上真实启动、真实停止、真实灌坏数据库/状态文件/删密钥、真实造失败请求、真实建/验/恢复备份、真实跑升级检查与回滚。
- 结论只写到证据能支撑为止；未能实测的（异机 DPAPI、升级全流程）标 `BLOCKED` 或 `静态判断`，不冒充已验证。

## 2. 结论摘要

| 等级 | 数量 | 说明 |
|---|---|---|
| P0 | 0 | 未发现“直接丢数据/已被利用/完全不可启动”的证据 |
| P1 | 7 | 含 6 条已复现 + 1 条运行期一次、判 NOT REPRODUCED |
| P2 | 11 | 含 9 条已复现/静态确证 + 1 条 NOT REPRODUCED + 1 条 BLOCKED |
| P3 | 7 | 体验/运维噪音/自动化风险 |
| P4 | 2 | 文案/观测性细节 |

**总体判断：可用，但“健康检查的语义”和“故障时是否 fail-open”存在实质性偏差。**
最危险的不是起不来，而是**起得来、探针还显示绿/部分绿，但关键安全闸与后台组件实际没工作**（OPS-002/003/004）。

---

## 3. 逐条发现

### OPS-001 · 启动（源码模式） · 数据目录隔离

- 文件:行号
  - `scripts/start_liuhao.py:62`（`LOG_DIR = ROOT / "logs"`）
  - `scripts/start_liuhao.py` 主流程（约 1005-1075）只透传 `LIUHAO_DATA_DIR`，**未设置** `LIUHAO_WORKSPACE_ROOT` / `CONVERSATION_DB_PATH` / `AUDIT_DB_PATH`
  - `src/ai/conversation_store.py:52`（默认 `<repo_root>/conversation_store.db`）
  - `src/ai/employee_store.py:112-125`（无 `LIUHAO_WORKSPACE_ROOT` 时 `<repo_root>/.liuhao_employees.json`）
  - `src/gateway/ai_management.py:909-922`（无 `LIUHAO_WORKSPACE_ROOT` 时 `<repo_root>/.liuhao_goals.json`）
- 表现：源码模式只设 `LIUHAO_DATA_DIR` 时，**对话库、员工名册、目标状态仍写仓库根**；日志却按数据目录规则解析到 `%LOCALAPPDATA%\LiuHao\data\logs`（`src/logging_config.py:125-160`）。一套“数据目录”实际被拆成多处。
- 是否复现：**已复现**。隔离运行启动快照显示 `conversation=<本地路径>/conversation_store.db:open`，而数据目录中只有 `config/integrity.key`；仓库根 `conversation_store.db` / `.liuhao_*` 同时被打开。补设 `LIUHAO_WORKSPACE_ROOT` + `CONVERSATION_DB_PATH` 后，启动快照即指向隔离目录。
- 旁证：单实例锁路径为 `InstanceLock(instance_root(ROOT))`（`scripts/start_liuhao.py` 约 748），源码模式不同数据目录仍共用同一个实例锁根，无法在同一源码树并行两个隔离实例（安装版由 `launch_installed.py` 设 `LIUHAO_INSTANCE_ROOT`，不受影响）。
- 根因：源码启动器只做了“半个隔离”，未把工作区根与各库路径一并重定向；各存储默认值以 `__file__` 推导仓库根。
- 影响面：开发/排障/自建源码部署；测试实例会与仓库根开发数据混用，排障结论可能建立在别人的数据上。
- 安全或数据风险：中。可能把隔离实例的写操作落到共享开发库；不会触碰 `D:\LiuHao` 安装版。
- 修复方案：`start_liuhao.py` 在确定 `data_dir` 后统一设置 `LIUHAO_WORKSPACE_ROOT`、`CONVERSATION_DB_PATH`、`AUDIT_DB_PATH`、`LIUHAO_INSTANCE_ROOT`（与 `launch_installed.py` 完全对齐），并把提示里的 `LOG_DIR` 改为实际生效的 `resolve_log_file()` 结果。
- 需要改的文件：`scripts/start_liuhao.py`（可选：统一抽到 `src/desktop/paths.py` 供两套启动器共用）。
- 验证方法：仅设 `LIUHAO_DATA_DIR` 启动，断言启动快照中 conversation/audit/employees/goals 四个路径全部位于该数据目录；仓库根 dbo mtime 不变。
- 等级：P1
- 状态：OPEN

### OPS-002 · 健康检查 · 执行围栏“绿而不设防”

- 文件:行号
  - `src/gateway/health.py:623-653`（`status` 初始即为 `healthy`；仅在 `armed && !installed` 才降级；`!armed` 只加 note）
  - `src/gateway/main.py:952`（`attach_default_executor_fence()`）
  - `docs/known-gaps.md:204`、`docs/ci/2026-11-04-execution-record.md:153-156`（`LIUHAO_EXECUTOR_FENCE*` 属 `gate-var-never-set`）
  - 仅 `docker-compose.prod.yml:104-116` 设置该组变量
- 表现：Windows 安装版/源码模式从不设置 `LIUHAO_EXECUTOR_FENCE=on`，于是默认拒绝的自主执行闸门**处于未武装状态**；`/v1/ready` 给 `executor_fence` 的 `status=healthy`、`user_message=执行隔离正常`。
- 是否复现：**已复现**。`/v1/ready` 的 `executor_fence` 实测：
  `{"status":"healthy","armed":false,"installed":false,"backend":"InMemoryExecutorLease","note":"executor fence gate not armed ... the default-deny autonomous-action gate is inactive","user_message":"执行隔离正常"}`
- 根因：把“未开启”定义为配置态而非故障，UI 文案又用“正常”概括；与 Windows 交付形态“从不开启”的事实叠加，形成系统性误导。
- 影响面：所有非 Docker 部署；运维看绿即认为自主执行被拦截，实际没有。
- 安全或数据风险：高（安全闸口形同虚设，且健康检查为其背书）。
- 修复方案：`armed=false` 时 `status` 保持 `healthy` 可以，但 `user_message` 必须改为“未启用（自主执行未被拦截）”；或在安装版启动器显式开启并配置 `LIUHAO_EXECUTOR_FENCE_LEASE_DIR/MAX_EXECUTORS`。二者至少做其一。
- 需要改的文件：`src/gateway/health.py`（文案/语义）、`scripts/launch_installed.py` + `scripts/start_liuhao.py`（若选择开启）。
- 验证方法：未设 `LIUHAO_EXECUTOR_FENCE` 时 `/v1/ready` 文案明确“未启用/未拦截”；设为 `on` 且安装成功时 `installed=true`、`/v1/ready` 仍绿；设为 `on` 但审计库不可用时 `/v1/ready` 必须 503 且指名 `executor_fence`。
- 等级：P1
- 状态：OPEN（与 `docs/known-gaps.md` 记录一致，但健康检查语义仍需修）

### OPS-003 · 灾难恢复 · 审计库缺失/损坏时 fail-open 继续服务

- 文件:行号
  - `src/gateway/main.py:952`（围栏挂载失败仅打 ERROR，不阻断启动）
  - 失败时日志原文：`EXECUTOR FENCE INSTALL FAILED -- autonomous actions will run UN-FENCED (fail-open risk)`
  - 同轮日志：`POLICY DECISION NOT RECORDED ...` × 多条、`kernel_action audit failed ...`
- 表现：`AUDIT_DB_PATH` 未配置（源码默认）或审计库被灌坏时，网关照常提供登录/对话/业务 API；执行围栏退化为 `InMemory` 且未安装，策略决策不落审计链。
- 是否复现：**已复现**（两种触发都实测）：(a) 未设 `AUDIT_DB_PATH`；(b) 将 `audit_store.db` 写成非 SQLite 内容。两次均启动成功、`/v1/health` 200，日志出现 fail-open 原文与逐条 `POLICY ... NOT RECORDED`。
- 根因：启动期为“可用性优先”，审计/围栏失败只降级不阻断；且默认安装形态没有审计库路径。
- 影响面：审计合规、事后追责、自主执行安全。
- 安全或数据风险：高。审计链断点 + 安全闸未装，但服务对外表现为“正常接收请求”。
- 修复方案：至少做到“未配置审计库=启动失败”或“显式降级并拒绝自主执行动作”；不要只靠 `/v1/ready` 的 503 提醒。安装版启动器应无条件注入 `AUDIT_DB_PATH`。
- 需要改的文件：`src/gateway/main.py`（启动策略）、`scripts/launch_installed.py`、`scripts/start_liuhao.py`。
- 验证方法：删除/损坏审计库后启动，断言：(1) 自主执行入口被拒绝；或 (2) 进程拒绝启动并给出中文可执行指引；二者必居其一。当前实测是“继续服务且不拦截”。
- 等级：P1
- 状态：OPEN

### OPS-004 · 启动 · 本地排程器每次启动必失败且不上报

- 文件:行号
  - `src/tasks/scheduler_host.py:171-176`（`asyncio.create_task(...)` 时无运行中的事件循环）
  - 失败原文：`本地排程器启动失败：RuntimeError: no running event loop`
  - `src/gateway/main.py` 启动后台阶段把该失败吞掉（`/v1/startup` 的 `background.status=done`、`background.errors` 为空）
- 表现：每次启动都 ERROR，提醒/定时任务不会执行；启动器却只报“后台组件未就绪（原因未上报）”。
- 是否复现：**已复现 4 次**（23:56、00:02、00:04、00:06 每轮启动均出现同一 traceback）。
- 根因：在非事件循环线程/同步上下文调用 `asyncio.create_task`。
- 影响面：所有提醒、定时工作流、周期任务；用户会以为“设了提醒但没响”。
- 安全或数据风险：中（功能静默缺失，无数据损坏）。
- 修复方案：把 `start()` 投递到网关事件循环（如 `loop.create_task` 或 `asyncio.run_coroutine_threadsafe`），并把启动失败计入 `/v1/startup.background.errors`，让启动器把它显示出来。
- 需要改的文件：`src/tasks/scheduler_host.py`、`src/gateway/main.py`（后台阶段错误汇聚）。
- 验证方法：启动后 `/v1/startup.background.errors` 为空且无该 traceback；创建一个 1 分钟后的提醒，实测触发。
- 等级：P1
- 状态：OPEN

### OPS-005 · 日志与告警 · 就绪探针 503 被计入错误率，触发 CRITICAL 误告警

- 文件:行号
  - `src/gateway/main.py:2029-2041`（中间件对所有请求 `record_request(process_time, status_code)`，无探针豁免）
  - `src/observability/metrics.py:718`（520-599 计入 `error_rate_percent`，仅 5xx）
  - `src/observability/alerts/__init__.py:68-83`（错误率 ≥5% 即 CRITICAL）
- 表现：只要 `/v1/ready` 返回 503（默认源码模式缺审计库、或任一组件 degraded），探针轮询本身把错误率顶高，产生“High Error Rate”。
- 是否复现：**已复现**。同一次隔离运行中先后出现：
  `[ALERT][CRITICAL] High Error Rate ... error_rate_percent = 50.0 exceeds threshold 5.0`
  `[ALERT][CRITICAL] High Error Rate ... error_rate_percent = 25.0 exceeds threshold 5.0`
  两次数值都只由 1 次 `/v1/ready` 503 造成（样本量 2/4）。
- 根因：健康探针与业务流量共用同一错误率口径，且低样本无最小样本量保护。
- 影响面：告警可信度、值班噪音；真实业务 5xx 会被探针噪音淹没。
- 安全或数据风险：低（但会训练运维忽略告警）。
- 修复方案：中间件排除 `/v1/health`、`/v1/ready`、`/v1/startup`（OverloadGuard 已有探针豁免可复用）；错误率规则增加最小样本量（如 ≥20 请求）。
- 需要改的文件：`src/gateway/main.py`、`src/observability/metrics.py`、`src/observability/alerts/__init__.py`。
- 验证方法：只轮询 `/v1/ready` 使其 503 持续 2 分钟，断言不产生 High Error Rate；再制造 20+ 次业务 5xx，断言仍能告警。
- 等级：P1
- 状态：OPEN

### OPS-006 · 升级与回滚 · 发布包未签名且清单自述“冒烟未通过、产物未验证”

- 文件:行号
  - `dist/release/LIUHAO_RELEASE.json`（`releases[0].notes` 原文：“安装器未做代码签名。本次构建冒烟未通过，产物未验证。（打包扫描发现 2 处运行文件里的开发目录路径字面量…）”）
  - `python -m src.upgrade check --app-root . --release-dir dist\release` 输出 `signature=false`
  - `src/upgrade/downloader.py:137-165`（无签名时明确“仅哈希校验，无签名”，不会假装通过）
  - `src/upgrade/filemanifest.py:122-180`（逐文件 SHA256，缺失/重复即拒绝）
- 表现：升级包只有 SHA256 + 文件 manifest，无签名；且该 0.1.1 产物的发布清单自己声明冒烟未通过。
- 是否复现：**已复现**（实跑 `upgrade check`，清单与 `signature:false` 均可读到）。
- 根因：交付流水线未接入签名，且“冒烟未通过”的产物仍被写入正式 release 目录。
- 影响面：升级到未验证产物；供应链完整性只靠同源传输的哈希（无法防篡改）。
- 安全或数据风险：高（数据迁移/回滚风险 + 供应链风险）。
- 修复方案：发布门禁拒绝“未签名/冒烟未通过”的包进入 `dist/release`；或在清单中标记 `channel=dev` 并禁止 `upgrade` 命令消费。
- 需要改的文件：打包/发布脚本（非本次可改范围，登记为交付侧）、`src/upgrade/manager.py`（对 `signature=false` 的正式 channel 应拒绝）。
- 验证方法：把“冒烟未通过”的包放入 release 清单，`upgrade` 必须拒绝并输出中文原因；签名包才允许。
- 等级：P1
- 状态：OPEN

### OPS-007 · 启动 · 失败提示里的日志路径是错的

- 文件:行号：`scripts/start_liuhao.py:62`（`LOG_DIR = ROOT/"logs"`）；提示打印处（约 500 行附近）；`src/logging_config.py:125-160`（实际日志目录按 `LIUHAO_LOG_DIR` > `LIUHAO_DATA_DIR` > `%LOCALAPPDATA%/LiuHao/data` 解析）
- 表现：启动器提示 `日志：D:\LiuHao-AI-OS\logs\liuhao.log、D:\LiuHao-AI-OS\logs\error.log`，而本次实际日志写在 `%TEMP%\...\logs2\`；默认源码模式则写在 `%LOCALAPPDATA%\LiuHao\data\logs`。运维按提示去仓库根找日志，找不到。
- 是否复现：**已复现**。设置 `LIUHAO_LOG_DIR` 的两次启动，控制台提示均为仓库根路径，而对应日志文件确实存在于隔离日志目录。
- 根因：提示字面量硬编码，未复用 `resolve_log_file()`。
- 影响面：所有启动失败/降级场景的现场定位速度。
- 安全或数据风险：低。
- 修复方案：提示改用 `resolve_log_file("liuhao.log")` / `("error.log")` 的真实结果；`LOG_DIR.mkdir` 同步修正。
- 需要改的文件：`scripts/start_liuhao.py`。
- 验证方法：设 `LIUHAO_LOG_DIR` 启动，提示路径与磁盘实际文件逐字一致。
- 等级：P2
- 状态：OPEN

### OPS-008 · 启动 · “原因未上报”掩盖已捕获的降级原因

- 文件:行号：`src/desktop/startup.py:123`（`errors or '原因未上报'`）
- 表现：后台组件已明确降级（审计库打不开、对话库损坏），启动器仍输出“核心可用，但后台组件未就绪（x.xs）：原因未上报”。
- 是否复现：**已复现**。损坏审计库/对话库启动时，控制台为“原因未上报”，而同一时刻日志里有完整的 `evt=startup.degraded components=[audit, conversation]` 与可执行 action。
- 根因：启动器读取的错误列表为空（详细原因在另一结构/日志里），格式化时用兜底文案掩盖。
- 影响面：用户拿不到“下一步该做什么”。
- 安全或数据风险：低。
- 修复方案：把 `startup.degraded` 的 `action` 摘要透传到启动器提示；无原因时才用兜底文案。
- 需要改的文件：`src/desktop/startup.py`、`src/gateway/main.py`（启动快照字段）。
- 验证方法：损坏审计库启动，提示中直接出现“审计库打不开：file is not a database；处理：隔离/重建损坏库后重启”。
- 等级：P2
- 状态：OPEN

### OPS-009 · 重启 · 源码模式重启后全员掉登录

- 文件:行号：`scripts/start_liuhao.py` 不注入 `LIUHAO_JWT_SECRET`；`scripts/launch_installed.py:368/421` 生成并复用 `config/app.env` 的密钥
- 表现：源码模式每次启动生成一次性 JWT 签名密钥，重启后旧令牌全部失效；安装版因 `app.env` 持久化不受影响。
- 是否复现：**已复现**（每轮源码启动均出现警告：`no <环境变量> in the environment: signing keys are generated for this process only, so tokens do not survive a restart and are not shared across workers`）。
- 根因：源码启动器缺少安装版已有的密钥自举步骤。
- 影响面：源码/自建部署的可用性；重启=用户被登出。
- 安全或数据风险：低（功能可用性）。
- 修复方案：`start_liuhao.py` 复用安装版的 `app.env` 生成逻辑（同数据目录、同权限收紧）。
- 需要改的文件：`scripts/start_liuhao.py`（或抽取公共 helper）。
- 验证方法：重启前后用同一令牌请求受保护接口，仍 200。
- 等级：P2
- 状态：OPEN

### OPS-010 · 灾难恢复 · 身份完整性密钥丢失后静默重建

- 文件:行号：`src/common/integrity_key.py:35-75`（密钥落点 `config/integrity.key`，独立 DPAPI purpose）；`scripts/start_liuhao.py:1000`（启动前 `_ensure_identity_integrity_key()`）
- 表现：删除密钥后重启，密钥被自动重建（487 字节、新 mtime），启动成功、无任何“密钥已重建/旧身份将失效”的日志或告警。
- 是否复现：**已复现**。删除后重启，`data2/config/integrity.key` 重新出现，`/v1/ready` 的 `human_identity_registry` 仍是 healthy（本机 0 个人类身份，`not_applicable`），日志中无重建提示。
- 根因：自举逻辑以“有无文件”为准，缺少“文件丢失=灾难事件”的判定与告警。
- 影响面：已注册人类身份的签名校验（旧签名对新密钥必然失败→按 fail-closed 丢弃）；审批链可能整体失效。
- 安全或数据风险：高（若已有身份/审批数据，等价于静默失去治理能力）。
- 修复方案：重建时写 ERROR/WARN 并明确“旧身份签名将失效，请从备份恢复 `config/integrity.key`”；在 `/v1/ready` 暴露 `integrity_key_state=regenerated`。
- 需要改的文件：`src/common/integrity_key.py`、`scripts/start_liuhao.py`、`scripts/launch_installed.py`。
- 验证方法：先注册 1 个人类身份→删除密钥→重启，断言 (a) 明确告警；(b) 已注册身份不静默消失（要么拒绝启动，要么显式 fail-closed 并给出恢复步骤）。
- 等级：P2
- 状态：OPEN

### OPS-011 · 健康检查 · 对话库损坏时仍宣称“登录/对话/模型配置可用”

- 文件:行号：`scripts/start_liuhao.py`（就绪提示文案，`核心就绪（登录/对话/模型配置可用）`）；`src/gateway/main.py` 的 `core_ready` 计算
- 表现：对话库被灌坏后，启动器仍说“核心就绪（含对话）”，而 `/v1/ready` 已 503、聊天接口报“无法打开对话历史库”。
- 是否复现：**已复现**。损坏 `conversation_store.db` 后启动，控制台为上述文案，同时日志 `evt=startup.degraded subsystem=conversation ... ready=false`。
- 根因：`core_ready` 的口径与文案不一致（文案把对话算进核心）。
- 影响面：用户误以为可正常聊天。
- 安全或数据风险：低。
- 修复方案：文案按真实组件状态生成（对话不可用时应说“登录/模型配置可用，对话不可用”）。
- 需要改的文件：`scripts/start_liuhao.py`、`src/desktop/startup.py`。
- 验证方法：损坏对话库启动，文案逐项与实际 `/v1/ready.checks` 一致。
- 等级：P2
- 状态：OPEN

### OPS-012 · 灾难恢复 · 员工/目标状态文件损坏：读静默回落、写才报错

- 文件:行号
  - `src/ai/employee_store.py:158`（校验失败时抛错）；`:647`（单条损坏跳过，不拖垮整份名册）
  - `src/gateway/ai_management.py:669-675`（启动时 `_load_goals()`，异常被吞并“startup must still proceed”）
- 表现：把 `.liuhao_employees.json` / `.liuhao_goals.json` 写成非法 JSON 后启动，网关正常起来、无告警；员工页会呈现“0 个员工/目标清空”的假象，直到用户执行写操作才可能报错。
- 是否复现：**已复现（半）**：损坏文件后启动未出现任何与员工/目标相关的 WARN/ERROR，`/v1/ready` 不涉及这两项；静态确证读取异常分支被吞。
- 根因：可恢复数据被设计成“尽力而为”，但缺少“文件损坏 → 隔离副本 + 告警”的处理。
- 影响面：名册/目标看似丢失，用户可能重建造成二次损坏。
- 安全或数据风险：中。
- 修复方案：解析失败时把原文件改名 `*.corrupt-<ts>` 保留，写 ERROR + `/v1/ready` 增加该项（degraded），并提示从备份恢复。
- 需要改的文件：`src/ai/employee_store.py`、`src/gateway/ai_management.py`、`src/gateway/health.py`。
- 验证方法：灌坏两文件后启动，断言 (a) 原文件被保留为 `.corrupt-*`；(b) 日志/就绪明确报该项；(c) UI 不显示“空名册”而显示“数据损坏，待恢复”。
- 等级：P2
- 状态：OPEN

### OPS-013 · 数据备份 · 普通备份不可恢复，只有加密包能恢复

- 文件:行号：`src/cli/backup_commands.py:105-157`（默认排除受保护文件、备份源映射）、`:173-236`（`.lhbak` AES-256-GCM 解密与恢复）
- 表现：`backup create --type full`（不带 `--encrypt`）成功产出包，但 `backup restore <id> --target <空目录>` 返回“no recoverable files / 无可恢复文件”，只有 `--encrypt --password` 的包能恢复出文件。
- 是否复现：**已复现**（同一环境下先建普通包再建加密包；普通包恢复失败，加密包对空目录恢复成功，产出 `config/integrity.key`）。
- 根因：普通包是按“排除密钥/凭据”的安全默认产出，但不是可恢复快照；产品未把“普通备份仅用于取证/迁移检查、恢复必须用加密包”说清楚。
- 影响面：用户以为做了备份，真出事时发现恢复不了。
- 安全或数据风险：高（备份的可用性预期被打破）。
- 修复方案：`restore` 对普通包直接给中文明确提示“该包不含可恢复数据，请使用加密备份”；`backup create` 默认改为加密（或至少在输出里显著标注“不可用于恢复”）。
- 需要改的文件：`src/cli/backup_commands.py`（文案/默认值）。
- 验证方法：普通包 restore 必须明确说明原因与替代路径；加密包 restore 必须成功；两者都有断言。
- 等级：P2
- 状态：OPEN

### OPS-014 · 数据备份 · 无自动定时备份、无备份失败告警

- 文件:行号：`src/cli/backup_commands.py`（仅 CLI 入口）；`src/tasks/local_scheduler.py` / `local_handlers.py` 未注册备份任务（`Grep schedule|daily|cron` 于 `src/tasks` 无命中备份）
- 表现：备份只能手动/CLI 触发；没有任何周期任务、失败告警或“上次成功备份时间”暴露。
- 是否复现：**静态判断**（代码检索无备份调度；未发现 UI 定时备份实现）。
- 根因：备份是运维工具而非产品内建能力。
- 影响面：长期运行用户实际“从不备份”。
- 安全或数据风险：高。
- 修复方案：内建每日备份（保留 N 份）、失败写 WARN + `/v1/ready` 暴露 `last_backup_at`；或至少在 UI 首启强提示。
- 需要改的文件：`src/tasks/local_handlers.py`、`src/cli/backup_commands.py`、`src/gateway/health.py`。
- 验证方法：启动后 24h 内自动生成至少 1 份加密备份；人为制造备份目录不可写，断言告警与就绪项。
- 等级：P2
- 状态：OPEN

### OPS-015 · 配置管理 · 无热加载；非法 YAML 仅告警后回落默认

- 文件:行号
  - `src/gateway/main.py:337`（`load_runtime_config`）、`:1836`（启动期只调用一次）
  - `src/gateway/main.py` YAML 读取分支：解析失败 → `logger.error` 后返回 `{}`，随后使用内置默认
  - `src/config_manager.py:57-69`（`_initialize` 捕获异常后 `_load_defaults()`）
- 表现：改 `config/default.yaml` 需重启；写坏 YAML 不报启动失败，而是静默用默认值（仅一条日志）。`/v1/ready` 不会因“配置解析失败”而 503。
- 是否复现：**静态判断**（代码路径确证；未实跑写坏 YAML，避免改仓库配置文件）。
- 根因：配置是一次性加载 + 容错优先。
- 影响面：用户以为改了配置，实际仍是默认；端口/安全相关配置被忽略而无感。
- 安全或数据风险：中（安全相关键被静默忽略）。
- 修复方案：解析失败时启动期 degraded 并在 `/v1/ready` 暴露；安全相关键（绑定地址/鉴权/围栏）解析失败必须拒绝启动。
- 需要改的文件：`src/gateway/main.py`、`src/gateway/health.py`、`src/config_manager.py`。
- 验证方法：写坏 `config/default.yaml` 启动，断言 (a) `/v1/ready` 503 且指向配置文件；(b) 不静默使用默认安全值。
- 等级：P2
- 状态：OPEN

### OPS-016 · 健康检查 · 探针免鉴权且暴露较细内部信息

- 文件:行号：`src/gateway/main.py`（`EXEMPT_PATHS` 含 `/v1/health`、`/v1/ready`）；`src/gateway/health.py` 的 checks 明细
- 表现：`/v1/ready` 无令牌即可读到 `api_key_manager.total_keys/active_keys`、`usage_ledger` 的（已脱敏）路径、各组件错误原文、审计事件计数等。
- 是否复现：**已复现**（无 Authorization 直接 200/503 并返回上述字段）。
- 根因：为了让探针可用而豁免鉴权，但返回体按“运维面板”粒度设计。
- 影响面：绑定 `0.0.0.0`（手机访问/局域网）时信息面扩大；错误原文可能含本地路径（当前已做部分脱敏）。
- 安全或数据风险：低-中（信息泄露）。
- 修复方案：未认证时只返回 `status`/`service`/`version` 三字段；明细要求本机回环或令牌。
- 需要改的文件：`src/gateway/health.py`、`src/gateway/main.py`。
- 验证方法：非回环来源无令牌请求，响应仅含最小字段；回环或已授权请求才返回明细。
- 等级：P3
- 状态：ACCEPTED RISK（探针免鉴权是既定设计；建议收敛返回体）

### OPS-017 · 停止 · 硬杀启动器残留陈旧 instance.json

- 文件:行号：`src/desktop/service.py:662-737`（优雅停止才 `cleanup_instance_files()`）；`scripts/start_liuhao.py` 的实例探测读取同文件
- 表现：直接结束启动器/杀进程后，`instance.json`（旧 PID/URL）残留；下次启动可正常接续（实测），但 `status`/诊断会展示陈旧 PID。
- 是否复现：**已复现**。杀掉启动器后 `inst/run/instance.json` 仍指向已退出 PID；8 秒内网关随之退出、端口释放；再次启动成功。
- 根因：清理只挂在优雅退出路径。
- 影响面：排障误判“还有实例在跑”。
- 安全或数据风险：低。
- 修复方案：启动时若探测到 PID 已不存在，先清理并记 INFO。
- 需要改的文件：`scripts/start_liuhao.py`、`src/desktop/service.py`（只读参考，改动需在启动器侧）。
- 验证方法：硬杀后重启，诊断输出不再展示陈旧 PID。
- 等级：P3
- 状态：OPEN

### OPS-018 · 启动 · 端口占用弹阻塞式 Tk 对话框，无人值守会挂起

- 文件:行号：`scripts/start_liuhao.py:189-267`（`_ask_port_choice` 的 `tkinter` 主循环）；`:270-297`
- 表现：CLI/脚本方式启动且端口被占、但当前会话有桌面时，会弹窗等待人工点击；在自动化/远程无人值守场景表现为“卡住不动”。实测默认 8080 被真实安装占用时，进程存活 12 秒无输出（弹窗等待）。
- 是否复现：**已复现**（受控启动 14 秒进程仍存活，无任何子进程被拉起，未进入网关启动阶段）。
- 根因：交互式错误处理未区分“有 TTY/无人值守”。
- 影响面：脚本化部署、服务化托管、CI。
- 安全或数据风险：低。
- 修复方案：非交互（无 TTY、`--no-browser`/服务模式、`LIUHAO_NONINTERACTIVE=1`）时跳过 Tk，直接打印 `[FAIL]` 并返回非零。
- 需要改的文件：`scripts/start_liuhao.py`。
- 验证方法：重定向 stdin/stdout 启动且端口被占，5 秒内退出并输出中文原因与建议端口。
- 等级：P3
- 状态：OPEN

### OPS-019 · 告警 · 高 CPU 阈值噪音

- 文件:行号：`src/observability/alerts/__init__.py`（`High CPU Usage` 阈值 80%）；实测出自 `src.observability.alerts.sinks`
- 表现：本机多会话编译/测试时每 60 秒一条 `[ALERT][MEDIUM] High CPU Usage ... 80.3/81.5/95.9/93.4`。
- 是否复现：**已复现**（连续 4 条 MEDIUM）。
- 根因：单机场景阈值偏低且无持续时间窗口。
- 影响面：告警疲劳。
- 安全或数据风险：低。
- 修复方案：连续 N 个采样超阈值才告警，或按 CPU 核数归一化。
- 需要改的文件：`src/observability/alerts/__init__.py`。
- 验证方法：短时峰值不告警，持续高位才告警。
- 等级：P3
- 状态：OPEN

### OPS-020 · 回滚 · 失败提示重复前缀

- 文件:行号：`src/upgrade/cli.py`（回滚失败输出）
- 表现：`python -m src.upgrade rollback --app-root <空目录>` 输出 `失败：失败：回滚失败（没有可回滚的升级记录）`。
- 是否复现：**已复现**（退出码 2）。
- 根因：两层都加了“失败：”前缀。
- 影响面：观感；不影响功能（无记录时拒绝回滚是正确行为）。
- 安全或数据风险：无。
- 修复方案：去掉重复前缀。
- 需要改的文件：`src/upgrade/cli.py`。
- 验证方法：输出只出现一次“失败：”。
- 等级：P3
- 状态：OPEN

### OPS-021 · 升级 · 下载中断无断点续传（仅重试）

- 文件:行号：`src/upgrade/downloader.py:198-214`（未下载完整即抛 `ChecksumMismatch`，提示重试）
- 表现：网络中断后从头再下；大包（本次 31.8 MB）在弱网下反复失败。
- 是否复现：**静态判断**（代码路径确证；未模拟断网）。
- 根因：未实现 Range 续传。
- 影响面：升级成功率。
- 安全或数据风险：低（不会装坏，只失败）。
- 修复方案：支持 Range + 分段校验；或至少显示“已下载 X/Y”。
- 需要改的文件：`src/upgrade/downloader.py`。
- 验证方法：中断后重跑，从断点继续且最终哈希一致。
- 等级：P3
- 状态：OPEN

### OPS-022 · 观测 · 健康探针 trace_id 恒为 unknown

- 文件:行号：`src/gateway/health.py`（`/v1/health`、`/v1/ready` 返回体 `trace_id:"unknown"`）
- 表现：探针请求不参与链路追踪；与业务请求的 `x-trace-id` 关联困难。
- 是否复现：**已复现**（两个探针响应均 `"trace_id":"unknown"`）。
- 根因：探针路由未走 trace 中间件或未生成 ID。
- 影响面：排障时无法把探针失败与日志关联。
- 安全或数据风险：无。
- 修复方案：探针也生成/回传 trace_id。
- 需要改的文件：`src/gateway/health.py`。
- 验证方法：响应头/体带非 unknown 的 trace_id，日志可检索到。
- 等级：P4
- 状态：OPEN

### OPS-023 · 升级 · “旧数据升级后可读”未实测

- 文件:行号：`src/upgrade/manager.py:1279-1318`（`VERIFYING` + 签名/大小复核）、`:1401-1432`（manifest 校验 + staging）、`:1479-1543`（overlay 到 app_root，写 `data-schema.json`）、`:1864-1893`（回滚）；`src/upgrade/dataversion.py:136-154`（数据版本高于目标时拒绝降级）
- 表现：代码具备“校验→staging→原子覆盖→回滚备份”的骨架与“拒绝数据降级”的保护；但本次**没有**在隔离副本上完整跑一次“旧库→升级→读取”。
- 是否复现：**静态判断 + 未完成实测**。仅实跑 `upgrade check` 与“无记录回滚”拒绝。
- 根因：缺可用的“旧版本→新版本”双版本数据与升级包回路（当前 release 清单自述冒烟未通过，不宜在真实数据上演练）。
- 影响面：升级后的数据兼容性缺乏实证。
- 安全或数据风险：中。
- 修复方案：补一条 E2E：旧 schema 数据目录 → 应用升级包 → 启动新版本 → 读取关键表。
- 需要改的文件：测试/发布侧（非本次改代码范围）。
- 验证方法：如上 E2E 通过，并验证回滚后旧版本能继续读。
- 等级：P3
- 状态：OPEN

### OPS-024 · 文档一致性 · 单端口模式依赖前端 `dist`

- 文件:行号：`docs/QUICKSTART-RUN.md:21`（已说明“若仓库里还没有 dist…”）、`README.md:83`（`cd apps/console/console`）
- 表现：源码默认单端口模式服务 `apps/console/console/dist`；未构建则 `/` 无 UI（日志明确提示 `console build output ... has no index.html -- not serving it (run npm run build ...)`）。文档已写明，属可接受。
- 是否复现：**已复现**（运行期一次该提示）；当前 `dist/index.html` 存在且完整，疑为并发构建瞬时态。
- 根因：源码态前端产物需单独构建。
- 影响面：首次源码启动者。
- 安全或数据风险：无。
- 修复方案：保持文档说明；启动器检测到缺 dist 时给出“npm run build”可复制命令。
- 需要改的文件：`scripts/start_liuhao.py`（提示文案）。
- 验证方法：删/移 dist 后启动，提示含可复制构建命令。
- 等级：P4
- 状态：ACCEPTED RISK

### OPS-025 · 启动 · 运行期一次“内置插件注册失败（NameError）”

- 文件:行号：`src/kernels/plugin/__init__.py:139-159`（`_reload_index()` → `_prune_placeholders_enabled()`；当前文件在第 69 行已定义该函数）
- 表现：00:04 那次启动出现 `REGISTER BUILTIN PLUGINS FAILED -- /v1/plugins will be empty: name '_prune_placeholders_enabled' is not defined` 与完整 traceback。
- 是否复现：**NOT REPRODUCED**。随后两次启动均未复现；当前工作树该函数存在。该工作树有多个并发会话在编辑，判定为并发编辑瞬时态，不作为稳定缺陷。
- 影响面（若稳定存在）：`/v1/plugins` 为空、插件能力全失。
- 修复方案：无需按稳定缺陷修；但需在“工作树稳定后”做一次冷启动回归确认。
- 需要改的文件：无（观察项）。
- 验证方法：工作树稳定后连续冷启动 3 次，`/v1/plugins` 非空且无该 traceback。
- 等级：P1（若稳定）；当前证据不足。
- 状态：NOT REPRODUCED

### OPS-026 · 启动 · 运行期一次“前端 dist 缺 index.html”

- 文件:行号：`src/gateway/console_static.py`（提示文案）；`apps/console/console/dist/index.html`
- 表现：00:06 启动提示 `console build output ... has no index.html -- not serving it`。
- 是否复现：**NOT REPRODUCED**。当前 `dist/index.html` 存在（1902 字节）；疑为并发会话正在 `vite build` 清空目录的瞬时窗口。
- 影响面（若稳定存在）：单端口 UI 404。
- 修复方案：无需按稳定缺陷修。
- 需要改的文件：无（观察项）。
- 验证方法：工作树稳定后冷启动，`GET /` 返回 200 且为控制台页面。
- 等级：P2（若稳定）；当前证据不足。
- 状态：NOT REPRODUCED

### OPS-027 · 数据备份 · 跨机器恢复未经真实异机验证

- 文件:行号：`src/backup/backup_manager.py:80-100`（跨机恢复说明：目标机无 DPAPI 时失败且不覆盖）、`:173-254`（导出解包 + 目标机 DPAPI 重新包裹）；`src/cli/backup_commands.py:173-236`
- 表现：加密包导出/恢复在**同机异目录**成功（`config/integrity.key` 已恢复）；但“恢复到另一台机器”依赖 DPAPI 重新包裹，本次没有第二台机器，无法实证。
- 是否复现：**BLOCKED**（无第二台 Windows 主机）。
- 根因：DPAPI 为机器/用户绑定，跨机必须重新包裹；实现已有分支，但缺真实异机回归。
- 影响面：换机迁移成功率。
- 安全或数据风险：中。
- 修复方案：补一条异机恢复演练（或提供不依赖 DPAPI 的迁移模式）。
- 需要改的文件：备份侧/验证流程。
- 验证方法：在另一台 Windows 上用同一加密包恢复到空目录，启动并登录成功。
- 等级：P2
- 状态：BLOCKED

---

## 4. 已验证为正常（不计为缺陷）

1. **端口归属识别有效**：`src/desktop/probe.py` 的 `describe_port_owner()` 能给出占用 PID；`port_is_free(8080)=False`、`port_is_free(18808)=False`，未误判。
2. **探针能区分“本家服务”**：伪造服务在 18809 上对 `/v1/startup` 返回 404、对其它 GET 返回 200 且无 `service` 字段；`probe_startup()` 返回 `reachable=False`（`legacy` 真/假都不被当作本家网关）。
3. **`/v1/health` 可证明“是我们的进程且活着”**：返回 `{"status":"ok","service":"liuhao-gateway","version":"0.1.1","api_contract":"1",...}`，无鉴权也可用。
4. **`/v1/ready` 能证明依赖组件状态**：配置 `AUDIT_DB_PATH` 后返回 200/ready、审计 `verified`；缺审计库时返回 503/degraded 并点名 `audit_store=unhealthy`（证明能力合格，语义问题见 OPS-002）。
5. **关闭启动器会带走网关**：强制结束启动器父进程后，8 秒内网关子进程退出、18808 端口释放，无孤儿。
6. **重复启动有兜底**：同数据目录+同端口第二次启动命中实例锁，转入 handoff，不再拉起第二个网关。
7. **停止后端口/子进程清理**：通过启动器正常停止时，网关进程退出、端口释放、实例锁释放；硬杀只残留 `instance.json`（见 OPS-017），下次启动可恢复。
8. **备份恢复失败不破坏现有数据**：加密包恢复指向非空目录被拒绝——原文“目标目录里已经有一整套数据（已含 config），拒绝恢复，避免覆盖成一份拼接数据”；错误口令恢复失败且目标目录保持为空；被拒后原 `integrity.key`（487 字节、原 mtime）完好。
9. **备份存放在数据目录之外**：默认备份根为数据目录的上一级 `backups`（`src/cli/backup_commands.py`），数据目录被删时备份仍在。
10. **日志有轮转与上界**：`liuhao.log`/`error.log` 各 10 MiB×6 ≈ 60 MiB/通道，合计 ≤120 MiB；`desktop.log` 10 MiB×5 ≈ 60 MiB；另有 14 天 / 256 MiB 保留策略（`src/logging_config.py:451-543`）。单机日志稳态上界约 180–256 MiB。
11. **一次失败请求可只靠日志定位**：带 `X-Trace-ID` 发坏 JSON 得 422，响应头回传 `x-trace-id`；服务端日志按生成的 trace_id 检索到该请求（请求级字段完整）。注意：服务端不采用客户端传入的 trace_id。
12. **升级校验链路存在且不装假**：`upgrade check` 可跑通；无签名时明确 `signature=false`；manifest 逐文件 SHA256；下载未完成立即失败且声明“程序未做任何改动”。
13. **回滚不会在无记录时动文件**：`upgrade rollback --app-root <空目录>` 直接拒绝（“没有可回滚的升级记录”），未移动任何文件。
14. **文档启动/停止命令与代码入口一致**：`python scripts/start_liuhao.py`（含 `--backend-only/--no-browser/--diagnose`）、`python scripts/liuhao_desktop.py stop|diagnose|status` 均在 `--help` 中真实存在；`python -m src.upgrade check` 与 `python -m src.upgrade rollback` 均可用；`docs/QUICKSTART-RUN.md:21` 对 `dist` 依赖有说明。

## 5. 最该先修的 5 条

1. **OPS-003**：审计库缺失/损坏时 fail-open 继续服务、策略决策不落审计——安全闸口在故障时消失。
2. **OPS-004**：本地排程器每次启动必失败——提醒/定时任务在所有部署形态下都不工作，且被启动器掩盖。
3. **OPS-005**：就绪探针 503 计入错误率——误报 CRITICAL，真实故障会被噪音淹没。
4. **OPS-001**：源码模式数据目录“四分五裂”——隔离实例写进仓库根，排障/测试会污染共享开发数据。
5. **OPS-006**：正式 release 里的包未签名、清单自述“冒烟未通过、产物未验证”——升级与回滚的信任基础不成立。

补充优先：**OPS-002**（`/v1/ready` 对未武装的执行围栏显示“执行隔离正常”）与 **OPS-013**（普通备份不可恢复）建议与上述同批修。

## 6. 未覆盖与限制

- 未在第二台 Windows 主机上做 DPAPI 异机恢复（OPS-027 BLOCKED）。
- 未跑“旧版本数据→升级包→新版本读取”的完整 E2E（OPS-023）；当前 release 清单自述冒烟未通过，不宜在真实数据上演练。
- 未修改 `config/default.yaml` 实测非法 YAML 的启动行为（避免改仓库配置，OPS-015 为静态确证）。
- 安装器 `setup.exe` 的安装/卸载/升级细节未验（明确排除，另有专人）。
- 托盘 UI 的“退出”未做 GUI 自动化点击验证；退出路径按 `src/desktop/app.py` + `service.py` 静态核对。
