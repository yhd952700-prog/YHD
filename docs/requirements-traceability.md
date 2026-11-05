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
> 最后刷新：2026-11-06（基于 `p36` HEAD `230b6e25`，工作区含其他流的未提交改动）。
> 本轮刷新：按 `docs/strategy-30-directions.md` 的真实核对结果补全 T-11、T-19～T-24，
> 并新增「红线清单」与「启动触发条件」；T-15 门禁安全值锁定已完成并通过实跑。

| 编号 | 要求（用户视角） | 落到哪一路 / 模块 | 关键代码或文档路径 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|---|
| T-01 | 填 API Key 就能接入模型，且不用重启后端 | 模型管理（gateway + ai） | `src/gateway/models.py`、`src/ai/model_config.py`、`src/ai/providers.py` | 部分完成 | `src/gateway/models.py:1` 明确「保存即生效（无需重启）」；`src/ai/providers.py:907`、`:979` 按配置逐次构造 provider；测试 `tests/test_gateway_models_api.py`、`tests/test_model_config.py`、`tests/test_providers.py` | 代码链路与单测在；本轮未用真实 DeepSeek Key 重跑端到端，故不标已完成 |
| T-02 | 只要一个能下载、能安装、填 Key 就能用的成品 | 打包 / 安装（安装器流） | `installer/liuhao.iss`、`packaging/build_windows_installer.ps1`、`packaging/stage_payload.py`、`scripts/first_run_wizard.py`、`scripts/launch_installed.py` | 部分完成 | 安装器为 Inno Setup 脚本（`installer/liuhao.iss:27-40`）；首次运行向导已提交（`scripts/first_run_wizard.py` + `tests/test_first_run_wizard.py`） | `installer/`、`packaging/` 当前**未被任何 commit 跟踪**；未产出安装包、未在干净 Windows 机器上实测安装 |
| T-03 | 不足的地方由我们自行判断并完善，不推回给用户决定 | 全部工程流 | 各流 commit 与 `docs/known-gaps.md` | 部分完成 | 并行流持续提交修复（如 `230b6e25`、`db0db76c`、`d768fa56`）；延后项改为登记而非推回用户 | 属工作方式，无单一交付物；以「不阻塞用户、问题自行收口」为准 |
| T-04 | 产品能自己修改代码 / 自我升级 | 进化（暂无归属流） | 仓库内无实现 | 未做 | 全仓库检索 `self[-_]update` / `self[-_]upgrade` / `auto[-_]update` / `自升级` 无任何实现命中 | 高风险能力，延后；需安全闸门、签名校验、回滚与审批，详见 `docs/known-gaps.md` |
| T-05 | UI 增「鎏灏管家」：一句话吩咐 → 自行安排员工干活 → 汇总结果；也可单独指派员工 | 控制台 + 执行内核 | `apps/console/console/src/components/Shell.tsx`、`apps/console/console/src/lib/nav.ts`、`src/kernels/execution/__init__.py`、`src/gateway/ai_management.py` | 部分完成 | 「管家」目前只是对话/工作台入口（`Shell.tsx:161-166`，`nav.test.ts:6` 锁定不被精简）；执行内核有 `_assign_agents`（`src/kernels/execution/__init__.py:634`）；员工管理 API 在 `src/gateway/ai_management.py:710-767` | **缺**「一句话 → 自动建目标 → 分派 → 汇总回话」的管家编排与汇总界面；现有是用户分别操作目标/员工 |
| T-06 | 界面科幻、未来风；品牌条款不许改 | 控制台视觉（前端流） | `docs/ui-scifi-direction.md`、`docs/ui-reference-terse.md`、`apps/console/console/src/os.css` | 部分完成 | 视觉规范文档已写（深色主基调、`--sc-*` 令牌、品牌不复制第三方资产）；主题令牌已在 `os.css:17-79` 落地 | 未做整站视觉走查；无「品牌条款不可改」的自动化校验 |
| T-07 | 独立代码中发现的隐患要处理完善（不是只写报告） | 各工程流 | 最近提交 | 部分完成 | `230b6e25`（焦点双环/AA 对比度）、`db0db76c`（手机底栏补管家入口）、`d768fa56`（中文否定审计措辞）、`6a6060ae`（测试身份隔离） | 仍有已知缺口：006 迁移 SQLite-only、扫描器 `exit-code 0`、CI 仅 SQLite 路径等 |
| T-08 | 能并行的任务就并行，别串行磨时间 | 编排（当前为人工多流） | 8 条工程流并行；CI job 并行 | 部分完成 | 本轮同一仓库 8 条流并行改代码；`.github/workflows/ci.yml` 多 job 并行 | 缺工具层面的自动分片/并行调度；本次靠人分流 |
| T-09 | 全量开源资源系统性研究：多平台多领域，记录地址/版本/许可证/依赖/维护/安全；未验证必须标注 | 开源情报流 | `docs/oss-radar/`（8 份，约 395 KB）、`oss-registry.yaml` | 部分完成 | `docs/oss-radar/` 覆盖安全合规、供应链、质量成本、工程品质、数据网络、Agent 编排、UI 交互、记忆检索；`oss-registry.yaml` 已跟踪并有 `tests/test_oss_registry_used_field.py` | `docs/oss-radar/` **未提交**；未证明逐项核对过版本/许可证/维护状态；「未验证」标注需抽查 |
| T-10 | 每个候选回答九问（是否已有同类能力/不足/能解决什么/源码借鉴/能否封装/是否破坏接口/许可证/安全维护成本/集成与回归测试） | 开源情报流 | `src/intel/proposals.py`、`src/intel/capabilities.py`、`docs/oss-radar/*.md` | 部分完成 | 提案生成器带免责声明（`src/intel/proposals.py:13-14`）；能力对照在 `capabilities.py` | 九问是否逐条落表未验证；多数候选文档未确认九问齐全 |
| T-11 | 30 个方向 + 用户没想到的方向，外加五大战略中心 | 战略/架构流 | `docs/strategy-30-directions.md` | 部分完成（研究完成，工程未完成） | `strategy-30-directions.md:11-18` 逐方向判定：**真有 8**（1、8、16、22、23、25、29、30）、**半成品 11**（2、3、7、13、17、20、21、24、26、27、28）、**假实现 4**（5 多模态、6 电脑操作、18 教育、19 创意）、**无实现 7**（4 科研、9 情报平台、10 数字孪生、11 IoT、12 机器人、14 空间智能、15 工业自动化）；逐条证据见 `:48-86` 矩阵 | 研究已完成且给出完成度；「真有」只是静态代码判定，报告 `:224-233` 明确未跑运行时 E2E，不能当已完成 |
| T-12 | 独立内部模块 LIUHAO Open Source Intelligence（发现分类/跟踪版本/许可证与依赖漏洞/找缺口/生成方案/自动测试/记录来源） | 开源情报模块 | `src/intel/`（models/github_client/scanner/licenses/proposals/report/store/capabilities/__main__） | 部分完成 | 模块有 CLI 入口（`src/intel/__main__.py`）与测试 `tests/test_intel_flow.py`、`tests/test_intel_licenses.py`、`tests/test_intel_store.py`；只发现不建议（`src/intel/__init__.py:3-18`） | `src/intel/` 整目录**未提交**；未接入 gateway；未用真实 GitHub API 跑过一次完整扫描（无运行证据）；不接控制台是战略结论（`strategy-30-directions.md:184`） |
| T-13 | 边界：尽可能广泛发现、严格筛选、只吸收有价值且合法兼容的 | 开源情报模块 | `src/intel/licenses.py`、`oss-registry.yaml` | 部分完成 | `licenses.py:143-161` 按 runtime/build/tool 场景判定；runtime 下 GPL/AGPL/unknown 一律拒绝；测试 `tests/test_intel_licenses.py:42-104` | 「有价值」评分与人工复核流程未实现 |
| T-14 | 推送 origin、修 CI（原任务书第 10、11 条） | 交付/CI（本路） | `.github/workflows/*.yml` | 部分完成 | 已推送 `af5b0989..230b6e25` 到 `origin/p36`；CI run `42104380309`（push 后排队/进行中） | 待看 CI 结果；已知隐患见下方「CI 隐患」，未修前不标完成 |
| T-15 | 11 个门禁变量按「安全优先」默写 | 配置/门禁（本路） | `scripts/verify_deployment_gate_vars.py`（`SEED_GATES`/`safe` 规则）、`docker-compose.prod.yml:37-127` | 已完成 | 门禁脚本实际运行 exit=0（PASS）；11+2 个变量在 `SEED_GATES` 增加 `safe` 规则并锁定；`tests/test_deployment_gate_vars_judgement.py` 43 项通过，`TestSafeValueLock` 锁定生产值（模拟执行=0、围栏=on、cap=4、路径都在 `/app/data`） | 安全值只在生产 bundle 上判定；staging 允许宽松（设计如此）。路径另用 Dockerfile 的 `mkdir -p /app/data` 与 console dist 校验 |
| T-16 | Windows 兼容 / pytest 超时 / etcd 健康检查 | 平台兼容（本路） | `tests/`、`scripts/verify_readiness.py`、`.github/workflows/ci.yml` | 部分完成 | BOM 致 `ast.parse` 失败已修（`19fadf35`）；etcd 健康检查改用含 shell 的 `bitnamilegacy/etcd:3.5.9` 并指向真实 `etcdctl`（`ci.yml:45-60`）；`pytest-timeout` 已进 `[tests]`（`pyproject.toml:130`） | 本机无 etcd，etcd 路径只能标「需 CI 环境验证」；慢用例识别未完成；Windows 路径/编码需继续抽查 |
| T-17 | 手机端实测（同一 Wi-Fi 打开 → 登录 → 发消息） | 运行验证（本路） | 待实测 | 未验证 | 暂无真实手机实测记录 | 需区分虚拟网卡地址，写清用户该用哪个地址；做不到的部分如实标注原因 |
| T-18 | 延后项不得消失，必须登记 | 文档（本路） | `docs/known-gaps.md` | 部分完成 | 已有清单与 semgrep 缺口（`ca900b99`）；本轮补充 6 项延后清单 | 见 `docs/known-gaps.md` 的「已明确延后、不得遗忘」 |
| T-19 | 知识闭环：知识入库 UI + 检索 + 对话注入（方向 3） | 知识 / 控制台 / 对话 | `src/knowledge/documents.py`、`vector_store.py`、`embedding.py`、`apps/console/console/src/pages/Directory.tsx:489`、`src/gateway/chat.py`、`src/ai/liuhao.py` | 未做 | 后端 chunk→embedding→向量入库是真链路；「知识中心」只是 OpenAPI 端点清单；`/v1/chat` 与 AgentRuntime 未注入检索结果（`strategy-30-directions.md:22,154-156`） | 触发条件见下方「启动触发条件」K-1 |
| T-20 | 进程内排程器：定时/循环 Goal（方向 8/20） | 目标执行 / 自动化 | `src/reliability/checkpointer.py`（只调度审计检查点）、无 taskiq/apscheduler 生产调用 | 未做 | `strategy-30-directions.md:23,158-160` | 不引入 Redis/外部队列；SQLite 持久化 + 单进程。触发条件 K-2 |
| T-21 | 备份/恢复/导出一键化（方向 28/23） | 运维 / 设置页 | `scripts/ops/backup.py:11`（自述「CLI but no production caller wired」） | 未做 | `strategy-30-directions.md:24,162-164` | **主线程正在改这块**，收尾时以最终状态刷新本行，不要按旧结论判断 |
| T-22 | 产物索引 + 目标→文件回溯（方向 26/7） | 执行 / 文件 | `src/gateway/files.py`（只读浏览）、`src/ai/workspace.py` | 未做 | `strategy-30-directions.md:25,166-168` | 触发条件 K-3 |
| T-23 | 明确不做清单（电脑自主操作 / 任意仓库改码提 PR / 金融交易 / 外部 KYC / 全网常驻采集 / 机器人 IoT 工业执行） | 战略（决策） | `docs/strategy-30-directions.md:188-220` | 延后（明确不做） | 分节理由：物理硬件依赖、高风险强监管、个人单机无日常价值、与产品形态冲突 | 不是「以后做」，是「不做」；重新捡起必须先改本节并给出安全/合规前提 |
| T-24 | 五大战略中心的决策：只做信息架构、命名与权限边界，不拆五套后端 | 战略 / 架构 | `docs/strategy-30-directions.md:42-46`、`:87-147` | 已完成（决策） | 理由：1 进程 1 主库 + 至少 9 个 SQLite，与「双击即用、零运维」冲突；中心边界不干净（知识被执链消费、安全拦执行、进化评估执行） | 若未来改为多服务，必须先解除「零运维」承诺并给出迁移与运维方案 |

