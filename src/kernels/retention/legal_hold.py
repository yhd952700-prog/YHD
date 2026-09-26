"""HD-06 — legal-hold interface.

A legal hold suspends any destructive retention action for the records it covers.
Holds are either *global* (cover everything) or *scoped* (cover one record id).
A hold may carry an optional expiry; until it expires (or is released) the
manager treats the covered records as non-deletable.
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
    """In-memory registry of active legal holds."""

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
