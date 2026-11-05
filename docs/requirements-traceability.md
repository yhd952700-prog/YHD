# 需求追溯表（Requirements Traceability）

> 用途：把用户历次说过的话逐条落到「哪一路 / 哪个模块 / 哪段代码 / 什么证据」，防止并行开发时需求静默消失。
>
> 状态口径（与验收标准一致，不允许含糊）：
> - **已完成**：有可运行代码 + 真实链路/真实运行验证（不只是单测）。
> - **部分完成**：代码或文档已存在，但缺真实链路验证、未提交、或只覆盖一部分。
> - **未验证**：实现可能在，但本次没有实际跑通证据。
> - **延后**：明确不做或排到后续，原因写在备注。
> - **未做**：仓库中找不到实现。
>
> 本表为汇总方填写，事实以仓库实际状态与 commit 为准；各流仍在改代码，收尾时会再刷新一次。
> 最后刷新：2026-11-06（`origin/p36` 已推至 `a5508a2a`；本路随后提交 `c13fd026`）。
> 本轮刷新：新增「30 方向逐条状态」全表（T-11 的细化）；更新 T-14（本轮推送与
> CI 红点收敛过程）、T-15（门禁 ref 被他人流改漂后重新对齐）、C-5（用例簇修复与
> 归属，含 policy 端点面 4→5 的裁决 `c13fd026`）。**仍标「部分完成」的，都是只有
> 单测没有真实链路，或代码尚未提交。**

