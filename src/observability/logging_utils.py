"""
Structured Logging Utilities for LiuHao AI OS

Provides:
- JSON structured logging
- Correlation ID propagation across async boundaries
- Standardized log fields for AI operations
- Integration with OpenTelemetry
"""

import logging
import logging.config
import uuid
import contextvars
from typing import Optional, Dict, Any
from contextlib import contextmanager
import structlog

# Context variable for correlation ID propagation
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Context variable for request/span metadata
request_context_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "request_context", default={}
)


def get_correlation_id() -> str:
    """Get current correlation ID, generating one if not present."""
    cid = correlation_id_var.get()
    if cid is None:
        cid = str(uuid.uuid4())[:8]
        correlation_id_var.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    """Explicitly set correlation ID for current context."""
    correlation_id_var.set(cid)


@contextmanager
def correlation_context(cid: Optional[str] = None):
    """Context manager for correlation ID."""
    old_cid = correlation_id_var.get()
    new_cid = cid or str(uuid.uuid4())[:8]
    correlation_id_var.set(new_cid)
    try:
        yield new_cid
    finally:
        correlation_id_var.set(old_cid)


@contextmanager
def request_context(**kwargs):
    """Context manager for request-level metadata."""
    old_ctx = request_context_var.get()
    new_ctx = {**old_ctx, **kwargs}
    request_context_var.set(new_ctx)
    try:
        yield new_ctx
    finally:
        request_context_var.set(old_ctx)


class CorrelationIdFilter(logging.Filter):
    """Logging filter to inject correlation ID into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        # Add request context if available
        ctx = request_context_var.get()
        for key, value in ctx.items():
            setattr(record, key, value)
        return True


def configure_logging(config_path: Optional[str] = None) -> None:
    """Configure structured logging from YAML config."""
    import yaml

    if config_path:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    else:
        # Default configuration
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                    "format": "%(timestamp)s %(level)s %(name)s %(correlation_id)s %(message)s",
                    "timestamp": True,
                },
                "console": {
                    "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(correlation_id)s | %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "filters": {
                "correlation_id": {
                    "()": CorrelationIdFilter,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "INFO",
                    "formatter": "console",
                    "stream": "ext://sys.stdout",
                    "filters": ["correlation_id"],
                },
                "json_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "INFO",
                    "formatter": "json",
                    "filename": "logs/liuhao-ai-os.jsonl",
                    "maxBytes": 10485760,
                    "backupCount": 10,
                    "encoding": "utf-8",
                    "filters": ["correlation_id"],
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["console", "json_file"],
            },
            "loggers": {
                "ai": {"level": "DEBUG", "handlers": ["console", "json_file"], "propagate": False},
                "ai.providers": {"level": "DEBUG", "handlers": ["console", "json_file"], "propagate": False},
                "ai.employee": {"level": "DEBUG", "handlers": ["console", "json_file"], "propagate": False},
            },
        }

    # Ensure log directory exists
    import os
    os.makedirs("logs", exist_ok=True)

    logging.config.dictConfig(config)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger with correlation ID support."""
    return logging.getLogger(name)


def get_structured_logger(name: str) -> structlog.BoundLogger:
    """Get a structlog logger with correlation ID support."""
    return structlog.get_logger(name)


# AI-specific logging helpers
def log_ai_request(logger: logging.Logger, provider: str, model: str, prompt_tokens: int = None, **kwargs):
    """Log an AI request with standardized fields."""
    logger.info(
        "AI request initiated",
        extra={
            "event_type": "ai_request",
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            **kwargs,
        },
    )


def log_ai_response(logger: logging.Logger, provider: str, model: str,
                    response_tokens: int = None, latency_ms: float = None, **kwargs):
    """Log an AI response with standardized fields."""
    logger.info(
        "AI response received",
        extra={
            "event_type": "ai_response",
            "provider": provider,
            "model": model,
            "response_tokens": response_tokens,
            "latency_ms": latency_ms,
            **kwargs,
        },
    )


def log_agent_task(logger: logging.Logger, agent_id: str, task: str, status: str, **kwargs):
    """Log an agent task execution."""
    logger.info(
        f"Agent task {status}",
        extra={
            "event_type": "agent_task",
            "agent_id": agent_id,
            "task": task,
            "status": status,
            **kwargs,
        },
    )


def log_goal_decomposition(logger: logging.Logger, goal_id: str, task_count: int, **kwargs):
    """Log a goal decomposition event."""
    logger.info(
        "Goal decomposed into tasks",
        extra={
            "event_type": "goal_decomposition",
            "goal_id": goal_id,
            "task_count": task_count,
            **kwargs,
        },
    )
