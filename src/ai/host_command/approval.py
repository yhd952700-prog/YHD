"""U1 — approval interface (human-sovereign escalation).

A command whose capability requires approval is escalated to a human via
:class:`ApprovalInterface`. By default nothing is granted; a human (or an
authorized approval channel) must explicitly grant. This mirrors the C-4 audited
grant pattern: the agent cannot self-approve a command that needs human
sovereignty. Granted approvals are one-shot (``consume``) so a single grant cannot
be replayed across commands.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from .models import HostCommandRequest


@dataclass
class ApprovalRequest:
    """A pending human-approval request for one host command."""

    approval_id: str
    reason: str
    request_summary: Dict[str, object]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | granted | denied


class ApprovalInterface:
    """In-process approval registry. Default: nothing granted."""

    def __init__(self) -> None:
        self._pending: Dict[str, ApprovalRequest] = {}
        self._granted: set = set()

    def request(self, req: HostCommandRequest, reason: str) -> ApprovalRequest:
        approval_id = f"approval-{uuid.uuid4().hex[:12]}"
        ar = ApprovalRequest(
            approval_id=approval_id,
            reason=reason,
            request_summary={
                "command": req.command,
                "adapter": req.adapter,
                "use_shell": req.use_shell,
                "actor": req.actor,
            },
        )
        self._pending[approval_id] = ar
        return ar

    def grant(self, approval_id: str, by: str = "human") -> bool:
        ar = self._pending.get(approval_id)
        if ar is None:
            return False
        ar.status = "granted"
        self._granted.add(approval_id)
        return True

    def deny(self, approval_id: str, by: str = "human") -> bool:
        ar = self._pending.get(approval_id)
        if ar is None:
            return False
        ar.status = "denied"
        self._granted.discard(approval_id)
        return True

    def is_granted(self, approval_id: str) -> bool:
        return approval_id in self._granted

    def granted_id_for(self, req: HostCommandRequest) -> Optional[str]:
        """Return the id of a *granted* approval matching ``req``, else ``None``.

        The broker uses this so a re-submitted (previously-escalated) command that
        a human has since granted is allowed without creating a new escalation on
        every retry. Matching is by the same summary fields captured at request
        time (command / adapter / use_shell / actor).
        """
        for approval_id in self._granted:
            ar = self._pending.get(approval_id)
            if ar is None:
                continue
            s = ar.request_summary
            if (
                s.get("command") == req.command
                and s.get("adapter") == req.adapter
                and s.get("use_shell") == req.use_shell
                and s.get("actor") == req.actor
            ):
                return approval_id
        return None

    def consume(self, approval_id: str) -> bool:
        """One-shot use: returns True once, then invalidates the grant."""
        if approval_id in self._granted:
            self._granted.discard(approval_id)
            return True
        return False

    def pending(self) -> Dict[str, ApprovalRequest]:
        return dict(self._pending)