---

## 附：CI 隐患（诊断，未必属本路可直接修）

| # | 隐患 | 证据 | 影响 | 归属 |
|---|---|---|---|---|
| C-1 | 006 迁移是 SQLite 专用 DDL（`strftime` / `AUTOINCREMENT`），Postgres 路径会直接崩；CI 无 Postgres 冒烟 | `alembic/versions/006_runtime_state_baseline.py:74`、`:102`、`:149`、`:264` | 声称支持 Postgres 时不可信 | 迁移/gateway 流 |
| C-2 | Trivy / pip-audit 非阻断（`--exit-code 0` / `|| true`） | `.github/workflows/ci.yml:141`、`:189` | 扫了不拦，等于无 CVE 门禁 | 安全流水线流 |
| C-3 | `uv` 版本低于 0.12.18 的 Windows 路径穿越问题 | 工作流中未固定 uv 版本 | Windows runner 可能被路径穿越影响 | CI 流 |

> 本表只记录诊断结论；C-1 涉及迁移与 `src/gateway` 他人领域，改动前需先报告。

---

## 「看起来有、实际不生效」红线清单

> 这一节单独列出，因为它们是**最容易让人误判进度**的东西：目录在、文件在、
> 测试绿，但运行时不走那条路。看到这些名字先读本节，不要当成能力已交付。

