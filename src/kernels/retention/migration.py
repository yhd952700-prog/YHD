"""HD-06 — migration compatibility for lifecycle metadata.

Lifecycle metadata is serialized inside a versioned envelope so future schema
changes can be applied forward-compatibly. The current writer stamps
``schema_version``; readers tolerate unknown future fields and fill missing
current fields with safe defaults. This keeps the retention subsystem safe to
evolve without a disruptive migration of historical metadata.
"""

from __future__ import annotations

from typing import Any, Dict

from .lifecycle import LifecycleMetadata, LifecycleState, RecordClass

CURRENT_RETENTION_METADATA_VERSION = 1


def serialize_lifecycle(meta: LifecycleMetadata) -> Dict[str, Any]:
    """Serialize metadata into a versioned, forward-compatible envelope."""
    envelope = meta.to_dict()
    envelope["_retention_meta_version"] = CURRENT_RETENTION_METADATA_VERSION
    return envelope


def _coerce_record_class(value: Any) -> RecordClass:
    try:
        return RecordClass(value)
    except (ValueError, TypeError):
        return RecordClass.IMMUTABLE_ORIGINAL  # safest default for unknown class


def _coerce_state(value: Any) -> LifecycleState:
    try:
        return LifecycleState(value)
    except (ValueError, TypeError):
        return LifecycleState.ACTIVE


def deserialize_lifecycle(data: Dict[str, Any]) -> LifecycleMetadata:
    """Reconstruct metadata from an envelope, tolerating future/unknown fields.

    Unknown future keys are preserved under ``extra``; missing current keys fall
    back to safe defaults. Never raises on a slightly-newer envelope.
    """
    extra = dict(data.get("extra", {}) or {})
    # Absorb any unexpected top-level keys into extra so nothing is silently lost.
    known = {
        "record_id", "record_class", "state", "created_at", "archived_at",
        "legal_hold_ids", "origin_hash", "schema_version", "extra",
        "_retention_meta_version",
    }
    for key, val in data.items():
        if key not in known and key not in extra:
            extra[key] = val

    return LifecycleMetadata(
        record_id=data["record_id"],
        record_class=_coerce_record_class(data.get("record_class")),
        state=_coerce_state(data.get("state", "ACTIVE")),
        created_at=float(data.get("created_at", 0.0)),
        archived_at=data.get("archived_at"),
        legal_hold_ids=list(data.get("legal_hold_ids", []) or []),
        origin_hash=data.get("origin_hash"),
        # The canonical envelope version key is ``_retention_meta_version``; fall
        # back to a legacy ``schema_version`` key, then the current default, so a
        # far-future envelope stamps its own version through (never downgraded).
        schema_version=int(
            data.get(
                "_retention_meta_version",
                data.get("schema_version", CURRENT_RETENTION_METADATA_VERSION),
            )
        ),
        extra=extra,
    )


def migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply forward migrations in place. Placeholder for future version bumps.

    Each step migrates from version N to N+1. Today there is only the initial
    version, so this is a pass-through that stamps the current version.
    """
    version = int(data.get("_retention_meta_version", CURRENT_RETENTION_METADATA_VERSION))
    # Future: while version < TARGET: data = _migrate_vN_to_vN1(data); version += 1
    data["_retention_meta_version"] = CURRENT_RETENTION_METADATA_VERSION
    return data
