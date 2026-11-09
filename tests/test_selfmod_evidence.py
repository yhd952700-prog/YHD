"""证据件（``.selfmod-state/evidence/<proposal_id>.json``）的落盘与校验测试。

证据件是 T1-3 的验收物：缺字段、门禁结果不全、审批缺失、diff 被改过，都必须判 FAIL，
而且检查函数与 ``finalize`` 用的是同一份规则。
"""

from __future__ import annotations

from pathlib import Path

from src.selfmod import (
    FileEdit,
    build_evidence,
    diff_stat,
    evidence_path,
    read_evidence,
    validate_evidence,
    write_evidence,
)


def _gates(status: str = "pass") -> list:
    return [
        {"id": f"G{i}", "name": f"G{i}", "status": status, "reason": f"G{i} 原因", "duration_ms": 0}
        for i in range(15)
    ]


def _doc(**overrides):
    base = dict(
        proposal_id="p-0001",
        tier="T1",
        stage="copy",
        result="PASS",
        base_version="0.1.1",
        base_hashes={"docs/guide.md": "abc"},
        edits=[{"path": "docs/guide.md", "content": "# 指南\n"}],
        diff="--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -1 +1 @@\n-a\n+b\n",
        diff_stat=diff_stat("--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -1 +1 @@\n-a\n+b\n"),
        gate_report={"gates": _gates(), "summary": {"pass": 15, "fail": 0, "skip": 0}},
        test={"command": "python -m pytest", "exit_code": 0, "passed": True, "counts": {"passed": 12}},
        approvals={"test": {"approver": "liuhao", "at": "2026-11-09T00:00:00+00:00"}},
        approver="liuhao",
    )
    base.update(overrides)
    return build_evidence(**base)


def test_write_read_roundtrip_validates(tmp_path: Path) -> None:
    doc = _doc()
    path = write_evidence(tmp_path / "state", doc)

    assert path == evidence_path(tmp_path / "state", "p-0001")
    assert path.is_file()

    loaded = read_evidence(tmp_path / "state", "p-0001")
    check = validate_evidence(loaded)
    assert check["ok"], check["problems"]
    assert check["checked_fields"] >= 18

    assert loaded["rollback_command"] == "python -m src.selfmod rollback --proposal-id p-0001"
    assert loaded["changed_files"] == ["docs/guide.md"]


def test_missing_field_is_rejected() -> None:
    doc = _doc()
    doc.pop("test")
    check = validate_evidence(doc)
    assert not check["ok"]
    assert any("test" in p for p in check["problems"])


def test_tampered_diff_is_rejected() -> None:
    doc = _doc()
    doc["diff"] = doc["diff"] + "+ 偷偷加的一行\n"
    check = validate_evidence(doc)
    assert not check["ok"]
    assert any("diff 哈希不符" in p for p in check["problems"])


def test_tampered_content_digest_is_rejected() -> None:
    doc = _doc()
    doc["result"] = "FAIL"
    check = validate_evidence(doc)
    assert any("content_sha256" in p for p in check["problems"])


def test_gate_list_must_cover_g0_to_g14() -> None:
    doc = _doc()
    doc["gates"] = doc["gates"][:14]
    check = validate_evidence(doc)
    assert not check["ok"]
    assert any("G0–G14" in p for p in check["problems"])


def test_finalize_requires_promote_approval() -> None:
    doc = _doc(stage="finalize")
    check = validate_evidence(doc)
    assert not check["ok"]
    assert any("promote" in p for p in check["problems"])


def test_missing_test_exit_code_is_rejected() -> None:
    doc = _doc(test={"command": "python -m pytest", "passed": True})
    check = validate_evidence(doc)
    assert not check["ok"]
    assert any("exit_code" in p for p in check["problems"])


def test_evidence_survives_new_process(tmp_path: Path) -> None:
    write_evidence(tmp_path / "state", _doc())
    raw = evidence_path(tmp_path / "state", "p-0001").read_text(encoding="utf-8")
    assert '"schema": "liuhao.selfevolution.evidence/1"' in raw
    assert '"rollback_command"' in raw
