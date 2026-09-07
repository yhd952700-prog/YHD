"""OpenTelemetry 链路追踪模块 for liuhao AI OS

Provides OpenTelemetry tracing setup with graceful degradation
when optional dependencies are not fully available.
"""

import logging
from typing import Any, Dict, Optional, TypeVar, Generic, Union
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry components gracefully
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry import _logging
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


def end_span(span_context):
    """结束一个 span

    Args:
        span_context: 由 create_span 返回的上下文管理器
    """
    if span_context is None:
        return
    if hasattr(span_context, '__exit__'):
        span_context.__exit__(None, None, None)


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


def create_span(name: str, **kwargs):
    """创建一个追踪 span

    Args:
        name: Span 名称
        **kwargs: 追踪属性（可包含 AISpanAttributes 值）
    """
    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available, returning no-op span context")
        return _NoopSpanContextmanager(name, kwargs.get("attributes", {}))
    tracer = get_tracer(name)
    span = tracer.start_span(name, **kwargs)
    return _SpanContextmanager(span)


def end_span(span_context):
    """结束一个 span

    Args:
        span_context: 由 create_span 返回的上下文管理器
    """
    if span_context is None:
        return
    if hasattr(span_context, '__exit__'):
        span_context.__exit__(None, None, None)


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