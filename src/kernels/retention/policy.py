"""HD-06 — retention policy interface and implementations.

A :class:`RetentionPolicy` turns (record metadata, config, now) into a
:class:`RetentionDecision`. The policy NEVER performs the action itself -- the
:class:`~src.kernels.retention.manager.RetentionManager` executes it, and the
manager's hard guard guarantees originals are never deleted regardless of what
the policy decides.

The shipped policies are:

* :class:`NoDeletePolicy` -- the safe default; retains everything.
* :class:`ArchiveOnlyPolicy` -- copies to cold storage, never deletes.
* :class:`ConfigurableRetentionPolicy` -- dispatches on the configured
  :class:`~src.kernels.retention.config.RetentionMode`, still honouring the
  immutable-original invariant.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .config import RetentionConfig, RetentionMode
from .lifecycle import LifecycleMetadata, LifecycleState, RecordClass


@dataclass
class RetentionDecision:
    """What the policy wants done with a record (the manager enforces safety)."""

    action: str  #: "keep" | "archive" | "delete_derivative" | "blocked_by_hold"
    rationale: str
    next_state: LifecycleState
    deletable_target: bool = False  #: True only when the target may be purged


class RetentionPolicy(ABC):
    """Policy-neutral retention decision interface."""

    @abstractmethod
    def decide(
        self, meta: LifecycleMetadata, config: RetentionConfig, now: float
    ) -> RetentionDecision:
        """Return the desired action for ``meta`` under ``config`` at time ``now``."""
        raise NotImplementedError


class NoDeletePolicy(RetentionPolicy):
    """Default policy: retain everything; never delete anything."""

    def decide(
        self, meta: LifecycleMetadata, config: RetentionConfig, now: float
    ) -> RetentionDecision:
        if meta.is_under_hold():
            return RetentionDecision(
                "blocked_by_hold", "active legal hold suspends all action",
                LifecycleState.HELD,
            )
        return RetentionDecision(
            "keep", "NO_DELETE policy: originals and derivatives retained",
            meta.state,
        )


class ArchiveOnlyPolicy(RetentionPolicy):
    """Copy originals to cold storage; never delete anything."""

    def decide(
        self, meta: LifecycleMetadata, config: RetentionConfig, now: float
    ) -> RetentionDecision:
        if meta.is_under_hold():
            return RetentionDecision(
                "blocked_by_hold", "active legal hold suspends archival",
                LifecycleState.HELD,
            )
        if meta.state in (LifecycleState.ACTIVE,):
            return RetentionDecision(
                "archive", "ARCHIVE_ONLY: copy to cold storage, retain original",
                LifecycleState.ARCHIVED,
            )
        return RetentionDecision("keep", "already archived / retained", meta.state)


class ConfigurableRetentionPolicy(RetentionPolicy):
    """Dispatch on the configured :class:`RetentionMode`.

    Even in ``DELETE_AFTER_DAYS`` mode, the immutable-original invariant holds:
    originals are kept; only derivative copies past their horizon may be purged.
    """

    def decide(
        self, meta: LifecycleMetadata, config: RetentionConfig, now: float
    ) -> RetentionDecision:
        if meta.is_under_hold():
            return RetentionDecision(
                "blocked_by_hold", "active legal hold suspends all action",
                LifecycleState.HELD,
            )
        if config.mode is RetentionMode.NO_DELETE:
            return NoDeletePolicy().decide(meta, config, now)
        if config.mode is RetentionMode.ARCHIVE_ONLY:
            return ArchiveOnlyPolicy().decide(meta, config, now)
        if config.mode is RetentionMode.DELETE_AFTER_DAYS:
            if meta.record_class is RecordClass.IMMUTABLE_ORIGINAL:
                return RetentionDecision(
                    "keep",
                    "DELETE_AFTER_DAYS never touches IMMUTABLE_ORIGINAL",
                    LifecycleState.ACTIVE,
                )
            age_days = (now - meta.created_at) / 86400.0
            if config.delete_after_days and age_days >= config.delete_after_days:
                return RetentionDecision(
                    "delete_derivative",
                    f"derivative past {config.delete_after_days}d horizon",
                    LifecycleState.PURGED,
                    deletable_target=True,
                )
            return RetentionDecision(
                "keep", f"within {config.delete_after_days}d retention horizon",
                meta.state,
            )
        return RetentionDecision("keep", "unknown mode -> keep", meta.state)
