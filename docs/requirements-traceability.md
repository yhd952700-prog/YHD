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
| T-11 | 30 个方向 + 用户没想到的方向，外加五大战略中心 | 战略/架构流 | `docs/strategy-30-directions.md` | 部分完成 | `docs/strategy-30-directions.md:91-131` 逐中心给出「能否成立/成本/值不值」；结论：五中心不拆服务，只做信息架构 | 属于设计结论，尚无对应「五大中心」独立代码落地；Knowledge Universe / Evolution Lab 明确为空壳风险 |
| T-12 | 独立内部模块 LIUHAO Open Source Intelligence（发现分类/跟踪版本/许可证与依赖漏洞/找缺口/生成方案/自动测试/记录来源） | 开源情报模块 | `src/intel/`（models/github_client/scanner/licenses/proposals/report/store/capabilities/__main__） | 部分完成 | 模块有 CLI 入口（`src/intel/__main__.py`）与测试 `tests/test_intel_flow.py`、`tests/test_intel_licenses.py`、`tests/test_intel_store.py`；只发现不建议（`src/intel/__init__.py:3-18`） | `src/intel/` 整目录**未提交**；未接入 gateway；未用真实 GitHub API 跑过一次完整扫描（无运行证据） |
| T-13 | 边界：尽可能广泛发现、严格筛选、只吸收有价值且合法兼容的 | 开源情报模块 | `src/intel/licenses.py`、`oss-registry.yaml` | 部分完成 | `licenses.py:143-161` 按 runtime/build/tool 场景判定；runtime 下 GPL/AGPL/unknown 一律拒绝；测试 `tests/test_intel_licenses.py:42-104` | 「有价值」评分与人工复核流程未实现 |
| T-14 | 推送 origin、修 CI（原任务书第 10、11 条） | 交付/CI（本路） | `.github/workflows/*.yml` | 部分完成 | 已推送 `af5b0989..230b6e25` 到 `origin/p36`；CI run `42104380309`（push 后排队/进行中） | 待看 CI 结果；已知隐患见下方「CI 隐患」，未修前不标完成 |
| T-15 | 11 个门禁变量按「安全优先」默写 | 配置/门禁（本路） | `scripts/verify_deployment_gate_vars.py`（`SEED_GATES`/默认机制） | 部分完成 | 门禁脚本本地运行 exit=1：仅剩 2 条 `seed-ref-drifted`（他人未提交改动导致行号漂移） | 11 条「默认关 vs 默认开」的安全优先取值与防回退测试待本路落地 |
| T-16 | Windows 兼容 / pytest 超时 / etcd 健康检查 | 平台兼容（本路） | `tests/`、`scripts/verify_readiness.py`、`.github/workflows/ci.yml` | 部分完成 | BOM 致 `ast.parse` 失败已修（`19fadf35`）；etcd 健康检查改用含 shell 的 `bitnamilegacy/etcd:3.5.9` 并指向真实 `etcdctl`（`ci.yml:45-60`）；`pytest-timeout` 已进 `[tests]`（`pyproject.toml:130`） | 本机无 etcd，etcd 路径只能标「需 CI 环境验证」；慢用例识别未完成；Windows 路径/编码需继续抽查 |
| T-17 | 手机端实测（同一 Wi-Fi 打开 → 登录 → 发消息） | 运行验证（本路） | 待实测 | 未验证 | 暂无真实手机实测记录 | 需区分虚拟网卡地址，写清用户该用哪个地址；做不到的部分如实标注原因 |
| T-18 | 延后项不得消失，必须登记 | 文档（本路） | `docs/known-gaps.md` | 部分完成 | 已有清单与 semgrep 缺口（`ca900b99`）；本轮补充 6 项延后清单 | 见 `docs/known-gaps.md` 的「已明确延后、不得遗忘」 |

---

## 附：CI 隐患（诊断，未必属本路可直接修）

| # | 隐患 | 证据 | 影响 | 归属 |
|---|---|---|---|---|
| C-1 | 006 迁移是 SQLite 专用 DDL（`strftime` / `AUTOINCREMENT`），Postgres 路径会直接崩；CI 无 Postgres 冒烟 | `alembic/versions/006_runtime_state_baseline.py:74`、`:102`、`:149`、`:264` | 声称支持 Postgres 时不可信 | 迁移/gateway 流 |
| C-2 | Trivy / pip-audit 非阻断（`--exit-code 0` / `|| true`） | `.github/workflows/ci.yml:141`、`:189` | 扫了不拦，等于无 CVE 门禁 | 安全流水线流 |
| C-3 | `uv` 版本低于 0.12.18 的 Windows 路径穿越问题 | 工作流中未固定 uv 版本 | Windows runner 可能被路径穿越影响 | CI 流 |

> 本表只记录诊断结论；C-1 涉及迁移与 `src/gateway` 他人领域，改动前需先报告。
