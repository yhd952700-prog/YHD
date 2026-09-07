"""Structlog-configured logging for liuhao AI OS"""
import structlog
import logging

def configure_logging():
    """Configure structlog for structured JSON output."""
    log_level = getattr(logging, "INFO", 20)
    
    shared_processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
    
    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    
    return logging.getLogger(__name__)
