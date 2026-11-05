"""审计回放：冷启动只读审计事件流，重建一次自动化会话的执行轨迹。

设计约束：

* **不依赖进程内缓存**：重建所需的全部信息都来自 ``AUTOMATION_ACTION`` 审计
  事件；调用方可以拿一个新的 :class:`AuditStore` 指向同一个库文件来复核。
* **不伪造明文**：审计只保存目标/文本的哈希摘要，所以回放给出的是摘要而非
  原文；"对什么"以 ``target`` 作用域 + ``target_ref_sha256`` 呈现。
* **完整性不隐瞒**：哈希链校验失败时照常重建，但 ``chain_verified=False`` 并
  写明原因，绝不把"验不了"当成"没问题"。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..kernels.audit import AuditEventType, AuditStore
from .contracts import CONSECUTIVE_FAILURE_LIMIT


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_sha256(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("sha256")
    return str(value) if value else None


@dataclass(frozen=True)
class ReplayStep:
    """一个步骤的重建结果。"""

    step_no: int
    action: str
    target: str
    target_ref_sha256: Optional[str]
    expected_focus_sha256: Optional[str]
    action_fingerprint: str
    reversibility: Optional[str]
    params: Dict[str, Any]
    stages: Tuple[str, ...]
    outcomes: Tuple[str, ...]
    errors: Tuple[str, ...]
    result: str
    first_seen_at: float
    last_seen_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_no": self.step_no,
            "action": self.action,
            "target": self.target,
            "target_ref_sha256": self.target_ref_sha256,
            "expected_focus_sha256": self.expected_focus_sha256,
            "action_fingerprint": self.action_fingerprint,
            "reversibility": self.reversibility,
            "params": self.params,
            "stages": list(self.stages),
            "outcomes": list(self.outcomes),
            "errors": list(self.errors),
            "result": self.result,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class SessionReplay:
    """一次会话的完整回放。"""

    principal: str
    session_id: str
    session_sha256: str
    chain_verified: bool
    events: int
    truncated: bool
    steps: List[ReplayStep] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    first_seen_at: Optional[float] = None
    last_seen_at: Optional[float] = None

    def _outcomes(self) -> List[str]:
        return [
            step.result
            for step in self.steps
            if step.result in ("executed", "failed", "rolled_back")
        ]

    def derive_state(self) -> Dict[str, Any]:
        """从步骤轨迹推导会话级状态（失败计数 / 是否熔断 / 回滚情况）。"""

        consecutive = 0
        max_consecutive = 0
        for result in self._outcomes():
            if result == "failed":
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            elif result == "executed":
                consecutive = 0
        return {
            "steps": len(self.steps),
            "executed": sum(1 for step in self.steps if step.result == "executed"),
            "failed": sum(1 for step in self.steps if step.result == "failed"),
            "rejected": sum(1 for step in self.steps if step.result == "rejected"),
            "pending": sum(1 for step in self.steps if step.result == "pending"),
            "rolled_back": sum(1 for step in self.steps if step.result == "rolled_back"),
            "rollback_failed": sum(
                1 for step in self.steps if step.result == "rollback_failed"
            ),
            "max_consecutive_failures": max_consecutive,
            "frozen": max_consecutive >= CONSECUTIVE_FAILURE_LIMIT,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "principal": self.principal,
            "session_id": self.session_id,
            "session_sha256": self.session_sha256,
            "chain_verified": self.chain_verified,
            "events": self.events,
            "truncated": self.truncated,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "state": self.derive_state(),
            "steps": [step.to_dict() for step in self.steps],
            "warnings": list(self.warnings),
        }


class AuditReplay:
    """从审计库重建会话；默认自己开一个只读用途的 store 实例。"""

    def __init__(self, store: Optional[AuditStore] = None, *, db_path: Optional[str] = None):
        if store is None and db_path is None:
            raise ValueError("AuditReplay requires either store or db_path")
        self._store = store or AuditStore(db_path=str(db_path))
        self._owns_store = store is None
        if self._owns_store:
            self._store.initialize()

    def rebuild(
        self,
        *,
        principal: str,
        session_id: str,
        limit: Optional[int] = None,
    ) -> SessionReplay:
        session_sha = _sha256_text(str(session_id))
        verified, _total = self._store.verify_integrity()

        events = self._store.query_events(
            principal_id=str(principal),
            event_type=AuditEventType.AUTOMATION_ACTION,
            limit=limit,
        )
        matching = [
            event
            for event in events
            if (event.get("details") or {}).get("session", {}).get("sha256") == session_sha
        ]
        total_matching = self._store.count_events(
            principal_id=str(principal),
            event_type=AuditEventType.AUTOMATION_ACTION,
        )
        truncated = total_matching > len(events)

        replay = SessionReplay(
            principal=str(principal),
            session_id=str(session_id),
            session_sha256=session_sha,
            chain_verified=bool(verified),
            events=len(matching),
            truncated=truncated,
        )
        if not verified:
            replay.warnings.append("audit_hash_chain_verification_failed")
        if truncated:
            replay.warnings.append("audit_query_truncated")

        step_events: Dict[int, List[Dict[str, Any]]] = {}
        for event in matching:
            details = event.get("details") or {}
            step_no = int(details.get("step_no") or 0)
            step_events.setdefault(step_no, []).append(event)
            timestamp = float(event.get("timestamp") or 0.0)
            replay.first_seen_at = (
                timestamp
                if replay.first_seen_at is None
                else min(replay.first_seen_at, timestamp)
            )
            replay.last_seen_at = (
                timestamp
                if replay.last_seen_at is None
                else max(replay.last_seen_at, timestamp)
            )

        for step_no in sorted(step_events):
            replay.steps.append(_build_step(step_no, step_events[step_no]))
        return replay


def _build_step(step_no: int, events: List[Dict[str, Any]]) -> ReplayStep:
    details = events[0].get("details") or {}
    stages: List[str] = []
    outcomes: List[str] = []
    errors: List[str] = []
    for event in events:
        stage = str((event.get("details") or {}).get("stage") or "")
        stages.append(stage)
        outcomes.append(str(event.get("outcome") or ""))
        error = _digest_sha256((event.get("details") or {}).get("error"))
        if error:
            errors.append(error)

    result = "pending"
    if any(stage == "intent" and outcome == "deny" for stage, outcome in zip(stages, outcomes)):
        result = "rejected"
    elif "result" in stages:
        result_index = stages.index("result")
        result = "executed" if outcomes[result_index] == "allow" else "failed"
    if "rollback_result" in stages:
        rollback_index = stages.index("rollback_result")
        result = (
            "rolled_back"
            if outcomes[rollback_index] == "allow"
            else "rollback_failed"
        )

    timestamps = [float(event.get("timestamp") or 0.0) for event in events]
    return ReplayStep(
        step_no=step_no,
        action=str(details.get("action") or ""),
        target=str(details.get("target") or ""),
        target_ref_sha256=_digest_sha256(details.get("target_ref")),
        expected_focus_sha256=_digest_sha256(details.get("expected_focus")),
        action_fingerprint=str(details.get("action_fingerprint") or ""),
        reversibility=details.get("reversibility"),
        params=dict(details.get("params") or {}),
        stages=tuple(stages),
        outcomes=tuple(outcomes),
        errors=tuple(errors),
        result=result,
        first_seen_at=min(timestamps),
        last_seen_at=max(timestamps),
    )
