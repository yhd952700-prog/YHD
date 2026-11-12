"""自进化证据件：每次尝试产一份可复读、可校验、防篡改的 JSON。

落盘位置 ``<state_dir>/evidence/<proposal_id>.json``（``.selfmod-state/`` 已在
``.gitignore``）。证据件不是日志：它必须能回答「改了什么、门禁逐条过没过、测试真实
通过多少、回滚点在哪、谁批的」，并且下一次启动能直接读回来做校验。

``validate_evidence`` 是 G10 的判定函数，也是 ``finalize`` 的准入条件；字段缺失、
schema 不符、或 ``diff`` / 正文哈希对不上，一律判 FAIL。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.common.atomic_write import atomic_write_text

SCHEMA = "liuhao.selfevolution.evidence/1"

REQUIRED_FIELDS: Sequence[str] = (
    "schema",
    "proposal_id",
    "tier",
    "stage",
    "result",
    "created_at",
    "base_version",
    "base_hashes",
    "edits",
    "changed_files",
    "diff",
    "diff_sha256",
    "diff_stat",
    "gates",
    "test",
    "approvals",
    "approver",
    "snapshot",
    "rollback_command",
    "content_sha256",
)

GATE_IDS: Sequence[str] = tuple(f"G{i}" for i in range(15))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def content_digest(doc: Dict[str, Any]) -> str:
    """对除 ``content_sha256`` 之外的全部字段取哈希（字段增删/改动都会暴露）。"""
    payload = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def rollback_command(proposal_id: str) -> str:
    return f"python -m src.selfmod rollback --proposal-id {proposal_id}"


def build_evidence(
    *,
    proposal_id: str,
    tier: str = "T1",
    stage: str = "copy",
    result: str = "FAIL",
    base_version: Optional[str] = None,
    base_hashes: Optional[Dict[str, Optional[str]]] = None,
    edits: Iterable[Dict[str, Any]],
    diff: str,
    diff_stat: Dict[str, Any],
    gate_report: Dict[str, Any],
    test: Optional[Dict[str, Any]] = None,
    approvals: Optional[Dict[str, Any]] = None,
    approver: str = "",
    snapshot: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装证据件；``content_sha256`` 最后计算，任何后续改动都会导致校验失败。"""
    edit_list = [
        {"path": str(e.get("path", "")), "content": e.get("content")} for e in edits
    ]
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "proposal_id": proposal_id,
        "tier": tier,
        "stage": stage,
        "result": result,
        "created_at": _utc_now(),
        "base_version": base_version,
        "base_hashes": dict(base_hashes or {}),
        "edits": edit_list,
        "changed_files": [e["path"] for e in edit_list],
        "diff": diff,
        "diff_sha256": _sha256_text(diff),
        "diff_stat": dict(diff_stat),
        "gates": list(gate_report.get("gates", [])),
        "gate_summary": dict(gate_report.get("summary", {})),
        "gate_mode": gate_report.get("gate_mode", "repo"),
        "test": dict(test or {}),
        "approvals": dict(approvals or {}),
        "approver": approver,
        "snapshot": dict(snapshot or {}),
        "rollback_command": rollback_command(proposal_id),
    }
    if extra:
        doc.update(extra)
    doc["content_sha256"] = content_digest(doc)
    return doc


def evidence_path(state_dir: Path, proposal_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", proposal_id) or "unknown"
    return Path(state_dir) / "evidence" / f"{safe}.json"


def write_evidence(state_dir: Path, doc: Dict[str, Any]) -> Path:
    """原子落盘，返回证据件路径。同一 proposal 重跑会覆盖为最新一次尝试。"""
    path = evidence_path(state_dir, str(doc.get("proposal_id", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2))
    return path


def read_evidence(state_dir: Path, proposal_id: str) -> Optional[Dict[str, Any]]:
    path = evidence_path(state_dir, proposal_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_evidence(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """返回 ``{"ok": bool, "problems": [...], "checked_fields": int}``。"""
    problems: List[str] = []
    if not isinstance(doc, dict):
        return {"ok": False, "problems": ["证据件缺失或不是对象"], "checked_fields": 0}

    missing = [name for name in REQUIRED_FIELDS if name not in doc]
    if missing:
        problems.append("缺少必填字段：" + "、".join(missing))

    if doc.get("schema") != SCHEMA:
        problems.append(f"schema 不符：{doc.get('schema')!r}")
    if doc.get("result") not in ("PASS", "FAIL"):
        problems.append(f"result 非法：{doc.get('result')!r}")
    if doc.get("stage") not in ("copy", "finalize", "post-apply"):
        problems.append(f"stage 非法：{doc.get('stage')!r}")

    edits = doc.get("edits")
    if not isinstance(edits, list) or not edits:
        problems.append("edits 为空：证据件必须记录改了什么文件")
    else:
        for idx, edit in enumerate(edits):
            if not isinstance(edit, dict) or not edit.get("path") or "content" not in edit:
                problems.append(f"edits[{idx}] 缺少 path/content")

    gates = doc.get("gates")
    if not isinstance(gates, list) or len(gates) != len(GATE_IDS):
        problems.append(f"gates 必须是 {len(GATE_IDS)} 条（G0–G14）")
    else:
        seen = [str(g.get("id")) for g in gates if isinstance(g, dict)]
        if seen != list(GATE_IDS):
            problems.append(f"门禁顺序/编号不符：{seen}")
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            if gate.get("status") not in ("pass", "fail", "skip"):
                problems.append(f"{gate.get('id')} 状态非法：{gate.get('status')!r}")
            if not str(gate.get("reason") or "").strip():
                problems.append(f"{gate.get('id')} 缺少中文原因")

    test = doc.get("test")
    if not isinstance(test, dict) or "exit_code" not in test:
        problems.append("test 缺少 exit_code（无法证明测试真实跑过）")

    approvals = doc.get("approvals")
    if not isinstance(approvals, dict) or not (approvals.get("test") or {}).get("approver"):
        problems.append("缺少 test 阶段审批记录")
    if doc.get("stage") == "finalize" and not (approvals.get("promote") or {}).get("approver"):
        problems.append("finalize 阶段缺少 promote 审批记录")

    command = str(doc.get("rollback_command") or "")
    if "rollback" not in command or str(doc.get("proposal_id") or "") not in command:
        problems.append("rollback_command 不可用（必须包含 rollback 与 proposal_id）")

    diff = doc.get("diff")
    if isinstance(diff, str) and doc.get("diff_sha256"):
        if _sha256_text(diff) != doc["diff_sha256"]:
            problems.append("diff 哈希不符：证据件被改动过")

    if doc.get("content_sha256") and content_digest(doc) != doc["content_sha256"]:
        problems.append("content_sha256 不符：证据件被改动过")

    checked = len(REQUIRED_FIELDS) - len(missing)
    return {"ok": not problems, "problems": problems, "checked_fields": checked}
