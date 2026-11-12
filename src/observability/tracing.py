"""OpenTelemetry 链路追踪模块 for liuhao AI OS

Provides OpenTelemetry tracing setup with graceful degradation
when optional dependencies are not fully available.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry components gracefully
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: F401
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry import _logging  # noqa: F401
    _OTEL_AVAILABLE = True
except ImportError as e:
    _OTEL_AVAILABLE = False
    _OTEL_IMPORT_ERROR = str(e)


# Tracing utility functions for AI operation tracking
def create_span(name: str, **kwargs):
    """创建一个追踪 span

    Args:
        name: Span 名称
        **kwargs: 追踪属性
    """
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, returning no-op span context")
        return _NoopSpanContextmanager(name, kwargs.get("attributes", {}))
    tracer = get_tracer(name)
    span = tracer.start_span(name, **kwargs)
    return _SpanContextmanager(span)


def end_span(span_context, error: Optional[BaseException] = None):
    """结束一个 span

    Args:
        span_context: 由 create_span 返回的上下文管理器
        error: 可选的异常。传入时会在 span 上记录异常并把状态标记为 ERROR。

    ``error`` 参数是**必需的兼容面**：``src/ai/employee.py`` 与
    ``src/ai/goal_task_graph.py`` 的失败分支一直调用 ``end_span(span, error=e)``，
    而本函数此前只接受 1 个位置参数，于是**每一条失败路径在收尾时自己先崩**：
    ``TypeError: end_span() got an unexpected keyword argument 'error'``。
    后果不是"少了点可观测性"，而是失败被二次异常顶替 —— 调用方拿到的是一个
    与真实原因无关的 TypeError，真实的 task/provider 错误被吞掉。
    （2026-10-04 实测：goal_task_graph._execute_task_audited 的 except 分支
    100% 抛此 TypeError，而不是返回它自己写好的 {"status":"failed"} 结果。）
    记录异常本身必须容错：可观测性永远不能反过来打断业务路径。
    """
    if span_context is None:
        return
    if error is not None:
        try:
            _record_error(span_context, error)
        except Exception:  # noqa: BLE001 - 可观测性不得抛
            logger.debug("failed to record error on span", exc_info=True)
    try:
        if hasattr(span_context, '__exit__'):
            span_context.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - 同上，收尾也不得抛
        logger.debug("failed to end span", exc_info=True)


def _record_error(span_context, error: BaseException) -> None:
    """把异常写进 span（OTel 有 record_exception 就用，否则退化为 no-op）。"""
    # _SpanContextmanager 包了一层，真正的 span 在 .span 上。
    target = getattr(span_context, "span", span_context)
    record = getattr(target, "record_exception", None)
    if callable(record):
        record(error)
    setter = getattr(target, "set_attribute", None)
    if callable(setter):
        setter("error.type", type(error).__name__)
        setter("error.message", str(error))


class _SpanContextmanager:
    """Span 上下文管理器封装"""

    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, *args):
        if self.span and hasattr(self.span, 'end'):
            self.span.end()


class _NoopSpanContextmanager:
    """No-op span context manager when OTel is not available"""

    def __init__(self, name: str, attributes: Dict[str, Any]):
        self.name = name
        self.attributes = attributes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class AISpanAttributes:
    """AI 操作追踪属性类

    可直接通过类属性访问预定义键：
    - AISpanAttributes.GOAL_ID -> "goal_id"
    - AISpanAttributes.TASK_ID -> "task_id"
    - AISpanAttributes.MODEL_NAME -> "model_name"
    - AISpanAttributes.OPERATION_TYPE -> "operation_type"

    也可实例化并传入自定义属性：
    - attrs = AISpanAttributes(goal_id="my-goal", extra="value")
    """

    GOAL_ID = "goal_id"
    TASK_ID = "task_id"
    MODEL_NAME = "model_name"
    OPERATION_TYPE = "operation_type"
    AGENT_TYPE = "agent_type"
    AGENT_TASK = "agent_task"

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def get_AISpanInstance(**kwargs):
    """创建 AISpanAttributes 实例的便捷函数

    用法：attrs = get_AISpanInstance(goal_id="my-goal")
    """
    return AISpanAttributes(**kwargs)


def track_ai_operation(operation_type: str, model: Optional[str] = None,
                       duration_ms: Optional[float] = None, **kwargs):
    """追踪 AI 操作指标

    Args:
        operation_type: 操作类型 (e.g., 'completion', 'embedding', 'classification')
        model: 使用的模型名称
        duration_ms: 耗时 (ms)
        **kwargs: 额外属性
    """
    if not _OTEL_AVAILABLE:
        logger.debug(f"OTel not available, skipping AI operation tracking: {operation_type}")
        return

    meter = get_meter("liuhao-ai-os")
    if meter is None:
        return

    # Record model call count
    model_count = meter.create_counter(
        "liuhao_model_calls_total",
        description="模型调用计数",
        unit="1",
    )
    model_count.add(1, {"operation": operation_type, "model": model or "unknown"})

    # Record latency if provided
    if duration_ms is not None:
        model_latency = meter.create_histogram(
            "liuhao_model_call_latency_ms",
            description="模型调用延迟 (ms)",
            unit="ms",
        )
        model_latency.record(duration_ms, {"operation": operation_type, "model": model or "unknown"})


# --- Tracer / Meter / Setup (graceful degradation) ---------------------------
# These are referenced by observability/__init__.py (get_tracer/get_meter/
# setup_tracing/setup_metrics) and by src/gateway/main.py, but were never
# defined in this module — a latent import bug that kept the gateway from
# booting. Added here with graceful degradation when OTel is unavailable.


class _NoopTracer:
    """No-op tracer used when OpenTelemetry is unavailable."""

    def start_span(self, name, *args, **kwargs):
        return _NoopSpanContextmanager(name, kwargs.get("attributes", {}))


def get_tracer(name: str = "liuhao-ai-os"):
    """Get a Tracer instance (no-op fallback if OTel is unavailable)."""
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, returning no-op tracer")
        return _NoopTracer()
    return trace.get_tracer(name)


def get_meter(name: str = "liuhao-ai-os"):
    """Get a Meter instance (None fallback if OTel is unavailable)."""
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, returning no meter")
        return None
    return metrics.get_meter(name)


def setup_tracing(
    service_name: str = "liuhao-ai-os",
    otlp_endpoint: str = "http://localhost:4317",
) -> None:
    """Set up OpenTelemetry tracing (graceful no-op if unavailable)."""
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, skipping tracing setup")
        return
    try:
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    except Exception as e:  # noqa: BLE001
        logger.warning("Tracing setup failed: %s", e)


def setup_metrics(
    service_name: str = "liuhao-ai-os",
    otlp_endpoint: str = "http://localhost:4317",
) -> None:
    """Set up OpenTelemetry metrics (graceful no-op if unavailable)."""
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, skipping metrics setup")
        return
    try:
        metrics.set_meter_provider(MeterProvider())
    except Exception as e:  # noqa: BLE001
        logger.warning("Metrics setup failed: %s", e)
