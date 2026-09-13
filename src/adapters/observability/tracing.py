"""[LEGACY / NOT WIRED] Tracing module for LiuHao AI OS.

⚠️ 接线状态（2026-09-13 评审核实）：本模块**未接入运行时**。
全仓没有任何 src/gateway/ 或 src/ai/ 模块 import 它；它只被同目录的
observability_adapter.py 通过相对 import（``from .tracing import
get_correlation_context``）引用，而后者本身也未被运行时消费。
网关与 AI 层实际使用的权威 tracing 实现是顶层 ``src/observability/tracing.py``
（OpenTelemetry 封装；gateway/main.py:23 即 ``from ..observability.tracing
import get_tracer``）。

本模块是一套**自定义 correlation-id / Span** 实现，与权威实现**公开接口完全不同**
（本模块导出 CorrelationID / get_correlation_id / Span 等；权威导出
create_span / get_tracer / AISpanAttributes 等），二者是**意图重复、已被取代**
的关系。请勿将其接线进运行时；需要 tracing 能力请改用 src/observability/tracing。

（以下为原 docstring）
Provides correlation-ID-driven trace exports for distributed systems
observability.
"""

import uuid
import time
from threading import local
from typing import Dict, Any, Optional, List


class CorrelationID:
    """Thread-local correlation ID management."""

    def __init__(self):
        self._local = local()

    @property
    def id(self) -> str:
        """Get the current correlation ID, generating one if needed."""
        if not hasattr(self._local, 'id'):
            self._local.id = str(uuid.uuid4())
        return self._local.id

    @property
    def context(self) -> Dict[str, str]:
        """Get the correlation context dictionary."""
        return {
            'correlation_id': self.id,
            'timestamp': str(time.time()),
        }


# Thread-local instance
_correlation_id_local = local()


def set_correlation_id(cid: str) -> None:
    """Set the correlation ID for the current thread."""
    _correlation_id_local.id = cid


def get_correlation_id() -> str:
    """Get the current correlation ID, generating one if needed."""
    if not hasattr(_correlation_id_local, 'id'):
        _correlation_id_local.id = str(uuid.uuid4())
    return _correlation_id_local.id


def get_correlation_context() -> Dict[str, str]:
    """Get the full correlation context."""
    return {
        'correlation_id': get_correlation_id(),
        'timestamp': str(time.time()),
    }


def generate_trace_id() -> str:
    """Generate a new trace ID for distributed tracing."""
    return str(uuid.uuid4())


def generate_span_id() -> str:
    """Generate a new span ID for trace spans."""
    return format(uuid.uuid4().int >> 64, '016x')


class Span:
    """A single span in a trace."""

    def __init__(
        self,
        name: str,
        parent_span_id: Optional[str] = None,
        kind: str = 'internal',
    ):
        self.span_id = generate_span_id()
        self.trace_id = (
            parent_span_id  # Would be parent's trace_id in real usage
            or generate_trace_id()
        )
        self.name = name
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.start_time = None
        self.end_time = None
        self.attributes: Dict[str, Any] = {}
        self.events: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Mark the span start time."""
        self.start_time = time.time()

    def end(self) -> None:
        """Mark the span end time."""
        self.end_time = time.time()

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Dict[str, Any] = None) -> None:
        """Add an event to the span."""
        event = {
            'name': name,
            'timestamp': time.time(),
        }
        if attributes:
            event.update(attributes)
        self.events.append(event)

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary for export."""
        duration = None
        if self.start_time and self.end_time:
            duration = self.end_time - self.start_time

        return {
            'trace_id': self.trace_id,
            'span_id': self.span_id,
            'parent_span_id': self.parent_span_id,
            'name': self.name,
            'kind': self.kind,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration': duration,
            'attributes': self.attributes,
            'events': self.events,
        }


# Export functions
__all__ = [
    'CorrelationID',
    'set_correlation_id',
    'get_correlation_id',
    'get_correlation_context',
    'generate_trace_id',
    'generate_span_id',
    'Span',
]
