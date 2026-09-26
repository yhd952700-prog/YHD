"""HD-06 — lifecycle metadata for retention records.

Every retained artifact is described by :class:`LifecycleMetadata`, which records
its :class:`RecordClass` (original vs derivative) and :class:`LifecycleState`.
The ``record_class`` is the linchpin of the immutable-history guarantee: only
:attr:`RecordClass.DERIVATIVE` records may ever be deleted, and only when they
are not under an active legal hold.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class RecordClass(str, Enum):
    """What kind of record this is -- the basis of the no-delete invariant."""

    #: The canonical, authoritative copy (e.g. the audit/evidence chain). Never
    #: deletable, overwritable, or destroyable by any retention policy.
    IMMUTABLE_ORIGINAL = "IMMUTABLE_ORIGINAL"
    #: A copy or derived artifact (e.g. an archive, an export). May be purged
    #: under an explicit, configured policy once its horizon passes and no hold
    #: applies. Never the source of truth.
    DERIVATIVE = "DERIVATIVE"


class LifecycleState(str, Enum):
    """Where a record is in its retention lifecycle."""

    ACTIVE = "ACTIVE"      #: live, retained
    ARCHIVED = "ARCHIVED"  #: copied to cold storage; original still retained
    HELD = "HELD"          #: under an active legal hold (deletion suspended)
    EXPIRED = "EXPIRED"    #: past its retention horizon (derivatives only)
    PURGED = "PURGED"      #: derivative copy removed (originals never reach this)


@dataclass
class LifecycleMetadata:
    """Per-record lifecycle metadata (versioned for migration compatibility)."""

    record_id: str
    record_class: RecordClass
    state: LifecycleState = LifecycleState.ACTIVE
    created_at: float = field(default_factory=time.time)
    archived_at: Optional[float] = None
    legal_hold_ids: list = field(default_factory=list)
    #: Integrity anchor (e.g. a hash of the original) so a copy can be verified
    #: against its source even after the source has been archived.
    origin_hash: Optional[str] = None
    #: Schema version of this metadata envelope (forward-compatible migrations).
    schema_version: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)

    def is_immutable_original(self) -> bool:
        return self.record_class is RecordClass.IMMUTABLE_ORIGINAL

    def is_under_hold(self) -> bool:
        return bool(self.legal_hold_ids)

    def is_deletable(self) -> bool:
        """True only for derivative records that are not under an active hold.

        Originals are NEVER deletable. This is the single source of truth the
        manager consults before any destructive action.
        """
        return (
            self.record_class is RecordClass.DERIVATIVE
            and not self.legal_hold_ids
            and self.state in (LifecycleState.EXPIRED, LifecycleState.ARCHIVED)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_class": self.record_class.value,
            "state": self.state.value,
            "created_at": self.created_at,
            "archived_at": self.archived_at,
            "legal_hold_ids": list(self.legal_hold_ids),
            "origin_hash": self.origin_hash,
            "schema_version": self.schema_version,
            "extra": self.extra,
        }
