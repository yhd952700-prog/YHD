# 全量回归基线覆盖（2026-11-06）

> 目的：把「这轮回归到底覆盖了哪个提交、哪些提交靠增量补」写死，
> 避免再把「全量基线 + 增量」说成「全量覆盖最终 HEAD」。

## 1. 结论

- 本轮全量回归的基线：`c5c2b79273a970f86ebc2ece160f499899da82db`
  （干净 detached worktree `D:\copilot-worktrees\liuhao-final-c5c2b792`，在 temp 根之外）。
- 决策时 HEAD：`e9c1b61881efae7aa3b3c0d41887111aa6d57f0d`。
- **诚实表述**：本轮是「**全量基线 `c5c2b792` + `e9c1b618` 的文档级差异 + 指定增量核验**」，
  **不是**「全量覆盖最终 HEAD」。
- `git diff --name-only c5c2b792 e9c1b618` 只有 1 个文件：
  `docs/self-upgrade.md`（纯文档，无代码/测试变化）。

## 2. 逐提交核对

核对命令：`git merge-base --is-ancestor <commit> c5c2b792`（退出码 0 = 已包含）。

| 提交 | 内容 | 已包含在基线 `c5c2b792` | 覆盖方式 |
|---|---|---|---|
| `bc1d7b2b` | fix(upgrade): honor --allow-same-or-dowgrade and resolve installer data dir | **是** | 全量回归包含（代码+测试都在基线树里） |
| `747af1fd` | docs(upgrade): use the canonical flag name and fix argparse help escaping | **是** | 全量回归包含（含 `src/upgrade/cli.py` 与 `tests/test_upgrade_cli.py`） |
| `e9c1b618` | docs(upgrade): record same-version/downgrade and installed data-dir evidence | **否** | 仅文档；并入 §3 增量核验，不产生代码覆盖缺口 |

> 网关 UTF-8 修复 `b664cfc2` 也是基线 `c5c2b792` 的祖先，同样在全量回归内。

## 3. 增量核验（在 `e9c1b618` 的干净 worktree 上跑）

范围：`tests/` 下与两处修复直接相关的文件。

```bash
python -m pytest -q \
  tests/test_upgrade_api.py tests/test_upgrade_apply_rollback.py tests/test_upgrade_cli.py \
  tests/test_upgrade_data_guard.py tests/test_upgrade_disaster_recovery.py \
  tests/test_upgrade_download_verify.py tests/test_upgrade_selfcheck.py \
  tests/test_upgrade_snapshot.py tests/test_upgrade_version_source.py \
  tests/test_upgrade_weekly_check.py \
  tests/desktop/test_startup_gate.py tests/desktop/test_desktop_system_layer.py \
  tests/test_start_liuhao.py \
  tests/test_gateway_logging_encoding.py tests/observability/test_logging_rotation.py \
  tests/test_startup_status_api.py
```

| 项目 | 值 |
|---|---|
| 基线 | `e9c1b618`（代码树与 `c5c2b792` 相同，仅多一份文档） |
| 结果 | 待回填（本轮全量跑完后执行） |
| JUnit | `docs/evidence/regression-delta-e9c1b618.xml`（跑完后生成） |

## 4. 全量回归证据

- 基线提交：`c5c2b792`
- 日志：`D:\cache\temp\verify-full-c5c2b792.log`
- 逐关报告：`D:\copilot-worktrees\liuhao-final-c5c2b792\scripts\readiness_report.json`
- 逐关原始输出：待跑完后落到 `docs/evidence/regression-c5c2b792.txt`
