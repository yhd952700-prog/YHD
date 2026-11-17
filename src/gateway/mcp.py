"""MCP 内部只读工具的网关入口（7-3 Step 1）。

只做一件事：让网关能经真实 MCP 协议调通进程内 MCP Server 的两个只读工具
（``memory.search`` / ``system.status``，见
``src/adapters/mcp/mcp_adapter.py: call_internal_tool``）。

规矩：

* 只有 Owner 能调（``_is_root``）；开关 ``LIUHAO_MCP_ADAPTER_ENABLED``
  默认关闭，关闭时 503、不静默、不回假结果。
* 白名单之外一律 422，不接受任意工具名；外部 MCP Server 不在这里接，
  仍默认不启用。
* 每次调用写审计（``src/kernels/audit/``，与例程接口同一写法），
  按调用方账号归属（Owner 即根账号）。
"""

from __future__ import annotations

import asyncio
import logging
from importlib import metadata as _metadata
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .approvals import _is_root, _principal

logger = logging.getLogger(__name__)


def _audit(action: str, *, principal_id: str, outcome: str,
           details: Dict[str, Any], correlation_id: str) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            principal_id or "system",
            AuditScope.L1,
            outcome,
            {"action": action, **details},
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮 MCP 调用
        logger.warning("mcp audit failed: %s", action)


def _package_version(name: str) -> Optional[str]:
    try:
        return str(_metadata.version(name))
    except Exception:  # noqa: BLE001 - 没装就是没装，如实报 None
        return None


class MCPCallRequest(BaseModel):
    tool: str = Field(default="", max_length=100)
    arguments: Dict[str, Any] = Field(default_factory=dict)


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    from .approvals import _no_viewer

    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/mcp", tags=["mcp"])

    def _require_root(viewer: Any) -> str:
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_owner", "message": "只有 Owner 能调 MCP 内部工具"},
            )
        return _principal(viewer)

    @router.get("/status")
    def mcp_status(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """MCP 接线状态：开关、版本口径、可调的内部工具。"""
        owner = _require_root(viewer)
        from src.adapters.mcp.mcp_adapter import (
            MCP_INTERNAL_TOOLS,
            MCP_WIRING_STATUS,
            is_adapter_enabled,
        )

        return {
            "enabled": is_adapter_enabled(),
            "wiring_status": MCP_WIRING_STATUS,
            "internal_tools": list(MCP_INTERNAL_TOOLS),
            "versions": {
                "mcp": _package_version("mcp"),
                "fastmcp_slim": _package_version("fastmcp-slim"),
            },
            "owner": owner,
        }

    @router.post("/call")
    async def mcp_call(
        payload: MCPCallRequest, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        """经真实 MCP 协议调一次内部只读工具。"""
        owner = _require_root(viewer)
        tool = (payload.tool or "").strip()
        if not tool:
            raise HTTPException(
                status_code=422,
                detail={"code": "empty_tool", "message": "要调的工具名不能为空"},
            )
        from src.adapters.mcp.mcp_adapter import (
            MCPDisabledError,
            MCPToolError,
            call_internal_tool,
        )

        try:
            result = await call_internal_tool(
                tool, payload.arguments, owner, timeout=30.0
            )
        except MCPDisabledError as exc:
            _audit(
                "mcp.call_disabled", principal_id=owner, outcome="deny",
                details={"tool": tool, "reason": str(exc)}, correlation_id="",
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "mcp_disabled",
                    "message": "MCP 未启用（LIUHAO_MCP_ADAPTER_ENABLED 默认关闭）。"
                    "打开开关后才能调内部只读工具。",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_tool", "message": str(exc)},
            ) from exc
        except MCPToolError as exc:
            _audit(
                "mcp.call_failed", principal_id=owner, outcome="deny",
                details={"tool": tool, "error": str(exc)}, correlation_id="",
            )
            raise HTTPException(
                status_code=502,
                detail={"code": "mcp_tool_error", "message": str(exc)},
            ) from exc
        except asyncio.TimeoutError as exc:
            _audit(
                "mcp.call_timeout", principal_id=owner, outcome="deny",
                details={"tool": tool, "timeout_seconds": 30}, correlation_id="",
            )
            raise HTTPException(
                status_code=504,
                detail={"code": "mcp_timeout", "message": "内部 MCP 工具调用超时（30 秒）"},
            ) from exc
        _audit(
            "mcp.call", principal_id=owner, outcome="success",
            details={"tool": tool}, correlation_id="",
        )
        return {"tool": tool, "result": result}

    return router
