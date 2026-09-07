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

from .config import settings
from .database import engine, Base, get_session_local
from .logging import setup_logging, get_logger
from .health import check_health, HealthStatus
from .ready import check_ready, ReadyStatus

__all__ = [
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