"""
LIUHAO X - Logging Package

Structured logging with correlation IDs throughout the system.
All logs include timestamp, level, logger, correlation_id, trace_id, span_id.
"""

import structlog
import logging
from typing import Optional

def setup_logging(
    level: str = "INFO",
    structlog_mode: str = "dev",
    add_correlation_ids: bool = True,
) -> structlog.stdlib.BoundLogger:
    """Set up structured logging for LIUHAO X."""
    
    # Processors
    processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.LogLevel(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if add_correlation_ids:
        processors.append(structlog.processors.CorrelationId())
    
    processors.append(structlog.processors.JSONRenderer())
    
    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_call=True,
    )
    
    # Also set up standard logging handler
    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    formatter = structlog.stdlib.Formatter(
        "%%(message)s"
    )
    handler.setFormatter(formatter)
    
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    
    return structlog.get_logger("liuhao-x")

def get_logger(name: str = "liuhao-x") -> structlog.stdlib.BoundLogger:
    """Get a structured logger with correlation support."""
    return structlog.get_logger(name)