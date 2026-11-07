# 鎏灏 LIUHAO AI OS — 能力地图（2026-11-06 复核）

> **判定口径**：严格沿用 [`docs/strategy-30-directions.md`](strategy-30-directions.md) 第 6 节“四关”判定法，不另立标准。
> **复核基线**：`HEAD 82f95450`（2026-11-06）。
> **复核方法**：`git grep -n "<顶层符号>" -- src ":!<模块自身目录>"`，**按顶层符号查，不按目录路径查**（本仓库大量相对导入，grep 目录会漏）。
> **状态定义**（同第 6.4 节）：
> - 🟢 **真接线**：四关全过；有非测试调用方；调用点默认不返回空；最后一步是真后端。
> - 🟡 **半成品**：有真实实现，但缺调用方、缺产品面（UI/API/排程），或默认不可达。
> - 🔴 **假实现**：调用点返回空、只有 fake 后端、只有占位/枚举，或只被测试导入。
> - ⚫ **完全没有**：第 1 关就不过（仓库内无实现）。

---

## 0. 四关判读表（本次复核实际执行）

| 关 | 问题 | 本次用的命令 | 判据 |
|---|---|---|---|
| 1 存在 | 文件/目录在吗 | `Glob` / `Get-ChildItem` | 在 ≠ 可用 |
| 2 可达 | 谁在**非测试生产代码**里调用它 | `git grep -n "<顶层符号>" -- src ":!<模块自身目录>"` | 0 命中 ⇒ 未接线；只剩 CLI/测试 ⇒ 不算生产 |
| 3 门槛 | 调用点默认是否返回空/None | 读调用点 + 闸门默认值 | 默认返回空 ⇒ 用户看不到 |
| 4 后端 | 最后一步真执行还是 fake | 读 `backends/` + `git grep "fake\|not implemented"` | 只有 fake/桩 ⇒ 假实现 |

---

## 1. 感知层（Perception）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 1.1 | 文本对话输入/输出 | 🟢 真接线 | `src/gateway/chat.py:152` → `src/ai/liuhao.py:200` `assistant.chat()`；`src/gateway/main.py:969` 挂载 | 是（登录后） | 真实 Provider，见 4.1；**未配置模型时回落 `MockProvider`**（`src/ai/providers.py:926`），回复正文自带 `Mock response to:`，不伪装真实回答 |
| 1.2 | 文件读取/列目录 | 🟢 真接线 | `src/gateway/files.py:1011-1012` 挂载；工具侧 `src/ai/tools/kernel_tools.py:332/352` | 是（只读） | 真实 `Path.read_text` / `iterdir`，路径经 `resolve_in_workspace` 收敛 |
| 1.3 | 知识库检索（作为“读资料”的感知） | 🟡 半成品 | `src/gateway/chat.py:159/185` → `src/gateway/knowledge_context.py:104` → `src/knowledge/documents.py`；`src/api/routes/knowledge.py:47-83` | **条件可达**：需 embedding provider（`src/knowledge/embedding.py:345-395`），默认 `auto` 只在 `OPENAI_API_KEY` 或 `OLLAMA_BASE_URL` 存在时启用；否则 `status.enabled=False` 并静默降级 | 真实向量库 + 真实 embedding；空库/失败时如实标注“本次未使用知识库” |
| 1.4 | 图像理解 | 🔴 假实现 | 无生产调用方 | 不可达 | `src/ai/perception.py:170` 明确 `modality understanding not implemented`；`supports_vision` 仅标志位 |
| 1.5 | 语音/音频/视频理解 | ⚫ 完全没有 | 无 | 不可达 | `plugins/audio`、`plugins/video` 未加载（运行时只扫 `src/plugins`） |
| 1.6 | 屏幕/桌面环境感知 | 🔴 假实现 | 调用点存在但被闸门拦住，见 3.6 | 默认不可达 | `src/automation/backends/` 只有 `fake.py`（文件头写明不接触真实屏幕/键鼠） |
| 1.7 | 手机端浏览器访问 | 🟡 半成品 | 同源静态 `src/gateway/console_static.py` + `apps/console/console/public/sw.js` | 取决于驾驶舱构建产物是否存在 | 前端 PWA 真实；**真机流程未验证**，见 §7 能力边界 |
| 1.8 | Windows 托盘/诊断包 | 🟡 半成品 | `src/desktop/`（本轮另一流在改，`git status` 显示未提交修改） | 待确认启动链是否默认拉起 | pywin32 托盘、HKCU 自启、诊断包均有真实代码；本轮不对其下最终结论 |

