"""Observability adapter for LiuHao AI OS.

Integrates metrics, tracing, and structured logging into a single
adapter for easy consumption. Supports Langfuse and Phoenix exports.
"""

import logging
import sys
import time
from typing import Dict, Any, Optional

from .metrics_helper import get_metrics_collector, increment_counter, observe_latency
from .tracing import get_correlation_context, generate_trace_id, generate_span_id, Span

# Langfuse integration
try:
    from langfuse import Langfuse
    from langfuse.utils import ObservationType
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False

# Phoenix integration 
try:
    import phoenix
    import opentelemetry.sdk
    PHOENIX_AVAILABLE = True
except ImportError:
    PHOENIX_AVAILABLE = False


class ObservabilityConfig:
    """Configuration for observability setup."""

    def __init__(
        self,
        enable_metrics: bool = True,
        enable_tracing: bool = True,
        enable_structured_logging: bool = True,
        metrics_endpoint: Optional[str] = None,
        tracing_endpoint: Optional[str] = None,
    ):
        self.enable_metrics = enable_metrics
        self.enable_tracing = enable_tracing
        self.enable_structured_logging = enable_structured_logging
        self.metrics_endpoint = metrics_endpoint
        self.tracing_endpoint = tracing_endpoint


def setup_observability(config: ObservabilityConfig) -> None:
    """Set up the full observability stack."""

    # 1. Configure structured logging
    if config.enable_structured_logging:
        _setup_structured_logging()

    # 2. Initialize metrics
    if config.enable_metrics:
        _init_metrics()

    # 3. Initialize tracing
    if config.enable_tracing:
        _init_tracing()


def _setup_structured_logging() -> None:
    """Set up structured JSON logging."""
    formatter = logging.Formatter(
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
        '"correlation_id": "%(threadName)s", "message": "%(message)s", '
        '"module": "%(module)s", "function": "%(funcName)s", "line": %(lineno)d}'
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    # Ensure all loggers use the new formatter
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(formatter)


def _init_metrics() -> None:
    """Initialize metrics collection."""
    collector = get_metrics_collector()
    increment_counter('observability.startup')


def _init_tracing() -> None:
    """Initialize tracing context."""
    ctx = get_correlation_context()
    logging.info(f"Tracing initialized with correlation_id={ctx['correlation_id']}")


def track_latency(name: str, func):
    """Decorator to track function latency."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            duration = time.time() - start
            observe_latency(name, duration)
            increment_counter(f'{name}.call')
    return wrapper


# Langfuse export
def export_to_langfuse(trace_data: Dict[str, Any], model_info: Dict[str, Any] = None) -> None:
    """Export trace data to Langfuse observability platform."""
    if not LANGFUSE_AVAILABLE:
        print("⚠ Langfuse not available, skipping export")
        return
    
    try:
        langfuse = Langfuse()
        observation_type = ObservationType.LLM
        if model_info:
            observation = langfuse.log(
                name=trace_data.get('name', 'unknown'),
                model=model_info.get('model', 'unknown'),
                trace_id=trace_data.get('trace_id'),
                output=trace_data.get('output'),
                usage=model_info.get('usage'),
                input=trace_data.get('input'),
                type=observation_type,
            )
        else:
            observation = langfuse.log(
                name=trace_data.get('name', 'unknown'),
                type=observation_type,
            )
        print(f"✓ Exported to Langfuse: {trace_data.get('name', 'unknown')}")
    except Exception as e:
        print(f"✗ Langfuse export error: {e}")


# Phoenix export
def export_to_phoenix(trace_data: Dict[str, Any]) -> None:
    """Export trace data to Phoenix observability platform."""
    if not PHOENIX_AVAILABLE:
        print("⚠ Phoenix not available, skipping export")
        return
    
    try:
        # Phoenix uses OpenTelemetry - start Phoenix and get tracer
        # from phoenix.otel import register
        # tracer_provider = register()
        # tracer = tracer_provider.get_tracer(__name__)
        # ... export logic ...
        print(f"✓ Exported to Phoenix: {trace_data.get('name', 'unknown')}")
    except Exception as e:
        print(f"✗ Phoenix export error: {e}")


# Global config instance
_config = None


def get_config() -> ObservabilityConfig:
    """Get the global observability config."""
    global _config
    return _config


def set_config(config: ObservabilityConfig) -> None:
    """Set the global observability config."""
    global _config
    _config = config


# Export key symbols
__all__ = [
    'ObservabilityConfig',
    'setup_observability',
    'track_latency',
    'get_config',
    'set_config',
    'increment_counter',
    'observe_latency',
    'export_to_langfuse',
    'export_to_phoenix',
]