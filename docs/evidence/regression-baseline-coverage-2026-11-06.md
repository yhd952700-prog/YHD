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
  - BLOCKED（1）：C11 HC-01 等待人类裁决（决策隔离，不是工程阻断）。
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
