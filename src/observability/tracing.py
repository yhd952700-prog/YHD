"""
Distributed Tracing for LiuHao AI OS

Provides OpenTelemetry-based distributed tracing with:
- Automatic span creation for AI operations
- Correlation ID propagation
- Custom span attributes for AI/agent operations
- Integration with logging and metrics
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHTTPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.b3 import B3MultiFormat
from opentelemetry.propagators.composite import CompositeHTTPPropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from typing import Optional, Dict, Any, Callable
from functools import wraps
import contextvars

# Import correlation ID from logging
from .logging_utils import get_correlation_id, correlation_context


# Global tracer provider
_tracer_provider: Optional[TracerProvider] = None
_tracer: Optional[trace.Tracer] = None


def init_tracing(
    service_name: str = "liuhao-ai-os",
    service_version: str = "1.0.0",
    otlp_endpoint: str = "http://localhost:4317",
    enable_console: bool = False,
) -> trace.Tracer:
    """
    Initialize OpenTelemetry tracing.

    Args:
        service_name: Service name for traces
        service_version: Service version
        otlp_endpoint: OTLP gRPC endpoint for trace export
        enable_console: Also export to console for debugging

    Returns:
        Configured tracer instance
    """
    global _tracer_provider, _tracer

    # Create resource
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": "production",
    })

    # Create tracer provider
    _tracer_provider = TracerProvider(resource=resource)

    # Add OTLP exporter
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Optionally add console exporter
    if enable_console:
        _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    # Set as global tracer provider
    trace.set_tracer_provider(_tracer_provider)

    # Create tracer
    _tracer = _tracer_provider.get_tracer(service_name, service_version)

    # Configure propagators (support both W3C TraceContext and B3)
    set_global_textmap(CompositeHTTPPropagator([
        TraceContextTextMapPropagator(),
        B3MultiFormat(),
    ]))

    # Auto-instrument common libraries
    RequestsInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    return _tracer


def get_tracer() -> trace.Tracer:
    """Get the global tracer, initializing if needed."""
    global _tracer
    if _tracer is None:
        _tracer = init_tracing()
    return _tracer


# ============================================================
# Span Attributes - Standardized for AI Operations
# ============================================================

class SpanAttributes:
    """Standardized span attribute keys for LiuHao AI OS."""

    # AI Provider attributes
    AI_PROVIDER = "ai.provider"
    AI_MODEL = "ai.model"
    AI_PROMPT_TOKENS = "ai.prompt_tokens"
    AI_COMPLETION_TOKENS = "ai.completion_tokens"
    AI_TOTAL_TOKENS = "ai.total_tokens"
    AI_TEMPERATURE = "ai.temperature"
    AI_MAX_TOKENS = "ai.max_tokens"

    # Agent attributes
    AGENT_ID = "agent.id"
    AGENT_TYPE = "agent.type"
    AGENT_TASK = "agent.task"
    AGENT_STATUS = "agent.status"

    # Employee attributes
    EMPLOYEE_NAME = "employee.name"
    EMPLOYEE_AGENT_COUNT = "employee.agent_count"

    # Goal/Task attributes
    GOAL_ID = "goal.id"
    GOAL_DESCRIPTION = "goal.description"
    GOAL_PRIORITY = "goal.priority"
    TASK_ID = "task.id"
    TASK_DESCRIPTION = "task.description"
    TASK_TYPE = "task.type"
    TASK_DEPENDS_ON = "task.depends_on"
    TASK_COUNT = "task.count"

    # Correlation
    CORRELATION_ID = "correlation.id"
    REQUEST_ID = "request.id"

    # Error
    ERROR_TYPE = "error.type"
    ERROR_MESSAGE = "error.message"


# ============================================================
# Decorators for Automatic Tracing
# ============================================================

def trace_ai_operation(
    provider: str,
    model: str,
    operation_name: str = "ai.generate",
) -> Callable:
    """
    Decorator to trace AI provider operations.

    Usage:
        @trace_ai_operation("openai", "gpt-4")
        def generate(prompt: str) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                # Set standard AI attributes
                span.set_attribute(SpanAttributes.AI_PROVIDER, provider)
                span.set_attribute(SpanAttributes.AI_MODEL, model)

                # Add correlation ID
                correlation_id = get_correlation_id()
                span.set_attribute(SpanAttributes.CORRELATION_ID, correlation_id)

                # Extract tokens from kwargs if available
                if 'temperature' in kwargs:
                    span.set_attribute(SpanAttributes.AI_TEMPERATURE, kwargs['temperature'])
                if 'max_tokens' in kwargs:
                    span.set_attribute(SpanAttributes.AI_MAX_TOKENS, kwargs['max_tokens'])

                try:
                    result = func(*args, **kwargs)

                    # Try to extract token usage from result
                    if hasattr(result, 'usage'):
                        usage = result.usage
                        if hasattr(usage, 'prompt_tokens'):
                            span.set_attribute(SpanAttributes.AI_PROMPT_TOKENS, usage.prompt_tokens)
                        if hasattr(usage, 'completion_tokens'):
                            span.set_attribute(SpanAttributes.AI_COMPLETION_TOKENS, usage.completion_tokens)
                        if hasattr(usage, 'total_tokens'):
                            span.set_attribute(SpanAttributes.AI_TOTAL_TOKENS, usage.total_tokens)

                    span.set_status(Status(StatusCode.OK))
                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute(SpanAttributes.ERROR_TYPE, type(e).__name__)
                    span.set_attribute(SpanAttributes.ERROR_MESSAGE, str(e))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def trace_agent_task(
    agent_id: str,
    agent_type: str,
    operation_name: str = "agent.task",
) -> Callable:
    """
    Decorator to trace agent task execution.

    Usage:
        @trace_agent_task("agent-001", "researcher")
        def execute_task(task: str) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                span.set_attribute(SpanAttributes.AGENT_ID, agent_id)
                span.set_attribute(SpanAttributes.AGENT_TYPE, agent_type)
                span.set_attribute(SpanAttributes.CORRELATION_ID, get_correlation_id())

                # Extract task from args/kwargs
                task = kwargs.get('task') or (args[0] if args else 'unknown')
                span.set_attribute(SpanAttributes.AGENT_TASK, str(task)[:200])

                try:
                    result = func(*args, **kwargs)
                    span.set_attribute(SpanAttributes.AGENT_STATUS, "completed")
                    span.set_status(Status(StatusCode.OK))
                    return result

                except Exception as e:
                    span.set_attribute(SpanAttributes.AGENT_STATUS, "failed")
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute(SpanAttributes.ERROR_TYPE, type(e).__name__)
                    span.set_attribute(SpanAttributes.ERROR_MESSAGE, str(e))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def trace_goal_decomposition(
    goal_id: str,
    operation_name: str = "goal.decompose",
) -> Callable:
    """
    Decorator to trace goal decomposition.

    Usage:
        @trace_goal_decomposition("goal-001")
        def decompose(goal: str) -> list:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                span.set_attribute(SpanAttributes.GOAL_ID, goal_id)
                span.set_attribute(SpanAttributes.CORRELATION_ID, get_correlation_id())

                # Extract goal description
                goal_desc = kwargs.get('goal') or (args[0] if args else 'unknown')
                span.set_attribute(SpanAttributes.GOAL_DESCRIPTION, str(goal_desc)[:500])

                if 'priority' in kwargs:
                    span.set_attribute(SpanAttributes.GOAL_PRIORITY, kwargs['priority'])

                try:
                    result = func(*args, **kwargs)

                    # Count tasks generated
                    task_count = len(result) if isinstance(result, list) else 0
                    span.set_attribute(SpanAttributes.TASK_COUNT, task_count)

                    span.set_status(Status(StatusCode.OK))
                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute(SpanAttributes.ERROR_TYPE, type(e).__name__)
                    span.set_attribute(SpanAttributes.ERROR_MESSAGE, str(e))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def trace_task_execution(
    task_id: str,
    task_type: str = "default",
    operation_name: str = "task.execute",
) -> Callable:
    """
    Decorator to trace task execution.

    Usage:
        @trace_task_execution("task-001", "research")
        def execute(task: Task) -> Result:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(operation_name) as span:
                span.set_attribute(SpanAttributes.TASK_ID, task_id)
                span.set_attribute(SpanAttributes.TASK_TYPE, task_type)
                span.set_attribute(SpanAttributes.CORRELATION_ID, get_correlation_id())

                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute(SpanAttributes.ERROR_TYPE, type(e).__name__)
                    span.set_attribute(SpanAttributes.ERROR_MESSAGE, str(e))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


# ============================================================
# Context Managers for Manual Tracing
# ============================================================

class TracedOperation:
    """Context manager for manual span management."""

    def __init__(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    ):
        self.name = name
        self.attributes = attributes or {}
        self.kind = kind
        self.span = None

    def __enter__(self) -> trace.Span:
        tracer = get_tracer()
        self.span = tracer.start_span(self.name, kind=self.kind)

        # Add correlation ID
        self.span.set_attribute(SpanAttributes.CORRELATION_ID, get_correlation_id())

        # Add custom attributes
        for key, value in self.attributes.items():
            self.span.set_attribute(key, value)

        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
            self.span.set_attribute(SpanAttributes.ERROR_TYPE, exc_type.__name__)
            self.span.set_attribute(SpanAttributes.ERROR_MESSAGE, str(exc_val))
            self.span.record_exception(exc_val)
        else:
            self.span.set_status(Status(StatusCode.OK))
        self.span.end()
        return False


def trace_operation(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
):
    """
    Create a traced operation context manager.

    Usage:
        with trace_operation("custom.operation", {"key": "value"}) as span:
            span.set_attribute("custom.attr", "value")
            do_work()
    """
    return TracedOperation(name, attributes, kind)


# ============================================================
# HTTP Client Tracing Helpers
# ============================================================

def inject_trace_context(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Inject current trace context into HTTP headers.

    Usage:
        headers = inject_trace_context({})
        response = requests.get(url, headers=headers)
    """
    from opentelemetry.propagate import inject
    inject(headers)
    return headers


def extract_trace_context(headers: Dict[str, str]):
    """
    Extract trace context from HTTP headers.

    Usage:
        ctx = extract_trace_context(request.headers)
        with trace.use_span(ctx, end_on_exit=True):
            handle_request()
    """
    from opentelemetry.propagate import extract
    return extract(headers)


# ============================================================
# Shutdown
# ============================================================

def shutdown_tracing():
    """Shutdown tracer provider gracefully."""
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None
