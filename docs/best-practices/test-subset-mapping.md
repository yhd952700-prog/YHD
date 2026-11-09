# 改动路径 → 必跑测试子集

> 目的：日常改动不再跑全量回归（实测一次 3 小时 39 分），只跑受影响子集；
> 全量只在发版门禁跑一次。本文同时规定三个「贵用例」的 `slow` 标记策略。

## 1. 规则

1. 先看 `git diff --name-only`，按第 2 节的映射表得到最小文件集。
2. 映射表没覆盖的新目录，按「同名测试目录 + 反向 import 搜索」兜底：
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q tests/<同名目录>
   # 再补：grep 出 import 了改动模块的测试文件
   ```
3. 子集跑红时，**先按文件分组，再单文件复跑**：单文件绿、整套红 = 顺序/共享状态耦合，
   不是功能缺陷。
4. 改 `src/` 下任意 `__init__.py` 或公共常量时，把映射表对应行整行跑，不要只跑一个文件。

## 2. 映射表

| 改动路径 | 必跑子集 |
|---|---|
| `src/gateway/*.py` | `tests/gateway/` + `tests/test_gateway_auth.py`、`tests/test_console_single_port.py`、`tests/test_dashboard_analytics.py`、`tests/test_overload_isolation.py` |
| `src/gateway/chat.py`、`ai_management.py`、`kernels.py` | 上一行 + `tests/kernels/execution/test_real_execution_wired.py`、`tests/integration/test_execution_loop_e2e.py`、`tests/gateway/test_chat_knowledge.py`、`tests/gateway/test_p10_real_job_e2e.py` |
| `src/security/*` | `tests/security/`、`tests/tenant/` + `tests/test_gateway_auth.py`、`tests/test_cross_account_isolation.py`、`tests/gateway/test_account_scope_three_layers.py` |
| `src/desktop/*`、`scripts/first_run_gui.py` | `tests/desktop/` + `tests/test_first_run_gui.py`、`tests/test_vault_dependency_honesty.py` |
| `src/upgrade/*`、`src/gateway/account_backup.py` | `tests/test_upgrade_disaster_recovery.py` + `tests/gateway/test_account_backup_*.py`、`tests/gateway/test_data_portability.py` |
| `packaging/*`、`installer/*`、`scripts/purge_installed_config.py` | `tests/test_build_bundle_jwt.py`、`tests/test_purge_installed_config.py`、`tests/test_installed_launcher_hygiene.py`、`tests/distribution/` |
| `scripts/verify_*.py` | `tests/test_dependency_declaration_surface.py`、`tests/test_deployment_gate_vars_judgement.py`、`tests/test_contracts_drift_gate.py` |
| `docs/*` | `tests/consistency/` + `tests/test_contracts_drift_gate.py` |
| `apps/console/console/src/*` | 前端 `npm test -- --run` + `tests/test_console_single_port.py` |
| `pyproject.toml` / `requirements.txt` | `tests/test_dependency_declaration_surface.py`、`tests/distribution/`、`tests/test_installed_launcher_hygiene.py` |

## 3. 贵用例与 `slow`

以下三处默认跳过（`tests/conftest.py` 的 `--run-slow` 机制），只在发版门禁开启：

| 用例 | 实测成本 | 标记方式 |
|---|---|---|
| `tests/test_dependency_declaration_surface.py` | 单文件 200 s 上下（真实扫 `src/` + 探测可导入性） | 模块级 `pytestmark = pytest.mark.slow` |
| `tests/test_deployment_gate_vars_judgement.py::test_real_run_reports_ignored_artifacts_as_info_not_findings` | 真实子进程跑部署门禁，> 180 s，已放宽到 420 s | 函数级 `@pytest.mark.slow` |
| `tests/test_contracts_drift_gate.py`（与 `test_p10_real_job_e2e.py` 同组） | 合并实测 17 分 37 秒 | 模块级 `pytestmark = pytest.mark.slow` |

用法：

```powershell
# 日常 / 改代码后：slow 自动跳过
.\.venv\Scripts\python.exe -m pytest -q <子集>

# 发版门禁：显式开启 slow
.\.venv\Scripts\python.exe -m pytest -q --run-slow
```

## 4. 维护

- 新增 `src/<包>` 目录时，在同一提交里补上第 2 节对应行；否则下一个改该包的人会跑漏。
- 新增单次 > 3 分钟的用例，打 `slow` 并在本文第 3 节登记，写清实测成本和理由。
- 门禁自身也必须可信：`scripts/verify_dependency_declaration_surface.py` 遇到无法
  `ast.parse` 的文件（如 UTF-8 BOM）会直接 FAIL 并打印路径，不允许 `[BLOCKED]` 后
  继续报 PASS；回归用例见 `tests/test_dependency_declaration_surface.py`
  的 `test_bom_source_file_makes_the_gate_fail`。

## 5. `gate_contract` 与三态退出码

**`gate_contract` 标记**：把「门禁契约」用例单独标出来（当前 5 个文件：
`tests/kernels/test_lifecycle_runtime_driven.py`、`tests/kernels/test_kernel_registry.py`、
`tests/kernels/test_build_cloud_bundle_arming.py`、`tests/test_check_unknown_register.py`、
`tests/test_deployment_gate_vars_judgement.py`）。本地允许 skip 并打印原因（环境缺依赖时
不假装检查过）；CI 的 `Gate contract tests must not skip` 步骤
（`.github/workflows/ci.yml:122-136`）单独跑 `pytest -m gate_contract --junitxml=...`，
解析 XML 断言 `skipped == 0`，有 skip 就 `::error::NOT VERIFIED` 并让该步失败。
**跳过的契约 = 没检查的契约**，不能算绿。

**三态退出码**（不是「非 0 一律失败」的二元）：

| 脚本 | PASS | FAIL / ERROR | NOT_VERIFIED |
|---|---|---|---|
| `scripts/verify_deploy_readiness.py` | `0` | `1`（真实损坏 / 报错） | `3`（本环境无法证明，不是绿） |
| `scripts/verify_readiness.py` | `0` | `2`（FAIL 矛盾，或 checker 跑不起来） | `3`（NOT_VERIFIED / BLOCKED） |

契约原文见 `verify_deploy_readiness.py:23-30` 与 `verify_readiness.py:1114-1125`
（「pending verification is NOT release-ready」）：`exit 3` 必须带显式 warning，
CI 不得把它渲染成通过。各脚本以自己的 docstring 为准，不要求全局统一：
`scripts/verify_image_mirrors.py:100-102` 按其契约仍用 `2` 表示 NOT_VERIFIED。
