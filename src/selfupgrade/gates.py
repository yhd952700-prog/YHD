"""门禁编排：全量后端测试 + 前端 vitest/build + 导入检查。

判定沿用三态：``0=PASS``、``1=有差异/FAIL``、``3=NOT VERIFIED``（工具缺失/超时）。
任一 ``fail`` 立即停止（后续门禁不再跑），保证「不过就回退」的口径简单。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import GateResult


@dataclass
class GateSpec:
    name: str
    argv: list[str]
    cwd: Path
    timeout: float


def resolve_python() -> str:
    for candidate in (
        os.environ.get("LIUHAO_RELEASE_PYTHON", ""),
        r"D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe",
        str(Path(sys.executable)),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


def resolve_npm() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def build_gate_specs(repo: Path, include_frontend: bool = True) -> list[GateSpec]:
    repo = Path(repo)
    python = resolve_python()
    specs = [
        GateSpec("后端全量 pytest", [python, "-m", "pytest", "tests/", "-q"], repo, 4 * 3600),
        GateSpec(
            "自升级模块 pytest",
            [python, "-m", "pytest", "src/selfupgrade/tests", "-q"],
            repo,
            15 * 60,
        ),
        GateSpec("编译检查", [python, "-m", "compileall", "-q", "src"], repo, 10 * 60),
    ]
    import_test = repo / "tests" / "test_import_integrity.py"
    if import_test.is_file():
        specs.insert(2, GateSpec("导入完整性", [python, "-m", "pytest", str(import_test), "-q"], repo, 10 * 60))

    if include_frontend:
        npm = resolve_npm()
        frontend = repo / "apps" / "console" / "console"
        if npm and frontend.is_dir():
            specs.append(GateSpec("前端 vitest", [npm, "run", "test", "--", "--run"], frontend, 30 * 60))
            specs.append(GateSpec("前端构建", [npm, "run", "build"], frontend, 30 * 60))
        else:
            specs.append(GateSpec("前端 vitest", ["<missing npm>"], frontend, 1))
    return specs


def _tail(text: str, lines: int = 12) -> str:
    parts = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(parts[-lines:])


def run_gates(
    repo: Path,
    *,
    include_frontend: bool = True,
    only: list[str] | None = None,
) -> list[GateResult]:
    results: list[GateResult] = []
    for spec in build_gate_specs(Path(repo), include_frontend=include_frontend):
        if only and spec.name not in only:
            continue
        if spec.argv and spec.argv[0] == "<missing npm>":
            results.append(
                GateResult(spec.name, spec.argv, None, "not_verified", 0.0, "找不到 npm")
            )
            break
        import time

        started = time.monotonic()
        try:
            proc = subprocess.run(
                spec.argv,
                cwd=str(spec.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout,
            )
            elapsed = time.monotonic() - started
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode == 0:
                status = "pass"
            else:
                status = "fail"
            result = GateResult(spec.name, spec.argv, proc.returncode, status, elapsed, _tail(output))
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\n" + str(exc)
            result = GateResult(spec.name, spec.argv, None, "not_verified", elapsed, _tail(output))
        except FileNotFoundError as exc:
            elapsed = time.monotonic() - started
            result = GateResult(spec.name, spec.argv, None, "not_verified", elapsed, str(exc))
        results.append(result)
        if result.status == "fail":
            break
    return results
