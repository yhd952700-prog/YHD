# 全量回归基线覆盖（2026-11-06）

> 目的：把「这轮回归到底覆盖了哪个提交、哪些提交靠增量补」写死，
> 避免再把「全量基线 + 增量」说成「全量覆盖最终 HEAD」。

## 1. 结论

- 本轮全量回归的基线：`bb6b654c16265cb78a0f247253517d949990bbaf`
  （干净 detached worktree `D:\copilot-worktrees\liuhao-final-bb6b654c`，在 temp 根之外，
  `git status` 为空）。
- 解释器：`d:\LiuHao-AI-OS\.venv\Scripts\python.exe`（py3.12.14）；
  命令 `python -u scripts/verify_readiness.py --full`，进程退出码 `0`。
- 结果：`7458 tests` → `0 failed, 0 errors, 58 skipped`；
  `PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY = False`。
  - PASS（9）：C1 编译/导入、C2 干净环境可启动、C3 稳定测试、C4 无高危密钥且失败关闭、
    C5 审计链 32/32、C6 崩溃/恢复、C8 指标 48 家族、C10 独立验证、C12 无未解释的重大失败。
  - NOT VERIFIED（2）：C7 数值性能门禁（本机 profile 无基线，数值门禁 SKIP，不变量仍检查）、
    C9 无真实 CD（Dockerfile + alembic 在）。
    **补证（不改写上述历史结果）：**C7 已补采基线、C9 已补安装树验证，见 §7；`bb6b654c` 那一轮
    的判定文本仍是它当时的事实，不回填成 PASS。
  - BLOCKED（1）：C11 HC-01 待裁决（决策隔离，不是工程阻断）；5 项裁决与建议见 §7.3。
- **失败归因：无。** `FAIL=0`，C3/C12 均 `0 failed`，没有需要区分「假失败 / 真红」的条目。
- 决策时 HEAD：`d2a62b0e`。基线之后已有 `e0163d59`、`d2a62b0e` 两个提交；
  此外工作区还有未提交的安全修复、管家链路、多账号体系改动。
- **口径（必须沿用的说法）**：本轮是「**基线 `bb6b654c` 的全量覆盖**」，
  **不是最终 HEAD 的全量结论，不是交付凭证**。最终 hash 冻结后必须再跑一轮全量，
  那一轮才是交付凭证。

## 2. 逐提交核对

核对命令：`git merge-base --is-ancestor <commit> bb6b654c`（退出码 0 = 已包含，1 = 未包含）。

| 提交 | 内容 | 在基线 `bb6b654c` 内 | 覆盖方式 |
|---|---|---|---|
| `bc1d7b2b` | fix(upgrade): honor --allow-same-or-dowgrade and resolve installer data dir | **是**（0） | 全量回归包含（代码+测试在基线树里） |
| `747af1fd` | docs(upgrade): canonical flag name + argparse help escaping | **是**（0） | 全量回归包含（含 `src/upgrade/cli.py` 与 `tests/test_upgrade_cli.py`） |
| `bf062ee8` | fix(gates): classify LOCALAPPDATA as a data-directory hint | **是**（0） | 全量回归包含 |
| `d7ee01b3` | fix(upgrade): 占用中的数据库不再先删 WAL 侧车 | **是**（0） | 全量回归包含 |
| `c6dd0a8c` | test(desktop): 锁住启动门排在健康探针之前 | **是**（0） | 全量回归包含 |
| `75e36f9e`、`78288054`、`de71d150`、`08484190`、`a9459cba`、`7acaa61c`、`bb6b654c` | 7 条 lint 提交（automation/desktop/gateway/intel/selfmod+upgrade/tasks/cli） | **是**（均 0） | 全量回归包含；逐条核过只改 lint，无行为变更 |
| `bb6b654c` | style(cli): 删除未使用的 `timedelta` 导入 | **是**（0，即基线本身） | 全量回归基线 |
| `e0163d59` | fix(guardrails): C-1 校验器跳过 `__main__` 入口 | **否**（1） | 定向增量：`scripts/verify_policy_c1.py` 实测 `RESULT: ALL GREEN` |
| `d2a62b0e` | fix(artifacts): 非安全用途 sha1 显式 `usedforsecurity=False` | **否**（1） | 定向增量：`tests/artifacts` `30 passed in 5.15s`；`verify_policy_c1.py` 同轮 ALL GREEN |

