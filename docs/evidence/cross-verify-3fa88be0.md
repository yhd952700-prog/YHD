# 交叉验证报告 — HEAD `3fa88be0`

> **本轮是「过程凭证，不是交付凭证」。** 账号、管家、韧性、安全四路各自绿，但别的路仍在提交；
> 下面的结果只证明「这一个 HEAD 上四方被放在同一棵树里同时跑过」，不代表可以用来交付。
> 交付凭证要等四路停止提交、在一个冻结 HEAD 上重跑同一套电池后才成立。

## 0. 基线与环境

| 项目 | 值 |
|---|---|
| 基线 hash | `3fa88be08bcac6ec4941b24c6587ea83ffc84495`（`p36`，`fix(resilience): 五类用户必撞故障面加固；合并分发流…`） |
| worktree | `D:\copilot-worktrees\liuhao-cross-3fa88be0`（detached HEAD；**跑前 `git status --short` 为空**） |
| venv | `d:\LiuHao-AI-OS\.venv\Scripts\python.exe` → CPython 3.12.14，Windows 10.0.19045 |
| 电池日志 | `D:\cache\temp\verify-full-3fa88be0.log`（含 `EXIT_CODE=2`、`ELAPSED_S=157`） |
| 机器报告 | worktree 内 `scripts/readiness_report.json`（`generated_at=2026-11-06T09:40:33Z`） |
| 电池耗时 | **157 s**（C3 在采集阶段即中断，未进入全量执行） |
| 补充全量日志 | `D:\cache\temp\verify-supp-3fa88be0.log`、JUnit `D:\cache\temp\supp-junit-3fa88be0.xml` |
| 补充全量耗时 | **3965.78 s（1:06:05）** |

## 1. C1–C12 结果（`scripts/verify_readiness.py --full` @ 3fa88be0）

| 条件 | 结果 | 证据摘要 |
|---|---|---|
| C1 Build | PASS | compileall clean；4 个入口模块可导入 |
| C2 Clean-env runnable | PASS | 干净环境启动 + `GET /v1/health` = 200 |
| C3 Stable tests | **FAIL** | 采集失败 `rc=2`：`ERROR tests/test_goal_ownership_isolation.py`；7521 collected, 1 error in 52.22s |
| C4 Security blocker=0 | PASS | 无高置信密钥；fail-closed 存在；CI 门 `bandit:high`、`semgrep:error` |
| C5 Data integrity | PASS | 哈希链 sig/alg 32/32 COMPLIANT（HC-02..08 + HC-11） |
| C6 Crash/recovery/failover | PASS | 6 个可靠性/故障套件通过（仅临时 DB） |
| C7 Performance | **NOT VERIFIED**（本电池内） | 瞬时 `sqlite3.OperationalError: disk I/O error`（`src/kernels/audit/__init__.py:473` `PRAGMA journal_mode=WAL`）；同一命令单独重跑 **PASS**（见 §3） |
| C8 Observability/ops | PASS | `/v1/metrics/prometheus` 200，10030 B，48 个真实指标族；UBX005 齐全 |
| C9 Deployment/upgrade/rollback | NOT VERIFIED | Dockerfile + alembic 在；真实 CD 缺失（honest） |
| C10 Independent verification | PASS | 独立验证 harness 在场 |
| C11 HC-01 real verification | BLOCKED | 人工裁决待定（Decision Isolation 生效，非工程阻塞） |
| C12 No unexplained major failures | **FAIL** | 无法采集套件（与 C3 同根因） |

电池汇总：`PASS=7 FAIL=2 BLOCKED=1 NOT_VERIFIED=2 ERROR=0`，`RELEASE READY = False`。

## 2. 逐条归因

补充全量（排除唯一无法采集的 `tests/test_goal_ownership_isolation.py`）结果：
**31 failed, 7433 passed, 54 skipped, 3 xfailed in 3965.78s**。
31 条失败按根因分类如下；修复前的数字，修复后见 §3。

