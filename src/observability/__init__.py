"""Observability Module for LiuHao AI OS

Provides:
- Span data model for distributed tracing
- Metric data model for performance monitoring
- Store for persistence and integrity
- Convenience functions
"""

from typing import Optional

from .models import (
    Span,
    SpanContext,
    AttributeValue,
    Event,
    Link,
    Resource,
    InstrumentationScope,
    ScopeSpans,
    Metric,
    MetricPoint,
    ResourceMetrics,
    SeverityNumber,
    SpanKind,
    Status,
)

from .store import ObservabilityStore, emit_span, emit_metric, get_observability_store

# Module-level store instance
_default_store: Optional[ObservabilityStore] = None


def get_observability_store() -> ObservabilityStore:
    """Get the default observability store instance."""
    global _default_store
    if _default_store is None:
        _default_store = ObservabilityStore()
    return _default_store


def emit_span(span: Span) -> str:
    """Emit (store) a span using the default store."""
    return get_observability_store().emit_span(span)


def emit_metric(metric: Metric) -> str:
    """Emit (store) a metric using the default store."""
    return get_observability_store().emit_metric(metric)


def setup_tracing(
    service_name: str = "liuhao-ai-os",
    otlp_endpoint: str = "http://localhost:4317",
) -> None:
    """设置 OpenTelemetry 链路追踪"""
    from .tracing import setup_tracing as _setup_tracing
    _setup_tracing(service_name=service_name, otlp_endpoint=otlp_endpoint)


def setup_metrics(
    service_name: str = "liuhao-ai-os",
    otlp_endpoint: str = "http://localhost:4317",
) -> None:
    """设置 OpenTelemetry 指标"""
    from .tracing import setup_metrics as _setup_metrics
    _setup_metrics(service_name=service_name, otlp_endpoint=otlp_endpoint)


def get_tracer(name: str):
    """获取 Tracer 实例"""
    from .tracing import get_tracer as _get_tracer
    return _get_tracer(name)


def get_meter(name: str):
    """获取 Meter 实例"""
    from .tracing import get_meter as _get_meter
    return _get_meter(name)


# Convenience exports
Severity = SeverityNumber