| # | 现象 | 真相 | 证据 |
|---|---|---|---|
| R-1 | 仓库顶层有 **61 个** `plugins/` 目录 | 插件管理器默认只扫描 `src/plugins`，顶层 `plugins/` **运行时不加载**；主程序只注册 builtin。把能力放进顶层 plugins 等于没接上 | 数量实测 61；默认目录 `src/plugins/manager.py:48`；入口只注册 builtin（`src/gateway/main.py`） |
| R-2 | `solutions/industry/` 有 **38 个**行业方案 | 只有 `tests/test_all_industry_solutions.py` 逐个导入；`src/` 中无任何引用，**产品运行时不加载行业模板** | 目录数实测 38；`src/` 检索 `solutions.industry` 无命中；测试 `tests/test_all_industry_solutions.py:32+` |
| R-3 | `src/automation/` 有完整动作/审批/围栏体系 | 后端只有 `fake.py`；三重闸门默认关闭；没有任何真实浏览器/键鼠/安卓执行 | `src/automation/backends/` 仅有 `fake.py`；`src/automation/gate.py:19`；`src/automation/__init__.py:1-6` |
| R-4 | 「知识中心」页面存在 | 前端 `Directory.tsx` 只是 OpenAPI 端点清单，不是知识库管理/检索界面；`/v1/chat` 不带检索上下文 | `apps/console/console/src/pages/Directory.tsx:489`；T-19 |
| R-5 | 反馈服务有接口 | 数据存在进程内 `dict`，重启即丢；没接数据库，不能算闭环 | `src/feedback/feedback_service.py:13` |
| R-6 | 控制台 dist 已构建 | 源码变更后是否重新构建**未验证**；不重建就看不到新 UI（属于部署纪律，不是功能缺失） | `strategy-30-directions.md:232` 标未验证 |

