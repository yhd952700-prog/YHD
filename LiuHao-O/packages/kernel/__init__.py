"""
LIUHAO X - Foundation Package

Core foundation for the Human-Sovereign Operating System for Autonomous Agent Systems.
Phase 1: Foundation - Repository, Settings, Logging, Config, Postgres, Redis, Docker, CI, Health, Ready.
"""

__title__ = "liuhao-x.foundation"
__version__ = "3.0.0"
__description__ = "Foundation layer for LIUHAO X"
__author__ = "LiuHao"
__license__ = "MIT"

from .config import Settings, get_settings
from .database import engine, Base, get_session_local
from .logging import setup_logging, get_logger
from .health import check_health, HealthStatus
from .ready import check_ready, ReadyStatus


def __getattr__(name: str):
    # Backward compatibility: `kernel.settings` stays available, but the
    # Settings instance is only created (and validated) on first access —
    # importing this package must not require LHX_* env vars to exist.
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Settings",
    "get_settings",
    "settings",
    "engine",
    "Base",
    "get_session_local",
    "setup_logging",
    "get_logger",
    "check_health",
    "HealthStatus",
    "check_ready",
    "ReadyStatus",
]