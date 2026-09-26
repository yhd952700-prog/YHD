"""HD-06 — retention manager (orchestrator + immutable-history enforcement).

The manager ties together the policy, legal-hold registry, archival target, and
observability. It is the **single enforcement point** for the hard invariant:

    original immutable history is NEVER deleted, overwritten, or destroyed.

It does this two ways:
  1. :meth:`register_original` refuses to overwrite an existing original.
  2. :meth:`_apply_decision` refuses to delete any ``IMMUTABLE_ORIGINAL`` even
     if a configured policy (e.g. DELETE_AFTER_DAYS) asks for it -- only
     derivative copies may be purged, and only when not under a legal hold.

All destructive actions are gated; observability is fail-soft.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from .archival import ArchivalTarget, LocalFsArchiveTarget
from .config import RetentionConfig, RetentionMode, load_retention_config
from .legal_hold import LegalHold, LegalHoldRegistry
from .lifecycle import LifecycleMetadata, LifecycleState, RecordClass
from .observability import RetentionEventSink, RetentionMetrics, default_event_sink
from .policy import (
    ConfigurableRetentionPolicy,
    RetentionDecision,
    RetentionPolicy,
)


class RetentionManager:
    """Policy-neutral retention orchestrator with a hard no-destroy-original guard."""

    def __init__(
        self,
        config: Optional[RetentionConfig] = None,
        cold_root: Optional[str] = None,
        policy: Optional[RetentionPolicy] = None,
        event_sink: Optional[RetentionEventSink] = None,
        holds: Optional[LegalHoldRegistry] = None,
    ) -> None:
        self.config = config or load_retention_config()
        self.policy = policy or ConfigurableRetentionPolicy()
        self.holds = holds or LegalHoldRegistry()
        self.metrics = RetentionMetrics()
        self._sink = event_sink or default_event_sink
        cold = cold_root or self.config.cold_storage_root
        self._archiver: ArchivalTarget = LocalFsArchiveTarget(cold)
        self._records: Dict[str, LifecycleMetadata] = {}

    # ------------------------------------------------------------------ registry
    def register_original(
        self, record_id: str, payload: bytes, origin_hash: Optional[str] = None
    ) -> LifecycleMetadata:
        """Register an immutable original. Never overwrites an existing original."""
        existing = self._records.get(record_id)
        if existing is not None and existing.record_class is RecordClass.IMMUTABLE_ORIGINAL:
            self._emit(
                "immutable_write_rejected",
                {"record_id": record_id, "reason": "original already exists"},
            )
            raise ValueError(f"refusing to overwrite IMMUTABLE_ORIGINAL {record_id!r}")
        meta = LifecycleMetadata(
            record_id=record_id,
            record_class=RecordClass.IMMUTABLE_ORIGINAL,
            origin_hash=origin_hash,
        )
        self._records[record_id] = meta
        return meta

    def register_derivative(
        self, record_id: str, source_original_id: str, payload: bytes
    ) -> LifecycleMetadata:
        """Register a derivative copy (may be purged under explicit policy)."""
        meta = LifecycleMetadata(
            record_id=record_id,
            record_class=RecordClass.DERIVATIVE,
            origin_hash=source_original_id,
        )
        self._records[record_id] = meta
        return meta

    def get(self, record_id: str) -> Optional[LifecycleMetadata]:
        return self._records.get(record_id)

    # ----------------------------------------------------------------- evaluation
    def evaluate(self, record_id: str, now: Optional[float] = None) -> RetentionDecision:
        """Evaluate + apply the policy for one record (honouring holds)."""
        meta = self._records.get(record_id)
        if meta is None:
            raise KeyError(record_id)
        now = now if now is not None else time.time()
        self.metrics.evaluations += 1

        if self.holds.is_held(record_id, now):
            decision = RetentionDecision(
                "blocked_by_hold", "active legal hold suspends action",
                LifecycleState.HELD,
            )
            self.metrics.blocked_by_hold += 1
            self._apply_decision(record_id, meta, decision)
            self._emit(
                "retention_evaluate",
                {"record_id": record_id, "outcome": "blocked_by_hold"},
            )
            return decision

        decision = self.policy.decide(meta, self.config, now)
        self._apply_decision(record_id, meta, decision)
        self._emit(
            "retention_evaluate",
            {
                "record_id": record_id,
                "outcome": decision.action,
                "mode": self.config.mode.value,
            },
        )
        return decision

    def _apply_decision(
        self, record_id: str, meta: LifecycleMetadata, decision: RetentionDecision
    ) -> None:
        action = decision.action
        if action == "archive":
            # Copy-only: store a cold copy; the original record stays retained.
            payload = self._records[record_id]
            _ = self._archiver.archive(record_id, _meta_bytes(payload), payload)
            if meta.state == LifecycleState.ACTIVE:
                meta.state = LifecycleState.ARCHIVED
                meta.archived_at = time.time()
            self.metrics.archives += 1
        elif action == "delete_derivative":
            # HARD GUARD: originals are never deletable, even if a policy asks for
            # it. Block, count, emit, and rewrite the decision so callers observe
            # the protected outcome (never a deletion of the original).
            if meta.record_class is RecordClass.IMMUTABLE_ORIGINAL:
                self.metrics.immutable_protected += 1
                self._emit(
                    "immutable_protected",
                    {
                        "record_id": record_id,
                        "reason": "policy asked to delete an original; blocked by hard guard",
                    },
                )
                decision.action = "keep"
                decision.rationale = "IMMUTABLE_ORIGINAL: deletion blocked by hard guard"
                decision.next_state = LifecycleState.ACTIVE
                decision.deletable_target = False
                return
            # Derivative purge: remove only the cold copy.
            try:
                self._archiver.backend().delete(record_id)
            except Exception:
                self.metrics.errors += 1
            meta.state = LifecycleState.PURGED
            self.metrics.deletes_derivative += 1
        elif action == "blocked_by_hold":
            meta.state = LifecycleState.HELD
        else:  # "keep" / unknown -> no state change, count as keep
            self.metrics.keeps += 1

    # ------------------------------------------------------------------ legal hold
    def place_legal_hold(
        self, reason: str, placed_by: str, scope: str = "global", ttl_days: Optional[float] = None
    ) -> LegalHold:
        return self.holds.place(reason, placed_by, scope=scope, ttl_days=ttl_days)

    def release_legal_hold(self, hold_id: str) -> bool:
        return self.holds.release(hold_id)

    # ------------------------------------------------------------------ reporting
    def summary(self) -> Dict[str, object]:
        by_state: Dict[str, int] = {}
        by_class: Dict[str, int] = {}
        for meta in self._records.values():
            by_state[meta.state.value] = by_state.get(meta.state.value, 0) + 1
            by_class[meta.record_class.value] = by_class.get(meta.record_class.value, 0) + 1
        return {
            "mode": self.config.mode.value,
            "protect_immutable_originals": self.config.protect_immutable_originals,
            "record_count": len(self._records),
            "by_state": by_state,
            "by_class": by_class,
            "metrics": self.metrics.snapshot(),
            "active_holds": len(self.holds.active_holds()),
        }

    # -------------------------------------------------------------------- helpers
    def _emit(self, event: str, details: Dict[str, object]) -> None:
        try:
            self._sink(event, details)
        except Exception:
            self.metrics.errors += 1


def _meta_bytes(meta: LifecycleMetadata) -> bytes:
    """Best-effort byte payload for an original's cold copy.

    The manager stores metadata, not payloads, so the cold copy is the metadata
    envelope itself (which is what must be preserved for audit continuity). A real
    deployment would pass the original bytes at registration time; this keeps the
    copy faithful to the record without requiring payload storage here.
    """
    import json

    from .migration import serialize_lifecycle

    return json.dumps(serialize_lifecycle(meta), sort_keys=True).encode("utf-8")
