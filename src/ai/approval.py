"""Human-in-the-loop 审批工作流（十源 packages 层 approval 包的真实实现）。

把「人类审批」从 evolution 的 human-approve 门、governance 的 EmergencyControl
中抽出来，成为一等概念：提交 → 待审 → 批准/拒绝 → 审计留痕。

复用而非重写：
- 审批决策经 audit kernel 记录（principal=approver），审批是**显式的人类决策**，
  不绕过 policy、不自动放行（NO-FAKE）。
- 状态机是确定性的：已决策的请求拒绝重复审批，未知请求诚实返回 None。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalRequest:
    """一条待审/已决的审批请求。"""

    id: str
    subject: str
    action: str
    requester: str
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH / CRITICAL
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "action": self.action,
            "requester": self.requester,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decided_by": self.decided_by,
            "reason": self.reason,
        }


class ApprovalWorkflow:
    """审批工作流：submit → pending → approve/reject → audit 留痕。

    ``audit_store`` 可注入（测试用 stub）；默认用真实 audit kernel。
    """

    def __init__(self, audit_store: Any = None) -> None:
        from src.kernels.audit import get_audit_store

        self._audit = audit_store if audit_store is not None else get_audit_store()
        self._requests: Dict[str, ApprovalRequest] = {}

    def submit(
        self,
        subject: str,
        action: str,
        requester: str,
        risk_level: str = "LOW",
    ) -> ApprovalRequest:
        """提交一条审批请求，进入 PENDING。"""
        req = ApprovalRequest(
            id=f"apr_{uuid.uuid4().hex[:12]}",
            subject=subject,
            action=action,
            requester=requester,
            risk_level=risk_level,
        )
        self._requests[req.id] = req
        return req

    def approve(self, request_id: str, approver: str, reason: str = "") -> Optional[ApprovalRequest]:
        return self._decide(request_id, approver, ApprovalStatus.APPROVED, reason, "allow")

    def reject(self, request_id: str, approver: str, reason: str = "") -> Optional[ApprovalRequest]:
        return self._decide(request_id, approver, ApprovalStatus.REJECTED, reason, "deny")

    def _decide(
        self,
        request_id: str,
        approver: str,
        status: ApprovalStatus,
        reason: str,
        outcome: str,
    ) -> Optional[ApprovalRequest]:
        req = self._requests.get(request_id)
        if req is None:
            return None  # 未知请求：诚实返回 None
        if req.status != ApprovalStatus.PENDING:
            return None  # 已决策：拒绝重复审批
        req.status = status
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = approver
        req.reason = reason

        # 审计留痕：人类显式决策（human_sovereignty_override），非自动放行。
        try:
            from src.kernels.audit import AuditEventType, AuditScope

            self._audit.log_event(
                event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE,
                principal_id=approver,
                scope=AuditScope.L0,
                outcome=outcome,
                details={
                    "approval_id": request_id,
                    "subject": req.subject,
                    "action": req.action,
                    "reason": reason,
                },
            )
        except Exception:
            # 审计失败不阻断审批决策本身（决策已落内存，审计尽力而为）。
            pass
        return req

    def status(self, request_id: str) -> Optional[str]:
        req = self._requests.get(request_id)
        return req.status.value if req else None

    def pending(self) -> List[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]