基线之后两个提交合计只动 2 个文件（`git diff --stat bb6b654c d2a62b0e`）：
`scripts/verify_policy_c1.py`（+9/−2）、`src/artifacts/index.py`（+2/−1）。

## 3. 本轮全量证据

| 项目 | 值 |
|---|---|
| 基线提交 | `bb6b654c16265cb78a0f247253517d949990bbaf` |
| worktree | `D:\copilot-worktrees\liuhao-final-bb6b654c`（temp 根之外，`git status` 为空） |
| 解释器 | `d:\LiuHao-AI-OS\.venv\Scripts\python.exe`（py3.12.14） |
| 原始日志 | `D:\cache\temp\verify-full-bb6b654c.log`（2,303 字节，退出码 0） |
| 仓库内副本 | [regression-bb6b654c.txt](regression-bb6b654c.txt)（2,303 字节，逐字节复制） |
| 逐关报告 | `D:\copilot-worktrees\liuhao-final-bb6b654c\scripts\readiness_report.json`（`release_ready: false`，`PASS/FAIL/BLOCKED/NOT_VERIFIED/ERROR = 9/0/1/2/0`） |

## 4. 增量核验（`bb6b654c` 上，**只作快速核验，不替代全量**）

范围：upgrade 全部 10 个测试文件、desktop 启动门、UTF-8 日志、gate vars、
tasks/reminders、automation、intel、selfmod、cli、gateway。

| 项目 | 值 |
|---|---|
| 基线 | `bb6b654c`（干净 worktree，与全量同一提交） |
| 结果 | `531 passed, 1 skipped, 0 failed`，`332.36s` |
| 日志 | `D:\cache\temp\delta-bb6b654c.log`（16,598 字节） |
| JUnit | [regression-delta-bb6b654c.xml](regression-delta-bb6b654c.xml)（67,855 字节，532 条：531 通过 + 1 skip） |

## 5. 基线之后（`bb6b654c..d2a62b0e`）的定向增量

| 项目 | 值 |
|---|---|
| 基线 | `d2a62b0e`（worktree `D:\copilot-worktrees\liuhao-delta-d2a62b0e`，temp 根之外） |
| 测试 | `python -m pytest tests/artifacts -q --timeout=120` → `30 passed in 5.15s`（退出码 0） |
| 门禁脚本 | `python scripts/verify_policy_c1.py` → `RESULT: ALL GREEN`（退出码 0；47 个装饰动作，allow-list 24+23 无重叠/无未分类/无过期） |
| 日志 | [regression-delta-d2a62b0e.txt](regression-delta-d2a62b0e.txt)（104 字节）、[regression-c1-d2a62b0e.txt](regression-c1-d2a62b0e.txt)（8,356 字节） |

这轮增量**不构成**「`d2a62b0e` 已全量验证」：它只覆盖这两个提交实际触及的 2 个文件，
没有重跑 7458 条全量、没有重跑 C2/C4/C5/C6/C8/C10 的端到端门禁。

## 6. 未覆盖项与下一步

| 范围 | 当前状态 | 关闭方式 |
|---|---|---|
| `bb6b654c`（含）及之前 | 全量覆盖，`RELEASE READY=False`（C7/C9 未验证、C11 待裁决） | 维持口径，不追加 |
| `e0163d59`、`d2a62b0e` | 只做定向增量（§5），**未全量** | 最终 hash 冻结后随下一轮全量一起覆盖 |
| 工作区未提交改动（安全修复、管家链路、多账号体系等） | **未覆盖**（不在任何提交，也不在这次 worktree 里） | 提交并冻结 hash 后，在干净 worktree 上重跑 `--full` |

最终交付凭证的唯一成立条件：**最终 hash 冻结后，在 temp 根之外的干净 worktree 上
用仓库 venv 解释器跑完 `scripts/verify_readiness.py --full`，并回填该轮的逐关结果。**
在此之前，本文与 `docs/DELIVERY-CHECKLIST.md` §5 里的任何数字都只代表基线覆盖，
不得对外表述为「最终版本已通过全量回归」。

## 7. 三格补证（2026-11-06）

