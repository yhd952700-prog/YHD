"""HD-06 — legal-hold interface.

A legal hold suspends any destructive retention action for the records it covers.
Holds are either *global* (cover everything) or *scoped* (cover one record id).
A hold may carry an optional expiry; until it expires (or is released) the
manager treats the covered records as non-deletable.

VOLATILE — A HOLD DOES NOT SURVIVE A PROCESS RESTART
----------------------------------------------------
:class:`LegalHoldRegistry` holds its state in ``self._holds``, an in-process
dict. Nothing is persisted. The contract above ("until it expires or is
released") is therefore only true **for the lifetime of one process**: a
restart, redeploy, crash, or worker reschedule constructs an empty registry and
silently lifts every hold, with no error and no log line. Verified by probe
(2026-10-05): place a hold -> ``is_held(record)`` is True; construct a fresh
registry, as a restart does -> the hold is gone.

This is NOT yet an active exposure, and the difference matters: this subsystem
is unreachable from any HTTP route and no production code calls
``place_legal_hold`` (the only reference outside this package is the
``src/observability/production.py:118`` liveness probe), so no hold can be
placed and then lost today. It is a **latent** defect that becomes a real
evidence-preservation failure the moment retention is wired to an operator
surface.

Why it matters when that happens: a legal hold exists precisely to survive
ordinary operational events. Its whole purpose is to hold evidence through
litigation, incident response, and the deploys that happen *during* those. A
hold that a `docker restart` silently removes has preserved nothing — it has
merely postponed destruction to the next deploy, and it will do so without a
trace, so an operator has no way to learn that protection was lifted.

Scope note (HD-06 is an open HUMAN DECISION — see
``docs/autonomous/UNKNOWN-TO-OWNER.md`` U52 and the R7 retention decision):
the *final* retention policy is the owner's to set and is deliberately NOT
decided here. What this module must not do is let a reader believe a
durable-sounding guarantee that the implementation does not provide. Making the
registry durable is a code change for the retention owner, not a policy
decision — this docstring records the obligation so it cannot be lost.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LegalHold:
    """A single legal hold placed on one or all records."""

    hold_id: str
    reason: str
    placed_by: str
    scope: str = "global"  # "global" or a specific record id
    placed_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None  # None => indefinite

    def is_active(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if self.expires_at is None:
            return True
        return now < self.expires_at


class LegalHoldRegistry:
    """In-memory registry of active legal holds — VOLATILE, not durable.

    State lives in ``self._holds`` only. A restart/redeploy/crash/worker
    reschedule empties it and every hold is silently lifted (see the module
    docstring for the probe that demonstrates this). Callers must not treat a
    hold as durable evidence preservation until that changes.
    """

    def __init__(self) -> None:
        self._holds: Dict[str, LegalHold] = {}

    def place(
        self,
        reason: str,
        placed_by: str,
        scope: str = "global",
        ttl_days: Optional[float] = None,
    ) -> LegalHold:
        hold_id = f"hold-{uuid.uuid4().hex[:12]}"
        expires_at = None
        if ttl_days is not None:
            expires_at = time.time() + ttl_days * 86400.0
        hold = LegalHold(
            hold_id=hold_id, reason=reason, placed_by=placed_by,
            scope=scope, expires_at=expires_at,
        )
        self._holds[hold_id] = hold
        return hold

    def release(self, hold_id: str) -> bool:
        return self._holds.pop(hold_id, None) is not None

    def active_holds(self, now: Optional[float] = None) -> List[LegalHold]:
        return [h for h in self._holds.values() if h.is_active(now)]

    def holds_for(self, record_id: str, now: Optional[float] = None) -> List[LegalHold]:
        return [
            h for h in self.active_holds(now)
            if h.scope == "global" or h.scope == record_id
        ]

    def is_held(self, record_id: str, now: Optional[float] = None) -> bool:
        return bool(self.holds_for(record_id, now))

    def snapshot(self) -> List[dict]:
        return [h.__dict__ for h in self._holds.values()]
