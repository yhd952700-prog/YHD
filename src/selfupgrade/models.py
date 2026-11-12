"""自升级用到的数据结构与三态退出码。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 三态口径（沿用发布门禁）
EXIT_PASS = 0
EXIT_DIFF = 1
EXIT_ARG_IO = 2
EXIT_NOT_VERIFIED = 3


@dataclass
class FileFailure:
    target: str
    expected_sha256: str
    actual_sha256: str
    added: int = 0
    removed: int = 0
    changed_lines: list[str] = field(default_factory=list)
    diff_unavailable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "added": self.added,
            "removed": self.removed,
            "changed_lines": self.changed_lines,
            "diff_unavailable": self.diff_unavailable,
        }


@dataclass
class VerifyResult:
    ok: bool
    exit_code: int
    seq: int | None = None
    manifest_sha256: str | None = None
    problems: list[str] = field(default_factory=list)
    failures: list[FileFailure] = field(default_factory=list)
    source_drift: list[str] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "seq": self.seq,
            "manifest_sha256": self.manifest_sha256,
            "problems": self.problems,
            "failures": [f.to_dict() for f in self.failures],
            "source_drift": self.source_drift,
            "checked_at": self.checked_at,
        }


@dataclass
class GateResult:
    name: str
    command: list[str]
    exit_code: int | None
    status: str  # pass / fail / not_verified
    duration_seconds: float
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": " ".join(self.command),
            "exit_code": self.exit_code,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 1),
            "output_tail": self.output_tail,
        }


@dataclass
class SmokeResult:
    ok: bool
    exit_code: int
    installed: bool
    detail: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "installed": self.installed,
            "detail": self.detail,
            "evidence": self.evidence,
        }
