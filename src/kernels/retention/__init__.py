"""HD-06 — policy-neutral retention subsystem.

Builds a retention architecture that **never deletes, overwrites, or destroys
original immutable history** by default, with every final rule reachable through
a single reserved switch (``LIUHAO_RETENTION_POLICY``) so the owner can set the
real policy later without code changes. The legal retention period is a sovereign
decision and is NOT decided here.

Public API
----------
* :class:`RetentionConfig`, :func:`load_retention_config`, :func:`parse_policy_spec`,
  :data:`RETENTION_POLICY_ENV` -- switch-driven configuration (default NO_DELETE).
* :class:`RetentionMode` -- NO_DELETE / ARCHIVE_ONLY / DELETE_AFTER_DAYS (reserved).
* :class:`LifecycleMetadata`, :class:`LifecycleState`, :class:`RecordClass` --
  per-record lifecycle metadata; ``IMMUTABLE_ORIGINAL`` is the no-delete anchor.
* :class:`RetentionPolicy`, :class:`NoDeletePolicy`, :class:`ArchiveOnlyPolicy`,
  :class:`ConfigurableRetentionPolicy` -- policy interface + implementations.
* :class:`LegalHold`, :class:`LegalHoldRegistry` -- legal-hold interface.
* :class:`ArchivalTarget`, :class:`LocalFsArchiveTarget`,
  :class:`ObjectStorageArchiveTarget`, :class:`ColdStorageBackend`,
  :class:`LocalFsColdStorage` -- provider-neutral archival / cold storage.
* :class:`CapacityEstimator`, :class:`CapacityReport` -- capacity-planning hooks
  (warn-only; never delete).
* :class:`RetentionManager`, :class:`RetentionMetrics` -- orchestrator +
  observability, with the hard no-destroy-original guard.
* :mod:`src.kernels.retention.migration` -- versioned, forward-compatible metadata.
"""

from __future__ import annotations

import threading
from typing import Optional

from .config import (
    RETENTION_POLICY_ENV,
    RetentionConfig,
    RetentionMode,
    load_retention_config,
    parse_policy_spec,
)
from .lifecycle import (
    LifecycleMetadata,
    LifecycleState,
    RecordClass,
)
from .policy import (
    ArchiveOnlyPolicy,
    ConfigurableRetentionPolicy,
    NoDeletePolicy,
    RetentionDecision,
    RetentionPolicy,
)
from .legal_hold import LegalHold, LegalHoldRegistry
from .archival import (
    ArchivalTarget,
    ColdStorageBackend,
    LocalFsArchiveTarget,
    LocalFsColdStorage,
    ObjectStorageArchiveTarget,
    ObjectStorageConfig,
)
from .capacity import CapacityEstimator, CapacityReport
from .observability import (
    RetentionEventSink,
    RetentionMetrics,
    default_event_sink,
)
from .manager import RetentionManager
from .migration import (
    CURRENT_RETENTION_METADATA_VERSION,
    deserialize_lifecycle,
    migrate,
    serialize_lifecycle,
)

_global_manager: Optional[RetentionManager] = None
_global_lock = threading.Lock()


def get_retention_manager() -> RetentionManager:
    """Get or create the global retention manager.

    Mirrors the singleton-getter contract the other kernels expose (see
    ``src.kernels.trust.get_trust_manager``). ``src/observability/production.py``
    requires one ``get_*`` getter per kernel package on disk, so until this
    existed the readiness report silently omitted retention -- a whole retention
    subsystem with no health probe.

    Default posture is unchanged: ``RetentionManager`` loads ``NO_DELETE`` unless
    ``LIUHAO_RETENTION_POLICY`` says otherwise, and never destroys an
    ``IMMUTABLE_ORIGINAL``.
    """
    global _global_manager
    if _global_manager is None:
        with _global_lock:
            if _global_manager is None:
                _global_manager = RetentionManager()
    return _global_manager


__all__ = [
    "RETENTION_POLICY_ENV",
    "RetentionConfig",
    "RetentionMode",
    "load_retention_config",
    "parse_policy_spec",
    "LifecycleMetadata",
    "LifecycleState",
    "RecordClass",
    "ArchiveOnlyPolicy",
    "ConfigurableRetentionPolicy",
    "NoDeletePolicy",
    "RetentionDecision",
    "RetentionPolicy",
    "LegalHold",
    "LegalHoldRegistry",
    "ArchivalTarget",
    "ColdStorageBackend",
    "LocalFsArchiveTarget",
    "LocalFsColdStorage",
    "ObjectStorageArchiveTarget",
    "ObjectStorageConfig",
    "CapacityEstimator",
    "CapacityReport",
    "RetentionEventSink",
    "RetentionMetrics",
    "default_event_sink",
    "RetentionManager",
    "get_retention_manager",
    "CURRENT_RETENTION_METADATA_VERSION",
    "serialize_lifecycle",
    "deserialize_lifecycle",
    "migrate",
]
