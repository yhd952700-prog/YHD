"""Lightweight tracing / span hooks for LIUHAO reliability.

NON-INTRUSIVE: spans are emitted as structured log records (via
``structured_log``) and their durations are recorded into a Histogram metric.
No external tracing backend is required. To enable production distributed
tracing, ship these span events to an OTel exporter; this module only provides
the local primitive.

Example
-------
    tracer = get_tracer()
    with tracer.span("verify_integrity", attributes={"scope": "audit"}):
        verify_audit_integrity()
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from .metrics import histogram
from .structured_log import get_struct_logger

_span_duration = histogram(
    "liuhao_span_duration_seconds",
    "Duration of traced spans in seconds.",
    "seconds",
)
_tracer_logger = get_struct_logger("liuhao.reliability.tracing")


class Span:
    """A single traced span, usable as a context manager."""

    def __init__(
        self,
        tracer: "Tracer",
        name: str,
        attributes: Optional[Dict[str, object]] = None,
    ) -> None:
        self._tracer = tracer
        self.name = name
        self.attributes: Dict[str, object] = attributes or {}
        self.start: float = 0.0
        self.end: float = 0.0
        self.duration: float = 0.0

    def __enter__(self) -> "Span":
        self.start = time.perf_counter()
        _tracer_logger.debug("span_start", span=self.name, **self.attributes)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.end = time.perf_counter()
        self.duration = self.end - self.start
        status = "error" if exc_type is not None else "ok"
        _span_duration.observe(self.duration)
        _tracer_logger.info(
            "span_end",
            span=self.name,
            duration_s=round(self.duration, 6),
            status=status,
            **self.attributes,
        )
        return False  # never suppress exceptions


class Tracer:
    """Creates spans. Stateless factory; safe to share a single instance."""

    def span(self, name: str, attributes: Optional[Dict[str, object]] = None) -> Span:
        return Span(self, name, attributes)


_TRACER = Tracer()


def get_tracer() -> Tracer:
    return _TRACER