---

## 启动触发条件（什么时候才动这些延后项）

| 编号 | 对应 | 启动条件 | 启动后第一步 |
|---|---|---|---|
| K-1 | T-19 知识闭环 | 已配置可用 embedding provider，且用户提出「让 AI 用我的知识库回答」 | 先做知识入库 UI + `/v1/chat` 检索注入，再谈自动分类 |
| K-2 | T-20 排程器 | 出现「每天/每周自动执行」的真实 Goal 需求 | 进程内调度 + SQLite 持久化，不引入 Redis/外部队列 |
| K-3 | T-21/T-22 备份与产物索引 | 用户要求「换机迁移/可恢复」或「从目标反查产物」 | 先接现有 `scripts/ops/backup.py`（主线程改完后以最终状态为准），再做产物索引 |
| K-4 | D-1 自升级 | 签名校验、审批票据、回滚与沙箱四件套齐备 | 只允许「生成 diff → 人工审批 → 沙箱测试 → 原子替换」 |
| K-5 | D-6 情报中心后续 | 拿到真实 advisory 配额，且用户需要订阅式漏洞/更新提醒 | 先接 advisory 查询，再做订阅通知 |
| K-6 | D-5 30 方向服务化 | 明确解除「双击即用、零运维」承诺并给出迁移/运维方案 | 否则维持 T-24 决策：只做信息架构 |
| K-7 | T-11 假实现转真 | 用户点名某个方向（如创意/教育）并有明确使用场景 | 先补真实链路与验收，再更新「真有/半成品/假实现」矩阵 |

