"""Data contracts for controlled self-modification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class ProposalStatus(str, Enum):
    PENDING = "pending"          # waiting for a human to confirm
    CONFIRMED = "confirmed"      # human confirmed; may be tested on a copy
    TESTING = "testing"
    PASSED = "passed"            # copy + tests passed; real workspace untouched
    REJECTED = "rejected"        # tests failed / timed out / resource limit
    APPLIED = "applied"          # explicitly promoted to the real workspace
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class FileEdit:
    path: str
    content: Optional[str]  # None = delete the file

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "content": self.content, "delete": self.content is None}


@dataclass
class Proposal:
    id: str
    created_at: str
    updated_at: str
    title: str
    rationale: str
    status: ProposalStatus
    diff: str
    edits: List[FileEdit]
    base_hashes: Dict[str, Optional[str]]
    test_command: Optional[List[str]]
    base_version: Optional[str] = None
    reason: str = ""
    approver: str = ""
    approved_at: Optional[str] = None
    test_summary: Dict[str, Any] = field(default_factory=dict)
    backup_dir: Optional[str] = None
    applied_at: Optional[str] = None
    rolled_back_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "rationale": self.rationale,
            "status": self.status.value,
            "diff": self.diff,
            "edits": [edit.to_dict() for edit in self.edits],
            "base_hashes": self.base_hashes,
            "test_command": self.test_command,
            "base_version": self.base_version,
            "reason": self.reason,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "test_summary": self.test_summary,
            "backup_dir": self.backup_dir,
            "applied_at": self.applied_at,
            "rolled_back_at": self.rolled_back_at,
        }


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float = 300.0
    memory_mb: int = 2048
    cpu_seconds: float = 0.0  # 0 = disabled
    poll_interval_seconds: float = 0.1
    max_output_bytes: int = 200_000


@dataclass
class RunResult:
    command: Sequence[str]
    returncode: int
    timed_out: bool
    limit_reason: str
    duration_seconds: float
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.limit_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "limit_reason": self.limit_reason,
            "duration_seconds": round(self.duration_seconds, 3),
            "output_tail": self.output_tail,
            "passed": self.passed,
        }