---

## 2. 记忆层（Memory）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 2.1 | 多轮会话历史持久化 | 🟢 真接线 | `src/ai/liuhao.py:194-198` `_conv_store.load_recent()` / `get_last_turn()`；`src/ai/conversation_store.py:156` 单例 | 是 | SQLite + WAL，落 `conversation_store.db` |
| 2.2 | 长期记忆内核（L1/L2） | 🟢 真接线 | `src/ai/agent_runtime.py:248` `get_memory_kernel()`；`src/ai/agent_factory.py:149`；`src/ai/tools/kernel_tools.py:98` 记忆检索工具 | 是 | `src/kernels/memory/store.py` 真实 SQLite 后端 |
| 2.3 | 个人画像（偏好/事实/称呼） | 🟢 真接线 | `src/ai/liuhao.py:523-526` 注入提示词；`src/gateway/profile.py` 读写面；`src/ai/tools/kernel_tools.py:216-257` 三个画像工具 | 是 | `src/ai/personal_context.py` SQLite + 下沉 memory kernel |
| 2.4 | 知识库入库/分块/向量 | 🟢 真接线 | `src/api/routes/knowledge.py:53` ingest；`src/knowledge/documents.py:119`；`src/knowledge/vector_store.py` | 条件可达（同 1.3，需 embedding） | 真实 chunk→embedding→向量；同 `doc_id` 旧 chunk 残留是已知缺陷（审查 0-11，未修） |
| 2.5 | 知识图谱（语义网络） | 🔴 假实现 | `git grep "SemanticNetwork" -- src` 仅命中 `src/ai/memory/semantic_network.py` 自身 | 不可达 | 无持久化调用链，仅测试导入 |
| 2.6 | 记忆压缩/清理 | 🟡 半成品 | `src/kernels/memory/__init__.py:159` `auto_cleanup()` 存在 | 取决于内核生命周期是否触发，未见定时驱动 | 真实实现，缺调度 |

---

## 3. 行动层（Action）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 3.1 | 对话内工具调用 | 🟢 真接线 | `src/ai/liuhao.py:202` `make_tools()` → `src/ai/tools/kernel_tools.py`；注册/校验/激活 `liuhao.py:203-207` | 是 | 真实工具：记忆检索、审计查询、系统状态、受限 Python 计算、读文件、列文件、画像写入 |
| 3.2 | 工作区写文件（产物生成） | 🟢 真接线 | `src/ai/tools_local.py:268` 注册 `local_file_write`；`src/kernels/capability/__init__.py:485` 能力；`src/kernels/execution/__init__.py:575` 执行 | 是（目标执行链内） | 真实写盘，路径固定在 workspace 根内 |
| 3.3 | 目标/员工执行链（创建→执行→暂停→重规划） | 🟢 真接线 | `src/gateway/ai_management.py` 挂载于 `main.py:988`；`src/ai/agent_runtime.py`；`src/ai/goal_task_graph.py` | 是（登录后） | 真实任务图 + 执行围栏 |
| 3.4 | 项目容器与产物索引 | 🟢 真接线 | `src/gateway/projects.py`（`main.py:1016`）；产物索引 `src/artifacts/index.py`，接入提交 `5a4244ce`，说明补于 `82f95450` | 是 | 真实扫描已完成 `file_write` 任务 |
| 3.5 | 定时/周期任务（本地排程器） | 🟡 半成品 | 库：`src/tasks/local_scheduler.py`（提交 `3bda8c7d`）；入口：`src/workers/scheduler.py:33/59` `build_local_scheduler` | **默认不可达**：桌面启动链 `scripts/launch_installed.py` → `scripts/start_liuhao.py` 不拉起该进程；只有手动 `python -m src.workers.scheduler` 才生效 | 真实 SQLite 队列、退避重试、死信、重启恢复 |
| 3.6 | 电脑自主操作（键鼠/屏幕） | 🔴 假实现 | 接线点真实：`src/ai/liuhao.py:202` → `src/ai/tools/kernel_tools.py:369` `build_automation_tools` | **默认可用工具数 = 0**：`src/automation/tools.py:61-65` 三闸未全开返回 `[]`；`src/automation/gate.py:85-109` 三个 env 默认全关 | 唯一后端 `src/automation/backends/fake.py` |
| 3.7 | MCP 接入外部工具 | 🔴 假实现 | `src/adapters/mcp/mcp_adapter.py` 自身定义 `initialize_mcp()`；`git grep "initialize_mcp" -- src ":!src/adapters/mcp"` **0 命中** | 默认关闭（`mcp_adapter.py:51`），且即使打开也无人调用 | 适配器代码真实，但无运行时入口 |
| 3.8 | Android / Windows 桌面自动化（第 3/4 层） | ⚫ 完全没有 | 无 adb/`input tap`/UIA/`SendInput` 生产代码 | 不可达 | 无真实设备/桌面后端 |
| 3.9 | GitHub / 第三方 SaaS 集成 | 🔴 假实现 | `src/integrations/*_adapter.py` 无注册表消费方；`github_adapter.py` 无实例化方 | 不可达 | 适配器类存在，无调用链 |
| 3.10 | 命令行备份/恢复 | 🟡 半成品 | `src/cli/__init__.py:219` → `src/cli/backup_commands.py`；`src/backup/backup_manager.py`（本切片提交 `586d9b5c`） | 只有 CLI 入口，控制台/API 无入口 | 真实字节流备份 + `VACUUM INTO` + 恢复校验；加密仍 `NotImplementedError` |

