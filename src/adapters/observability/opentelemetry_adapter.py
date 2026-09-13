"""[LEGACY / NOT WIRED] OpenTelemetry adapter for LiuHao AI OS.

⚠️ 接线状态（2026-09-13 评审核实）：本模块**未接入运行时**。
全仓没有任何 src/gateway/ 或 src/ai/ 模块 import 它；同目录内也无其他模块
引用它。网关与 AI 层实际使用的权威 OpenTelemetry 封装是顶层
``src/observability/tracing.py``（gateway/main.py:23 即 ``from ..observability
.tracing import get_tracer``）。

本模块是与权威实现**意图重复、已被取代**的遗留 OTLP 导出实现。请勿将其接线进运行时。

（以下为原 docstring）
Provides OpenTelemetry Protocol (OTLP) export for metrics and traces.
This integrates with existing OpenTelemetry infrastructure.
"""

import logging
import os
from typing import Optional

try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False


def init_otel_tracer(
    service_name: str = "liuhao-ai-os",
    endpoint: Optional[str] = None,
) -> None:
    """Initialize OpenTelemetry tracer provider."""
    if not OPENTELEMETRY_AVAILABLE:
        logging.warning("OpenTelemetry not installed - tracing disabled")
        return

    # Create tracer provider
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)

    # Set up export endpoint
    if endpoint is None:
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    # Add span processor
    span_processor = BatchSpanProcessor(endpoint)
    tracer_provider.add_span_processor(span_processor)

    print(f"OpenTelemetry tracer initialized for {service_name}")


def init_otel_meter(
    service_name: str = "liuhao-ai-os",
    endpoint: Optional[str] = None,
) -> None:
    """Initialize OpenTelemetry meter provider."""
    if not OPENTELEMETRY_AVAILABLE:
        logging.warning("OpenTelemetry not installed - metrics disabled")
        return

    # Create meter
    metrics.get_meter(service_name)

    # Set up export
    if endpoint is None:
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    # Create metric reader
    reader = PeriodicExportingMetricReader(endpoint)
    meter_provider = MeterProvider(readers=[reader])
    metrics.set_meter_provider(meter_provider)

    print(f"OpenTelemetry meter initialized for {service_name}")


# Export functions
__all__ = [
    'init_otel_tracer',
    'init_otel_meter',
]
