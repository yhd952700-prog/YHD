"""Web Push HTTP 面（大升级第二季 V2c）。

* ``GET /v1/push/status`` — 开关状态 + 本账号订阅数（不含端点明文列表）。
* ``GET /v1/push/vapid-key`` — 订阅用的 applicationServerKey（公钥）。
* ``POST /v1/push/enabled`` — 总开关（默认关；关着时订阅登记被拒）。
* ``POST /v1/push/subscriptions`` — 登记本账号的浏览器订阅。
* ``DELETE /v1/push/subscriptions`` — 按 endpoint 注销本账号订阅。
* ``POST /v1/push/test`` — 给本账号全部订阅发一条测试推送。

挂载时由 ``main.py`` 加人类主权闸门。VAPID 私钥只在数据目录（0600），
本面任何响应都不含私钥与他人订阅。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.tasks import webpush

from .account_context import AccountContext, deny, require_account_principal

router = APIRouter(prefix="/v1/push", tags=["push"])

#: SW 脚本单独走公开路由：浏览器拉 Service Worker 脚本不带应用 token，
#: 与主 PWA 的 /sw.js 静态文件同口径（脚本公开、不含任何密钥）。
sw_router = APIRouter(tags=["push"])

#: 推送专用 Service Worker（后端直出，作用域 /push/，不碰 PWA 主 SW、
#: 不改 dist）：只做 push → showNotification 与点击打开应用两件事。
PUSH_SW_JS = """/* Jarvis push worker (V2c) — scope /push/ */
self.addEventListener('push', function (event) {
  var data = { title: '贾维斯', body: '' };
  try { if (event.data) data = event.data.json(); } catch (e) {}
  event.waitUntil(self.registration.showNotification(data.title || '贾维斯', {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: 'jarvis-push'
  }));
});
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
"""


def _require_self(context: AccountContext) -> str:
    if getattr(context, "viewed_as_by", None):
        raise deny(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")
    return context.principal


class EnabledRequest(BaseModel):
    enabled: bool


class SubscriptionRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)
    keys: Dict[str, str]


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)


@router.get("/status")
def push_status(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    return webpush.push_status(_require_self(context))


@router.get("/vapid-key")
def push_vapid_key(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    _require_self(context)
    if not webpush.push_enabled():
        raise HTTPException(status_code=409, detail="推送总开关还没开。")
    return {"public_key": webpush.vapid_public_key()}


@router.post("/enabled")
def push_set_enabled(
    body: EnabledRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    _require_self(context)
    return webpush.set_push_enabled(body.enabled)


@router.post("/subscriptions")
def push_subscribe(
    body: SubscriptionRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    account = _require_self(context)
    try:
        return webpush.add_subscription(
            account, {"endpoint": body.endpoint, "keys": body.keys}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/subscriptions")
def push_unsubscribe(
    body: UnsubscribeRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    account = _require_self(context)
    return webpush.remove_subscription(account, body.endpoint)


@router.post("/test")
def push_test(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    account = _require_self(context)
    return webpush.send_push(webpush.TEST_PUSH, account=account)


@sw_router.get("/v1/push/sw.js")
def push_service_worker() -> Response:
    """推送 SW 脚本：公开（脚本本身不含任何密钥），带作用域放行头。"""
    return Response(
        content=PUSH_SW_JS,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/push/", "Cache-Control": "no-cache"},
    )
