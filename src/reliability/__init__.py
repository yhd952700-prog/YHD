"""LIUHAO reliability & SRE observability scaffolding (stdlib-only).

This package provides dependency-free reliability signals that *complement* (never
replace) the existing ``src.observability`` Prometheus/OTel stack. It is
OBSERVATIONAL ONLY: it never mutates the audit record of record; chain-fork
healing is owned by U3 (chain-fork recovery framework — see
``docs/autonomous/UNKNOWN-TO-OWNER.md``).

Modules
-------
metrics         Counter / Gauge / Histogram primitives + Prometheus text exposition.
audit_metrics   Audit-chain signals wired to ``src.kernels.audit`` public API (read-only).
structured_log  JSON structured-logging helpers (own namespace, non-intrusive).
tracing         Lightweight span/trace hooks (emitted as structured logs + duration metric).

Hard boundary honoured: no subsystem here modifies kernel logic; every module
ships with tests, verification notes, docs (module docstrings), observability
(the metrics themselves), and recovery semantics (Evidence=missing is surfaced,
never swallowed).
"""
from __future__ import annotations

from .metrics import (
    Counter,
    Gauge,
    Histogram,
    Metric,
    MetricRegistry,
    REGISTRY,
    collect_prometheus_text,
    counter,
    gauge,
    get_metric_registry,
    histogram,
)
from .audit_metrics import (
    audit_evidence_status,
    audit_failure_total,
    audit_fork_count,
    audit_integrity_ok,
    audit_total_events,
    refresh_audit_metrics,
)
from .structured_log import StructuredLogger, get_struct_logger
from .tracing import Span, Tracer, get_tracer

__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "Metric",
    "MetricRegistry",
    "REGISTRY",
    "collect_prometheus_text",
    "counter",
    "gauge",
    "get_metric_registry",
    "histogram",
    "audit_evidence_status",
    "audit_failure_total",
    "audit_fork_count",
    "audit_integrity_ok",
    "audit_total_events",
    "refresh_audit_metrics",
    "StructuredLogger",
    "get_struct_logger",
    "Span",
    "Tracer",
    "get_tracer",
]
