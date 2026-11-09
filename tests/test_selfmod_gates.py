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
import shutil
from pathlib import Path

import pytest

from src.selfmod import FileEdit, PathPolicy, ResourceLimits, run_gates

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
    assert g0 is not None and g0.status == "fail", path
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
    assert g0 is not None and g0.status == "fail"
    assert "数据库结构变更" in g0.reason


def test_g4_doc_link_gate_runs_and_fails_on_broken_link(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    guide = root / "docs" / "guide.md"
    guide.write_text("# 指南\n\n见 [不存在的文件](missing.md)\n", encoding="utf-8")

    report = _run(root, [FileEdit("docs/guide.md", "# 指南\n")], _diff("docs/guide.md"))

    g4 = report.gate("G4")
    assert g4 is not None and g4.status == "fail"
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
    assert g8 is not None and g8.status == "fail"
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