> 本节只补 C7 / C9 / C11 的证据，**不重跑全量、不改写 §1 的 `bb6b654c` 结论，
> 也不改任何判定口径**。因此 `PASS=9 FAIL=0 BLOCKED=1 NOT_VERIFIED=2`、`RELEASE READY=False`
> 仍是 `bb6b654c` 那一轮的事实；真正改变它们的是下一轮冻结 hash 后的全量。

### 7.1 C7：从「无基线只能 SKIP」到「有基线可判定」

- 采集：worktree `D:\copilot-worktrees\liuhao-final-bb6b654c`（temp 根之外，clean），
  仓库 venv py3.12.14，`bench_audit_append.py --quick` **6 次取中位数**（异常样本未丢弃）。
- 写入：`scripts/bench_baseline.json` 新增 profile
  `Windows|py3.12.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick`（保留旧 py3.13 条目）。
- 中位数基线：单条 **644.25 事件/秒**、p95 **2.577 ms**、batch10 **4,639.1**、batch100 **11,156.4**、
  多进程不含启动 **8,015.85** / 含启动 **2,004.7**、恢复 **0.0445 s**；四个不变量全 true。
- 判定：`python scripts/verify_readiness.py --only C7` → **PASS**；
  独立验证样本 `636.8 事件/秒 / p95 2.654 ms` → `PERFORMANCE GATE: PASS`。
- **方差如实**：单条吞吐极差/中位数 **80.0%**、p95 **1594.4%**、batch100 **90.1%**、
  多进程不含启动 **142.6%**、恢复 **76.4%**。基线代表「安静窗口」，忙碌时门禁会失败。
- **定位（owner 2026-11-06 裁决）**：维持「**本机安静窗口可判定、非 CI 阻断门禁**」；
  方差数字原样保留；**不许为了让门禁好看去调阈值**（`±25%` 容差未动，`scripts/bench_baseline.json`
  本轮只新增 py3.12.14 profile）。
- 证据：[c7-performance-baseline-2026-11-06.md](c7-performance-baseline-2026-11-06.md)、
  [bench-baseline-py312-2026-11-06.json](bench-baseline-py312-2026-11-06.json)（6,249 字节）、
  [c7-gate-validation-2026-11-06.log](c7-gate-validation-2026-11-06.log)（1,785 字节）、
  [c7-readiness-only-2026-11-06.log](c7-readiness-only-2026-11-06.log)（493 字节）。

### 7.2 C9：**原口径 NOT_VERIFIED + 分发形态已独立验证**（两条结论同时存在）

| 口径 | 结论 | 依据 |
|---|---|---|
| **原口径（Docker / CD）** | **`C9 = NOT_VERIFIED`（判定未改动）** | compose v2 插件缺失、镜像未构建、真实 CD 按设计缺失（`cd_absent=True`） |
| **分发形态（安装树）** | **已独立验证** | 真实安装树副本（10,769 文件 / 204,834,804 字节，自带 py3.12.14 runtime）上两轮 `check → apply → 重启 → rollback` |

- 第 1 轮：check 0.71s → apply **12.50s**（备份 512 文件、安装 **662 文件**、自检真实启动 5.48s 且 `/v1/health`=200）→
  0.1.1 重启 **5.34s** health 200 → rollback **2.29s**（恢复 513 文件）→ 0.1.0 重启 9.87–9.90s health 200。
- 第 2 轮（带真实用户 SQLite）：apply **12.62s**（数据 3 文件 / 14,878 字节、`databases_checked=1`、`tables_checked=1`）→
  rollback **2.45s**（恢复 515 文件）。
- 数据保全：哨兵 `c9-sentinel.txt` SHA256 `051F79B9…A4A9` 升级与回滚后不变；
  用户库行 `(1, 'keep-across-upgrade-and-rollback')` 全程在。
- 披露：回滚是**覆盖恢复**不是字节级还原（备份后新建的 `audit_store.db*` 会保留）。
- 边界：载荷 `liuhao-0.1.1.zip`（662 文件 / 2,741,388 字节 / sha256 `1a56e7a8…9c10`）**不含前端 `console/`**
  ——该缺陷已单独修复（下一条）；升级只校验 SHA256、无签名；未覆盖真实 CD。
- **不合并**：把安装树验证写成 C9 PASS 会掩盖「镜像未构建 / CD 不存在」；
  把原口径写成覆盖了升级链会掩盖本轮真做过的安装树验证。
