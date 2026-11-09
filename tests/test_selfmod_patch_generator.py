"""T1-1: the generator writes candidates, never applies them, never drops findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.selfmod import InvalidEdit, SelfModError
from src.selfmod.generator import CANDIDATE_SCHEMA, Finding, generate, generate_all

EXAMPLES = "examples/use_cases/customer_support_bot.py"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "examples" / "use_cases").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "ARCHITECTURE.md").write_text(
        "# 架构\n\n2. 不把 TODO 当完成。\n", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "verify_doc_links.py").write_text("print('gate')\n", encoding="utf-8")
    (root / EXAMPLES).write_text(
        'ARTICLE = "电话支持：400-XXX-XXXX（工作日 9:00-18:00）"\n', encoding="utf-8"
    )
    return root


def _out_dir(workspace: Path) -> Path:
    return workspace / "outputs" / "self-improve" / "candidates"


def _phone_finding() -> Finding:
    return Finding.from_dict(
        {
            "id": "gap-todo-a129f0d0",
            "source": "todo",
            "severity": "minor",
            "title": f"XXX {EXAMPLES}:219 电话支持：400-XXX-XXXX",
            "evidence": f"[todo] {EXAMPLES}:219: 电话支持：400-XXX-XXXX",
            "paths": [EXAMPLES],
            "auto_eligible": True,
        }
    )


def test_masked_phone_generates_candidate(workspace: Path) -> None:
    before = (workspace / EXAMPLES).read_text(encoding="utf-8")
    record = generate(_phone_finding(), workspace=workspace, out_dir=_out_dir(workspace))

    directory = _out_dir(workspace) / "gap-todo-a129f0d0"
    assert record["verdict"] == "candidate"
    assert record["path_check"] == "pass"
    assert record["applied"] is False
    assert record["test_command"] == [sys.executable, "-m", "pytest"]

    diff = (directory / "patch.diff").read_text(encoding="utf-8")
    assert "-" in diff and "400-XXX-XXXX" in diff
    assert "+" in diff and "400-000-0000" in diff
    assert (directory / "proposal.json").exists()
    assert (directory / "finding.json").exists()

    # Never applied: the workspace file is byte-identical.
    assert (workspace / EXAMPLES).read_text(encoding="utf-8") == before


def test_prohibited_path_finding_is_recorded_as_unfixable(workspace: Path) -> None:
    finding = Finding.from_dict(
        {
            "id": "gap-todo-299e9fea",
            "source": "todo",
            "severity": "minor",
            "title": "FIXME scripts/self_improve/collectors_ops.py:1",
            "paths": ["scripts/verify_doc_links.py"],
        }
    )
    record = generate(finding, workspace=workspace, out_dir=_out_dir(workspace))
    directory = _out_dir(workspace) / "gap-todo-299e9fea"

    assert record["verdict"] == "unfixable"
    assert record["reason_code"] == "prohibited_path"
    assert record["applied"] is False
    assert "scripts/verify_doc_links.py" in record["reason"]
    assert (directory / "unfixable.json").exists()
    assert not (directory / "patch.diff").exists()


def test_doc_todo_finding_records_semantic_reason(workspace: Path) -> None:
    finding = Finding.from_dict(
        {
            "id": "gap-todo-4958f7a0",
            "source": "todo",
            "severity": "minor",
            "title": "TODO docs/ARCHITECTURE.md:120 2. 不把 TODO 当完成",
            "paths": ["docs/ARCHITECTURE.md"],
        }
    )
    record = generate(finding, workspace=workspace, out_dir=_out_dir(workspace))
    assert record["reason_code"] == "placeholder_not_mechanical"
    assert record["verdict"] == "unfixable"


def test_output_dir_outside_self_improve_is_refused(workspace: Path, tmp_path: Path) -> None:
    with pytest.raises(SelfModError):
        generate(_phone_finding(), workspace=workspace, out_dir=tmp_path / "elsewhere")


def test_unsafe_finding_id_is_refused(workspace: Path) -> None:
    finding = Finding.from_dict({"id": "../../escape", "paths": [EXAMPLES]})
    with pytest.raises(InvalidEdit):
        generate(finding, workspace=workspace, out_dir=_out_dir(workspace))


def test_generate_all_records_every_finding(workspace: Path) -> None:
    findings = [
        _phone_finding(),
        Finding.from_dict(
            {
                "id": "gap-todo-4958f7a0",
                "source": "todo",
                "title": "TODO docs/ARCHITECTURE.md:120",
                "paths": ["docs/ARCHITECTURE.md"],
            }
        ),
        Finding.from_dict(
            {
                "id": "gap-known-gaps-afed01e3",
                "source": "known-gaps",
                "title": "自升级 + 受控自改代码",
                "paths": [],
            }
        ),
    ]
    summary = generate_all(findings, workspace=workspace, out_dir=_out_dir(workspace))

    assert summary["total"] == 3
    assert summary["candidates"] == ["gap-todo-a129f0d0"]
    assert len(summary["unfixable"]) == 2
    assert set(summary["unfixable"]) == {"gap-todo-4958f7a0", "gap-known-gaps-afed01e3"}
    assert summary["applied"] == []

    run_file = json.loads(
        (_out_dir(workspace) / "_run.json").read_text(encoding="utf-8")
    )
    assert run_file["total"] == 3
    for finding_id in summary["candidates"] + summary["unfixable"]:
        directory = _out_dir(workspace) / finding_id
        assert (directory / "finding.json").exists()
        assert (directory / "patch.diff").exists() or (directory / "unfixable.json").exists()
    assert CANDIDATE_SCHEMA in json.dumps(
        json.loads((_out_dir(workspace) / "gap-todo-a129f0d0" / "proposal.json").read_text(encoding="utf-8")),
        ensure_ascii=False,
    )