---

## 4. 认知层（Cognition）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 4.1 | 真实模型调用（OpenAI/DeepSeek/Ollama/Moonshot/Anthropic/Google） | 🟢 真接线 | `src/ai/providers.py:1001` `resolve_provider()` ← `src/ai/liuhao.py:160`、`src/ai/model_router.py:481/485/508` | 是（配置后） | 真实 HTTP/SDK 调用；密钥来自模型配置中心（AES-256-GCM 解密） |
| 4.2 | 模型配置中心与任务路由 | 🟢 真接线 | `src/gateway/models.py`（`main.py:1048`）；`src/ai/model_router.py:465` `provider_for_task("chat")` ← `src/gateway/chat.py:88` | 是 | 真实 SQLite 配置 + 加密密钥；保存即生效 |
| 4.3 | 人格/系统提示/工具协议编排 | 🟢 真接线 | `src/ai/liuhao.py:158-209` | 是 | 真实提示词组装与工具描述 |
| 4.4 | 目标分解 | 🟡 半成品 | 生产：`src/ai/agent_runtime.py:237` `ExecutableAwareDecomposer`（`src/ai/planner_contract.py`） | 是（仅在可执行分支） | LLM 规划器 `src/ai/planner/llm_planner.py` **无生产调用方**；生产只走契约分解器 |
| 4.5 | 推理/反思链 | 🔴 假实现 | `git grep "from .reasoning\|import reasoning" -- src` **0 命中** | 不可达 | `src/ai/reasoning/` 仅测试导入 |
| 4.6 | 多智能体委员会 / 黑板 | 🔴 假实现 | `committee`/`blackboard` 仅命中自身文件 | 不可达 | 无编排消费方；生产是单员工 resolver |
| 4.7 | 结果评估 | 🟢 真接线 | `src/ai/agent_runtime.py:36` `src.kernels.evaluation`；`src/ai/agent_factory.py:243` `evaluate_simple` | 是 | 真实评估内核（非 LLM 打分） |
| 4.8 | 自进化/自我改进（未实现，仅测试导入） | 🔴 假实现 | `EvolutionEngine` 仅命中 `src/ai/evolution.py` 自身 | 不可达 | 仅测试 |
| 4.9 | 用户反馈学习 | 🔴 假实现 | `src/feedback/feedback_api.py` 未挂载到网关；服务为内存 `dict` | 不可达 | 无持久化、无 API 路由 |
| 4.10 | 成本核算（token 记账） | 🟢 真接线 | `src/ai/agent_factory.py` 使用 `src/ai/economy.py` 的 Budget/Billing | 是（有定价时） | 真实算术；无定价时如实返回 None |

---

