# 用户要求逐条对账（2026-11-08）

> 口径：把发起人原话逐条对到"现在真实做到什么程度"，只写能复核的事实。判"已做到"必须指明在哪个版本上验过；判"部分做到"必须写清缺口；判"未做到"必须写清阻塞。
>
> **本文件只读核查产出，未改任何产品代码、未提交、未改动 `dist\`。**

## 0. 基线与环境

| 项 | 值 |
|---|---|
| 仓库 | `D:\LiuHao-AI-OS` |
| 当前 HEAD | `416f7109`（2026-11-07 12:00:50 -0800，`fix(installer): SaveStringsToUTF8File …`） |
| 六项隔离复验所用固定快照 | `b4d4052`（`git archive` 导出，临时目录运行，避免合包流改动干扰） |
| 更早一轮基线 | `301eded5`（同六项的首轮核查） |
| 安装包基线 | `dist/installer/DELIVERY.txt` 标注对应提交 `f74fa14e`（origin/p36 tip） |
| 隔离实例方式 | 每个用例随机端口 + 独立临时数据目录 + 清空 `LIUHAO_*`/`AUDIT_DB_*` 环境变量，真实 HTTP 调用，真实界面点击 |
| 证据临时目录 | `C:\Users\Administrator\AppData\Local\Temp\liuhao-verify\` |

工作区当前有未提交的在途改动（`src/ai/providers.py`、`src/ai/model_config.py`、`src/gateway/models.py`、`ModelManager.tsx` 等），与本清单结论直接相关，已在第 3、8 条单独标注"在途"。

---

## 1. 能下载、能安装、填 Key、能用；别人下载安装也能直接用

**判定：已做到（有证据；边界见下）**

已核验：
- 交付物存在且可校验：`dist\installer\LiuHao-Setup-0.1.0.exe`，34,731,647 字节，SHA256 `01BE18BBE4C07923486FB27BA59C23C23F42A6E86F89C8967B1F86C0DEC64DA2`，与 `dist\installer\SHA256SUMS.txt` 一致。
- payload 冒烟（`dist\smoke-report.json`）逐项 PASS：`health`、`console_index`、`register_identity`、`identity_store_in_data_dir`、`restart_health`、`login`、`authenticated_dashboard`、`chat`。
- 干净安装未配模型时，聊天返回的是明确引导语而非崩溃：`[演示数据] 还没有配置模型…请到「设置 → 模型」填写 API Key`（`smoke-report.json` 的 `chat_detail`）。
- 安装验收（`dist\installer\DELIVERY.txt`，独立临时数据目录 + 显式 `/DIR /DATA`）：
  - 默认路径 `D:\LiuHao`：安装 25 s / 10,038 文件 / 首启向导走通 / health 200；
  - 中文+空格路径 `D:\验收 测试\LiuHao`：28 s / 10,038 文件 / marker 回读一致 / health 200；
  - 非默认路径 `D:\apps\liuhao-nondefault`：27 s / 10,038 文件 / health 200；
  - 卸载：交互三次确认后 exit 0，程序目录与数据目录均消失。
- "别人也能直接用"的可移植性证据：payload 内嵌 runtime；安装版启动器把 `AUDIT_DB_PATH` 等固定到数据目录并忽略外部同名变量；冒烟在剥离 `LIUHAO_*`/`AUDIT_DB_*` 的子进程里运行仍 PASS。

**证据边界（不夸大）**：本机无真实云端 API Key，云端"填 Key 后真实对话"未端到端验过；"第二台物理机"未实测，跨机行为是用独立数据目录/独立实例模拟的。

---

## 2. 安装到 D 盘，不用 C 盘；D 盘装不上时有回退

**判定：部分做到**

已核验（HEAD `416f7109` 的 `installer/liuhao.iss`）：
- `DefaultDirName=D:\LiuHao`，数据目录默认 `D:\LiuHaoData`，程序与数据分离。
- 有真实探测与回退代码：`GetDriveTypeW('D:\')` + 写探测文件 `.liuhao-write-probe.tmp`；D 盘不可用或不可写时，程序退到当前用户可写的 autopf 目录（非管理员即 `%LOCALAPPDATA%\Programs\LiuHao`），数据退到 `%LOCALAPPDATA%\LiuHao`，并在界面显示"因为 D 盘不可用，已装到 …"。
- 默认 D 盘路径已实测通过（第 1 条安装验收，25 s / health 200）。

缺口：
- **"无 D 盘"回退分支没有在真实无 D 盘环境跑过**；现有证据只到代码路径与安装包构建（`dist\staging-fallback-test\` 有构建产物，但无对应验收日志）。
- 已知遗留：`data-dir.txt` 由安装器以系统 ANSI(GBK) 写入，非中文系统 + 非 ASCII 安装路径存在乱码风险（`DELIVERY.txt` 第 47 行）。

---

## 3. 填 Key 即用、不重启后端

**判定：部分做到（机制已实测，云端 Key 未实测）**

已核验：
- 模型配置按请求重载，不依赖进程启动时单例。隔离实例中通过 `POST /v1/models` 新建 Ollama 模型配置后，**紧接着**发起 `/v1/chat`，200 且在 5.22 s 内返回真实模型回复，全程未重启后端（`probe_func.log`，基线 `301eded5`）。
- 六项复验中同样路径复现：新建模型 → 立即真实对话成功（`verify6_http_b4d4052.log`）。

缺口：
- 真实云端 Key（OpenAI/DeepSeek/Moonshot/Google/Anthropic）无可用样本，未做"填入 Key → 立即对话"的端到端；目前只验证了 Ollama（无需 Key）与 mock。
- 工作区正在把 `provider=openai` 扩展为"任意 OpenAI 兼容服务"并保留原生多轮 messages（`src/ai/providers.py` 未提交改动），属**在途**，不在 HEAD 判定内。

---

## 4. UI 科幻未来风，但品牌条款一个字不许改

**判定：部分做到**

已核验：
- 视觉方向与令牌已落地：`docs/ui-scifi-direction.md` 明确深色主基调、`--sc-*` 令牌；`apps/console/console/src/os.css` 有对应令牌区。
- 品牌文字未被替换：界面仍在用「鎏灏智能中枢」「鎏灏管家」「LiuHao AI OS / 鎏灏 OS」等原有品牌串（`Login.tsx`、`Shell.tsx`、`charts.tsx` 等），未发现第三方品牌顶替。
- 品牌条款约束已写进视觉规范：`docs/ui-scifi-direction.md` 第 10 行"名称、六边形标志、金色品牌徽记保持原样，不重绘、不改名、不替换"。

缺口：
- **没有"品牌条款不可改"的自动化校验**（`docs/requirements-traceability.md` T-06 明确记为缺口）。
- 整站视觉走查未完成；`docs/ui-audit-20261106/` 仍在改动中（工作区 `M`），不能作为稳定验收证据。

---

## 5. "鎏灏管家"能一句话安排员工干活，也能单独指派，最终给结果

**判定：已做到**

已核验（真实模型 `qwen2.5:3b`，`Ollama`，`scripts/acceptance/butler_real_model_acceptance.py`）：
- `RESULT {"status":"PASS","goal_state":"completed","task_count":2,...}`，耗时 104.52 s；两个任务均为 `completed`（"调研AI开源项目"、"整理AI开源项目要点"）。
- `summary_excerpt` 是真实生成内容（非模板），`VERDICT {"verdict":"PASS"}`。
- 单独指派：六项复验中根把目标指名给取用副本 `adopted-emp`，`POST /v1/goals/assign` 201，轮询到 `state=completed`，任务归属 `employee=adopted-emp`，summary 非空（`verify6_http_b4d4052.log`）。

**边界**：该轮验收 `live_web=false`，检索被显式关闭并在结果里写明"确认未联网且未伪造实时结果"，不是伪造成功。

**另一处记录（低级 UI 缺陷，不推翻本条）**：手机底栏把 `workbench` 的短标签写成"管家"（`Shell.tsx:168-173`），而桌面「鎏灏管家」是另一个入口。手机用户点"管家"进的是工作台对话页，不是管家页。见缺陷 D3。

---

## 6. 多账号

**总判定：部分做到（8 个子项里 7 项已做到，1 项有缺口）**

证据主线：`scripts/acceptance/account_tree_e2e.py` 隔离实例 292 项检查全部 PASS（`.probe_tmp/acct_e2e_run.log`）；六项复验 76 项检查 74 PASS / 2 FAIL（`verify6_http_b4d4052.log`）。

### 6.1 主账号控制所有子账号权限 —— 已做到
- 权限授予/回收、状态启用/停用、删除、Key 授权开关均有真实端点与审计；越权一律中性 404（`ACCOUNT_SUBTREE_DENIED`，文案不含 parent/depth/root）。
- 删除有下级返回 409 `ACCOUNT_DELETE_HAS_CHILDREN`；根不可删除 409 `ACCOUNT_DELETE_ROOT_FORBIDDEN`；被删账号令牌立即失效 `ACCOUNT_UNAVAILABLE`。

### 6.2 子账号只控自己的下级、不能越级 —— 已做到
- 成员可建直接下级（200）；成员列表只返回其直接下级（`['grand1']`）。
- 成员读上级 404、改上级权限 404、读上级用量 404；第三层读成员/用量均 404。

### 6.3 主账号可用子账号 Key，子账号只能用自己的 —— 已做到
- 成员把 Key 授权给根后，根借用成员 Key 对话 200；成员自己用同一 Key 200。
- 根视角 `calls=2`、`by_actor=['member1','root1']`；成员视角 `calls=2` 且无 `by_actor`、只有自己的额度；成员 11 个界面 0 次出现借用方。

### 6.4 子账号记忆归自己、主账号可用全部 —— 已做到
- 根 `scope=subtree&q=` 可搜到成员记忆并带 `account_id` 来源；`/v1/memory/adopt` 复制到根库（`source=adopt:<成员>`，标签 `from:/origin:`），成员原件与可见性不变。
- 成员只能搜到自己的条目；成员读根副本 404，且与"不存在条目"响应逐字相同；成员请求 `scope=subtree` 404；成员审计里看不到根的取用动作。

### 6.5 子账号不能知道自己是子账号 —— 已做到
- 真实界面点击：根身份区原文「我的工作空间」，成员身份区原文「我的工作空间」，完全一致。
- `identityBarRole()` 对所有账号返回同一常量；身份条不含「主账号/子账号/可管理 N 位成员」等层级词。
- 成员侧员工/目标/用量/审计/Key 等界面均不出现上级动作痕迹。

### 6.6 主账号换机/忘密有解法 —— 已做到
- 跨机（独立实例 + 独立数据目录）用同一账号密码登录成功，成员可见范围仍只有其直接下级。
- 忘密：用恢复码在 B 机重置根密码 200；恢复码一次性（重复使用 401 `RECOVERY_CODE_INVALID`）。

### 6.7 主账号打包带走全部 —— 已做到
- 备份清单与密文确实包含**记忆与会话**（导出清单 `account_memory.entries=2 / owners=[memberq,rootq]`、`conversation.turns=4`；本地解密后密文内同样 2/4）；新实例导入回执 `memory=2 / conversations=4`；导入后成员原密码登录 200，能搜到自己的记忆 1 条，指定 `session_id=default` 能读到 2 turns。
- D1 修复后复验（2026-11-08，`account_tree_e2e.py` 372 项）：导入后成员 `GET /v1/chat/history?session_id=member-session` 2 turns，`GET /v1/chat/sessions` 返回 `['member-session']`（`跨机会话可在列表发现` PASS），且不含根的 `root-session`；界面工作台挂载时按后端列表补全会话并回填首条消息标题（`ChatPanel.tsx` 会话发现 effect，回归 `ChatPanel.test.tsx::ChatPanel 会话发现`）。

### 6.8 成员无需邀请码自助注册 —— 已做到
- 默认允许自助注册（200 / True）；自助注册成功（`self1`）。
- 主账号可关闭（200 / False），关闭后自助注册 403 `ACCOUNT_SELF_REGISTER_DISABLED` 且文案中性；关闭期间主账号建成员仍可用；邀请码激活仍可用；恢复允许后自助注册恢复。

---

## 7. 装完不出现"数据不可用"、不卡、不报错

**判定：部分做到**

已核验：
- 安装验收 5 个场景全部走通（默认 D、中文+空格路径、非默认路径、覆盖升级、卸载），首启向导完成、health 200（`DELIVERY.txt`）。
- payload 冒烟 8 项全 PASS，包含注册、重启后登录、已认证仪表盘、聊天（`smoke-report.json`）。
- 仪表盘"数据不可用"是**诚实空态**而非假数据：`src/gateway/dashboard.py:172` 注释明确"数据不可用而不是画一条假曲线"。

缺口 / 遗留：
- 覆盖安装不会删除新清单里没有的旧文件，旧 `config/production.yaml` 残留（`DELIVERY.txt` 第 46 行）。
- `data-dir.txt` 在非中文系统的非 ASCII 路径存在乱码风险（第 47 行）。
- 会话恢复后的"找不到历史"（D1）与知识库删除 403（D2）不属于安装期，但会让用户认为"东西丢了/操作报错"，影响本条体感。

---

## 8. 所有模型填 Key 就能用

**判定：部分做到（在途）**

已核验：
- 支持的 provider 名单（HEAD `src/ai/model_config.py`）：`mock, openai, anthropic, google, ollama, moonshot, deepseek`；其中 `mock`/`ollama` 不需要 Key。
- 实测可用：`ollama`（真实 `qwen2.5:3b` 对话 200，5.22 s）、`mock`（冒烟链路）。
- 模型 Key 加密存储、按请求解密构造 provider，`base_url` 校验为 http/https。

缺口：
- `openai / anthropic / google / moonshot / deepseek` 均无可用 Key，**未逐家端到端**验证"填 Key 即可用"。
- 本轮工作区正在补 OpenAI 兼容协议的 `chat`/`chat_stream`（保留多轮 messages），说明"任意 OpenAI 兼容服务"尚在收口，属**在途**，不能按已完成计。

---

## 9. 不能有 BUG

**判定：未做到**

不是"看起来有"，而是本轮隔离复验真实撞到、可稳定复现的缺陷如下（除 D1 已修外，其余只登记，未修）：

### D1（中）备份导入后会话不可发现 —— 已修（2026-11-08）
- 最小复现：实例 A 成员在 `session_id=ui-history` 对话 → 根导出 → 新实例 B 导入 → 成员登录。`GET /v1/chat/history?session_id=ui-history` 返回 2 turns；`GET /v1/chat/sessions` 返回 `[]`；真实界面成员进「工作台」看不到导入前那条 `Q7紫罗兰-历史-8842`。
- 根因：`src/gateway/chat.py` 的会话注册表只在进程内存、只在聊天请求时填充，`list_sessions` 不读 `conversation_turns`；导入只写消息表，不回填注册表。前端 `ChatPanel` 只用 localStorage 随机 session id，且从不调用 `/v1/chat/sessions`，新机器上无法发现旧会话。
- 修复：后端 `ConversationStore.list_session_ids` 从按账号归一化的会话键反查历史（e474b562，`/v1/chat/sessions` 不再依赖进程内注册表，导入后即可列出）；前端 `ChatPanel` 挂载时调用 `listSessions()` 合并本地清单、落盘并回填标题，失败静默不阻断对话。
- 证据：`tests/gateway/test_account_backup_sessions.py`（导入后 `list_sessions("member1") == ["ui-history"]`，且不串根/他人）；`ChatPanel.test.tsx::ChatPanel 会话发现`；验收 `导出导入 · 跨机会话可在列表发现 PASS`。
- 影响面（修复前）：备份迁移后旧会话在 UI 中不可见；数据未丢，API 指定 `session_id` 仍可读。仅影响"会话发现"，不影响记忆搜索。

### D2（低）知识库删除越权不是中性 404
- 最小复现：成员 `DELETE /v1/knowledge/documents/<根文档>` → 403 `{"detail":"该操作需要管理员权限（admin 角色或 L0 人类主权）"}`；删除不存在文档同样 403。
- 根因：`src/api/routes/knowledge.py` 的 DELETE 先过 `require_admin_principal` 管理员门禁，之后才做账号归属 scope；成员在门禁即被拒。
- 影响面：字面契约"成员删他人一律中性 404"未达成；存在/不存在同形，无存在性泄露；成员也无法删除自己的文档（管理员专属）。

### D3（低）手机底栏把「工作台」叫成「管家」
- 最小复现：窄屏打开控制台，底栏第二项显示"管家"；点击进入的是工作台（对话页），不是「鎏灏管家」页。
- 根因：`apps/console/console/src/components/Shell.tsx:168-173` 对 `workbench` 使用短标签"管家"，而 `PRIMARY_NAV` 里「鎏灏管家」是独立 `butler` 项，且不在 `MOBILE_NAV_KEYS`。
- 影响面：手机端"管家"语义与桌面冲突，用户找不到一句话拆解入口；不影响接口能力。

### 观察项（未定性为缺陷）
- 员工改名接口响应曾出现 `{"name": null, "status": null}`（持久化与界面刷新正常），属响应体一致性瑕疵，未在本轮复验中重新确认。
- 管家指名任务里，"只回复一句话"的指令被规划成一次检索任务并完成；结果真实，但不是字面的一行确认。若要求"指令保真"，需另立缺陷。
- 2026-11-08 复验：`备份导出排除 · 导出清单记录了排除项` 两轮均 FAIL（`export_data_snapshot` 后 `manifest.excluded_protected` 为 0，而 `backup_data` 为 2）；同组"代码包无数据受保护文件"首轮 FAIL（列出 `config/auth_secrets.json`）、次轮 PASS，未定因，仓库工作区确实存在验收辅助脚本写入的 `config/auth_secrets.json`，疑为测试隔离问题。属导出记账/测试隔离问题，不是 D1，也不影响 6.7 的数据完整性结论，未修。

---

## 10. 证据索引

临时证据目录：`C:\Users\Administrator\AppData\Local\Temp\liuhao-verify\`

| 证据 | 路径 |
|---|---|
| 六项复验日志（76 项，74 PASS） | `verify6_http_b4d4052.log` |
| 六项复验结构化结果 | `verify6_http_results.json` |
| 备份密文样本 | `bundle_v6.json` |
| 导出/导入界面走查 | `ui_import_hold.log`、`ui_import_info.json` |
| 身份区截图（根） | `ui\root-identity.png` |
| 身份区截图（成员） | `ui\member-identity.png` |
| 导入后成员工作台截图 | `ui\imported-member-workbench.png` |
| 固定快照（六项复验） | `snap-b4d4052\` |
| 账号树 292 项验收日志 | `D:\LiuHao-AI-OS\.probe_tmp\acct_e2e_run.log` |
| 多账号 8 项复验日志（372 项，1 FAIL，2026-11-08） | `D:\LiuHao-AI-OS\.probe_tmp\acct_e2e_d1_2.log` |
| D1 后端回归（导入后会话列表） | `D:\LiuHao-AI-OS\tests\gateway\test_account_backup_sessions.py` |
| D1 前端回归（工作台会话发现） | `D:\LiuHao-AI-OS\apps\console\console\src\components\ChatPanel.test.tsx` |
| 管家真实模型验收日志 | `D:\LiuHao-AI-OS\.probe_tmp\butler_run.log` |
| payload 冒烟报告 | `D:\LiuHao-AI-OS\dist\smoke-report.json` |
| 安装/升级/卸载交付记录 | `D:\LiuHao-AI-OS\dist\installer\DELIVERY.txt` |
| 视觉方向与品牌约束 | `D:\LiuHao-AI-OS\docs\ui-scifi-direction.md` |
| 既有需求追踪表（T-06 已记品牌缺口） | `D:\LiuHao-AI-OS\docs\requirements-traceability.md` |

---

## 11. 汇总表

| # | 要求 | 判定 | 一句话缺口 |
|---|---|---|---|
| 1 | 下载/安装/填 Key/能用；别人也能用 | 已做到 | 云端真实 Key 与第二台物理机未实测（证据边界） |
| 2 | 装 D 盘，不占 C；D 不可用有回退 | 部分做到 | 回退分支未在真实无 D 盘环境实测；GBK 路径风险 |
| 3 | 填 Key 即用、不重启 | 部分做到 | 热生效机制已验证；云端 Key 未端到端；OpenAI 兼容在途 |
| 4 | 科幻风 + 品牌条款不改 | 部分做到 | 无品牌自动化校验；整站视觉走查未完成 |
| 5 | 管家一句话派活 / 单独指派 / 给结果 | 已做到 | 手机端"管家"标签指向错误入口（D3） |
| 6 | 多账号 8 个子项 | 已做到 | 6.1–6.8 全绿；备份导入后会话列表与界面发现已随 D1 修复复验 |
| 7 | 装完不"数据不可用"、不卡、不报错 | 部分做到 | 覆盖升级旧文件残留；D2 影响体感 |
| 8 | 所有模型填 Key 就能用 | 部分做到 | 5 家云端 provider 无 Key 未端到端；兼容层在途 |
| 9 | 不能有 BUG | 未做到 | D2（低）/D3（低）可稳定复现（D1 已修） |
