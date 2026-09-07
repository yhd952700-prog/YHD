"""
Gateway Module for LiuHao AI OS

FastAPI-based API Gateway serving as the unified entry point for all services.
Provides REST + WebSocket endpoints, rate limiting, request validation,
and health check endpoints.

Exports:
- app: FastAPI application instance
- get_app(): Create app instance
"""

from .main import get_app, app

__all__ = ["get_app", "app"]