## 5. 主动性层（Proactivity）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 5.1 | 下达目标后自动执行 | 🟢 真接线 | `src/gateway/ai_management.py` 创建/暂停/重规划端点 | 是（需用户先下达） | 真实 AgentRuntime |
| 5.2 | 定时/周期自主动作 | 🟡 半成品 | 同 3.5 | 默认不可达（启动链不拉排程进程） | 真实 SQLite 排程器 |
| 5.3 | 提醒 / 日程 / 待办 | ⚫ 完全没有 | `plugins/calendar` 未加载；无运行时 | 不可达 | 无 |
| 5.4 | 主动建议 / 推送 | ⚫ 完全没有 | 告警 `src/observability/alerts` 只面向运维指标，不是用户主动服务 | 不可达 | 无 |
| 5.5 | 失败自愈 / 自动重试 | 🟡 半成品 | `src/tasks/local_scheduler.py` 有指数退避与死信；`src/reliability/checkpointer.py` 只调度审计检查点 | 审计链重试可达；用户任务重试默认不可达（排程进程未拉起） | 真实实现，缺默认入口 |

---

## 6. 信任层（Trust）

| # | 能力 | 状态 | 关 2 调用方（证据） | 关 3 默认可达 | 关 4 后端 |
|---|---|---|---|---|---|
| 6.1 | 登录 / JWT 会话 | 🟢 真接线 | `src/gateway/auth.py`（`main.py:1056`）；`src/security/jwt_handler.py` | 是 | 真实签名 + 吊销库 |
| 6.2 | 人类主权闸门 `require_human_principal` | 🟢 真接线 | `src/gateway/main.py:948-1052` 逐路由挂载 | 是（登录后） | 真实 JWT 校验；**已知权限口子**：新注册人类默认 L0、按 HTTP 方法判写（审查 0-10，未修） |
| 6.3 | 审计哈希链写入 | 🟢 真接线 | `src/kernels/audit`；`src/gateway/audit.py` 只读查询面；`verify_integrity()` | 是 | 真实链式哈希；**运行时状态 UNVERIFIED**（主链曾分叉），见 `docs/hc01-status.md` |
| 6.4 | 审批授权记录 | 🟡 半成品 | `src/gateway/policy.py:225/248/265` 端点，`main.py:1052` 挂载 | 可记录授权 | **没有执行端点**：`main.py:896-901` 明确“记录不等于放行，执行只能 in-process” |
| 6.5 | 模型密钥加密存储 | 🟢 真接线 | `src/ai/model_config.py`（AES-256-GCM）；`src/gateway/models.py` 脱敏返回 | 是 | 真实加密落盘；`model_config.key` 与库必须成对备份 |
| 6.6 | 限流 | 🟢 真接线 | `src/gateway/main.py:691-716` `RateLimitMiddleware` / `GlobalRateLimitMiddleware` | 是 | 进程内桶；覆盖仅部分写接口、无 TTL 回收（审查 0-16，未修） |
| 6.7 | 数据目录隔离 / 升级不覆盖用户数据 | 🟢 真接线 | `scripts/launch_installed.py:181-232` 优先读 `data-dir.txt`（安装器默认写 `D:\LiuHaoData`），未命中时回退 `%LOCALAPPDATA%\LiuHao\data` | 是（安装版入口） | 真实文件系统布局 |
| 6.8 | PII 检测/脱敏 | 🔴 假实现 | `detect_pii` 仅被 `src/knowledge/rag_pipeline.py:23/82/114` 使用，而 `rag_pipeline` 无生产调用方 | 不可达 | 规则实现真实，未接入任何写入/检索路径 |
| 6.9 | 保留/归档策略 | 🔴 假实现 | `src/kernels/retention/policy.py:45`、`archival.py:31-98` 多个 `NotImplementedError`；无生产调用方 | 不可达 | 未实现 |
| 6.10 | 外部身份（IdP / KYC） | ⚫ 完全没有 | 无 | 不可达 | 无 |
| 6.11 | 桌面诊断包（脱敏） | 🟡 半成品 | `src/desktop/diagnostics.py` 有密钥脱敏与自检 | 另一流在改，本轮不下结论 | 真实文件打包 |

---

## 7. 能力边界（明确做不到 / 不应做）