- 证据：[c9-installed-upgrade-2026-11-06.md](c9-installed-upgrade-2026-11-06.md)、
  [c9-check.json](c9-check.json)、[c9-upgrade-1.json](c9-upgrade-1.json)、[c9-rollback-1.json](c9-rollback-1.json)、
  [c9-upgrade-2.json](c9-upgrade-2.json)、[c9-rollback-2.json](c9-rollback-2.json)、
  [c9-history.json](c9-history.json)（42,617 字节）、两份重启 stderr 日志。

**7.2.1 C9 缺陷修复：升级载荷缺前端 `console/`（2026-11-06 后续）**

- 缺陷：旧载荷 0 个 `console/` 成员 → 后端换新、前端不换，`/v1/health` 仍 200，
  界面静默不更新；接口形状一变就是随机 400 / 空面板。
- 修复：`console/` + 签章 `console/console-contract.json` 随包；升级前门禁
  （缺 index / 签章不符 / 契约不符 / 版本不符 → 拒绝并给中文原因，零改动）；
  自检比对真实 `/v1/health.api_contract`；构建期缺前端直接拒绝产出。
- 数字：新包 `liuhao-0.1.2.zip` **685 成员**（`console/` **12**）/ **2,961,979 字节** /
  解压 **10,047,927** / sha256 `ca46fc76…5D0A`，同参数复跑逐字节相同；
  旧包被拒绝（退出码 1，`console/` 保持 10 文件 / 496,026 字节 / `B6CCEB84…`，新增备份 0）；
  升级 18.72s / 安装 685 文件 / 自检启动 5.75s + health 200 / `console/` 变 **12 文件 / 496,344 字节**、
  `index.html` `B6CCEB84…` → `6F4F0C53…`；重启 5.07s 后 `/` 与 `/dashboard` 提供新 index；
  回滚 2.67s 精确回到 10 文件 / 496,026 字节 / `B6CCEB84…`，残留 0，数据不变。
- 边界：前端是模拟构建标记（非真实 `npm run build`）；未签名；只跑同契约。
- 证据：[c9-console-payload-2026-11-06.md](c9-console-payload-2026-11-06.md)、
  [c9b-console-contract-2026-11-06.json](c9b-console-contract-2026-11-06.json)、
  `c9b-build-0.1.2.txt`、`c9b-reject-0.1.1.txt`、`c9b-upgrade-0.1.2.json`、`c9b-rollback-0.1.0.json`、
  `c9b-history-0.1.2.json`、`c9b-health-0.1.2.json`、`c9b-restart-0.1.2.err.log` / `.out.log`。
- 单测：`tests/test_upgrade_console_contract.py` **10 passed**。

### 7.3 C11：5 项裁决已由 owner 裁决并记录 ADR

| # | 裁什么 | 裁决 |
|---|---|---|
| 1 | Baseline binding：A live / B 旧快照 / C 重采 | **A**（live 作运行源，旧快照仅作历史证据） |
| 2 | 快照保管断裂：接受并披露 / 隔离替换 / 标 UNRESOLVED | **接受并披露**（内容完整性有证，缺的只是操作者归因） |
| 3 | I1–I10：固定/参数化拆分 / 维持现状 / 全部参数化 | **固定/参数化拆分**（否则 `live=331,611` 会长期假红） |
| 4 | Schema：v1→v9 / 不做 / 双路 | **随第 1 项选 A ⇒ 不做 v1→v9** |
| 5 | 是否授权重采冻结基线 | **推迟，条件写死**：F1–F6 单写者/围栏上线并 soak 通过后，按只读 ACL + operator + 前后 hash 重采 |

- 裁决组合：**Path A + accept-with-disclosure + 固定/参数化拆分 + 不做 v1→v9 + 暂不授权重采。**
- 裁决落点：**已记录** [ADR-HC01-baseline-binding.md](../adr/ADR-HC01-baseline-binding.md)（2026-11-06）。
- **C11 判定未变**：`check_hc01` 仍恒派生 `BLOCKED`——这是**判定事实**
  （HC-01 是冻结的人类主权门），不是工程阻塞；记录 ADR 不改变 `RELEASE READY`，
  也没有为了让门禁变绿去改口径。保管断裂按「**保管链断裂、行为人未查明**」披露。
- 证据：[hc01-pending-decisions-2026-11-06.md](hc01-pending-decisions-2026-11-06.md)；
  `docs/hc01-status.md` §4.1 已加裁决指针。

