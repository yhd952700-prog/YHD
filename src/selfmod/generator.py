"""Deterministic candidate-patch generator (T1-1).

Input: one finding from ``outputs/self-improve/gap-report.json``.
Output: either a candidate patch or an explicit "unfixable" record — always
written under ``outputs/self-improve/``, never applied to the workspace.

Two invariants are enforced here rather than trusted:

* every edit path goes through :class:`~src.selfmod.paths.PathPolicy`, so the
  generator cannot propose a patch in a prohibited zone;
* the judging command comes from :class:`~src.selfmod.commands.TestCommandPolicy`,
  so the generator cannot pick a subset of tests.

``generate_all`` returns one record per finding. A finding that no rule-based
strategy can repair is still recorded with a reason code — nothing is dropped
silently. There is deliberately no model call and no auto-apply.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.common.atomic_write import atomic_write_text as _atomic_write_text

from .commands import TestCommandPolicy
from .errors import InvalidEdit, SelfModError
from .models import FileEdit
from .patcher import generate_diff, read_text
from .paths import PathPolicy

CANDIDATE_SCHEMA = "liuhao.self-improve.candidate/1"
UNFIXABLE_SCHEMA = "liuhao.self-improve.unfixable/1"
RUN_SCHEMA = "liuhao.self-improve.generate-run/1"

#: Findings are untrusted input (they come from scanned comments and docs), so a
#: finding id may never decide where a file is written.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")

#: ``400-XXX-XXXX`` style masked phone numbers.
_MASKED_PHONE = re.compile(r"\d{3,4}-X{3,}-X{4}")

_MAX_FILE_BYTES = 1024 * 1024
_MAX_DIFF_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    id: str
    source: str = ""
    severity: str = ""
    title: str = ""
    evidence: str = ""
    paths: Tuple[str, ...] = ()
    suggested_direction: str = ""
    auto_eligible: bool = False
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        paths = data.get("paths") or []
        return cls(
            id=str(data.get("id") or ""),
            source=str(data.get("source") or ""),
            severity=str(data.get("severity") or ""),
            title=str(data.get("title") or ""),
            evidence=str(data.get("evidence") or ""),
            paths=tuple(str(item) for item in paths),
            suggested_direction=str(data.get("suggested_direction") or ""),
            auto_eligible=bool(data.get("auto_eligible")),
            reason=str(data.get("reason") or ""),
            raw=dict(data),
        )


@dataclass(frozen=True)
class Repair:
    """A strategy's output: the edits plus why a human should look anyway."""

    strategy: str
    rationale: str
    edits: Tuple[FileEdit, ...]


def _safe_id(finding_id: str) -> str:
    if _SAFE_ID.match(finding_id):
        return finding_id
    raise InvalidEdit(f"发现 id 含不安全字符，拒绝落盘：{finding_id!r}")


def _output_root(workspace: Path, out_dir: str | Path) -> Path:
    root = Path(out_dir).resolve()
    allowed = (workspace / "outputs" / "self-improve").resolve()
    try:
        inside = root == allowed or allowed in root.parents
    except ValueError:
        inside = False
    if not inside:
        raise SelfModError(f"生成产物只能落在 {allowed} 下，拒绝写入：{root}")
    return root


def _classify_skip(
    finding: Finding, policy: PathPolicy
) -> Tuple[str, str]:
    """Return ``(reason_code, human_readable_reason)`` for a non-repairable finding."""
    if finding.paths:
        for raw in finding.paths:
            try:
                policy.normalise(raw)
            except SelfModError as exc:
                return (
                    "prohibited_path",
                    f"落点在永久禁区，规则引擎不得改：{raw}（{exc}）",
                )
    if finding.source == "todo":
        return (
            "placeholder_not_mechanical",
            "TODO/FIXME/XXX 命中需要判断是待办还是文档正文/示例里的占位符；"
            "规则引擎无法区分语义，交人工判定。",
        )
    if finding.source in ("pytest", "packaging", "sbom", "security"):
        return (
            "needs_implementation",
            "该来源的修复需要写新逻辑或更新依赖/门禁，不是确定性文本替换。",
        )
    return (
        "needs_human_judgement",
        "修复需要产品/安全/验收判断（是否做、做到什么程度），规则引擎无法自动决定。",
    )


def _strategy_masked_phone(
    finding: Finding, workspace: Path, policy: PathPolicy
) -> Optional[Repair]:
    """Replace a masked phone number in an example file with a same-shape fake."""
    if len(finding.paths) != 1:
        return None
    rel = finding.paths[0]
    if not rel.endswith(".py"):
        return None
    if "XXX" not in finding.title and "XXX" not in finding.evidence:
        return None
    try:
        policy.normalise(rel)
    except SelfModError:
        return None  # the skip path reports the prohibited zone
    old = read_text(policy.workspace / rel, max_bytes=_MAX_FILE_BYTES)
    if old is None:
        return None
    matches = _MASKED_PHONE.findall(old)
    if len(matches) != 1:
        return None  # zero or ambiguous anchor: not mechanical
    token = matches[0]
    replacement = token.replace("X", "0")
    new = old.replace(token, replacement, 1)
    return Repair(
        strategy="masked_phone_to_zero",
        rationale=(
            f"示例文件里的 {token} 是掩码式占位号码，会被占位符扫描器当成未完成文案；"
            f"同长度零替换为 {replacement}，保持示例格式。"
            "本候选只生成、不应用，合入前需人工确认示例语义。"
        ),
        edits=(FileEdit(rel, new),),
    )