| # | 数量 | 现象 | 归因 | 处置 |
|---|---|---|---|---|
| A | 25 | `TypeError: provider_for_task() got an unexpected keyword argument 'visible_to'` | **别的路提交不自洽**：`src/gateway/chat.py` 在 HEAD 里已传 `visible_to`，但 HEAD 的 `src/ai/model_router.py:provider_for_task` 没有该参数；主树**未提交**的 `model_router.py` 已有（:495-499）。涉及 `tests/gateway/test_chat_knowledge.py` 20、`tests/test_security_hardening.py` 1、`tests/test_gateway_auth.py` 1、`tests/test_profile_api.py` 3 | 不能由本切片代提交别人的在途文件，登记为阻塞 |
| B | 1 error | 采集中断：`ImportError: cannot import name 'GoalViewer' from 'src.gateway.ai_management'` | **别的路提交不自洽**：`tests/test_goal_ownership_isolation.py` 由 `68392942` 提交，但该提交的文件清单里没有 `src/gateway/ai_management.py`；`class GoalViewer`（:305）只存在于主树未提交的 diff（+977/−89） | 同上；修复归属账号切片 |
| C | 2 | `TypeError: string indices must be integers`（期望 `detail["code"]`） | **别的路提交不自洽**：测试期望结构化的 `agent_not_found` 错误体，HEAD 的 assign 路由仍 `detail=str(exc)`；主树未提交的 `ai_management.py` 已有 `GoalWorkError("agent_not_found")` | 同上 |
| D | 2 | 部署门变量签证：`SEED_GATES` 引用漂移 2 条 + `LIUHAO_PARENT_PID` 未归类 | **真回归（已修）**：`AUDIT_DB_PATH` 引用停在 `health.py:280`（实际读取在 :282）；`LIUHAO_CONSOLE_DIST` 引用停在 `console_static.py:124`（实际读取在 :127）；`LIUHAO_PARENT_PID` 是本切片 `src/common/parent_watch.py:37` 新增的 env 读取，未登记 | 修复见 §3（commit `ad4f5cf3`） |
| E | 1 | `test_baseline_really_has_only_the_py313_profile` 失败 | **测试没跟上已提交的改动**：C7 切片已把本机 py3.12 profile 写进 `scripts/bench_baseline.json`（`docs/evidence/bench-baseline-py312-2026-11-06.json`），而守卫测试仍断言「只有 py3.13」 | 更新守卫与 C7 说明文本，见 §3 |
| F | 1 | `test_audit_write_failure_keeps_previous_file_and_rolls_back: DID NOT RAISE OSError`（单跑必过） | **测试顺序依赖**：`tests/test_audit_single_source_of_truth.py:119-121` 会 `sys.modules.pop("src.audit.store")` 再重新 import，旧 `AuditStore` 类仍持有旧模块 globals；字符串目标 `"src.audit.store.atomic_write_text"` 打到了新模块上，补丁实际失效 | 改为按类所属模块打补丁，见 §3 |
| G | — | C7 电池内 `disk I/O error` | **环境瞬时**：同一命令（`bench_audit_append.py --quick --gate scripts/bench_baseline.json`）单独重跑 `RC=0`、`=== PERFORMANCE GATE: PASS ===`（10500 events，chain_verifies=true，多进程 0 lost） | `--only C7` 复核 **PASS** |

**结论：31 失败中 27 条（A 25 + C 2）与 1 条采集错误（B）同根因——账号/知识切片把测试与调用方提交了，把被调用的实现留在主树工作区。**
这 3 类只能在账号切片提交 `src/ai/model_router.py`（`visible_to`）与 `src/gateway/ai_management.py`（`GoalViewer` + 结构化错误体）之后才会消失；本切片不代提交他人在途文件。

## 3. 真回归修复（已落库）

修复提交：`ad4f5cf3526720f00d397257ad79e04520485f04`（`fix(verify): 交叉验证巡检到的真回归`，5 files, +27/−8）

- `tests/scripts/test_readiness_c7_profile_gate.py`：守卫改为同时接受 py3.13 默认 profile 与本机实测 py3.12 profile；
- `scripts/verify_readiness.py`：C7 的 `known_limitation` 由「baseline 只有 py3.13、本机 SKIP」改为实测事实（本机 PASS，CI 仍 SKIP）；
- `scripts/verify_deployment_gate_vars.py`：`AUDIT_DB_PATH` → `health.py:282`、`LIUHAO_CONSOLE_DIST` → `console_static.py:127`，`LIUHAO_PARENT_PID` 归入 `ALLOWLIST` 并写明未设置分支；
- `tests/test_fault_hardening.py`：补丁目标改为 `sys.modules[type(store).__module__]`，消除模块 pop 造成的顺序依赖；
- `.gitignore`：新增 `.liuhao_goals.json.lock`（仓库根的该未跟踪锁文件已删除）。

修复后定点复核：

- `pytest tests/test_fault_hardening.py tests/scripts/test_readiness_c7_profile_gate.py tests/test_deployment_gate_vars_judgement.py -q`
  → **66 passed, 1 skipped**（94.46 s）
- `python scripts/verify_readiness.py --only C7` → **PASS**（`bench --quick --gate PASS on this machine's own profile`）

## 4. 耗时汇总

| 阶段 | 耗时 |
|---|---|
| 主电池 `--full`（C3 采集中断） | 157 s |
| 补充全量（排除 1 个不可采集文件） | 3965.78 s（1:06:05） |
| 定点复核（3 个测试文件） | 94.46 s |
| C7 单独复核 | `--only C7` 一次，PASS |

## 5. 给 owner 的可执行结论

1. **HEAD `3fa88be0` 当前不可能全绿**：唯一缺口是账号/知识切片未提交的 `src/ai/model_router.py` 与 `src/gateway/ai_management.py`。两处提交后，27 条失败 + 1 条采集错误应全部消失，届时应在一个新的冻结 HEAD 上重跑 `--full` 才能出交付凭证。
2. 本轮已修掉不依赖别人的 4 条真回归/守卫失效，并把 `.liuhao_goals.json.lock` 从仓库根清掉、写进 `.gitignore`。
3. 工作树清理：未跟踪锁文件已删除；未 push、未提交任何他人的在途文件（提交 `ad4f5cf3` 仅 5 个文件，逐文件 `git add`）。
