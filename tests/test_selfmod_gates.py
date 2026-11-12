"""SE-Gate（G0–G14）行为测试。

这些用例证明三件事：

1. 永久禁区是硬拦截，且发生在前置阶段（后续门禁不会把结论翻成 PASS）；
2. 文档门禁真的会执行 ``scripts/verify_doc_links.py``，链接断掉时该命令退出 1；
3. 门禁没有 ``force`` / 子集选择一类参数，调用方无法绕过。

用例里的临时仓库只复制 ``scripts/verify_doc_links.py``：其余门禁脚本缺失时按
spec §3「工具缺失 = FAIL」记失败，这正是生产语义，不在这里放宽。
"""

from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path

import pytest

from src.selfmod import FileEdit, PathPolicy, ResourceLimits, run_gates
from src.selfmod.gates import (
    _execute,
    console_build_gate,
    packaging_dry_run_gate,
    verify_console_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LIMITS = ResourceLimits(timeout_seconds=120)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "spec").mkdir(parents=True)
    (root / "docs" / "spec" / "SELF-EVOLUTION.md").write_text("# spec\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "verify_doc_links.py", root / "scripts" / "verify_doc_links.py")
    return root


def _diff(path: str, added: int = 1, removed: int = 0) -> str:
    lines = [f"--- a/{path}\n", f"+++ b/{path}\n", "@@ -1 +1 @@\n"]
    lines += [f"-old {i}\n" for i in range(removed)]
    lines += [f"+new {i}\n" for i in range(added)]
    return "".join(lines)


def _run(root: Path, edits, diff: str, **kwargs):
    return run_gates(
        root,
        edits=edits,
        diff=diff,
        policy=PathPolicy(root),
        limits=LIMITS,
        **kwargs,
    )


@pytest.mark.parametrize(
    "path",
    [
        "src/gateway/policy.py",
        "src/security/sandbox.py",
        "installer/setup.iss",
        "packaging/version.json",
        "requirements.txt",
        "tests/test_selfmod_gates.py",
        "docs/spec/SELF-EVOLUTION.md",
        "alembic/versions/0001_init.py",
    ],
)
def test_g0_blocks_permanent_zones(tmp_path: Path, path: str) -> None:
    root = _repo(tmp_path)
    report = _run(root, [FileEdit(path, "changed\n")], _diff(path))

    g0 = report.gate("G0")
    assert g0 is not None, path
    assert g0.status == "fail", path
    assert report.result == "FAIL"
    # 硬拦截之后不许有门禁"通过"到可以合入——其余门禁必须是 skip。
    assert all(g.status == "skip" for g in report.gates[1:])


def test_g0_blocks_ddl_and_test_weakening_in_new_lines(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = "docs/guide.md"
    diff = (
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1,2 @@\n"
        "+CREATE TABLE secret_seen (id integer);\n"
        "+import pytest\n"
    )
    report = _run(root, [FileEdit(path, "x\n")], diff)
    g0 = report.gate("G0")
    assert g0 is not None
    assert g0.status == "fail"
    assert "数据库结构变更" in g0.reason


def test_g4_doc_link_gate_runs_and_fails_on_broken_link(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    guide = root / "docs" / "guide.md"
    guide.write_text("# 指南\n\n见 [不存在的文件](missing.md)\n", encoding="utf-8")

    report = _run(root, [FileEdit("docs/guide.md", "# 指南\n")], _diff("docs/guide.md"))

    g4 = report.gate("G4")
    assert g4 is not None
    assert g4.status == "fail"
    link_cmd = [
        c for c in g4.commands if c.command and c.command[-1].endswith("verify_doc_links.py")
    ]
    assert link_cmd, "G4 必须真实执行 verify_doc_links.py"
    assert link_cmd[0].exit_code == 1
    assert "RESULT: FAIL" in link_cmd[0].output_tail


def test_g8_rejects_diff_over_per_file_limit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = "docs/big.md"
    report = _run(root, [FileEdit(path, "new line\n" * 500)], _diff(path, added=500))

    g8 = report.gate("G8")
    assert g8 is not None
    assert g8.status == "fail"
    assert "单文件上限" in g8.reason
    assert report.gate("G0").status == "pass"


def test_no_force_or_subset_parameter_exists() -> None:
    params = set(inspect.signature(run_gates).parameters)
    for banned in ("force", "skip", "only", "gates", "subset", "ignore", "override"):
        assert banned not in params


def test_drill_workspace_without_spec_marks_all_gates_skip(tmp_path: Path) -> None:
    root = tmp_path / "drill"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")

    report = _run(root, [FileEdit("app.py", "x = 2\n")], _diff("app.py"))

    assert report.gate_mode == "not-applicable"
    assert report.result == "PASS"
    assert {g.status for g in report.gates} == {"skip"}
    assert len(report.gates) == 15


# ----------------------------------------------------------------------
# G6：只放行控制台构建 + 产物同源校验；G7：打包面继续关闭
# ----------------------------------------------------------------------
def _console_repo(tmp_path: Path, build_body: str) -> Path:
    """造一个带最小控制台与真实 provenance 模块的临时仓库。"""
    root = _repo(tmp_path)
    console = root / "apps" / "console" / "console"
    console.mkdir(parents=True)
    (console / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-console",
                "version": "1.2.3",
                "scripts": {"build": "node build.js"},
            }
        ),
        encoding="utf-8",
    )
    (console / "build.js").write_text(build_body, encoding="utf-8")
    packaging = root / "packaging"
    packaging.mkdir()
    shutil.copy2(
        REPO_ROOT / "packaging" / "console_provenance.py",
        packaging / "console_provenance.py",
    )
    return root


def _cmd_runner(root: Path):
    return lambda gate_id, command: _execute(
        command, cwd=root, limits=LIMITS, timeout_seconds=120
    )


_BUILD_OK = (
    "const fs=require('fs');"
    "fs.mkdirSync('dist',{recursive:true});"
    "fs.writeFileSync('dist/index.html','<html>ok</html>');"
    "fs.writeFileSync('dist/app.js','console.log(1)');"
)
_BUILD_EMPTY = "console.log('no dist produced');"


def test_g6_passes_console_build_when_provenance_matches(tmp_path: Path) -> None:
    root = _console_repo(tmp_path, _BUILD_OK)

    gate = console_build_gate(
        ["apps/console/console/src/App.tsx"], root, _cmd_runner(root)
    )

    assert gate.status == "pass", gate.reason
    meta = json.loads(
        (root / "apps" / "console" / "console" / "dist" / "console-build.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["schema"] == "liuhao.console-build/1"
    assert meta["source_sha256"]
    assert meta["files_sha256"]
    assert meta["command"] == "npm --prefix apps/console/console run build"
    ok, reason = verify_console_provenance(root)
    assert ok, reason


def test_g6_fails_when_build_produces_no_dist(tmp_path: Path) -> None:
    root = _console_repo(tmp_path, _BUILD_EMPTY)

    gate = console_build_gate(
        ["apps/console/console/src/App.tsx"], root, _cmd_runner(root)
    )

    assert gate.status == "fail"
    assert "dist/index.html" in gate.reason


def test_g6_skips_when_no_frontend_change(tmp_path: Path) -> None:
    root = _console_repo(tmp_path, _BUILD_OK)

    gate = console_build_gate(["docs/guide.md"], root, _cmd_runner(root))

    assert gate.status == "skip"
    assert gate.not_applicable is True
    assert "不适用" in gate.reason


def test_g7_rejects_packaging_and_installer_changes() -> None:
    for path in ("packaging/version.json", "installer/setup.iss"):
        gate = packaging_dry_run_gate([path])
        assert gate.status == "fail", path
        assert gate.not_applicable is False, path
        assert "未开放" in gate.reason
        assert "打包白名单" in gate.reason


def test_g7_rejects_packaging_artifact_paths() -> None:
    for path in ("release/liuhao.iss", "build/setup.nsi", "dist/installer.wxs"):
        assert packaging_dry_run_gate([path]).status == "fail", path


def test_g7_skips_frontend_change_because_packaging_face_untouched() -> None:
    """前端改动只归 G6；G7 不得因此变成整条提案的总闸。"""
    gate = packaging_dry_run_gate(["apps/console/console/src/App.tsx"])
    assert gate.status == "skip"
    assert gate.not_applicable is True
    assert "不适用" in gate.reason


def test_g7_skips_unrelated_changes() -> None:
    for path in ("docs/guide.md", "src/core/app.py"):
        gate = packaging_dry_run_gate([path])
        assert gate.status == "skip", path
        assert gate.not_applicable is True, path


def test_src_only_change_keeps_g6_g7_not_applicable(tmp_path: Path) -> None:
    """只动 src/ 的提案：G6/G7 都记不适用，不得记为 FAIL，也不得算作 PASS。"""
    root = _repo(tmp_path)
    path = "src/core/app.py"
    report = _run(root, [FileEdit(path, "x = 2\n")], _diff(path))

    for gate_id in ("G6", "G7"):
        gate = report.gate(gate_id)
        assert gate is not None, gate_id
        assert gate.status == "skip", gate_id
        assert gate.not_applicable is True, gate_id
    payload = report.to_dict()
    assert "G6" in payload["not_applicable"]
    assert "G7" in payload["not_applicable"]
    assert payload["summary"]["skip"] >= 2

