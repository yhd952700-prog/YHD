# 总任务台账（MASTER TASK LEDGER）

> **唯一不漏项的真相源。** 本文件汇总用户历次提出的**全部实质要求**，逐条给状态、归属与证据；
> 其他文档（[需求追溯表](requirements-traceability.md)、[known-gaps](known-gaps.md)、
> [交付清单](DELIVERY-CHECKLIST.md)、[战略优先级矩阵](strategy-priority-matrix.md)）与本表冲突时，
> **以本表及可复核的实测证据为准**，并应回改对应文档。
>
> 快照：2026-11-08；本表创建时 HEAD = `b618f8ab679802f5f94628e32bcb9ececa6c8717`（分支 `p36`）。
> **`72fd9b17` 的交付凭证已作废**（其全量数字只作历史记录，见 E-16），`b4406497` 的 CI 为 failure（§10.2）。
> 本轮就绪判定对象 = **新代码 tip `416f7109`**（含 0.1.1 版本号 `f7603a8e` 与四个 ISCC 修复），
> 全量凭证见 [§11.2](#112-新-tip416f7109-的全量凭证) 与
> [evidence/verify-full-416f7109-2026-11-08.md](evidence/verify-full-416f7109-2026-11-08.md)。
> **`416f7109` 只是代码 tip，不是交付冻结 hash**：冻结前置仍欠 8 项（[§11](#11-新一轮冻结前置清单2026-11-08) P-1～P-8）。
> 本轮 26 个修复/文档提交的逐条登记见 [§10](#10-本轮修复登记2026-11-07)。
> 交叉验证基线是 `3fa88be0`，其报告 [cross-verify-3fa88be0.md](evidence/cross-verify-3fa88be0.md)
> 明确写死：**过程凭证，不是交付凭证**。

## 0. 四态口径（不允许含糊）

| 状态 | 判定标准 | 备注 |
|---|---|---|
| **已完成** | 有可复核的真实运行/真实链路证据（日志、JSON、测试数字、提交 hash）；只有代码或只有单测不算 | 证据列必须能点开或能按 hash 复现 |
| **在跑** | 有明确归属流，代码/测试已入库或在工作区推进，但**未收口**（缺真实 E2E、缺 UI、缺提交、或 CI 红） | 写明是**哪条流**；未提交的工作区代码不算完成 |
| **待做** | 无流在推进，或只有文档/设计；实现缺失或只有未验收的代码 | 包含「未验证」——未验证不是已完成 |
| **明确延后** | 已作出「本轮不做」的裁决，写清为什么、依赖什么、什么算关掉 | 不得在交付报告里写成已完成 |

> 与 known-gaps 的「未验证」对应关系：本表把「未验证」并入 **待做**（并在备注写死原因），
> known-gaps 保留其独立的「未验证」标签。两处都不得把未验证写成通过。

---

## 1. 交付与安装（普通用户三步链路）

| 编号 | 用户要求（原话口径） | 归属流 | 状态 | 证据 / 还差什么 |
|---|---|---|---|---|
| A-01 | 下载、安装、填 Key 就能用（无管理员权限，装到用户目录） | 安装器 | **待做**（无流在跑） | `installer/liuhao.iss`、`packaging/*` 已由 git 跟踪（`git ls-files` 实测命中）；2026-11-06 15:05 复查：**无任何打包/python 进程在跑**，`_precheck` worktree 最后写入 13:07:59、`liuhao-pkg-0be49e1d` 13:17:28，`dist/installer/` 仍为 **0 个文件**。见 E-13 |
| A-02 | 首次运行向导：建主账号 → 选模型 → 填 Key | 安装器 | **待做**（未验证） | `scripts/first_run_wizard.py` + `tests/test_first_run_wizard.py` 已提交；快照时无安装器进程在跑，未在命令行窗口逐字键入（E-9） |
| A-03 | 运行中填 Key，**不重启后端**即生效 | 网关 / 模型管理 | **待做**（未验证） | `src/gateway/models.py:1` 自述「保存即生效」；`src/ai/providers.py:907,979` 按配置逐次构造；单测在；快照时无网关流进程，**未用真实厂商 Key 重跑端到端** |
| A-04 | 真实厂商 Key 能真实对话 | 网关 / 安装器 | **待做** | 本机无 Key，只用 OpenAI 兼容桩验证 HTTP 路径；E-5 未关闭 |
| A-05 | 端口被占明确报错（不自动顺延），以控制台打印地址为准 | 分发 / 启动器 | **已改定** | 2026-11-08 决定：不自动换端口，避免地址漂移；旧 E2E 日志 `18130→18131 自动顺延` 的行为已作废 |
| A-06 | 出错要能看懂（Key 错 / 模型名错 / 上游停 / 服务没起） | 网关 | **已完成** | `failure-modes-2026-11-06.json`：401 / 404 / 503 / `WinError 10061` 四类实测可读 |
| A-07 | 最终安装包 + SHA256 + 明确的对应源码提交 | 安装器 | **待做**（无流在跑） | 15:05 复查无打包进程，`dist/installer/` 为空；`DELIVERY-CHECKLIST.md` §0 / §0.1 的路径、字节、SHA256、源码提交**仍全部待回填** |
| A-08 | 安装包代码签名（避免「未知发布者」） | 安装器 | **明确延后** | E-8；依赖证书与签名流水线。关掉标准：签名校验通过且失败拒绝安装并留审计 |
| A-09 | 可卸载（含静默卸载不挂住） | 安装器 | **待做** | E-4：`unins000.exe /VERYSILENT` 停在「是否保留数据」确认框 |
| A-10 | 安装包内的前端是当前版本（不是旧 dist） | 前端 / 安装器 | **待做** | E-7：前端仍在改，装的是构建时 `console/dist`；最终包必须重建 |
| A-11 | 在干净 Windows 机器上实测安装 | 安装器 | **待做** | 从未在干净机跑过完整「双击 → 逐字向导 → 首条消息」 |
| A-12 | 手机连同一 Wi-Fi，打开 → 登录 → 发消息 | 运行验证 | **待做**（未验证） | 服务侧 LAN `192.168.31.171:8000` 返回 200 已验证；手机侧打开/登录/发消息无设备未验证（T-17、`docs/mobile-access.md`） |
| A-13 | 给普通用户的三步说明（装、填 Key、发消息） | 文档 | **已完成** | `docs/END-USER-GUIDE.md`，commit `a97d5862` |
| A-14 | 安装前必读（风险与前提） | 文档 | **已完成** | `installer/assets/BEFORE-INSTALL.txt`（1,651 字节） |
| A-15 | 用户必撞的五类故障面要加固（单实例/休眠断网/时间跳变/磁盘/并发） | 韧性 | **已完成** | `3fa88be0`；单跑 **501 passed, 19 skipped in 230.93s**；新增 16 条故障面用例 **16 passed**；详见 `fault-surfaces-2026-11-06.md` |
| A-16 | 故障面的真实边界（真睡眠/真拔线/真 ENOSPC/真托盘点击/双窗口/真时钟） | 韧性 | **明确延后**（未验证） | known-gaps F-1～F-6；现有替代证据为进程内模拟，**不得写成已验证** |

## 2. 管家与 AI 员工

| 编号 | 用户要求（原话口径） | 归属流 | 状态 | 证据 / 还差什么 |
|---|---|---|---|---|
| B-01 | 「鎏灏管家」：一句话吩咐 → 自行安排员工 → 汇总结果 | 控制台 + 网关 | **待做**（未验证） | `94003942` 建管家面板、`3510cab3` 接线点名派工/失败重试/增量进度/待办审批；`Butler.tsx` 链路为 `POST /v1/goals(background) → GET /v1/goals/{id} → 汇总`；快照时无管家流进程，**未跑真实一句话 E2E** |
| B-02 | 也可单独指派某个员工 | 控制台 + 网关 | **待做**（未验证） | `POST /v1/goals/{id}/assign` 与 `_assign_agents`（`src/kernels/execution/__init__.py:634`）；快照时无管家流进程，真实执行未验 |
| B-03 | AI 员工可创建、可管理 | 网关 / 员工管理 | **待做**（未验证） | 员工管理 API 在 `src/gateway/ai_management.py:710-767`，`EmployeeStore` 已入库；快照时无对应流进程，真实运行未验 |
| B-04 | 员工接到任务后真实执行并回报 | 执行内核 | **待做**（未验证） | `AgentRuntime`/Goal 执行链有真实代码与单测（`31e66269` 补齐接线）；快照时无执行内核流进程，未见端到端员工执行证据 |
| B-05 | 定时提醒（到点响） | 任务 | **待做**（未验证） | `manage_reminders` 第 10 个内置工具 + `3bda8c7d` 进程内 SQLite 排程器；快照时无 tasks 流进程；**「创建后重启服务仍按时触发」未验证**（D-3/D-8） |
| B-06 | 本地语音输入（离线、音频不外传） | 任务（新） | **明确延后** | D-3：缺离线 ASR 接线与设备权限/隐私边界。关掉标准：离线识别一段中文并可直接发送，音频不落盘 |
| B-07 | 操作前预览（将做什么、影响、如何撤销） | 自动化 | **明确延后** | D-3：只有确认票据文本 `preview` 字段，无 UI 结构化预览/撤销；依赖 B-08 |
| B-08 | 自动化真实后端（浏览器 / 安卓 / 桌面键鼠） | 自动化 | **明确延后** | D-4：唯一后端 `backends/fake.py`，三重闸门默认关闭；关掉标准见 known-gaps D-4 |

## 3. 账号、权限与越级

> 归属：**账号流**（后端）+ **控制台流**（UI）。本组除 C-15 外均为 **待做（未验证）**：被调用实现已由
> `24fca472` 补齐、控制台已由 `1e1855dc` 提交；但快照时无账号/控制台流进程在跑，
> 且没有任何账号侧真实端到端验收证据。**C-15（八项之外的两条边界）已于 2026-11-07 用真实 HTTP +
> Playwright 实测全绿**（109 项 0 失败），证据见其行；它不改变 C-01～C-14 的「未验证」结论。

| 编号 | 用户要求（原话口径） | 归属流 | 状态 | 证据 / 还差什么 |
|---|---|---|---|---|
| C-01 | 首次安装第一个注册者自动成为主账号；根一旦存在永久关闭「注册根」 | 账号流 | **待做**（未验证） | 语义见 `docs/account-hierarchy.md` §0-6、§9.3；实现与真实首启未在同一 HEAD 复验 |
| C-02 | 成员**自助注册**（无邀请码，默认开启，主账号可关） | 账号流 | **待做**（未验证） | `8eeca8a2` 提交自助注册与开关（`GET/PATCH /v1/accounts/self-registration`）；控制台设置项已随 `1e1855dc` 提交；无真实 E2E |
| C-03 | 自助注册**不能**自选上级/角色/深度，**不能**注册成根 | 账号流 | **待做**（未验证） | 请求体 `extra=ignore`（`SelfRegisterRequest`）；内核固定 `parent=根/depth=1`；无真实 E2E |
| C-04 | 主账号拥有核心能力；子账号保留完整日常体验 | 账号流 | **待做**（未验证） | `account-hierarchy.md` §2.1 能力矩阵；代码侧 scope 已细化，业务面尚未全部接 `require_account_principal`（§12） |
| C-05 | 越级必须拒绝（子用上级 Key、读他人记忆/会话/文件/审计） | 账号流 | **待做**（未验证） | `src/kernels/identity/_accounts.py` 错误码与路径判定已在；无跨账号真实 E2E |
| C-06 | 子账号界面**无层级痕迹**（不出现上级/子树/「受限」等词） | 控制台 + 账号流 | **待做**（未验证） | `1c27bedb` 去层级词、`8c94c09d` 只增可读字段；`apps/console/**` 已随 `1e1855dc` 提交；UI 审计流 11:52 的三层巡检报告未入库，只能作旁证 |
| C-07 | 主账号**可用子账号的 Key**（须有共享授权） | 账号流 | **待做**（未验证） | `allow_superior_key_use` + `ACCOUNT_KEY_CONSENT_REQUIRED`（`src/kernels/identity/__init__.py`）；无真实调用 E2E |
| C-08 | 子账号「不知情」的**边界**：界面不暴露调用细节，但**不承诺技术完全不可知** | 账号流 / 文档 | **待做**（未验证） | 口径已写死 `account-hierarchy.md` §5.6：可知共享开关与自己的额度，不可知谁调/何时调；供应商后台额度仍可被观察。实现与文案未验收 |
| C-09 | 停用/删除连锁：吊销 Key、会话失效、记忆归档、仅叶子可删 | 账号流 | **待做**（未验证） | §5.5/§5.7 规格已写；代码路径在 `_accounts.py`；无真实 E2E |
| C-10 | 费用与用量**双记**：账单记 Key owner，用量记 actor | 账号流 | **待做**（未验证） | §5.3 定稿；`src/cost/cost_tracker.py:245` 历史无 principal 参数，需显式字段；未验证 |
| C-11 | 主账号**跨机登录**：导出加密凭据包 → 新机导入即认领 | 账号流 | **待做**（未验证） | `src/kernels/identity/_backup.py` 与 `/v1/accounts/backup/*` 存在；`7e2c0eff` 限定「仅全新实例」；无换机真实 E2E |
| C-12 | 忘记密码：恢复码/恢复文件重置；两者皆失则重建身份体系 | 账号流 | **待做**（未验证） | `rotate_recovery_code`/`consume_recovery_code` 在 `_accounts.py`；无真实 E2E，UI 未验 |
| C-13 | 账号数据分区：工作区文件、会话、审计按账号隔离 | 账号流 | **待做**（未验证） | `68392942` 提交「账号三块隔离」；无跨账号真实 E2E |
| C-14 | 登录页状态机 S0–S5 与中性文案 | 控制台 | **待做**（未验证） | 规格 `account-hierarchy.md` §9.2；`Login.tsx` 已随 `1e1855dc` 提交；未验收 |
| C-15 | 八项之外的边界：①子账号被降权后**旧会话立即失效**（不能继续做原来能做的事）；②主账号导出包在**新数据目录**恢复后，账号树 / Key 归属 / 记忆归属仍成立（不只是文件复制成功） | 账号流 + 控制台流 | **已完成**（2026-11-07 16:14–16:17 实测） | `scripts/acceptance/account_matrix_acceptance.py`（固定端口 19008 / 数据目录 `D:\_audit\account-matrix-2`）**109 项检查 0 失败、退出码 0**（8 项主体 98 + B1 6 + B2 5）。B1：降权后旧令牌下一次请求 `401 CREDENTIAL_STALE`，重新登录后也不能再建下级；B2：新数据目录上账号树 4 节点、三把 Key 归属行都在且 workerB 列表不含主账号 Key、workerB 用主 Key `403 ACCOUNT_KEY_NOT_OWNED`、子树记忆 4 条覆盖 `boss/workerA/workerA1/workerB`。证据 `D:\_audit\account-matrix-2\report.json` |

## 4. 30 方向、五大中心与开源情报

| 编号 | 用户要求（原话口径） | 归属流 | 状态 | 证据 / 还差什么 |
|---|---|---|---|---|
| D-01 | 研究 30 个方向 + 用户没想到的方向，逐条给完成度 | 战略 | **已完成** | `docs/strategy-30-directions.md`：真有 8 / 半成品 11 / 假实现 4 / 无实现 7；文档自述**未跑运行时 E2E** |
| D-02 | 五大战略中心（只做信息架构、命名与权限边界，不拆五套后端） | 战略/架构 | **已完成**（决策） | `strategy-30-directions.md:42-46,87-147`；T-24 |
| D-03 | 「现在 8」方向要有可操作入口与真实链路验证 | 各方向流 | **待做** | 8 项（1、3、8、22、23、26、28、30）只有静态可达；运行时 E2E 未做 |
| D-04 | 「下一阶段 9」方向（2、5、7、9、16、17、20、24、29） | 各方向流 | **明确延后**（排期） | 依赖「现在」项或外部 Provider/资产；见 `strategy-priority-matrix.md` §0 |
| D-05 | 「不现在做 13」（不做 8 + 仅设计 5） | 战略 | **明确延后** | 矩阵 §2；不做 8：6、10、11、12、14、15、18、19；仅设计 5：4、13、21、25、27 |
| D-06 | 六类明确不做（电脑自主操作/任意仓库改码提 PR/金融交易/外部 KYC/全网常驻采集/机器人 IoT 工业执行） | 战略（决策） | **明确延后**（不做） | `strategy-30-directions.md:188-220`；重新捡起须先改本节并给安全/合规前提 |
| D-07 | LIUHAO 开源情报：发现分类/跟踪版本/许可证与依赖漏洞/找缺口/生成方案/自动测试/记录来源 | 情报 | **待做**（未验证） | `src/intel/` 已入库 `0535d321`，CLI `src/intel/__main__.py`；**漏洞查询未接线**（`scanner.py:298,428`）；快照时无 intel 流进程，未用真实 GitHub API 跑完整扫描 |
| D-08 | 每个候选回答九问 | 情报 | **待做** | `src/intel/proposals.py:13-14` 只有免责声明；九问是否逐条落表未验证 |
| D-09 | 全量开源资源研究：地址/版本/许可证/依赖/维护/安全，未验证必须标注 | 情报 | **已完成**（文档） | `docs/oss-radar/` 8 份约 395 KB + `oss-registry.yaml`；逐项版本/许可证/维护状态**未人工抽查** |
| D-10 | 情报中心后续：漏洞查询、缺口对照、更新提醒 | 情报 | **明确延后**（D-6） | 依赖 advisory 配额与调度/通知；关掉标准见 known-gaps D-6 |

## 5. 进化、安全、质量与文档

| 编号 | 用户要求（原话口径） | 归属流 | 状态 | 证据 / 还差什么 |
|---|---|---|---|---|
| E-01 | 产品能自我升级（备份 → 自检 → 应用 → 失败回滚） | upgrade | **待做**（未验证） | `85e1f639`/`3afa5a56`/`4b519bbe` 引入 `src/upgrade/` + `tests/test_upgrade_selfcheck.py`；C9 安装树两轮升级/回滚已独立验证（`docs/evidence/c9-*-2026-11-06.md`）；快照时无 upgrade 流进程，`test_unreadable_database_fails_the_check` 是否仍红待本轮全量给出 |
| E-02 | 产品能**受控自改代码**并热更新 | upgrade / 安全 | **明确延后** | D-1：需签名、审批票据、沙箱、原子替换与回滚四件套；理由与验收标准见 known-gaps D-1 |
| E-03 | 安全边界：默认仅本机可达、Host/Origin 守卫、fail-closed、无明文 Key、自动化三闸 | 安全 | **已完成** | `c56ea742` 本机默认 + Host/Origin 守卫；`secret-scan-2026-11-06.json` 0 命中；`deployment gate vars` exit=0 |
| E-04 | 11 个门禁变量按「安全优先」默写 | 配置/门禁 | **已完成** | T-15：`verify_deployment_gate_vars.py` 实跑 `RESULT: PASS`、exit=0；`TestSafeValueLock` **7 passed**；未调低任何阈值 |
| E-05 | 推送 origin、修 CI 到全绿 | 交付/CI | **已完成** | 冻结 hash `72fd9b17` 的两条 CI run 全绿：`42323536105`（Release Readiness Contract C1..C12 + Independent Verification C10）与 `42323536110`（Build / Run Tests 3.11+3.12 / Architecture Gate / Security Scan / Guardrails / Console A11y / Smoke / Observability / P0 Gate 共 13 个 job，全部 success）；两腿一致 `7596 passed / 40 skipped / 3 xfailed`（=7639，与本机全量 collected 相同） |
| E-06 | 文档说的事和程序做的事必须一致（OpenAPI 以运行时路由为准） | 网关契约 | **已完成** | `fd0d006f`：运行时 **89 paths / 0 ghost / 0 missing**；冻结 hash 上 CI 实跑 `PASS: contract matches runtime -- 118 paths (+1 documented schema-excluded), 0 ghosts, 0 missing, methods agree` |
| E-07 | 延后项不得消失，必须登记 | 文档 | **已完成** | `docs/known-gaps.md` D-1～D-10、E-1～E-18、F-1～F-6、R-1～R-6 + 本台账 |
| E-08 | 不足的地方自行判断完善，不推回给用户决定 | 全部工程流 | **已完成**（工作方式） | 延后项改为「登记 + 验收标准」，不向用户抛选择题；见 known-gaps 各条 |
| E-09 | 能并行的任务就并行 | 编排 | **待做** | 本轮同仓多流并行、CI 多 job；缺工具层自动分片，靠人分流；快照时无编排流进程 |
| E-10 | 界面科幻、未来风；品牌条款不许改 | 前端 | **待做**（未验证） | `docs/ui-scifi-direction.md` + `os.css` `--sc-*` 令牌 + P1 视觉 `cc3af30d`；`apps/console/**` 已随 `1e1855dc` 提交；UI 审计流 11:52 的 37 张三账号截图报告未入库，整站走查与品牌自动校验未做 |
| E-11 | 知识闭环：入库 UI → 检索 → 对话注入 | 知识 | **待做**（UI 未验证） | `69db011e`/`79bd69c8`/`095ecdb9`/`8858c77e`/`82404fdb`；`/v1/chat` 注入 `build_knowledge_context`。2026-11-09 已完成 API 层六项真机 E2E（证据 `c:\Users\Administrator\.trae-cn\assistant\2026-11-10\kb-e2e\outputs\run.md` 与 `r1_*.json`/`r2_*.json`，绑定 HEAD `0c3d70ba`）：入库 5 块/5 向量、切分 5 chunk、真实 nomic 768 维、检索 top_score=0.8211、对话 used=true/4 来源含「蓝鲸-7734」、未配置 provider 503 全 PASS；**仍缺控制台入库 UI 与引用来源展示，另有引用对齐 12/14、7 个不存在问题放行 5 个、删 A 误删 B 词法索引三项质量缺陷待修/复验**（`c:\Users\Administrator\.trae-cn\assistant\2026-11-09\knowledge-real-usability\outputs\knowledge-real-usability.md`） |
| E-12 | 备份/恢复/导出一键化 | 运维/设置 | **待做** | `dcf025c3` 让 CLI 操作真实归档；`scripts/ops/backup.py:11` 自述无生产调用方；无设置页与恢复流程（D-9） |
| E-13 | 产物索引 + 目标→文件回溯 | 执行/文件 | **待做**（未验证） | `c14ab20c` 索引 CLI、`82f95450` 接入执行链（`31e66269` 补齐执行接线）；快照时无 artifacts 流进程；缺权限过滤、控制台入口、回放界面（D-10） |
| E-14 | HC-01 基线裁决（可证/不可证分离） | 证据/裁决 | **已完成**（裁决） | [ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md) 记录 owner 5 项裁决；C11 状态由「`BLOCKED — HUMAN DECISION PENDING`」改为「**已裁决（ADR-HC01）**」；机器门 `check_hc01` 仍按冻结人类主权门打印 `BLOCKED`（不改口径，不是「裁决未做」） |
| E-15 | 交叉验证 `3fa88be0` 的 C3/C12 FAIL（29 条）逐条归因并修掉 | 账号/知识/执行 | **已完成** | 根因：实现留在工作区、测试先提交。收口提交 `24fca472`（`model_router.visible_to`、`GoalViewer`、结构化错误体）、`31e66269`（执行/规划运行时接线）、`1e1855dc`（控制台请求字段）、`662ad816`（部署门 refs / 审计断言 / fault 补丁）、`d9797c2a`（测试加载器 `sys.modules`）。复核：`5a11baa0` 7620/0/0、`ae7935a7` 7620/0/0、`72fd9b17` 7639/0/0 |
| E-16 | 最终冻结 HEAD 的交付级全量凭证 | 交付 | **已完成** | 冻结 hash `72fd9b17`（`origin/p36` tip），干净 worktree `D:\copilot-worktrees\liuhao-delivery-72fd9b17`，`.venv` py3.12.14，`--full` 于 2026-11-06 18:41:23 完成：`PASS=10 FAIL=0 BLOCKED=1(C11) NOT_VERIFIED=1(C9) ERROR=0`，C3/C12 = 7639 collected / 0 failed / 0 errors / 61 skipped，C7 首跑 PASS（未重跑）。**裁决前** `RELEASE READY=False` 的两个原因是 C9（无真实 CD）+ C11（人工裁决未定）；**ADR-HC01 记录后** C11 已裁决，False 的理由**只剩 C9**——桌面分发形态没有真实 CD 环境可验，是**形态差异**不是缺陷；安装树的真实升级/回滚（`c9b-*` 证据）已覆盖交付需求，但**不等于「真实 CD 已验过」**。机器 `check_hc01` 仍打印 `BLOCKED`（冻结门设计，不改口径、不重跑）。证据：`docs/evidence/regression-baseline-coverage-2026-11-06.md` §8、`D:\cache\temp\verify-full-72fd9b17.log` |
| E-17 | 打包白名单必须覆盖方法体内延迟导入的依赖 | 打包 | **已完成** | `3ec7d755`、`b5ee295c`；`_MEASURE_CODE` / `EXPLICIT_DISTS` / `_validate_runtime` 三处同步；运行期自检 58→63→72 个发行包；`httpx`/`sqlalchemy`/`anthropic` 离线可 import |
| E-18 | `src/upgrade/startup_probe.py` 把 `/openapi.json` 当健康兜底 | upgrade | **待做**（已登记，不修） | 每路径只探一次、超时 1.0s、采纳首条 2xx 不回头；冷启动首连慢时会误采纳 static JSON，服务其实健康。**关闭条件**见 `docs/known-gaps.md` E-18（优先路径多轮重试 + `health_path_is_fallback` 标注 + 断言改为 `ok + http_status==200 + health_path ∈ 配置集合` + 冷启动慢响应用例） |

---

## 6. 四态统计

| 状态 | 条数 | 占比 | 分布 |
|---|---|---|---|
| 已完成 | **19** | 28.4% | A-05/06/13/14/15；C-15；D-01/02/09；E-03/04/05/06/07/08/14/15/16/17 |
| 在跑 | **0** | 0% | 18:50 复查无任何流进程在跑（仅 11/6 00:47 起的僵尸 pytest 1052/18116，CPU 不增长）；`dist/installer/` 仍 0 个文件 |
| 待做 | **38** | 56.7% | A-01/02/03/04/07/09/10/11/12；B-01～05；C-01～14；D-03/07/08；E-01/09/10/11/12/13/18 |
| 明确延后 | **10** | 14.9% | A-08/16；B-06/07/08；D-04/05/06/10；E-02 |
| **合计** | **67** | 100% | 5 个主题块 |

> 说明：**「待做」38 条里，账号域 14 条是「代码/测试在、真实链路未证」**，不是空的；
> **「已完成」19 条仍不含安装包**：最终安装包（A-01/A-07）由分发流产出，凭证（E-16）已出。
> 已登记不修的新项是 E-18（`startup_probe` 健康兜底），关闭条件写在 `known-gaps.md`。

---

## 7. 与三份既有文档的交叉核对（不一致清单与统一动作）

> 核对对象：`requirements-traceability.md`、`known-gaps.md`、`DELIVERY-CHECKLIST.md`、
> `strategy-priority-matrix.md`。编号 I-x 为本次发现的**真实不一致**（不是措辞差异）。

| # | 不一致 | 哪份文件说什么 | 实测 / 本表口径 | 统一动作 |
|---|---|---|---|---|
| I-1 | 自升级 / 自改代码状态 | T-04 写「未做」「仓库内无实现」 | `src/upgrade/` 已由 `85e1f639`/`3afa5a56`/`4b519bbe` 提交，并带 `tests/test_upgrade_selfcheck.py`；known-gaps D-1 记「部分完成」 | **改 T-04** 为「部分完成（自升级链已入库；受控自改代码明确延后）」 |
| I-2 | 管家是否只有聊天入口 | T-05 写「缺管家编排与汇总界面」 | `94003942` 已建 Butler 面板、`3510cab3` 已接线派工/重试/增量/审批 | **改 T-05** 备注：界面已在，缺的是真实一句话 E2E |
| I-3 | 安装器代码是否被跟踪 | T-02 写「`installer/`、`packaging/` 当前未被任何 commit 跟踪」 | 实测 `git ls-files installer packaging` 命中 10 个文件（含 `.iss`、构建/打包脚本） | **删 T-02 的错误断言**，改为「脚本已跟踪；缺最终包与干净机实测」 |
| I-4 | 知识域 422 vs 400 | known-gaps D-7 写「本轮复跑有 2 项 422 vs 400 失败（属知识流在改）」 | T-19 写「已由知识流收口」，并给出 `test_documents_admin_api.py` **17 passed** | **改 D-7**：删除「仍失败」表述，注明已收口 |
| I-5 | 安装版首启 E2E 能否算完成 | DELIVERY §2 #4 状态写「已完成」 | 同表 E-13 与 §0.1 明确产物不可复现、不能当最终包证据 | **改 §2 #4** 状态为「部分完成（结论保留，产物不可复现）」 |
| I-6 | 全量回归是否代表最终 HEAD | DELIVERY §2 #16 写「已完成（基线 bb6b654c）」 | 交叉验证 `3fa88be0`：`PASS=7 FAIL=2 BLOCKED=1 NOT_VERIFIED=2`，C3/C12 **FAIL**，31 failed | **改 §2 #16** 为「基线已完成；最终 HEAD 未通过」；新增 §5.9 引用交叉验证 |
| I-7 | 「下一阶段 9」算不算延后 | known-gaps 四类统计把 30 方向写成「22 = 下一阶段 9 + 不现在做 13」并计入「决定延后」 | 矩阵把 9 项列为 **planned-next（计划做）**，不是 decided-deferred；且原表合计 `2+3+22=27` 却写 `24`（算术错误） | **改四类统计**：明确延后 = 2 主条目 + 3 子项 + 13 不现在做 = **18**；9 项 planned-next 记「待做」，合计 0/16/18/0 |
| I-8 | 方向 25/16/29 到底是「真有」还是「不做/下一阶段」 | T-11 把 25、16、29 列为「真有 8」；known-gaps 30 方向表把 25 记 design-only、16/29 记 planned-next；矩阵把 25 记「不现在做（仅设计）」 | 两者是**不同轴**：T-11「真有」= 静态存在实现；矩阵「现在/下一阶段/不现在做」= 排期优先级。25 静态有 CLI 但优先级为 design-only | **两处加口径说明**：两轴不可相加；「真有 8」≠「现在 8」（集合不同：现在 8 = 1、3、8、22、23、26、28、30） |
| I-9 | 手机端未验证是否登记 | T-17、`mobile-access.md`、`capability-map.md` 都写「手机侧未验证」；known-gaps 自称登记所有未验证项，却**没有**这条 | 服务侧已验证、手机侧无设备；且未设 `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 时身份行全被拒 ⇒「无人可登录」 | **新增 known-gaps E-14**（手机真机未验证 + 登录阻塞事实） |
| I-10 | 交叉验证失败是否进缺口登记 | `docs/evidence/cross-verify-3fa88be0.md` 记录 C3/C12 FAIL、31 failed；known-gaps 无对应条目 | 27 条失败 + 1 条采集错误同根因：账号/知识切片提交了测试与调用方，把 `model_router.visible_to`、`ai_management.GoalViewer`/结构化错误体留在工作区 | **新增 known-gaps E-15**（归属账号/知识切片；提交后必须重跑） |
| I-11 | 四类统计合计算术 | known-gaps 四类表写「合计 24」，但同行解释是「2 主条目 + 3 子项 + 22 方向」= 27 | 27 才对；且 22 里混入了 9 项 planned-next | 与 I-7 一并修正为 18 + 9（待做） |

**本次没有发现冲突、因而未改的：**

- `DELIVERY-CHECKLIST` 的 C9「原口径 NOT_VERIFIED + 安装树分发形态已验证」双口径 —— 两结论同时成立，不是矛盾。
- `DELIVERY-CHECKLIST` 的 C7 本机 PASS / 非 CI 阻断 —— 与 `bench_baseline` 和方差披露一致。
- `known-gaps` 的 semgrep 本地不可用 —— 与 CI 里 `mcp<2` 已修不是同一环境，两者都成立。

---

## 8. 今天为止，还差什么才能交付给普通用户「下载 → 安装 → 填 Key → 用」

> 只列**真实未完成项**；没有把「在跑」写成「快好了」。按阻塞程度排序。

| # | 还差什么 | 为什么是阻塞 | 关掉的可验证标准 | 对应条目 |
|---|---|---|---|---|
| 1 | **没有可交付的最终安装包（有包，但不是这一版）** | `dist/installer/` 现有 0.1.0 四件套（34,731,647 字节 / `01BE18BB…64DA2`，`DELIVERY.txt` 绑定 `f74fa14e`），但**不是 0.1.1、也不是候选 `416f7109` 的产物**；0.1.1 包还卡在 ISCC 编译修复上 | 产出 `LiuHao-Setup-0.1.1.exe` + `SHA256SUMS.txt` + `DELIVERY.txt`，`DELIVERY.txt` 的源码提交 = 冻结 hash；回填 `DELIVERY-CHECKLIST` §0（见 §7 P-2） | A-01、A-07、P-2 |
| 2 | **没有在干净 Windows 机器上逐字走完** | 历史 E2E 用的是临时构建、`--skip-wizard`、复用向导函数；安装器无法从任何提交复现 | 干净机：双击安装 → 命令行逐字建号/填 Key → 控制台自动打开（默认原生窗口，网页模式才是浏览器） → 首条消息成功；全程录屏/日志 | A-02、A-11、E-13 |
| 3 | **真实厂商 Key 未端到端验证** | 现只有本地 OpenAI 兼容桩；`invalid_key/key_revoked/quota_exhausted` 分类没在真实云 Key 上复现 | 用真实 DeepSeek（或等价）Key 跑 `scripts/verify_first_run_e2e.py`，保存原始输出 | A-04 |
| 4 | **安装包内的前端可能过期** | 前端仍在改；不重建 `console/dist` 就会出现「后端新、界面旧」的静默失败 | 前端冻结后重建 dist，跑升级前门禁（缺 `index.html`/签章不符即拒绝） | A-10、E-10 |
| 5 | **手机/换机登录仍未真机验证**（密钥自动注入已实现） | 身份完整性密钥不再阻塞：启动器首次启动自动生成并安全落盘到数据目录（全新安装/旧数据升级/密钥丢失三路径），但手机侧「打开→登录→发消息」仍无设备实测 | 真机在同一 Wi-Fi 完成一次登录+发消息，留存日志/截图 | A-12、E-14 |
| 6 | ~~账号切片未提交实现，交叉验证 C3/C12 仍 FAIL~~ → **已关闭（2026-11-06 18:41）** | 根因是「实现留在工作区、测试先提交」，不是产品缺陷。`24fca472` / `31e66269` / `1e1855dc` / `662ad816` / `d9797c2a` 逐类补齐后，`72fd9b17 --full` 的 C3 与 C12 均为 **7639 collected / 0 failed / 0 errors / 61 skipped** | 无剩余动作 | E-15 |
| 7 | ~~最终冻结 HEAD 没有交付级全量凭证~~ → **已关闭（凭证已出；C11 已裁决，False 只剩 C9 的形态差异）** | 冻结 hash `72fd9b17` 上 `--full`：`PASS=10 FAIL=0 BLOCKED=1(C11) NOT_VERIFIED=1(C9) ERROR=0`（历史事实，不回写）。**裁决前** False 的两个原因是 C9 + C11；**ADR-HC01 记录后** C11 = **已裁决（ADR-HC01）**，False 的理由**只剩 C9**：桌面分发形态没有真实 CD 环境可验，属**形态差异**；安装树真实升级/回滚（`c9b-*` 证据）已覆盖交付需求，但**不等于「真实 CD 已验过」**。机器 `check_hc01` 仍打印 `BLOCKED`（冻结门设计，不改口径）。安装包仍缺（A-01/A-07，分发流） | 分发流交付最终安装包 + SHA256（A-07）；C9 保持形态差异说明，不为变绿改口径 | E-16、E-05、E-14 |
| 8 | **安装包未签名**（非安装阻塞，但会被 Windows 拦一次） | 无证书 ⇒「未知发布者」；部分杀软可能误报 | 代码签名 + 校验失败拒绝安装并留审计 | A-08（明确延后） |
| 9 | **卸载与静默部署未验证** | `/VERYSILENT` 卡在「保留数据」确认框，批量部署会挂住 | 自动化卸载跑通；交互式卸载也实测一次 | A-09（待做） |

**结论：第 1～5 项是「普通用户下载安装使用」的真实阻塞（第 6、7 项已关闭）；第 8～9 项不阻塞首次使用，但属于交付完整性。**

---

## 9. 证据索引（本表点名的主要文件）

- 交叉验证：[cross-verify-3fa88be0.md](evidence/cross-verify-3fa88be0.md)（基线 `3fa88be0`；**过程凭证**）
- 故障面：[fault-surfaces-2026-11-06.md](evidence/fault-surfaces-2026-11-06.md)
- 安装 E2E：[e2e-installed-2026-11-06.json](evidence/e2e-installed-2026-11-06.json)（产物不可复现）
- 失败分支：[failure-modes-2026-11-06.json](evidence/failure-modes-2026-11-06.json)
- 密钥扫描：[secret-scan-2026-11-06.json](evidence/secret-scan-2026-11-06.json)
- 全量基线：[regression-bb6b654c.txt](evidence/regression-bb6b654c.txt)（7458 tests, 0 failed）
- C9 升级/回滚：[c9-installed-upgrade-2026-11-06.md](evidence/c9-installed-upgrade-2026-11-06.md)、[c9-console-payload-2026-11-06.md](evidence/c9-console-payload-2026-11-06.md)
- HC-01 裁决：[ADR-HC01-baseline-binding.md](adr/ADR-HC01-baseline-binding.md)
- 新 tip 全量凭证：[verify-full-416f7109-2026-11-08.md](evidence/verify-full-416f7109-2026-11-08.md)（代码 tip `416f7109`，本轮 `--full`）
- 账号规格：[account-hierarchy.md](account-hierarchy.md)（§5.6 知情边界、§9.10 跨机、§9.11 忘密、§12 现状差距）
- 缺口登记：[known-gaps.md](known-gaps.md)（D/E/F/R 四组）
- 需求逐条：[requirements-traceability.md](requirements-traceability.md)（T-01～T-25，已按本表回改）

---

## 10. 本轮修复登记（2026-11-07）

> 基线 `b4406497`（`origin/p36`），覆盖 `2738e16a..b4406497` 的 26 个提交。
> §10 使用收口口径：**已完成**＝代码入库且回归可复现；**已验证**＝另有真实进程/真实 HTTP/真实文件系统级证据（非纯 mock）；
> **待验证**＝实现已入库但缺自动化回归或真实链路复验；**已知缺口**＝本轮未修或外部依赖。
> 与 §0 的映射：已完成/已验证 → §0「已完成」，待验证 → §0「在跑」，已知缺口 → §0「明确延后」。
> 本表不合并进 §6 的 63 条原始需求统计，避免两套口径互相污染。

| 编号 | 事项 | 位置 | 修了什么 | 验证方式 | 提交 | 本轮状态 |
|---|---|---|---|---|---|---|
| R-01 | 员工库/身份库并发写丢写 | `src/ai/employee_store.py`、`src/kernels/identity/_persistence.py`、`src/common/atomic_write.py` | 读改写加跨实例文件锁并合并远端，临时文件 + 原子替换；并发雇佣不再互相覆盖 | `tests/ai/test_employee_store_concurrency.py`（10 线程真实文件读写） | `c9a83cd5` | **已验证** |
| R-02 | 凭据/API Key/主密钥/planner 状态并发写 | `src/security/secret_store.py`、`src/security/api_keys.py`、`src/ai/goal_task_graph.py`、`src/gateway/ai_management.py` | 串行化并原子替换写；跨进程读改写不再丢键/覆盖状态 | `tests/test_persistence_concurrency.py`（多进程） | `4fa50ff9` | **已验证** |
| R-03 | 设置/注册表/RBAC/审计 HWM/插件/可观测性并发写 | `src/gateway/account_settings.py`、`src/model_gateway/model_registry.py`、`src/models/registry.py`、`src/security/rbac.py`、`src/security/audit_logger.py`、`src/plugins/manager.py`、`src/observability/store.py` | 统一 lock+merge 原子写；审计水位单调推进 | `tests/test_persistence_concurrency_secondary.py` | `f206cc51` | **已验证** |
| R-04 | 管家结论误丢：终态被并发保存覆盖 | `src/ai/goal_task_graph.py` | 目标/任务终态单调合并，`completed` 不被迟到的 `running` 覆盖，结论字段保留 | `tests/test_persistence_concurrency.py` 终态用例 | `b61bf26a` | **已完成** |
| R-05 | 账号锁死与可恢复初始化（含日志/身份库落数据目录） | `src/gateway/accounts.py`、`src/gateway/auth.py`、`src/kernels/identity/__init__.py`、`src/logging_config.py`、`config/logging.yaml` | 根初始化可中断续跑，失败不把账号永久锁死；日志/身份库按数据目录解析 | `tests/gateway/test_account_root_init_recovery.py`、`tests/kernels/identity/test_identity_data_dir_contract.py`；**但 autouse fixture 打挂 9 个升级用例，见 §10.2 F-01** | `6a2e361e` | **待验证** |
| R-06 | 恢复码：老根补发、本机自救、契约对齐 | `src/gateway/accounts.py`、`src/gateway/auth.py`、`apps/console/console/src/pages/Recovery.tsx`、`scripts/acceptance/account_tree_e2e.py` | 老根账号补发恢复码、新增本机自救入口，恢复码顺序契约与后端一致 | `tests/gateway/test_account_recovery_legacy_upgrade.py`、`tests/gateway/test_account_root_init_recovery.py` | `49bf52bd` | **已验证** |
| R-07 | 日志多进程轮转丢记录 | `src/logging_config.py`、`config/logging.yaml`、`src/gateway/audit.py`、`src/gateway/health.py` | 多进程安全轮转与独立日志文件，避免轮转窗口丢/重记录 | `tests/observability/test_logging_multiprocess.py`（双进程真实写）、`test_logging_rotation.py` | `6a2e361e`、`fd396351` | **已验证** |
| R-08 | 备份不携带记忆/会话，成员可见借用关系 | `src/gateway/account_backup.py`、`src/gateway/account_memory.py`、`src/ai/conversation_store.py`、`src/gateway/account_usage.py` | 备份同时导出内核记忆与会话；成员用量隐藏借用来源；根账号接管路径 | `tests/gateway/test_account_backup_stores.py`、`test_account_usage_privacy.py`、`test_account_adoption.py` | `eaf53a0b` | **已完成** |
| R-09 | 目标/聊天失败被静默吞掉、并发执行串台 | `src/gateway/chat.py`、`src/gateway/ai_management.py`、`src/kernels/execution/__init__.py`、`src/knowledge/ingest_jobs.py` | 持久化/流式失败改为显式错误事件；并发目标事件按执行隔离 | `tests/gateway/test_goal_truth_concurrency.py`、`test_chat_failure_truth.py`、`tests/kernels/execution/test_concurrent_goal_event_isolation.py` | `301eded5` | **已验证** |
| R-10 | 聊天失败文案中性化、演示 mock 显式化、工具结果结构化 | `src/gateway/chat.py`、`src/ai/liuhao.py`、`src/ai/providers.py` | 不再回显上游英文原文；演示模式明确标注；工具结果结构统一 | `tests/gateway/test_chat_failure_truth.py`、`tests/test_liuhao_tools.py`；**但新增 `assistant.provider` 直取打挂 3 个 profile 用例，见 §10.2 F-02** | `b4d4052b` | **待验证** |
| R-11 | 越权：项目/知识库/产物未按账号隔离 | `src/gateway/projects.py`、`src/gateway/files.py`、`src/gateway/chat.py`、`src/knowledge/documents.py`、`src/knowledge/vector_store.py`、`src/api/routes/knowledge.py` | 全部读写按账号作用域过滤；文档删除改 scope 判定而非管理员门槛 | `tests/test_cross_account_isolation.py`、`tests/knowledge/test_documents_admin_api.py`、`tests/gateway/test_projects_persistence_honesty.py` | `913e9b5a`、`682ddb06` | **已验证** |
| R-12 | 老记忆未迁移、会话键不兼容、会话列表重启为空 | `src/gateway/account_memory.py`、`src/ai/conversation_store.py`、`src/gateway/chat.py`、`src/gateway/main.py` | 0.1.0 老记忆幂等迁移；会话键双向兼容；重启后会话列表仍可列出 | 实现已入库；全量顺序下 `test_chat_history_endpoint_reads_persisted` 失败（单跑通过），且新增 `PERSONAL_CONTEXT_DB_PATH` 未登记门禁变量，见 §10.2 F-03/F-04 | `e474b562` | **待验证** |
| R-13 | 摘要断字：全角标点链接边界、兜底截断切半句 | `src/ai/llm_support.py` | 按全角标点切分链接边界；兜底摘要按句子边界截断 | `tests/ai/test_llm_support_summary_guards.py` | `f8f5d73b` | **已完成** |
| R-14 | 失败暴露英文异常/内部术语 | `src/gateway/models.py`、`src/gateway/diagnostic_bundle.py`、`src/gateway/identity.py`、`apps/console/console/src/lib/errorMessage.ts`、`apps/console/console/src/pages/*` | 失败路径改中性中文；新增一键诊断包；控制台文案去内部术语并加文案门禁 | `tests/gateway/test_failure_copy.py`、`tests/gateway/test_diagnostic_bundle.py`、`tests/gateway/test_account_wording.py`、`uiCopyGate.test.ts` | `1c802f17`、`8c04bbcc`、`b4406497` | **已完成** |
| R-15 | 安装器 D2–D5 与 D 盘默认路径 | `installer/liuhao.iss`、`installer/ChineseSimplified.isl`、`installer/assets/BEFORE-INSTALL.txt`、`packaging/stage_payload.py`、`src/upgrade/manager.py` | D 盘默认与不可用时回退、中文向导、未签名说明、D2 备份嵌套目录 | `tests/test_upgrade_apply_rollback.py`；**0.1.0 安装包构建于 11-06 17:29，早于本提交，未用新包复验** | `9e537633` | **待验证** |
| R-16 | 交付面文案与许可证随包 | `packaging/licenses/*`、`packaging/THIRD-PARTY-NOTICES.txt`、`packaging/make_release.py`、`docs/INSTALL-WINDOWS.md`、`docs/END-USER-GUIDE.md`、`installer/assets/BEFORE-INSTALL.txt` | 许可证随包、向导脚本名与路径口径统一、Key 指引与发布数字回填 | 许可证文件与 `packaging/stage_payload.py` 白名单核对；文档链接门禁 | `f54c14b1`、`013afd67`、`0e80117d`、`beffaf05` | **已完成** |
| R-17 | 长跑治理：审计增量校验与保留、目标归档、连接回收 | `src/kernels/audit/verification.py`、`src/kernels/audit/__init__.py`、`src/gateway/audit.py`、`src/gateway/ai_management.py`、`src/ai/providers.py` | 审计校验增量锚定、保留水位、终态目标迁 append-only 分片、provider 连接回收 | `tests/kernels/audit/test_audit_retention.py`、`tests/gateway/test_goals_archive.py`、`tests/test_ready_audit_evidence_consistency.py`；**修复后未重跑 2h soak** | `fd396351` | **待验证** |
| R-18 | 首启自检探针误判（E-18） | `src/upgrade/startup_probe.py`、`src/upgrade/selfcheck.py`、`src/upgrade/cli.py` | 健康路径多轮重试，兜底 `/openapi.json` 显式标记而非静默 | `tests/test_upgrade_selfcheck.py`（23 passed，自检沙箱） | `22c2e867` | **待验证** |

### 10.1 本轮仍为已知缺口（不得写成已完成）

| 编号 | 事项 | 现状 | 关掉标准 |
|---|---|---|---|
| G-1 | 安装包代码签名（E-8） | 无证书/签名流水线，Windows 仍提示「未知发布者」 | 签名校验通过；签名失败拒绝安装并留审计 |
| G-2 | 席位/授权（seat）类能力 | 本仓库无席位计费、授权数限制或并发席位实现，本轮未做、也未列入本轮范围 | 明确席位模型与授权校验点，并有对应回归 |
| G-3 | 仅 Windows x64 | 只交付 Windows x64 安装树；macOS/Linux 与 arm64 无产物（E-20 路径矩阵仍开放） | 目标平台产物 + 干净机安装复验 |
| G-4 | 真实远端升级源 | 升级/回滚只在本机安装树验证；C9 仍为「桌面分发形态差异」 | 真实 CD 源可拉取、可回滚并留存证据 |
| G-5 | 真实厂商 Key / Anthropic 原生端点（E-5） | 仍只有 OpenAI 兼容桩；Anthropic 原生端点未实网验证 | 真实 Key 跑通首条消息与失败分类 |
| G-6 | 手机真机（E-14） | 服务端 LAN 可达已验证，真机登录+发消息未验证 | 同一 Wi-Fi 真机完成一次登录+发消息 |
| G-7 | 8/16GB 低配与 24h 浸泡（E-21、F-1～F-6） | 仅 5 账号 / 2h soak；真睡眠/真拔线/真 ENOSPC 未验证 | 低配机 24h 浸泡 + 真实故障注入 |
| G-8 | 修复后未重跑 2h soak | R-17 的保留水位/归档只有回归测试，长跑曲线未复验 | 在 `b4406497` 上重跑 2h soak，p50/内存/DB 增长不随运行时长线性上升 |

**§10 四态统计（18 条）：已验证 7 条（R-01/02/03/06/07/09/11）、已完成 5 条（R-04/08/13/14/16）、待验证 6 条（R-05/10/12/15/17/18，其中 R-05/R-10/R-12 为回归红）；已知缺口 8 条（G-1～G-8）。**

### 10.2 发布回归（`b4406497`）红项与根因

`origin/p36` 推送后 CI run `42386438527` **失败**：两腿数字一致（`17 failed / 7705 passed / 40 skipped / 3 xfailed`），
其余 6 个下游门禁（Guardrails、Guardrails Extended、Security Scan、Linting、Observability、P0 Gate）按 CRITICAL-5 看门狗显式红，未出现「静默跳过」。
本地用同一 venv 复跑同批文件 **16 failed**（`test_chat_history` 单跑通过、全量顺序失败），确认**不是环境差异**。

| 编号 | 失败面 | 用例数 | 根因提交 | 根因（已定位到行级） | 修复方向（超出本轮 docs-only 边界） |
|---|---|---|---|---|---|
| F-01 | 升级状态/备份/自检落点 | 9 | `6a2e361e` | `tests/conftest.py` autouse fixture 无条件 `setenv("LIUHAO_DATA_DIR", tmp_path/data)`，升级器 `resolve_data_dir()` 优先读该变量，导致 `<app>/data/upgrade-state|upgrade-backups` 不再创建 | fixture 不应覆盖升级用例；升级测试应显式传入 data_dir，或 fixture 放行 `test_upgrade_*` |
| F-02 | profile API | 3 | `b4d4052b` | `src/gateway/chat.py:376/397/398` 直取 `assistant.provider.owner_principal/model`，测试 `FakeAssistant` 无该属性 | 改为 `getattr(..., None)` 兜底，或给 Fake 补 `provider` |
| F-03 | 会话列表全量顺序为空 | 1 | `e474b562` | 全量顺序下 `test_chat_history_endpoint_reads_persisted` 断言 `0 == 4`；单跑通过 → 用例间数据目录/会话键状态污染 | 迁移应幂等且按用例隔离；补重启后会话列用例 |
| F-04 | 部署门禁变量判定 | 2 | `e474b562` | 新增 `PERSONAL_CONTEXT_DB_PATH`（`src/gateway/account_memory.py:437`）未登记 `SEED_GATES`/`ALLOWLIST`；另有 `AUDIT_DB_PATH`/`LIUHAO_API_KEY_STORE`/`LIUHAO_ALLOW_SIMULATED_EXECUTION`/`LIUHAO_WORKSPACE_ROOT` 的 `SEED_GATES` 引用行号漂移 | 登记新变量并更新 `SEED_GATES` 引用行 |
| F-05 | 依赖诚实性 | 1 | `6a2e361e` | `tests/gateway/test_account_root_init_recovery.py` 以 UTF-8 BOM 开头（`EF BB BF`），`ast.parse` 报 `U+FEFF` | 去掉 BOM 重新保存 |

> 结论：**这是真回归，不是 CI 环境问题**。在 docs-only 边界内无法转绿；必须先修 F-01～F-05（涉及 `tests/`、`src/`），
> 再在新 hash 重跑两腿与全部下游门禁。当前 `b4406497` **不可作为交付冻结候选**。

**2026-11-08 复核（新 tip `416f7109`）**：`b4406497` 之后的 5 个提交
（`f7603a8e` 版本号 0.1.1、`d57fa52e`/`6b47829b`/`7c0a252a`/`416f7109` 四个 ISCC 编译修复）
**都没有触及 F-01～F-05 的根因文件**（`tests/conftest.py`、`src/gateway/chat.py`、
`src/gateway/account_memory.py`、`tests/gateway/test_account_root_init_recovery.py`），
因此这五类失败在新 tip 上**按预期仍在**——它们只能靠改 `tests/`/`src/` 关掉，而本轮被
「不改业务代码」的约束挡住。新 tip 的实测数字见 §11.2 与
[evidence/verify-full-416f7109-2026-11-08.md](evidence/verify-full-416f7109-2026-11-08.md)。

### 10.3 2026-11-08 新登记缺口（七项，逐项写死关掉标准）

> 只登记、不新开修复流。已登记过的项沿用原编号，不另起新号，避免同一件事两个身份。

| 编号 | 事项 | 现状（2026-11-08 实测/代码位置） | 关掉标准（可判真伪） | 原登记 |
|---|---|---|---|---|
| G-9 | **Gemini 真实成功调用未验证** | 只有失败路径证据：真实请求到达上游并回 `400 API_KEY_INVALID`，错误分类与中文文案正确；成功路径只有假客户端回归。`91be3d10` 已切 `google.genai`，但**没有一次带真实 Key 的成功回复** | 用真实 Gemini Key 完成一次 `POST /v1/chat` 并收到非空回复（留存时间戳/耗时/模型 id/回复摘要，不含 Key）；`known-gaps` 对应行改「已关闭 + 证据指针」 | 新增 |
| G-10 | **自定义 Base URL 的 SSRF 风险——书面结论已出（风险未消除）** | 2026-11-09 书面结论与实测：保存/测试/更新/运行时四路径共用同一目标校验——非 http(s) 400、解析到私网/回环/链路本地/保留/多播 400、带 Key 走 http 400、跨域重定向与探测期 DNS 漂移 400（`src/gateway/models.py:1128-1187`；探测重定向拒绝 `:987-1003`；行号按 2026-11-09 工作区代码，证据未绑定 HEAD）；回环只对 `provider=ollama` 或本地推理端口（11434/1234/8000/8080）放行。**DNS 重绑定只有代码级证据**（`src/ai/net_guard.py:131-180` 线程钉住解析），无真实 DNS 层实测；`provider=ollama` 时回环端口不限为已登记残余风险。证据 `docs/evidence/g10-ssrf-2026-11-09.md` | 补真实 DNS 环境端到端重绑定实测并留请求/响应；在此之前只写「已防护」、**不得写「风险已消除」** | 新增（细则见 [DELIVERY-CHECKLIST §7.2](DELIVERY-CHECKLIST.md#72-自定义-base-url-的-ssrf-风险结论待评估不写成已通过)） |
| G-3 | **仅 Windows x64** | 只产出 Windows x64 安装树；无 macOS/Linux、无 arm64；`E-20` 路径矩阵仍开放 | 目标平台产物 + 干净机安装复验 | §10.1 |
| G-4 | **无真实 CD / 无真实远端升级源** | 升级、回滚只在本机安装树（`c9-*`/`c9b-*` 证据）；**没有**任何一次从真实 CD 源拉取、校验签名、回滚的实跑。C9 = `NOT VERIFIED` | 真实 CD 源可拉取、可校验、可回滚并留证；否则 C9 只能是 `NOT VERIFIED` | §10.1 |
| G-6 | **手机未验证** | 服务侧 LAN `192.168.31.171:8000` 返回 200 已验证；真机打开/登录/发消息无设备、无证据 | 同一 Wi-Fi 真机完成一次登录 + 发消息并留屏/日志 | §10.1 |
| G-11 | **24h 浸泡进行中（未收口）** | 长跑 harness 正在跑（`--minutes 1440`，跨日才结束）；已有 2h soak 与 5 账号数字，**24h 尚未产出结论**；期间发生的 GC/水位/DB 增长曲线都还没有 | 24h 跑完并给出 p50/p95 延迟、RSS、审计库与 WAL 增长曲线，证明**不随运行时长线性上升**；中途崩溃也算不通过 | 新增（关联 E-21） |
| G-12 | **8/16GB 低配未实测** | 本机 32GB / 24 逻辑核；低配数字只有「不外推」的说明 | 8GB 与 16GB 各跑一轮首启 + 基础对话 + 30 分钟常驻，记录峰值 RSS 与是否触发 OOM/换页 | §10.1 G-7 |

**§10.3 统计：新登记 7 项（新增编号 G-9/G-10/G-11/G-12，沿用 G-3/G-4/G-6）；本轮关闭 0 项。**
「登记」不等于「可以忽略」：G-9 属**结论缺失**；G-10 **书面结论已出（2026-11-09）但风险未消除**（DNS 重绑定无真实 DNS 层实测）；
G-3/G-4/G-6/G-11/G-12 属**环境/形态缺口**。两类都在 §11.1 C 的「可带缺口发布」清单里，但必须随交付说明一起披露。

---

## 11. 新一轮冻结前置清单（2026-11-08）

> **代码 tip（本轮就绪判定的对象）= `8450009b`**（2026-11-09 05:26 `fix(cli): 启动脚本移除 8090 配对中继`；本表 2026-11-08 快照写 `416f7109`，此后 HEAD 已被其他线推进多轮，以 `git log -1` 实测为准）。**当前 tip 仍不是交付冻结 hash**——冻结 hash 由 0.1.2 冻结重打（r6）时确定。`416f7109` 时期「没有产出安装包」的注记也已过时：`dist/installer` 顶层现有 0.1.1 两件套（2026-11-08 构建），0.1.2 包待 r6。
> 验收细则（每条含 ① ② ③ 编号条件）在
> [DELIVERY-CHECKLIST §7](DELIVERY-CHECKLIST.md#7-新一轮冻结前置清单2026-11-07-起算)；
> 本表是一句话版，冲突时以细则为准。

| 编号 | 前置项 | 什么算完成（一句话） | 状态（2026-11-09 更新） |
|---|---|---|---|
| P-1 | 版本号 0.1.1 全链一致 | 源码/pyproject/frontend/iss 全是 0.1.1，版本一致性用例绿，**包名与 `/v1/health` 自报也是 0.1.1** | 「包是 0.1.0」已过时（2026-11-09 实测）：`dist/installer` 顶层现有 `LiuHao-Setup-0.1.1.exe`（29,567,136 字节）+ `liuhao-0.1.1-upgrade.zip`（2026-11-08 构建）。目标口径本身已推进：源码三处（`pyproject.toml:3`、`src/version.py:8`、`installer/liuhao.iss:25`）均已是 0.1.2，当前真实矛盾是「源码 0.1.2 vs 产物/清单 0.1.1」（`SHA256SUMS.txt`/`LIUHAO_RELEASE.json` 仍 0.1.1），0.1.2 包待 r6 出包 |
| P-2 | 安装包重打并绑定源码提交 | `LiuHao-Setup-0.1.2.exe` 四件套齐、SHA256 实测一致、`DELIVERY.txt` 的源码提交 = 冻结 hash、装一次首启 ready | 未完成（2026-11-09 复核）：`dist/` 下无任何 `*0.1.2*` 产物；installer 顶层无 `DELIVERY.txt`、未绑定源码提交；上一轮 r5 重打在发布包步骤失败（`freeze-0.1.2-r5\build.log`：`--strict-dev-paths` 命中 `scripts/issue_console_token.py:3-4` 两处字面量，exit 1），根因修复在工作区待提交，r6 前须先入库 |
| P-3 | 真 Key 对话验收 | 真实厂商 Key 首条消息成功 + 错误 Key 复现 401 且文案中性，原始输出入库 | 已完成（2026-11-08 安装版实测，2026-11-09 复核）：真实 DeepSeek Key 不重启即用，`POST /v1/chat` 200/1.90s，回环三写 `SECURITY_REJECTED=0`，10,018 文件 Key 明文扫描 0 命中（`dist/release-gate/smoke/20261108-real-key/CONCLUSION.md` S-06/S-07/S-08 全 PASS）；错误 Key 401 文案中性另有 2026-11-06 四类失败实测（`docs/evidence/failure-modes-2026-11-06.json`），2026-11-09 补「测试连接」`invalid_key` 分类本机桩复现（`docs/evidence/failure-invalidkey-2026-11-09.md`：归类/中性主文案/数据目录 Key 明文 0 命中三断言全过；该复现绑定 HEAD `8450009b`，仅覆盖 provider=openai 的 401，当前 HEAD `0c3d70ba` 未复跑），原始输出均已入库 |
| P-4 | 任意模型接入 | 自定义 `base_url` + 任意 `model` 保存即生效、能对话；非法地址报错可读 | 部分完成（2026-11-09 更新）：「在跑（工作区未提交）」前提已不成立（工作区未提交项实测 7 个）；保存即生效有安装级实证（real-key CONCLUSION §二，同 PID 不重启）；网关级自定义 ollama 配置 `qwen2.5:3b` 走真实 HTTP PASS（`docs/acceptance/butler-gateway-acceptance-2026-11-08.json`）；G-10 SSRF 书面结论已出（`docs/evidence/g10-ssrf-2026-11-09.md`：私网/链路本地实测 400、带 Key 必须 https、回环白名单放行、跨域重定向拒绝），但「DNS 重绑定被拒」只有代码级证据（线程钉住解析），真实 DNS 层实测仍缺，**按 §10.3 G-10 口径风险未消除** |
| P-5 | 管家页可用化 | 真实一句话 → 派工 → 汇总，失败分支中性可重试，有 goal id + 事件序列证据 | 已完成（网关级真模型，2026-11-08）：`docs/acceptance/butler-gateway-acceptance-2026-11-08.json` `verdict=PASS`，5 场景全 PASS——一句话派活（goal `c4c63683`，2 任务 completed，723 字结论带 4 条真实来源）、单独指派（192 落到指定员工）、并发峰值 2、失败如实（路径越界报错中性、artifacts 空）、结果格式；复跑文档 `docs/ACCEPTANCE-BUTLER-REAL-MODEL.md` |
| P-6 | 两腿回归 + 全部 CI 门禁在冻结 hash 上绿 | 两条工作流同 hash 全 success，两腿数字一致，Guardrails 契约探针真跑、Security Scan 未被上游阻断 | 未完成（判定对象已陈旧，需随新冻结 hash 更新）：所引 `b4406497` 的 CI `42386438527` 属旧 hash 的运行，磁盘无新 CI 凭证可推翻或确认；HEAD 现为 `0c3d70ba`（2026-11-10 实测），判定对象须等 0.1.2 冻结 hash 落定后在该 hash 上重跑 |
| P-7 | `--full` 在冻结 hash 的 clean worktree 上重跑 | 跑前 `git status` 干净，逐关数字与日志/JUnit 落盘，C7 若红单独 `--only C7` 定性 | 未完成（对象未定）：判定对象须等 0.1.2 冻结 hash；全量 pytest 本轮仍在跑（PID 6704/32668），结果未出，**不得写成已通过** |
| P-8 | 前端 dist 与源码同源 | 冻结 hash 上重建 `console/dist`，包内引用 hash 一致，登录首屏在 1280×720/1366×768 可见 | 部分（2026-11-09 复核）：0.1.2 包尚未产出（见 P-2），冻结 hash 上重建 `console/dist` 并核对包内引用 hash 的凭证本轮未取得 |

> **2026-11-10 实测事实**：主仓全量 pytest 本轮仍在跑（PID `6704`/`32668`，2026-11-09 09:00 启动），结论未出；P-6/P-7 的判定对象仍须等 0.1.2 新冻结 hash，**不得据此写成已通过**。

## 11.1 宣布 `RELEASE READY=True` 还差什么（必须 / 可以带缺口发布）

机器 `release_ready` 要求 **12 关全 `PASS`**，`FAIL`/`NOT VERIFIED`/`BLOCKED`/`ERROR` 任一项都让它是 `False`。

### A. 必须（机器口径——缺一项就永远是 False）

| 关 | 现状 | 变成 `PASS` 的唯一条件 | 可达性 |
|---|---|---|---|
| C9 Deployment / upgrade / rollback | `NOT VERIFIED`（本轮 worktree 无 `.venv`，checker 只能走 `Dockerfile + alembic` 兜底分支） | 在 `.venv` 存在的 checkout 上跑，`scripts/verify_deploy_readiness.py` 退出码 0 且无 `FAIL` 行（docker CLI + compose v2 + alembic 齐备），并把真实升级/回滚链在该环境复跑 | **可达**（环境/形态问题，不是产品缺陷） |
| C11 HC-01 | `BLOCKED` | 只有**新 ADR** 改变 HC-01 的机器口径才可能（例如给 C11 一个可机器判定的替代口径，并把 HC-01 移出冻结门集合） | **当前不可达**（`check_hc01` 恒 `BLOCKED`；ADR-HC01 §4.3 禁止为变绿改口径） |

> **所以：在 ADR-HC01 仍有效、`check_hc01` 仍按冻结门打印 `BLOCKED` 的前提下，`RELEASE READY=True`
> 是结构上不可达的。**本轮只把 C11 的**文案**改对（明写「裁决已记录、门按设计冻结」），
> 没有把状态改成 `PASS`——那会把治理门洗成绿灯。

### B. 必须（交付口径——机器 verdict 不变，但决定敢不敢对外发布）

P-1 版本号全链一致、P-2 安装包重打并绑定 hash、P-3 真 Key 验收、P-4 任意模型接入、
P-5 管家页可用化、P-6 两腿 + CI 绿、P-7 `--full` 凭证绑定 hash。

### C. 可以带缺口发布（登记 + 披露即可，不阻塞 True）

| 缺口 | 编号 | 可带的原因（一句话） | 必须怎么披露 |
|---|---|---|---|
| Gemini 真实成功调用未验证 | G-9 | 失败路径是真实到达上游的证据，成功路径有假客户端回归；缺「有 Key 成功一次」 | 交付说明写「Gemini 仅验证失败分类，成功路径未验证」 |
| 自定义 Base URL 的 SSRF | G-10 | 书面结论已出（2026-11-09）：私网/链路本地/带 Key http/跨域重定向实测 400，回环白名单放行；DNS 重绑定仅代码级证据，**风险未消除** | 交付说明用 `docs/evidence/g10-ssrf-2026-11-09.md` §4 披露文案：「DNS 重绑定防护已在线程级固定解析结果，但尚未在真实 DNS 环境做端到端重绑定实测；将模型类型设为 Ollama 时允许指向本机任意端口，请仅对可信的本机服务使用」 |
| 仅 Windows x64 | G-3 | 交付形态就是 Windows 桌面安装树 | 系统要求写 Windows 10/11 x64 |
| 无真实 CD | G-4 | 桌面分发形态差异；安装树升级/回滚已验，但**不等于**真实 CD 已验 | `RELEASE READY=False` 的唯一形态理由，不得写成「已全部验证」 |
| 无真实远端升级源 | G-4 | 升级源只有本机安装树 | 升级说明写「远端源未接入」 |
| 手机未验证 | G-6 | 服务端 LAN 可达已验，缺真机 | 手机访问写「实验性，未真机验收」 |
| 24h 浸泡进行中 | G-11 | 2h soak 已验，24h 在跑未收口 | 性能数字标注「2h 实测，24h 未完成」 |
| 安装包未签名 | G-1 | 只有「未知发布者」提示 | 安装说明保留未签名步骤 |
| 8/16GB 低配未实测 | G-12 | 本机 32GB，数字不外推 | 内存要求标注「未在低配实测」 |

**一句话结论**：机器 `True` 卡在 C9（可达）与 C11（需新 ADR）两条口径上；**交付**能不能发布
卡在 P-1～P-7；其余九项属于「登记 + 披露即可发布」的缺口，一条都不许写成「已全部验证」。

## 11.2 新 tip（`416f7109`）的全量凭证

| 项 | 事实 |
|---|---|
| 判定对象 | `416f7109`（代码 tip；`origin/p36` 在合并前的 tip） |
| worktree | `D:\copilot-worktrees\liuhao-delivery-416f7109`（pin 到 `416f7109`，跑前 `git status --porcelain` 为空） |
| 解释器 / 命令 | `.venv` Python 3.12.14 / `python -u scripts/verify_readiness.py --only C1,C2,C4,C5,C6,C7,C8,C9,C10,C11` |
| 机器 verdict | `PASS=7  FAIL=0  BLOCKED=1  NOT_VERIFIED=2  ERROR=0` → **`RELEASE READY = False`** |
| 逐关数字 | [DELIVERY-CHECKLIST §5.12](DELIVERY-CHECKLIST.md#512-新-tip-416f7109-的就绪凭证2026-11-08) 与 [evidence/verify-full-416f7109-2026-11-08.md](evidence/verify-full-416f7109-2026-11-08.md) |
| C3 / C12 | **本轮未取得凭证**：三次全量尝试都被本机并发清理杀掉（JUnit 0 字节 → 按完整性规则不能读成零失败）。上一可比凭证：CI `b4406497` = `17 failed / 7705 passed / 40 skipped / 3 xfailed`，失败集合 F-01～F-05；F-01～F-05 未修，C12 必红 |
| C7 | 两次：**1057.0** 与（单独重跑）**1380.1**，阈值 1503.5（基线 2004.7，容差 25%）→ `NOT VERIFIED`（负载不可判，不是回归） |
| C9 | `NOT VERIFIED`（worktree 无 `.venv`，落 `Dockerfile + alembic` 兜底；`real CD absent=True`） |
| C11 | `BLOCKED`（状态不变）；本轮只改证据文案，见 ADR-HC01 §1.2 的 2026-11-08 补充 |
| 文档提交 | `576f46b6`（+ 本次补正提交），新 tip = 该提交 |

**纯文档校验（提交前实跑，原文）**：

```text
$ git diff --name-only 416f7109..HEAD -- src/ apps/ contracts/ installer/ packaging/
(无输出)
```

即：`416f7109` 之后只有文档、证据与就绪脚本（`scripts/verify_readiness.py` 的 C11 文案与
`CONDITION_META`）变化，`src/ apps/ contracts/ installer/ packaging/` **零改动**。
`scripts/` 不在上面五个受保护目录内，属本轮明确允许的「就绪脚本判定口径」改动。



---

## 12. 收尾进度与未提交区（2026-11-08 实测）

> 本节是我方收尾线的实测快照；上游 `origin/p36` 的 §10（本轮修复登记）与 §11（冻结前置）
> 已原样保留在前，两者不互相覆盖。表中「29 项 / 36 项」均为当时快照，当前未提交项以
> `git status` 实测为准（软测批 11 个文件仍按裁决冻结，等 ④ 落地）。


### 12.1 已入库：`apps/console` 72 项（六批）

| 批次 | 提交 | 内容 | 文件数 | 本批验证 |
|---|---|---|---|---|
| 1 | `4559a23b` | 升级面板 + upgrade 封装 + api/contracts | 7 | 18 passed |
| 2 | `b44336bf` | 知识库 + `Settings.tsx`（整文件：升级入口 + 只读向量卡） | 6（+688/−60） | 21 passed |
| 3 | `ccb7df3b` | 账号鉴权 UI（会话能力/切换/成员/身份/目录） | 13（+348/−94） | 26 passed |
| 4 | `9afd9ea9` | 诊断导出、图表、概览口径 | 13（+402/−44） | 35 passed |
| 5 | `4842e2cf` | 项目/备份/恢复/首启/其余页面 | 15 | 20 passed |
| 6 | `aa881ca0` | 构建配置 + 跨模块门禁测试 | 18 | 90 passed |

收工全量（六批全部落地后）：`npx tsc -b` **0**；`npx vitest run` **49 文件 / 505 条全过**；
`npm run lint` **0 error / 39 warning**（`[toFixed] OK`）；`pytest tests/test_import_integrity.py` **2 passed**。

### 12.2 未提交：29 项（19:52 实测，流式线仍在写）

| 归属 | 项数 | 文件 |
|---|---|---|
| 流式线 `a81d1ecb` | 24 | 生产：`App.css`、`ChatPanel.tsx`、`chatClient.ts`、`src/ai/liuhao.py`、`src/ai/providers.py`、`src/ai/tools/kernel_tools.py`、`src/gateway/chat.py`、`src/tasks/reminder_tool.py`；既有测试 8 个（`test_execution_loop_e2e`、`test_conversation_store`、`test_economy`、`test_liuhao_assistant`、`test_liuhao_tools`、`test_local_tool_execution`、`test_model_config`、`test_providers`）；新增测试 8 个（`test_stream_cancel_audit`、`test_tool_error_feedback_humanized`、`test_tool_prompt_contract`、`test_sse_bypasses_gzip`、`test_stream_disconnect_releases_lock`、`test_stream_done_frame_on_error`、`test_reminder_fire_chain`、`test_reminder_tool_contract`） |
| 软测线 | 4 | `src/gateway/health.py`、`src/gateway/main.py`、`tests/test_gateway_health_wiring.py`、`docs/operations/soak-report-2026-11-08.md` |
| 一致性 | 1 | `tests/consistency/`（内含 `test_crash_consistency.py`、`_crash_child.py`） |

**为什么从 22 变 29**：19:39–19:51 之间流式线又写了 `App.css`、`main.py` 和 5 个新测试；
19:51 仍有文件落盘，说明**尚未停写**。停写回执到达前不提交、不对账、不打包。

**19:58 复核（数字继续在动，只作快照）**：流式线又改了 `ChatPanel.test.tsx` / `chatClient.test.ts`；
知识库线落下 `src/ai/tools/builtin.py`、`src/api/routes/knowledge.py`、`src/knowledge/documents.py`；
软测线落下 `src/gateway/rate_limiter.py`（新根因）、`src/gateway/ai_management.py`。
除本表 3 份文档外，未提交项已到 **36**。**以停写回执为准，不以这张表为准。**

### 12.3 两个交付节点（详见 [DELIVERY-CHECKLIST.md](DELIVERY-CHECKLIST.md) §0.3）

| 节点 | 门槛 | 明确缺什么 |
|---|---|---|
| **A. 安装冒烟包**（内部，不对外） | 流式线停写并逐文件对账、其文件已提交；打包前置「必须清零项」清零 | 满载僵死第二条根因（限流中间件在事件循环上同步查 JWT 撤销库 SQLite）修复；`health.py` / `test_gateway_health_wiring.py` / `soak-report`；`tests/consistency/` 凭证；30 分钟满载窗口结论 |
| **B. 终版** | 三条修复入库 + **30 分钟满载窗口达标** + `tests/consistency/` 连跑 3 次全过 + preflight 达标（15 PASS / 2 SKIP / 0 FAIL） | 无；不达标则只出冒烟包，不出终版（红线） |

**口径作废**：「满载僵死已修复」不成立。软测第三次 21.8 分钟又僵死、6 分 41 秒不自愈；
8 小时数据不存在（`rss8h5` 19:46 被终止，实际只有 22 分钟）。终版门槛里**旧成绩单不算数**，
新根因修完必须重跑 30 分钟窗口。

### 12.4 `tests/consistency/` 判定条件（记死）

**宿主 CPU 连续 2 分钟 <15% 才开跑；连跑 3 次全过才算过；1 次红按真问题查，
不许写成「环境抖动」后放过。** 未满足前不提交。
