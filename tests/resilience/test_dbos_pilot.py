"""DBOS pilot 验收：默认关闭 + 真杀进程后重启续跑。

这两个断言比任何「理论上可以恢复」都硬：

* 不设开关时 CLI 必须拒绝运行（退出码 2），且不创建任何 DBOS 文件。
* `--kill-at 2` 在 step 2 里 `os._exit(17)`（硬杀，不走 atexit/DBOS destroy）。
  随后同一 workflow id 重启：step 1 不能重跑（步骤日志里只有一份 step1），
  step 2/3 必须续跑并返回 `workflow-complete`。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(db_dir: Path, *args: str, enabled: bool) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("LIUHAO_DBOS_PILOT", None)
    if enabled:
        env["LIUHAO_DBOS_PILOT"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "src.resilience.dbos_pilot", "--db-dir", str(db_dir), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_pilot_is_disabled_by_default_and_creates_nothing(tmp_path):
    proc = _run(tmp_path / "pilot", enabled=False)
    assert proc.returncode == 2, proc.stderr
    assert "DISABLED" in proc.stdout
    assert not (tmp_path / "pilot").exists()


def test_dbos_is_optional_extra_not_a_runtime_dependency():
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [dep.lower() for dep in data["project"]["dependencies"]]
    assert not any(dep.startswith("dbos") for dep in deps)
    extras = data["project"].get("optional-dependencies", {})
    assert any(dep.lower().startswith("dbos") for dep in extras.get("dbos", []))
    assert not any(dep.lower().startswith("apscheduler") for dep in deps)


def test_hard_kill_then_restart_resumes_workflow(tmp_path):
    # DBOS 是可选 extra（`pip install -e ".[dbos]"`），CI 的 unit job 只装
    # `.[tests]`，没有 dbos。没有依赖时这个用例无法成立 —— 跳过并说明原因，
    # 不要让它以"exit 1 vs 17"的假失败形式红掉（那正是 CI run 42127243953 的根因：
    # ModuleNotFoundError: No module named 'dbos' → RuntimeError → 退出码 1）。
    pytest.importorskip(
        "dbos", reason='DBOS pilot 依赖可选 extra：pip install -e ".[dbos]"'
    )
    db_dir = tmp_path / "pilot"

    first = _run(db_dir, "--kill-at", "2", enabled=True)
    # 硬杀只保证"非零 + 停在 step 2"，不钉死具体数字：POSIX 下被信号终止时
    # subprocess 的 returncode 表现与 Windows 不同（本机 Windows 实测 17，
    # 即 os._exit(17) 原样返回；POSIX 若真是信号终止会是 -N）。
    # 真正的失败形态是"没杀掉却正常走完(0)"或被依赖缺失伪装成别的退出码(1)。
    assert first.returncode != 0, (
        f"expected the process to die inside step 2, got exit {first.returncode}\n"
        f"stdout:\n{first.stdout}\nstderr:\n{first.stderr}"
    )
    assert "HARD KILL inside step 2" in first.stdout, first.stdout
    assert "DBOS pilot is DISABLED" not in first.stdout
    assert "ModuleNotFoundError" not in first.stderr, first.stderr
    log = (db_dir / "pilot_steps.log").read_text(encoding="utf-8").splitlines()
    assert log == ["step1"]

    second = _run(db_dir, enabled=True)
    assert second.returncode == 0, second.stderr
    assert "workflow-complete" in second.stdout

    resumed = (db_dir / "pilot_steps.log").read_text(encoding="utf-8").splitlines()
    # 关键证据：step1 只有一份（恢复重放跳过了已完成的步骤），step2/3 续跑完成。
    # 也就是说硬杀后重启不会产生重复执行。
    assert resumed == ["step1", "step2", "step3"]
    assert resumed.count("step1") == 1

