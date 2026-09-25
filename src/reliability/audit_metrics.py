"""Audit-chain reliability metrics (NON-INTRUSIVE).

Reads the authoritative audit store's *public* API only:

  - ``src.kernels.audit.audit_failure_count()``   in-memory counter, cheap
  - ``src.kernels.audit.audit_stats()``           one COUNT query, cheap
  - ``src.kernels.audit.verify_audit_integrity()`` full chain scan, EXPENSIVE

It NEVER writes to the audit store and NEVER hooks its write path. The metrics
are OBSERVATIONAL. Chain-fork *healing* itself is owned by U3 (see
``docs/autonomous/UNKNOWN-TO-OWNER.md``); this module only exposes the signals.

Signals
-------
audit_evidence_status  gauge   1 = store reachable, 0 = missing/crashed (Evidence=missing)
audit_failure_total    counter monotonic count of failed audit writes (CRIT-1C / D17 Layer 1)
audit_total_events     gauge   row count in the authoritative store
audit_fork_count       gauge   forks detected (reserved; =0 until the U3 verifier is wired)
audit_integrity_ok     gauge   1 = last integrity scan passed, 0 = failed (only set when a check ran)

RECOVERY SEMANTICS: any failure to reach the store is reported as
``audit_evidence_status = 0`` (Evidence=missing). We NEVER silently swallow a
missing/crashed store behind a green dashboard.
"""
from __future__ import annotations

from typing import Any, Dict

from .metrics import counter, gauge

# Registered into the global REGISTRY on first import.
audit_evidence_status = gauge(
    "audit_evidence_status",
    "Audit authoritative store reachable (1) or missing/crashed (0). Evidence=missing signal.",
    "bool",
)
audit_failure_total = counter(
    "audit_failure_total",
    "Monotonic count of audit writes that failed since process start (CRIT-1C / D17 Layer 1).",
    "count",
)
audit_total_events = gauge(
    "audit_total_events",
    "Row count in the authoritative audit store.",
    "events",
)
audit_fork_count = gauge(
    "audit_fork_count",
    "Audit chain forks detected (reserved; 0 until the U3 verifier is wired).",
    "forks",
)
audit_integrity_ok = gauge(
    "audit_integrity_ok",
    "Last full integrity scan result (1 pass / 0 fail). Only set when a check has run.",
    "bool",
)


def refresh_audit_metrics(with_integrity_check: bool = False) -> Dict[str, Any]:
    """Populate audit metrics from the live authoritative store.

    Cheap path (``with_integrity_check=False``, default): reads the in-memory
    failure counter and a single COUNT query. Safe to call on every scrape.

    Expensive path (``with_integrity_check=True``): additionally runs a full
    chain integrity scan (~32 s per million rows). Call on a slow cadence only.

    Returns a small status dict for callers and tests.
    """
    status: Dict[str, Any] = {
        "store_reachable": False,
        "failures": 0,
        "total_events": 0,
        "integrity_ok": None,
        "error": None,
    }

    try:
        # Lazy import keeps this module importable even when the kernel is absent
        # (unit tests monkeypatch ``src.kernels.audit``).
        from src.kernels.audit import (
            audit_failure_count,
            audit_stats,
            verify_audit_integrity,
        )
    except Exception as exc:  # pragma: no cover - defensive
        audit_evidence_status.set(0)
        status["error"] = f"import_failed:{exc!r}"
        return status

    try:
        failures = int(audit_failure_count())
        audit_failure_total.set(failures)
        status["failures"] = failures

        stats = audit_stats() or {}
        total = int(stats.get("total_events", 0))
        audit_total_events.set(total)
        status["total_events"] = total

        audit_evidence_status.set(1)
        status["store_reachable"] = True

        if with_integrity_check:
            ok, _verified = verify_audit_integrity()
            audit_integrity_ok.set(1 if ok else 0)
            status["integrity_ok"] = bool(ok)
    except Exception as exc:
        # Store was importable but a call failed -> treat as Evidence=missing.
        audit_evidence_status.set(0)
        status["error"] = f"store_error:{exc!r}"

    return status
