"""多渠道接入的网关入口（七家合并 P4 → C-额外任务 3）。

* ``GET /v1/channels`` — 渠道状态（Owner 可见）：是否接线、凭证配了
  没有、开关、Joseph 自己接通的步骤。
* ``POST /v1/channels/{name}/enable|disable`` — Owner 开关渠道；
  没配凭证时 409（不假装连上了）。
* ``POST /v1/channels/webhook/telegram`` — Telegram 推送入口：
  不走人类登录，认 ``X-Telegram-Bot-Api-Secret-Token``（没配 Secret
  时拒绝一切推送）。
* ``POST /v1/channels/webhook/whatsapp`` — Twilio WhatsApp 推送入口：
  不走人类登录，认 Twilio 请求签名（``X-Twilio-Signature``）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

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
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮渠道接口
        logger.warning("channels audit failed: %s", action)


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    from .approvals import _no_viewer

    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/channels", tags=["channels"])

    def _require_root(viewer: Any) -> str:
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_owner", "message": "只有 Owner 能看渠道配置"},
            )
        return _principal(viewer)

    @router.get("")
    def list_channels_endpoint(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """渠道状态：5 个候选，含是否接线/配好凭证/开关/接通步骤。"""
        from src.ai.channels import channel_statuses

        _require_root(viewer)
        return {"channels": channel_statuses()}

    @router.post("/{name}/enable")
    def enable_channel_endpoint(
        name: str, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        """开一个渠道：没接线或没配凭证时 409（不假装连上了）。"""
        from src.ai.channels import (
            ChannelNotConfiguredError,
            ChannelNotReadyError,
            enable_channel,
        )

        owner = _require_root(viewer)
        try:
            result = enable_channel(name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "channel_not_found", "message": str(exc)}
            ) from exc
        except (ChannelNotReadyError, ChannelNotConfiguredError) as exc:
            _audit(
                "channel.enable_refused", principal_id=owner, outcome="deny",
                details={"channel": name, "reason": str(exc)}, correlation_id="",
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "channel_not_ready", "message": str(exc)},
            ) from exc
        _audit(
            "channel.enabled", principal_id=owner, outcome="success",
            details={"channel": name}, correlation_id="",
        )
        return result

    @router.post("/{name}/disable")
    def disable_channel_endpoint(
        name: str, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        """关一个渠道（开关落盘；凭证在环境变量里，不在这里动）。"""
        from src.ai.channels import disable_channel

        owner = _require_root(viewer)
        try:
            result = disable_channel(name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "channel_not_found", "message": str(exc)}
            ) from exc
        _audit(
            "channel.disabled", principal_id=owner, outcome="success",
            details={"channel": name}, correlation_id="",
        )
        return result

    # ── webhook：不走人类登录，认平台自己的签名 ─────────────────────

    @router.post("/webhook/telegram")
    async def telegram_webhook(request: Request) -> Dict[str, Any]:
        """Telegram 推送：Secret 验不过 401；渠道没开 409；其余一律 200。"""
        from src.ai.channels import (
            deliver_inbound,
            is_enabled,
            make_sender,
            normalize_telegram_update,
            verify_telegram_webhook,
        )

        if not verify_telegram_webhook(dict(request.headers)):
            _audit(
                "channel.webhook_refused", principal_id="system", outcome="deny",
                details={"channel": "telegram", "reason": "bad_or_missing_secret"},
                correlation_id="",
            )
            raise HTTPException(
                status_code=401,
                detail={"code": "bad_webhook_secret", "message": "Telegram 推送验签没过"},
            )
        if not is_enabled("telegram"):
            raise HTTPException(
                status_code=409,
                detail={"code": "channel_not_ready", "message": "Telegram 渠道还没打开"},
            )
        try:
            update = await request.json()
        except Exception as exc:  # noqa: BLE001 - Body 不是 JSON 时 400，不炸
            raise HTTPException(
                status_code=400, detail={"code": "bad_body", "message": str(exc)[:200]}
            ) from exc
        message = normalize_telegram_update(update if isinstance(update, dict) else {})
        if message is None:
            return {"ok": True, "handled": False, "reason": "不是文字消息或审批回调"}
        replier = None
        try:
            sender = make_sender("telegram")
            replier = lambda text: sender.send_message(message.external_sender_id, text)  # noqa: E731
        except Exception:  # noqa: BLE001 - 发不出去就不回执，不影响收消息
            logger.warning("telegram replier unavailable", exc_info=True)
        return {"ok": True, **deliver_inbound(message, replier=replier)}

    @router.post("/webhook/whatsapp")
    async def whatsapp_webhook(request: Request) -> Dict[str, Any]:
        """Twilio WhatsApp 推送：签名验不过 401；渠道没开 409；其余一律 200。

        注意：签名按 Twilio 收到的完整公网 URL 算；Jarvis 若在反向代理
        后面，要保证 Host 头就是 Twilio 配的那个公网地址，否则签名会对不上。
        """
        from src.ai.channels import (
            deliver_inbound,
            is_enabled,
            make_sender,
            normalize_twilio_whatsapp,
            verify_twilio_signature,
        )

        form = await request.form()
        params = {str(k): str(v) for k, v in form.multi_items()}
        signature = str(request.headers.get("x-twilio-signature") or "")
        if not verify_twilio_signature(str(request.url), params, signature):
            _audit(
                "channel.webhook_refused", principal_id="system", outcome="deny",
                details={"channel": "whatsapp", "reason": "bad_twilio_signature"},
                correlation_id="",
            )
            raise HTTPException(
                status_code=401,
                detail={"code": "bad_twilio_signature", "message": "Twilio 推送签名没过"},
            )
        if not is_enabled("whatsapp"):
            raise HTTPException(
                status_code=409,
                detail={"code": "channel_not_ready", "message": "WhatsApp 渠道还没打开"},
            )
        message = normalize_twilio_whatsapp(params)
        if message is None:
            return {"ok": True, "handled": False, "reason": "不是文字消息"}
        replier = None
        try:
            sender = make_sender("whatsapp")
            replier = lambda text: sender.send_message(message.external_sender_id, text)  # noqa: E731
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp replier unavailable", exc_info=True)
        return {"ok": True, **deliver_inbound(message, replier=replier)}

    return router