_STRATEGIES: Sequence[Tuple[str, Any]] = (("masked_phone_to_zero", _strategy_masked_phone),)


def _record_unfixable(
    finding: Finding, directory: Path, reason_code: str, reason: str
) -> Dict[str, Any]:
    record = {
        "schema": UNFIXABLE_SCHEMA,
        "finding_id": finding.id,
        "source": finding.source,
        "severity": finding.severity,
        "title": finding.title,
        "reason_code": reason_code,
        "reason": reason,
        "paths": list(finding.paths),
        "applied": False,
        "verdict": "unfixable",
    }
    _atomic_write_text(directory / "unfixable.json", json.dumps(record, ensure_ascii=False, indent=2))
    return record


def generate(
    finding: Finding,
    *,
    workspace: str | Path,
    out_dir: str | Path,
) -> Dict[str, Any]:
    """Write one candidate or one unfixable record; never touch the workspace."""
    workspace_path = Path(workspace).resolve()
    root = _output_root(workspace_path, out_dir)
    directory = root / _safe_id(finding.id)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        directory / "finding.json",
        json.dumps(finding.raw or {"id": finding.id}, ensure_ascii=False, indent=2),
    )

    policy = PathPolicy(workspace_path)
    for name, strategy in _STRATEGIES:
        repair = strategy(finding, workspace_path, policy)
        if repair is None:
            continue
        edits = list(repair.edits)
        # Path check is deliberately inside ``generate_diff``: one call both
        # validates every path and produces the reviewed artifact.
        diff, base_hashes = generate_diff(
            edits,
            policy,
            max_file_bytes=_MAX_FILE_BYTES,
            max_diff_bytes=_MAX_DIFF_BYTES,
        )
        command = TestCommandPolicy().validate([sys.executable, "-m", "pytest"])
        proposal = {
            "schema": CANDIDATE_SCHEMA,
            "finding_id": finding.id,
            "title": f"[候选] {finding.title}"[:200],
            "rationale": repair.rationale,
            "strategy": repair.strategy,
            "edits": [edit.to_dict() for edit in edits],
            "base_hashes": base_hashes,
            "test_command": command,
            "applied": False,
            "path_check": "pass",
            "verdict": "candidate",
        }
        _atomic_write_text(directory / "patch.diff", diff + "\n")
        _atomic_write_text(
            directory / "proposal.json",
            json.dumps(proposal, ensure_ascii=False, indent=2),
        )
        return proposal

    reason_code, reason = _classify_skip(finding, policy)
    return _record_unfixable(finding, directory, reason_code, reason)


def generate_all(
    findings: Iterable[Finding],
    *,
    workspace: str | Path,
    out_dir: str | Path,
) -> Dict[str, Any]:
    """Process every finding and write ``_run.json``; no finding is skipped silently."""
    workspace_path = Path(workspace).resolve()
    root = _output_root(workspace_path, out_dir)
    root.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    for finding in findings:
        records.append(generate(finding, workspace=workspace_path, out_dir=root))
    summary = {
        "schema": RUN_SCHEMA,
        "total": len(records),
        "candidates": [item["finding_id"] for item in records if item["verdict"] == "candidate"],
        "unfixable": [item["finding_id"] for item in records if item["verdict"] == "unfixable"],
        "applied": [],
    }
    _atomic_write_text(root / "_run.json", json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _load_findings(report_path: Path) -> List[Finding]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    gaps = data.get("gaps") if isinstance(data, dict) else None
    if not isinstance(gaps, list):
        raise SelfModError(f"报告里没有 gaps 列表：{report_path}")
    return [Finding.from_dict(item) for item in gaps if isinstance(item, dict)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="从扫描发现生成候选补丁（只落盘，不应用）")
    parser.add_argument("--report", default="outputs/self-improve/gap-report.json")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--out-dir", default="outputs/self-improve/candidates")
    parser.add_argument("--finding-id", action="append", default=None)
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    findings = _load_findings(Path(args.report))
    if args.finding_id:
        wanted = set(args.finding_id)
        findings = [item for item in findings if item.id in wanted]
        missing = wanted - {item.id for item in findings}
        if missing:
            raise SelfModError(f"报告里找不到这些发现：{sorted(missing)}")
    summary = generate_all(findings, workspace=workspace, out_dir=args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
