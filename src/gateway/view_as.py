"""ViewAs 只读视角：后端真只读（权限统一第 6 步，方案 A）。

以前 ViewAs 只是前端模拟：``App.tsx`` 把写按钮禁掉，但 Owner 的令牌本身
仍是 Owner 的，直接调接口照样能写。本模块让只读在后端成立：

* 前端开着 ViewAs 时，所有请求带 ``X-Liuhao-View-As: <目标账号>`` 头
  （``apps/console/console/src/lib/auth.ts`` 的 ``tokenHeaders`` 统一带）；
* :class:`ViewAsReadOnlyMiddleware`（挂在 ``main.py``）在路由之前拦截：
  写方法（非 GET/HEAD/OPTIONS）一律 403 ``VIEW_AS_READ_ONLY`` + 审计；
  非法目标（不存在、已停用、不在查看者子树内、无权查看）一律 403
  ``VIEW_AS_DENIED`` + 审计，绝不静默回落到 Owner 视角；
* 读请求由 :func:`gateway.account_context.require_account_principal` 把
  ``AccountContext`` 换成目标账号的——成员能看到什么，ViewAs 就看到什么；
  Owner 专属端点因此也自动拒绝（目标不是根）。

不改动令牌本身：ViewAs 不签发降权令牌，令牌仍是查看者自己的；视角只
存在于这一次请求里。退出 ViewAs（前端去掉请求头）即恢复。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from .account_context import VIEW_AS_HEADER, validate_view_as, view_as_target_var
from .policy import _human_claims, extract_bearer_token

logger = logging.getLogger(__name__)

#: 只读视角下放行的方法；其余一律 403。
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: 中间件不拦截的路径：登录/刷新/登出与公开探针（前端本来就不带该头，
#: 这里是纵深防御——即使带了头也不让它干扰登录与健康检查）。
_EXEMPT_PREFIXES = ("/v1/auth/",)
_EXEMPT_PATHS = frozenset(
    {
        "/v1/health",
        "/v1/ready",
        "/v1/metrics",
        "/v1/metrics/prometheus",
    }
)


def _audit_view_as(outcome: str, viewer: str, target: str, details: Dict[str, Any]) -> None:
    """ViewAs 拒绝写审计；审计失败不改变拒绝结果（只记日志）。"""
    try:
        from ..kernels.audit import AuditEventType, AuditScope, log_event

        event_type = (
            AuditEventType.VIEW_AS_WRITE_DENIED
            if outcome == "view_as.write_denied"
            else AuditEventType.VIEW_AS_DENIED
        )
        audit_details = dict(details)
        audit_details.setdefault("action", outcome)
        audit_details.setdefault("target", target)
        audit_details.setdefault("view_as_target", target)
        log_event(
            event_type=event_type,
            principal_id=viewer or "anonymous",
            scope=AuditScope.L0,
            outcome=outcome,
            details=audit_details,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不能把 403 变成 500
        logger.warning("view-as audit failed (%s): %s", outcome, exc)


def _caller_from_token(request_headers: Dict[str, str]) -> Optional[str]:
    """从请求头解析出人类令牌主体；无效/缺失/非人类时返回 ``None``。

    返回 ``None`` 时中间件直接放行——真正的 401 由下游
    ``require_bearer_payload`` 按原口径返回，这里不抢先定性。
    """
    token = extract_bearer_token(
        request_headers.get("x-liuhao-token"), request_headers.get("authorization")
    )
    if not token:
        return None
    try:
        from ..security import get_jwt_handler

        payload = get_jwt_handler().validate_access_token(token)
        subject, _metadata = _human_claims(payload)
        return subject
    except Exception:  # noqa: BLE001 - 令牌问题一律交给下游鉴权
        return None


async def _send_json_error(
    send: Callable, status: int, code: str, message: str
) -> None:
    body = json.dumps(
        {"detail": {"code": code, "message": message}}, ensure_ascii=False
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class ViewAsReadOnlyMiddleware:
    """ViewAs 只读中间件（纯 ASGI，挂在路由之前）。

    只在 ``X-Liuhao-View-As`` 请求头出现时生效；没有该头的请求原样放行，
    ViewAs 关闭时零影响。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers") or []
        }
        target = (headers.get(VIEW_AS_HEADER.lower()) or "").strip()
        if not target:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        viewer = _caller_from_token(headers)
        if viewer is None:
            # 无令牌 / 令牌无效：交给下游鉴权回 401，不在这里 403。
            await self.app(scope, receive, send)
            return
        try:
            validate_view_as(viewer, target)
        except HTTPException:
            _audit_view_as(
                "view_as.denied",
                viewer,
                target,
                {"method": str(scope.get("method") or ""), "path": path},
            )
            await _send_json_error(send, 403, "VIEW_AS_DENIED", "无权以该视角查看")
            return
        if str(scope.get("method") or "").upper() not in READ_ONLY_METHODS:
            _audit_view_as(
                "view_as.write_denied",
                viewer,
                target,
                {"method": str(scope.get("method") or ""), "path": path},
            )
            await _send_json_error(
                send,
                403,
                "VIEW_AS_READ_ONLY",
                "当前为只读视角，不能执行写操作；退出视角后重试",
            )
            return
        # 读请求：把已校验的目标写进请求上下文变量，
        # ``require_account_principal`` 据此返回目标账号的视角；请求结束复位。
        reset_token = view_as_target_var.set(target)
        try:
            await self.app(scope, receive, send)
        finally:
            view_as_target_var.reset(reset_token)
        return
