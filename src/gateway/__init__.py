"""
Gateway Module for LiuHao AI OS

FastAPI-based API Gateway serving as the unified entry point for all services.
Provides REST + WebSocket endpoints, rate limiting, request validation,
and health check endpoints.

Exports:
- app: FastAPI application instance
- get_app(): Create app instance
"""

# 必须在导入 ``main`` 之前先拿到时钟基准：``from .main import`` 会连带导入
# FastAPI 与全部 router，并在模块末尾创建 app —— 这正是"冷启动 15 秒"里
# 最容易被误算成"解释器很慢"的那一段。
from . import startup_profile as _startup_profile

_startup_profile.enable_from_env()
_startup_profile.mark("进入 src.gateway 包")

from .main import get_app, app  # noqa: E402

_startup_profile.mark("包导入完成（main + app 已构建）")

__all__ = ["get_app", "app", "startup_profile"]
