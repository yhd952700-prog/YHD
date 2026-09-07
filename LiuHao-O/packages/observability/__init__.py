"""observability — 十源 packages 层 facade.

指标 / 追踪 / 存储

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.observability import (
    ObservabilityStore,
    emit_span,
    emit_metric,
    get_observability_store,
)

from src.observability.metrics import (
    track_agent_task,
    track_coordination,
)

from src.observability.tracing import (
    create_span,
    end_span,
    track_ai_operation,
)

__all__ = [
    "ObservabilityStore",
    "emit_span",
    "emit_metric",
    "get_observability_store",
    "track_agent_task",
    "track_coordination",
    "create_span",
    "end_span",
    "track_ai_operation",
]