| 边界 | 结论 | 原因与证据 |
|---|---|---|
| **iPhone 端** | 做不到（本产品形态） | 当前是 Windows 单机 + 同源 HTTP；iPhone 只能通过局域网浏览器访问，且需手机与电脑同网、电脑不睡眠。无 iOS 原生应用、无 App Store 分发、无后台常驻能力。真机流程**未验证**。 |
| **UAC 提权** | 不做 | 本产品以普通用户权限运行（`%LOCALAPPDATA%` 安装），不请求管理员。提权会引入系统级写入与不可运维的故障面；`src/automation/gate.py:85` 三闸默认关闭，`backends` 仅 `fake.py`。 |
| **锁屏/登录界面操作** | 无法做到 | 锁屏属于 Windows 安全桌面（Secure Desktop），普通进程无法注入键鼠；`src/automation` 无真实后端。即便接 UIA/SendInput 也在锁屏下被系统拒绝。 |
| **反作弊 / 游戏防护绕过** | 不做且不可做 | 需要内核级驱动/注入，属安全软件对抗范畴，违反“不接触真实屏幕/键鼠”的现行设计（`src/automation/backends/fake.py` 文件头声明）。 |
| **mock 模型** | 仅开发态 | 未配置任何模型时 `src/ai/providers.py:926` 默认 `ProviderType.MOCK`，回复正文是 `Mock response to: ...`。它不是产品能力，只是“不伪造真实回答”的显式占位；用户必须配置真实 Provider 才算可用。 |
| **任意仓库改代码 / 提 PR** | 不做 | 当前只有受限 Python 计算 + 工作区写文件（`src/ai/tools_local.py`）；放开等于任意代码执行与供应链风险。 |
| **金融交易 / 医疗法律建议** | 不做 | 需外部行情/券商/合规框架与责任主体，单机个人产品不成立。 |
| **多节点 HA / 集群** | 不做 | 与“双击即用、无需运维”冲突；单机 SQLite + 单进程是产品前提。 |

---

## 8. 快效项（3 个低成本、明显提升可用性的改进）

| # | 改进 | 成本 | 收益 | 落点 |
|---|---|---|---|---|
| 8.1 | **模型管理页补 embedding 配置**（provider/模型/Key 三项），保存后知识库自动启用 | 1 个前端表单 + 复用现有加密存储 | 知识库从“要手改环境变量”变成“界面填完即用”；直接解锁 1.3 / 2.4 | `src/gateway/models.py` + `src/knowledge/embedding.py:345` |
| 8.2 | **启动器默认拉起本地排程进程**（或用同一进程内线程启动 `LocalScheduler`） | 1 个启动参数 + 进程管理 | 定时任务从“库存在但没人跑”变成默认可达；解锁 3.5 / 5.2 / 5.5 | `scripts/start_liuhao.py` + `src/workers/scheduler.py` |
| 8.3 | **控制台加“备份/恢复”入口**，调用已验证的 `BackupManager` | 1 个 API 路由 + 1 个按钮 | 用户数据信任底座从 CLI 变成一键操作；解锁 3.10 / 6.7 | `src/backup/backup_manager.py` + `src/gateway/` 新只读/写路由 |

---

## 9. 复核命令（可复现）

```powershell
# 关 2：按顶层符号查调用方（不要把目录当符号）
git grep -n "build_knowledge_context" -- src ":!src/gateway/knowledge_context.py"
git grep -n "get_memory_kernel"       -- src ":!src/kernels/memory"
git grep -n "build_automation_tools"  -- src ":!src/automation"
git grep -n "initialize_mcp"          -- src ":!src/adapters/mcp"
git grep -n "build_local_scheduler"   -- src ":!src/tasks"
git grep -n "get_knowledge_service"   -- src ":!src/knowledge"

# 关 3：闸门默认值
git grep -n "LIUHAO_AUTOMATION_ENABLED\|SUPPORTED_BACKENDS" -- src/automation
git grep -n "LIUHAO_LOCAL_SCHEDULER" -- src/tasks/local_scheduler.py
git grep -n "LIUHAO_MCP_ADAPTER_ENABLED" -- src/adapters/mcp

# 关 4：后端与桩标记
Get-ChildItem src/automation/backends -Name
git grep -n "not implemented\|NotImplementedError" -- src/automation src/kernels/retention
```

---

## 10. 本轮未验证 / 不确定项（不得当作“已通过”）

1. **`src/desktop/` 与 `installer/`、`packaging/` 正被另一条流修改**（`git status` 有未提交改动）。本图对桌面托盘只给“半成品”，不给定论。
2. **手机端真机流程**未实测（局域网访问、加入主屏幕、独立窗口、`/v1/*` 不被缓存、断网重连）。
3. **定时排程器**只做了静态接线判断，未在桌面启动链实跑。
4. **知识库**的 embedding 默认可达性按环境变量推断，未用真实 Key 实跑入库。
5. **HC-01 审计链**运行时状态以 `docs/hc01-status.md` 为准；本图只标“写入真实、状态 UNVERIFIED”。
</content>