| 编号 | 要求（用户视角） | 落到哪一路 / 模块 | 关键代码或文档路径 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|---|
| T-01 | 填 API Key 就能接入模型，且不用重启后端 | 模型管理（gateway + ai） | `src/gateway/models.py`、`src/ai/model_config.py`、`src/ai/providers.py` | 部分完成 | `src/gateway/models.py:1` 明确「保存即生效（无需重启）」；`src/ai/providers.py:907`、`:979` 按配置逐次构造 provider；测试 `tests/test_gateway_models_api.py`、`tests/test_model_config.py`、`tests/test_providers.py` | 代码链路与单测在；本轮未用真实 DeepSeek Key 重跑端到端，故不标已完成 |
| T-02 | 只要一个能下载、能安装、填 Key 就能用的成品 | 打包 / 安装（安装器流） | `installer/liuhao.iss`、`packaging/build_windows_installer.ps1`、`packaging/stage_payload.py`、`scripts/first_run_wizard.py`、`scripts/launch_installed.py` | 部分完成 | 安装器为 Inno Setup 脚本（`installer/liuhao.iss:27-40`）；首次运行向导已提交（`scripts/first_run_wizard.py` + `tests/test_first_run_wizard.py`） | `installer/`、`packaging/` 当前**未被任何 commit 跟踪**；未产出安装包、未在干净 Windows 机器上实测安装 |
| T-03 | 不足的地方由我们自行判断并完善，不推回给用户决定 | 全部工程流 | 各流 commit 与 `docs/known-gaps.md` | 部分完成 | 并行流持续提交修复（如 `230b6e25`、`db0db76c`、`d768fa56`）；延后项改为登记而非推回用户 | 属工作方式，无单一交付物；以「不阻塞用户、问题自行收口」为准 |
| T-04 | 产品能自己修改代码 / 自我升级 | 进化（暂无归属流） | 仓库内无实现 | 未做 | 全仓库检索 `self[-_]update` / `self[-_]upgrade` / `auto[-_]update` / `自升级` 无任何实现命中 | 高风险能力，延后；需安全闸门、签名校验、回滚与审批，详见 `docs/known-gaps.md`。**本轮工作树出现未提交的 `src/upgrade/`（他人流在建），未提交/未验收不算实现** |
| T-05 | UI 增「鎏灏管家」：一句话吩咐 → 自行安排员工干活 → 汇总结果；也可单独指派员工 | 控制台 + 执行内核 | `apps/console/console/src/components/Shell.tsx`、`apps/console/console/src/lib/nav.ts`、`src/kernels/execution/__init__.py`、`src/gateway/ai_management.py` | 部分完成 | 「管家」目前只是对话/工作台入口（`Shell.tsx:161-166`，`nav.test.ts:6` 锁定不被精简）；执行内核有 `_assign_agents`（`src/kernels/execution/__init__.py:634`）；员工管理 API 在 `src/gateway/ai_management.py:710-767` | **缺**「一句话 → 自动建目标 → 分派 → 汇总回话」的管家编排与汇总界面；现有是用户分别操作目标/员工 |
| T-06 | 界面科幻、未来风；品牌条款不许改 | 控制台视觉（前端流） | `docs/ui-scifi-direction.md`、`docs/ui-reference-terse.md`、`apps/console/console/src/os.css` | 部分完成 | 视觉规范文档已写（深色主基调、`--sc-*` 令牌、品牌不复制第三方资产）；主题令牌已在 `os.css:17-79` 落地 | 未做整站视觉走查；无「品牌条款不可改」的自动化校验 |
| T-07 | 独立代码中发现的隐患要处理完善（不是只写报告） | 各工程流 | 最近提交 | 部分完成 | `230b6e25`（焦点双环/AA 对比度）、`db0db76c`（手机底栏补管家入口）、`d768fa56`（中文否定审计措辞）、`6a6060ae`（测试身份隔离） | 仍有已知缺口：006 迁移 SQLite-only、扫描器 `exit-code 0`、CI 仅 SQLite 路径等 |
| T-08 | 能并行的任务就并行，别串行磨时间 | 编排（当前为人工多流） | 8 条工程流并行；CI job 并行 | 部分完成 | 本轮同一仓库 8 条流并行改代码；`.github/workflows/ci.yml` 多 job 并行 | 缺工具层面的自动分片/并行调度；本次靠人分流 |
| T-09 | 全量开源资源系统性研究：多平台多领域，记录地址/版本/许可证/依赖/维护/安全；未验证必须标注 | 开源情报流 | `docs/oss-radar/`（8 份，约 395 KB）、`oss-registry.yaml` | 部分完成 | 8 份文档覆盖安全合规、供应链、质量成本、工程品质、数据网络、Agent 编排、UI 交互、记忆检索（文件大小 36–70 KB/份），**本轮已随文档提交入库**；`oss-registry.yaml` 已跟踪并有 `tests/test_oss_registry_used_field.py` | 未证明逐项核对过版本/许可证/维护状态；「未验证」标注只做了 lint 层面保证，内容抽查未做 |
| T-10 | 每个候选回答九问（是否已有同类能力/不足/能解决什么/源码借鉴/能否封装/是否破坏接口/许可证/安全维护成本/集成与回归测试） | 开源情报流 | `src/intel/proposals.py`、`src/intel/capabilities.py`、`docs/oss-radar/*.md` | 部分完成 | 提案生成器带免责声明（`src/intel/proposals.py:13-14`）；能力对照在 `capabilities.py` | 九问是否逐条落表未验证；多数候选文档未确认九问齐全 |
| T-11 | 30 个方向 + 用户没想到的方向，外加五大战略中心 | 战略/架构流 | `docs/strategy-30-directions.md` | 部分完成（研究完成，工程未完成） | `strategy-30-directions.md:11-18` 逐方向判定：**真有 8**（1、8、16、22、23、25、29、30）、**半成品 11**（2、3、7、13、17、20、21、24、26、27、28）、**假实现 4**（5 多模态、6 电脑操作、18 教育、19 创意）、**无实现 7**（4 科研、9 情报平台、10 数字孪生、11 IoT、12 机器人、14 空间智能、15 工业自动化）；逐条证据见 `:48-86` 矩阵 | 研究已完成且给出完成度；「真有」只是静态代码判定，报告 `:224-233` 明确未跑运行时 E2E，不能当已完成 |
| T-12 | 独立内部模块 LIUHAO Open Source Intelligence（发现分类/跟踪版本/许可证与依赖漏洞/找缺口/生成方案/自动测试/记录来源） | 开源情报模块 | `src/intel/`（models/github_client/scanner/licenses/proposals/report/store/capabilities/__main__） | 部分完成 | 已入库（`0535d321`）；CLI 入口 `src/intel/__main__.py`，测试 `tests/test_intel_flow.py`、`tests/test_intel_licenses.py`、`tests/test_intel_store.py`；只发现不建议（`src/intel/__init__.py:3-18`） | 未接入 gateway；未用真实 GitHub API 跑过一次完整扫描（无运行证据）；不接控制台是战略结论（`strategy-30-directions.md:184`） |
| T-13 | 边界：尽可能广泛发现、严格筛选、只吸收有价值且合法兼容的 | 开源情报模块 | `src/intel/licenses.py`、`oss-registry.yaml` | 部分完成 | `licenses.py:143-161` 按 runtime/build/tool 场景判定；runtime 下 GPL/AGPL/unknown 一律拒绝；测试 `tests/test_intel_licenses.py:42-104` | 「有价值」评分与人工复核流程未实现 |
| T-14 | 推送 origin、修 CI（原任务书第 10、11 条） | 交付/CI（本路） | `.github/workflows/*.yml`、`scripts/verify_readiness.py`、`scripts/verify_image_build.py`、`Dockerfile` | 部分完成 | 已推送 `af5b0989..230b6e25`、`230b6e25..5a4576df`；本地历史被其他流改写后，用**合并提交** `4b57c9fb` 与 `origin/p36` 对齐（fast-forward 推送，**未强推、未 reset**），随后推送修复 `7d4d9aa0`。三个 CI 红已定位并修：①readiness C8 因指标端点改为 fail-closed 而 403 → 校验器带 token 抓取，**本机实跑 200 / 10027 bytes / 48 families**；②两条单测红（`test_full_repo_surface_has_no_uncaveated_claims`、`TestIgnoredPathsAreDeterministic`）→ 补 caveat + 去掉漂移 ref，**复跑 5 passed**；③Build Verification 因镜像内 `/app/logs` 不存在（`PermissionError`）→ Dockerfile 预建并 chown，另给镜像补 `LIUHAO_JWT_REVOCATION_DB` 落 `/app/data`；④Semgrep 作业装完即崩（`mcp` 2.x）→ 钉 `mcp<2`；⑤架构门禁两条**真实命中**：内核 `src/kernels/execution/__init__.py:1511` 反向 import `src.ai`（已把工作区根解析移入 `src/artifacts/index.py`）、`src/gateway/knowledge_context.py:209` 在 except 里 return True（已改为不把 return 放 except，语义不变）。**本地实测**：`semgrep` 0 findings（5 rules / 594 files）；`tests/test_execution_tool_bridge.py` 8 passed；`tests/artifacts + tests/kernels` 1430 passed, 20 skipped, 3 xfailed。Build Verification 在 `7d4d9aa0` 已转 **success**。本轮继续推送 `202f2b29`（三条用例簇修复）与 `e18169e6`（guardrail 收口：GATE-COUNT-MARKER 77→78；新增 `scripts/verify_first_run_e2e.py` 的接线，登记为 scheduled-only 并落在 `.github/workflows/first-run-e2e.yml`，缺 Key 时该工作流**红灯而非假绿**；`TestRefWiring` 复跑全绿） | `doc-audit-claim-lint` 在 `4b57c9fb` 上 success；主 CI 在 `e18169e6` 上仍红，但只剩 **C-5 里两条他人流的用例**（DBOS 硬杀、桌面 runner）；隐患 C-1/C-2/C-3 仍未修（见下表）。**最新 CI（run `42127243953`，HEAD `1dab3395`）**：4 条真实失败全部属他人流——桌面 runner、DBOS 硬杀（`exit 17 vs 1`）、`tests/test_console_invariants.py::test_api_uses_the_shared_error_reader`（console 域）、`tests/test_upgrade_selfcheck.py::test_unreadable_database_fails_the_check`（upgrade 域，提交态失败）；其余 Security / Observability / Linting / P0 / Guardrails Extended 均为 CRITICAL-5 watchdog 连带失败，不是新增问题。本路改动（policy 端点登记 + 文档）**零失败** |
| T-15 | 11 个门禁变量按「安全优先」默写 | 配置/门禁（本路） | `scripts/verify_deployment_gate_vars.py`（`SEED_GATES`/`safe` 规则）、`docker-compose.prod.yml:37-127` | 已完成 | 门禁脚本实际运行 **exit=0**（`RESULT: PASS`，11+2 个变量全部解析）；`TestSafeValueLock` 实跑 **7 passed / 37 deselected / 6.07s**，锁定生产值（模拟执行=0、围栏=on、cap=4、路径都在 `/app/data`）；另有一条真实运行断言复跑通过（`TestIgnoredPathsAreDeterministic` 5 passed）。本轮他流改动了 `src/gateway/ai_management.py`，门禁 ref 从 `:214` 漂到 `:266`，重新对齐后 **5 passed（53.63s）**，脚本 `RESULT: PASS`、exit=0 | 安全值只在生产 bundle 上判定；staging 允许宽松（设计如此）。路径另用 Dockerfile 的 `mkdir -p /app/data` 与 console dist 校验。**没有为让门禁变绿而调低任何阈值** |
| T-16 | Windows 兼容 / pytest 超时 / etcd 健康检查 | 平台兼容（本路） | `tests/`、`scripts/verify_readiness.py`、`.github/workflows/ci.yml`、`Dockerfile` | 部分完成 | BOM 致 `ast.parse` 失败已修（`19fadf35`）；etcd 健康检查用 `bitnamilegacy/etcd:3.5.9` + 真实 `etcdctl`（`ci.yml:45-60`）；**慢用例实测**：4 个 sleep/超时相关文件全跑 **117 passed, 1 skipped, 40.27s**，最慢 3.75s（`--timeout=60` 下无逼近用例，无需调大超时）；Windows 侧另修镜像内 `/app/logs` 缺失导致的启动即崩（本轮） | **etcd 未验证**：本机无 docker daemon、无 etcd，只能标「需 CI 环境验证」（不假装通过；本轮复核：本机无 `etcd` 二进制，`docker version` 报 `failed to connect ... dockerDesktopLinuxEngine`，daemon 未运行 ⇒ 确无本地验证条件）；2026-11-06 复扫 `src/ scripts/ config/ tests/` 的 `.py/.bat/.ps1/.cmd/.json/.yaml/.toml` 首三字节：**BOM_FILES=0**；`packaging/build_windows_installer.ps1` 保留 UTF-8 BOM 是 PowerShell 5.1 承载中文所必需，非缺陷；Windows 路径/编码仍需继续抽查 |
| T-17 | 手机端实测（同一 Wi-Fi 打开 → 登录 → 发消息） | 运行验证（本路） | `docs/mobile-access.md` | 未验证（仅服务侧已验证） | 服务侧实测：`127.0.0.1:8000` 与 LAN `192.168.31.171:8000` 均返回 200；默认路由 `Get-NetRoute 0.0.0.0/0` → WLAN；虚拟网卡 `172.31.48.1`(WSL)、`172.25.48.1`(Default Switch)、`169.254.*` 已识别并排除 | **手机侧打开/登录/发消息未验证**（无设备）；已记录可信的登录阻塞：`LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 未设置 ⇒ 所有人身份行被拒 ⇒「无人可登录」，附启动日志原文与处理步骤 |
| T-18 | 延后项不得消失，必须登记 | 文档（本路） | `docs/known-gaps.md` | 部分完成 | 已有清单与 semgrep 缺口（`ca900b99`）；本轮扩充为 D-1～D-10 + 红线清单 + 不做清单 | 见 `docs/known-gaps.md` 的「已明确延后、不得遗忘」 |
| T-19 | 知识闭环：知识入库 UI + 检索 + 对话注入（方向 3） | 知识 / 控制台 / 对话 | `apps/console/console/src/pages/Knowledge.tsx`、`src/gateway/chat.py:159,185`、`src/gateway/knowledge_context.py`、`src/knowledge/` | 部分完成 | `69db011e` 知识中心可用的导入/文档/分块预览/检索测试；`79bd69c8` 文档台账+完整分块+事务删除+上传+异步任务；`095ecdb9` 相关性门限与拒答；`8858c77e` 无相关内容跳过注入；`82404fdb` 拒答契约测试；`chat.py` 通过 `build_knowledge_context` 注入，带 `use_knowledge` 开关 | 代码链路已在；**未跑真实 embedding provider 下的 E2E**（上传→切分→向量→对话引用来源），「未配置时 503」也未实测。`tests/gateway/test_chat_knowledge.py` 复跑 **20 passed**、`tests/knowledge/test_documents_admin_api.py` **17 passed**（此前 422 vs 400 的失败已由知识流收口）。触发条件 K-1 |
| T-20 | 进程内排程器：定时/循环 Goal（方向 8/20） | 目标执行 / 任务调度 | `src/tasks/`（`3bda8c7d`：进程内 SQLite 排程器、重试退避、死信、重启恢复） | 部分完成（代码入库，E2E 未验证） | 代码 `src/tasks/local_scheduler.py`；单测 `tests/tasks/test_local_scheduler.py`、`tests/tasks/test_taskiq_queue.py` | **只有单测，没有真实定时 Goal 跑满周期 + 重启恢复的验证**；也尚未接 Goal 创建入口。不引入 Redis/外部队列。触发条件 K-2 |
| T-21 | 备份/恢复/导出一键化（方向 28/23） | 运维 / 设置页 | `src/cli/backup_commands.py`（`dcf025c3`）、`scripts/ops/backup.py:11`（自述无生产调用方） | 部分完成（CLI 真了，一键化未做） | `dcf025c3`：CLI 备份命令改为操作真实归档（SQLite `VACUUM INTO`、元数据随包）；`scripts/ops/backup.py` 仍有「no production caller wired」声明 | 无设置页入口、无恢复流程、无版本兼容校验 ⇒ 不标完成。触发条件 K-3 |
| T-22 | 产物索引 + 目标→文件回溯（方向 26/7） | 执行 / 文件 | `src/artifacts/`（`c14ab20c` 索引 CLI/报表；`82f95450` 接入执行链，可选、默认不变） | 部分完成（代码入库，无 UI/权限过滤） | 代码 `src/artifacts/index.py`；单测 `tests/artifacts/test_artifact_index.py`、`test_artifact_report.py`、`test_artifact_wiring.py` | 未做：按权限过滤（越权不可见）、控制台入口、执行回放界面。触发条件 K-3 |
| T-23 | 明确不做清单（电脑自主操作 / 任意仓库改码提 PR / 金融交易 / 外部 KYC / 全网常驻采集 / 机器人 IoT 工业执行） | 战略（决策） | `docs/strategy-30-directions.md:188-220` | 延后（明确不做） | 分节理由：物理硬件依赖、高风险强监管、个人单机无日常价值、与产品形态冲突 | 不是「以后做」，是「不做」；重新捡起必须先改本节并给出安全/合规前提 |
| T-24 | 五大战略中心的决策：只做信息架构、命名与权限边界，不拆五套后端 | 战略 / 架构 | `docs/strategy-30-directions.md:42-46`、`:87-147` | 已完成（决策） | 理由：1 进程 1 主库 + 至少 9 个 SQLite，与「双击即用、零运维」冲突；中心边界不干净（知识被执链消费、安全拦执行、进化评估执行） | 若未来改为多服务，必须先解除「零运维」承诺并给出迁移与运维方案 |
| T-25 | 文档说的事和程序做的事必须是一件：`openapi.yaml` 以运行时真实路由为准，且错误码与代码一致 | 网关契约 / CI | `openapi.yaml`、`scripts/verify_openapi_contract.py`、`.github/workflows/ci.yml:410-411` | 已完成 | `fd0d006f`：补齐 **15 个 missing path + 2 个 method（GET `/knowledge/documents`）** 与 5 个缺失 schema，运行时 **89 paths / 0 ghost / 0 missing**；核对 `assign`/`retry`/`pending` 的 404 `agent_not_found`、409 `agent_unavailable`、409 `task_not_retryable`、409 `goal_running` 与代码一致；校验脚本已在 CI 作为**必过**步骤 | 手工补写的响应说明由合并保留（`--write` 会重排/丢说明，超集合并脚本见报告）；若他流改路由后 CI 该步骤会转红，必须重新对齐而不是跳过 |

---

## 30 方向逐条状态

> 来源：`docs/strategy-30-directions.md` 第 1 节矩阵（该文档逐行给出实现路径与判定）。
> **该文档自述未运行服务、未跑测试、未做 E2E**，所以「真有」只代表静态可追到生产调用链。
> 本表按此口径保守登记：真有/半成品一律记「部分完成」，假实现记「未做（占位）」，
> 无实现记「未做」。矩阵行号 = `53 + 方向编号`（第 1 行 54，第 30 行 83）。

| # | 方向 | 战略判定 | 关键真实路径 | 来源 | 登记状态 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 通用智能与认知架构 | 真有（工程能力） | `src/ai/liuhao.py`、`providers.py`、`model_router.py`、`agent_runtime.py` | `strategy-30-directions.md:54` | 部分完成 | 有生产调用链；「统一认知架构」理论层不存在，也不新造 |
| 2 | 多智能体协作 | 半成品 | `src/ai/collaboration.py`、`committee.py`、`blackboard.py`；生产侧仅单员工 resolver | `:55` | 部分完成 | `committee`/`blackboard` 无非测试引用，多智能体编排未接线 |
| 3 | 长期记忆与知识图谱 | 半成品 | `src/kernels/memory/`、`src/knowledge/{documents,vector_store}.py`、`conversation_store.py` | `:56` | 部分完成 | 记忆/RAG 后端真；KG 未接线；真实 embedding E2E 未验证（见 T-19） |
| 4 | 自主研究与科学发现 | 无 | 全仓无论文/假设/实验闭环 | `:57` | 未做 | 需常驻外部论文源，不做 |
| 5 | 多模态智能 | 假实现 | `src/ai/perception.py` 仅文本，图像/音视频为显式 not implemented | `:58` | 未做（占位） | `plugins/{image,audio,video}` 只是本地文件处理且未加载 |
| 6 | 电脑自主操作 | 假实现 | `src/automation/`（唯一后端 `backends/fake.py`）、`src/ai/host_command/` | `:59` | 延后（明确不做真实后端） | 接线点真实但三闸默认关闭 ⇒ 默认可用工具数 0；见「明确不做清单」 |
| 7 | AI 软件工厂 | 半成品 | `src/ai/tools_local.py`、`src/plugins/sandbox/.../restricted_python_backend.py` | `:60` | 部分完成 | 仅受限 Python + 工作区写文件；无仓库编辑/测试运行/git/PR |
| 8 | 自动化工作流 | 真有（手动链） | `src/ai/goal_task_graph.py`、`agent_runtime.py`、`src/gateway/ai_management.py` | `:61` | 部分完成 | 手动执行链真实且有控制台表单；**缺排程**（见 T-20） |
| 9 | 全球信息情报 | 无（产品层） | 顶层 61 个 `plugins/` 运行时不加载；无调度器 | `:62` | 未做 | 只做本地订阅摘要的设想也未实现；不做情报平台 |
| 10 | 数字孪生与仿真 | 无 | `src/` 无仿真引擎 | `:63` | 未做 | 明确不做 |
| 11 | 物联网 | 无 | `src/` 无 MQTT/Modbus/OPC-UA | `:64` | 未做 | 明确不做 |
| 12 | 机器人与具身智能 | 无 | 无 ROS/rclpy/运动控制 | `:65` | 未做 | 明确不做 |
| 13 | 云端与边缘计算 | 半成品（部署物料） | `deploy/cloud/`、`docker-compose.yml`、`Dockerfile*` | `:66` | 部分完成 | 默认形态不依赖 Docker；仅作可选出口 |
| 14 | 空间智能 | 无 | 无 GIS/点云/3D 场景 | `:67` | 未做 | 明确不做 |
| 15 | 工业自动化 | 无 | 无 PLC/SCADA/产线协议 | `:68` | 未做 | 明确不做 |
| 16 | 企业管理智能体 | 真有（内部） | `src/gateway/ai_management.py`、`src/ai/employee_store.py`、`projects.py` | `:69` | 部分完成 | 内部运营真；外部系统适配器无消费方，控制台自述「没有接入真实账本数据源」 |
| 17 | 金融经济分析 | 半成品 | `src/ai/economy.py`（真实算术）接入 `agent_factory.py` 成本 | `:70` | 部分完成 | 只有 token 成本账本；无行情/持仓/交易，金融交易明确不做 |
| 18 | 个性化教育 | 假实现 | 38 个行业模板仅测试导入（`solutions/industry/education/`） | `:71` | 未做（占位） | 不做独立教育产品；模板不得对外当能力 |
| 19 | 创意生产 | 假实现 | 顶层 `plugins/{image,audio,video}` 未加载；无 TTS/STT/生成执行路径 | `:72` | 未做（占位） | 不做创意套件 |
| 20 | 个人数字生活管理 | 半成品 | `src/ai/personal_context.py`（KAREN SQLite 画像真）、`conversation_store.py` | `:73` | 部分完成 | 无日历/待办/提醒运行时，无排程 |
| 21 | 分布式系统 | 半成品 | `src/distribution/{coordination,lock,election,msg_bus}.py`、`kernels/execution/fence.py` | `:74` | 部分完成 | 局部真（执行围栏）；无多节点部署/集群调度 |
| 22 | 网络安全与数字身份 | 真有（本地） | `src/security/`（JWT/RBAC/加密/审计）、`src/gateway/{identity,policy}.py` | `:75` | 部分完成 | 本地身份/权限真；外部 IdP/KYC 不做 |
| 23 | 数据基础设施 | 真有（单机 SQLite） | 6 个 alembic 迁移 + 至少 9 个 SQLite 库 | `:76` | 部分完成 | 单机真；Postgres 路径崩溃风险未修（C-1），备份未接生产（T-21） |
| 24 | 自适应与持续优化 | 半成品 | `src/kernels/evaluation/`（AgentRuntime 调用，真） | `:77` | 部分完成 | 反馈服务是进程内 `dict`（R-5），`evolution.py` 仅测试引用 |
| 25 | 开源技术情报 | 真有 | `src/intel/`（CLI + `data/intel/osi_intel.db`）、`docs/oss-radar/` | `:78` | 部分完成 | CLI 真；无控制台、无调度（T-09/T-12） |
| 26 | 数字资产管理 | 半成品 | `src/gateway/files.py`（只读浏览）、`src/ai/workspace.py` | `:79` | 部分完成 | 无资产目录/版本/权限/检索；只做产物索引（T-22） |
| 27 | 隐私计算与可信 AI | 半成品 | `src/knowledge/{pii,security}.py`、`src/compliance/` | `:80` | 部分完成 | 策略库真但未接写入路径；无 MPC/TEE/差分隐私 |
| 28 | 高可用与灾难恢复 | 半成品 | `scripts/ops/backup.py`（自述无生产调用方）、`src/sre/disaster/` | `:81` | 部分完成 | 工具真未接线；DR 先做，HA 不做（T-21） |
| 29 | 跨平台生态 | 真有 | `src/gateway/`（单端口 HTTP）、`sdk/` 10 语言、`src/desktop/`、PWA | `:82` | 部分完成 | Web/SDK/PWA/桌面 CLI 真；安装包未在干净机实测（T-02） |
| 30 | 人机协同决策 | 真有 | `src/gateway/policy.py`、审批中心、`require_human_principal` | `:83` | 部分完成 | HTTP 契约测试真（`tests/test_policy_approval_http.py` 28 passed）；未在真实进程做端到端验收 |

> 合计与战略文档一致：真有 8 / 半成品 11 / 假实现 4 / 无实现 7。
> **假实现与无实现不得出现在对外能力清单**（验收标准见 `docs/known-gaps.md` D-5）。

---

## 附：CI 隐患（诊断，未必属本路可直接修）

| # | 隐患 | 证据 | 影响 | 归属 |
|---|---|---|---|---|
| C-1 | 006 迁移是 SQLite 专用 DDL（`strftime` / `AUTOINCREMENT`），Postgres 路径会直接崩；CI 无 Postgres 冒烟 | `alembic/versions/006_runtime_state_baseline.py:74`、`:102`、`:149`、`:264` | 声称支持 Postgres 时不可信 | 迁移/gateway 流 |
| C-2 | Trivy / pip-audit 非阻断（`--exit-code 0` / `|| true`） | `.github/workflows/ci.yml:141`、`:189` | 扫了不拦，等于无 CVE 门禁 | 安全流水线流 |
| C-3 | `uv` 版本低于 0.12.18 的 Windows 路径穿越问题 | 工作流中未固定 uv 版本 | Windows runner 可能被路径穿越影响 | CI 流 |
| C-4（已修） | Semgrep 作业装完即崩：pip 默认拉 `mcp` 2.x，semgrep 运行时要 `mcp.server.fastmcp` ⇒ `ImportError`，扫描前退出 ⇒ CRITICAL-5 报「架构门禁失败」，**日志里一条 finding 都没有**（假红） | `.github/workflows/ci.yml:1235-1243`；本地隔离 venv 实测：只装 semgrep → 崩；`semgrep "mcp<2"` → `5 rules / 594 files / 0 findings` | 真问题被工具链崩溃掩盖，门禁形同虚设 | 本路已修（钉 `mcp<2`） |
| C-5（部分已修，剩余属他人流） | 上一轮 CI 的 `Run Tests` 成簇红。**本路已修并推送 `202f2b29`**：`test_policy_approval_http.py`（令牌补 `metadata.kind`）**28 passed**、`test_contracts_drift_gate.py` **21 passed/3 skipped**、限流 lambda 参数（补 `trusted_proxies` 形参）**5 passed**。**他人流已修**：`test_chat_knowledge.py` **20 passed**、`tests/knowledge/test_documents_admin_api.py` **17 passed**。**仍红（他人流，本路不改）**：`tests/resilience/test_dbos_pilot.py::test_hard_kill_then_restart_resumes_workflow`（CI `exit 17 vs 1`；本地单跑 passed ⇒ 环境/时序敏感）、`tests/desktop/test_desktop_system_layer.py::test_runner_drains_inflight_request_and_refuses_new_ones` | `e18169e6` 推送前本地复跑：5 个文件合计 **77 passed, 3 skipped**；DBOS 单文件 **1 passed**。`fd0d006f` 的 CI 另暴露 **收集期中断**（`Verify test collection is complete` 报 `tests/tasks/test_local_scheduler.py` 与 `tests/tasks/test_reminders.py` 两个 ERROR，导致整棵测试树未跑）；干净 worktree 在 `30304247` 上复跑收集 **33 tests collected, 0 error** ⇒ 已由任务流修复。**端点面 4→5 的裁决（本路 `c13fd026`）**：`GET /v1/policy/approvals/pending` 是只读待办队列（admin 门、不执行内核动作、不改状态），已作为第 5 个端点显式登记进 `tests/test_policy_approval_http.py::TestThePolicySurfaceIsFixedAndHasNoExecutionRoute`，注明 §10.8.3-1 未反转；单文件复跑 **28 passed**（18.55s） | 全量红不代表本路失败；剩余两条（DBOS/桌面）需对应流在 CI 上复现并修 | 韧性流（DBOS）/ 桌面流 / 任务流（已修）/ 本路（端点面裁决） |

> 本表只记录诊断结论；C-1 涉及迁移与 `src/gateway` 他人领域，改动前需先报告。
> C-2/C-3 未修：属安全流水线与 CI 的版本固定策略。C-5 中**本路能改的用例簇已收口**
> （`202f2b29`），剩余 DBOS 硬杀与桌面 runner 两项属对应功能流（本地单跑通过，CI 失败需它们复现）。

---

## 「看起来有、实际不生效」红线清单

> 这一节单独列出，因为它们是**最容易让人误判进度**的东西：目录在、文件在、
> 测试绿，但运行时不走那条路。看到这些名字先读本节，不要当成能力已交付。

| # | 现象 | 真相 | 证据 |
|---|---|---|---|
| R-1 | 仓库顶层有 **61 个** `plugins/` 目录 | 插件管理器默认只扫描 `src/plugins`，顶层 `plugins/` **运行时不加载**；主程序只注册 builtin。把能力放进顶层 plugins 等于没接上 | 数量实测 61；默认目录 `src/plugins/manager.py:48`；入口只注册 builtin（`src/gateway/main.py`） |
| R-2 | `solutions/industry/` 有 **38 个**行业方案 | 只有 `tests/test_all_industry_solutions.py` 逐个导入；`src/` 中无任何引用，**产品运行时不加载行业模板** | 目录数实测 38；`src/` 检索 `solutions.industry` 无命中；测试 `tests/test_all_industry_solutions.py:32+` |
| R-3 | `src/automation/` 有完整动作/审批/围栏体系 | 后端只有 `fake.py`；三重闸门默认关闭；没有任何真实浏览器/键鼠/安卓执行 | `src/automation/backends/` 仅有 `fake.py`；`src/automation/gate.py:19`；`src/automation/__init__.py:1-6` |
| R-4 | ~~「知识中心」页面存在，但只是端点清单~~ **已解除** | `69db011e` 后知识中心已是导入/文档/分块预览/检索测试界面，`/v1/chat` 也注入检索上下文（`chat.py:159,185`）；剩下的是**真实 embedding E2E 未验证**，不再属于「假能力」 | `Directory.tsx`、`src/gateway/chat.py:159` |
| R-5 | 反馈服务有接口 | 数据存在进程内 `dict`，重启即丢；没接数据库，不能算闭环 | `src/feedback/feedback_service.py:13` |
| R-6 | 控制台 dist 已构建 | 源码变更后是否重新构建**未验证**；不重建就看不到新 UI（属于部署纪律，不是功能缺失） | `strategy-30-directions.md:232` 标未验证 |

---

## 启动触发条件（什么时候才动这些延后项）

| 编号 | 对应 | 启动条件 | 启动后第一步 |
|---|---|---|---|
| K-1 | T-19 知识闭环 | 已配置可用 embedding provider（代码已在：UI + `/v1/chat` 注入） | 跑真实 E2E：上传→切分→向量→对话引用来源；并验证未配置时返回 503 |
| K-2 | T-20 排程器 | 出现「每天/每周自动执行」的真实 Goal 需求 | 把已入库的 `src/tasks/` 排程器接到 Goal 创建入口，跑真实定时 + 重启恢复验收（不引入 Redis/外部队列） |
| K-3 | T-21/T-22 备份与产物索引 | 用户要求「换机迁移/可恢复」或「从目标反查产物」 | 备份：把已真实化的 `src/cli/backup_commands.py` 接到设置页 + 做恢复流程；产物索引：补权限过滤与控制台入口 |
| K-4 | D-1 自升级 | 签名校验、审批票据、回滚与沙箱四件套齐备 | 只允许「生成 diff → 人工审批 → 沙箱测试 → 原子替换」 |
| K-5 | D-6 情报中心后续 | 拿到真实 advisory 配额，且用户需要订阅式漏洞/更新提醒 | 先接 advisory 查询，再做订阅通知 |
| K-6 | D-5 30 方向服务化 | 明确解除「双击即用、零运维」承诺并给出迁移/运维方案 | 否则维持 T-24 决策：只做信息架构 |
| K-7 | T-11 假实现转真 | 用户点名某个方向（如创意/教育）并有明确使用场景 | 先补真实链路与验收，再更新「真有/半成品/假实现」矩阵 |

