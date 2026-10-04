"""U1 — approval interface (human-sovereign escalation).

A command whose capability requires approval is escalated to a human via
:class:`ApprovalInterface`. By default nothing is granted; a human (or an
authorized approval channel) must explicitly grant. This mirrors the C-4 audited
grant pattern: the agent cannot self-approve a command that needs human
sovereignty. Granted approvals are one-shot (``consume``) so a single grant cannot
be replayed across commands.

What a grant is *for* (alignment: 授权时看到的目标 = 执行时的目标)
----------------------------------------------------------------
One-shot alone does not make a grant specific. A grant answers "may this
command run once", not "may THIS command, in THIS directory, with THESE
capabilities, run once". If the approval's identity omits a field that changes
what the command *does*, then one human decision silently covers a family of
different actions.

``HostCommandRequest`` carries seven fields. This module originally fingerprinted
only four of them -- command / adapter / use_shell / actor -- and left out
``cwd`` and ``capabilities_required``. Measured on the real broker path
(``HostCommandBroker`` calls :meth:`ApprovalInterface.granted_id_for` on the DEFER
branch), with a grant issued for ``rm report.txt`` in
``cwd=/home/alice/workspace`` requiring ``['fs.delete']``:

  ==========================  ===================================  ======
  re-submitted command        differs from what the human saw        verdict
  ==========================  ===================================  ======
  identical                                              (control)  allow
  same command, cwd=/etc/liuhao                       different dir  ALLOW
  same command, cwd=/                                          root      ALLOW
  same command, cwd=None                                     no dir      ALLOW
  + requires ['fs.delete','net.admin']                 MORE power    ALLOW
  requires ['net.admin'] only                           different cap  ALLOW
  ==========================  ===================================  ======

The last two are the sharp end: the human authorised deleting one file, and
the system ran a command carrying an administrative capability it never
approved. One-shot consumption did not help -- each row is a freshly issued
grant, and the escape is available for every one of them.

So the fingerprint now covers all seven fields, and capabilities are compared as
a **subset relation** rather than for equality: a command asking for more
capability than the grant covers is refused, because "the human approved
``fs.delete``" must not silently become "the human approved ``fs.delete`` and
``net.admin``". Equality would also pass here in the safe direction but would
wrongly refuse a *narrower* re-submission, which is the legitimate retry this
method exists to serve.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional

from .models import HostCommandRequest


@dataclass
class ApprovalRequest:
    """A pending human-approval request for one host command."""

    approval_id: str
    reason: str
    request_summary: Dict[str, object]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | granted | denied


def _capabilities(req: HostCommandRequest) -> FrozenSet[str]:
    """The capability set a request declares, as a comparable frozenset."""
    return frozenset(req.capabilities_required or ())


def _same_request(summary: Dict[str, object], req: HostCommandRequest) -> bool:
    """True iff ``req`` is the *same* request the human was shown.

    Every field that can change what the command does participates. Omitting one
    is not a narrower check, it is a wider grant: see the module docstring for
    the measured rows that passed before ``cwd`` and ``capabilities_required``
    were included.

    ``capabilities_required`` is compared as "requested ⊆ granted", not for
    equality, so an escalation to *more* capability is refused while a narrower
    retry of the same command is still allowed.
    """
    if summary.get("command") != req.command:
        return False
    if summary.get("adapter") != req.adapter:
        return False
    if summary.get("use_shell") != req.use_shell:
        return False
    if summary.get("actor") != req.actor:
        return False
    # Target identity: the same command string against a different working
    # directory is a different action on a different object.
    if summary.get("cwd") != req.cwd:
        return False
    # Authority: a command needing more capability than was approved is not
    # the command that was approved.
    granted = summary.get("capabilities_required")
    granted_set = frozenset(granted) if isinstance(granted, (list, tuple, set, frozenset)) else frozenset()
    if not _capabilities(req).issubset(granted_set):
        return False
    return True


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
            # Fingerprint ALL of the request: a summary that omits a
            # behavioural field is a wider grant than the human gave.
            request_summary={
                "command": req.command,
                "adapter": req.adapter,
                "use_shell": req.use_shell,
                "actor": req.actor,
                "cwd": req.cwd,
                "capabilities_required": sorted(_capabilities(req)),
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
        every retry. Matching is by the full request fingerprint captured at
        request time -- command / adapter / use_shell / actor / cwd, plus
        ``capabilities_required`` as a subset check. Anything that differs in
        what the command *does* is a new command and needs a new approval; see
        :func:`_same_request` and the module docstring for the measured rows
        that this excludes.
        """
        for approval_id in self._granted:
            ar = self._pending.get(approval_id)
            if ar is None:
                continue
            if _same_request(ar.request_summary, req):
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
