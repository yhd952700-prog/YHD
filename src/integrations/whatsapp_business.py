"""WhatsApp 备用渠道加深（A3 集成与会议组）。

只走 **Twilio 官方 WhatsApp Business API**（已有框架见
``src/ai/channels.py``：Twilio 签名验推送、``TwilioWhatsAppSender``
发消息、Owner 绑定手机号）。本模块在框架上往深里接：

* 收到消息能回：Owner 的消息进聊天（复用 ``deliver_inbound``，
  同一条聊天链路、同一审批、同一审计）；
* 收到消息能办事：Owner 发 ``任务：<内容>`` 时记一条待办草稿
  （``TaskDraftStore``，等 Joseph 在操作台确认后才变正式任务）；
  别人发来的永远只作参考，不建任务、不点审批（防诱导第 0 关）；
* 发消息：复用 ``TwilioWhatsAppSender``（Twilio Messages API）。
  24 小时会话窗口外 WhatsApp 只允许发**模板消息**：本模块不假装
  能发模板——没配模板时如实返回 ``template_required`` 提示，
  模板要在 Twilio 控制台另配 Content SID（见状态字段）。

凭证只认环境变量（``src/ai/channels.py`` 同款，不在这里另起一套）：
``LIUHAO_TWILIO_ACCOUNT_SID``、``LIUHAO_TWILIO_AUTH_TOKEN``、
``LIUHAO_TWILIO_WHATSAPP_NUMBER``、``LIUHAO_WHATSAPP_OWNER_NUMBER``；
模板（可选）：``LIUHAO_TWILIO_WHATSAPP_TEMPLATE_SID``。
只判断“配了没有”，不读出、不记录、不返回任何凭证本身。

红线（Joseph 2026-11-14 08:07 亲口定）：账号不能被风控搞没。
所以绝不用非官方办法：不用个人号机器人、不扒 WhatsApp 私有协议、
不批量自动群发。见 ``docs/connections-routes.md``。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

#: 收到消息里“办事”的触发前缀（Owner 亲口说的才算；别人发来的不认）。
TASK_PREFIXES = ("任务：", "任务:", "记一下：", "记一下:")

#: 模板消息 Content SID（可选；只判断配了没有）。
WHATSAPP_TEMPLATE_SID_ENV = "LIUHAO_TWILIO_WHATSAPP_TEMPLATE_SID"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def whatsapp_status() -> Dict[str, Any]:
    """WhatsApp Business 接通状态（给设置页与 ``/v1/connections`` 用）。"""
    from src.ai.channels import is_configured, is_enabled, setup_steps

    configured = is_configured("whatsapp")
    template_configured = bool(_env(WHATSAPP_TEMPLATE_SID_ENV))
    missing = []
    if not _env("LIUHAO_TWILIO_ACCOUNT_SID"):
        missing.append("LIUHAO_TWILIO_ACCOUNT_SID")
    if not _env("LIUHAO_TWILIO_AUTH_TOKEN"):
        missing.append("LIUHAO_TWILIO_AUTH_TOKEN")
    if not _env("LIUHAO_TWILIO_WHATSAPP_NUMBER"):
        missing.append("LIUHAO_TWILIO_WHATSAPP_NUMBER")
    if not _env("LIUHAO_WHATSAPP_OWNER_NUMBER"):
        missing.append("LIUHAO_WHATSAPP_OWNER_NUMBER")
    return {
        "configured": configured,
        "enabled": is_enabled("whatsapp"),
        "status": (
            "ready"
            if (configured and is_enabled("whatsapp"))
            else "configured" if configured
            else "not_configured"
        ),
        "route": "official_api",
        "route_label": "Twilio 官方 WhatsApp Business API（Business 号码 + 模板消息）",
        "risk": "low",
        "missing": missing,
        "template_configured": template_configured,
        "template_note": (
            "24 小时会话窗口内可直接回文字消息；窗口外只能发模板消息，"
            "模板要在 Twilio 控制台配好 Content SID 再填进 "
            "LIUHAO_TWILIO_WHATSAPP_TEMPLATE_SID，没配时窗口外不发、不假装发了。"
            if not template_configured
            else "模板 SID 已配（只显示配了没有，不显示 SID 本身）。"
        ),
        "setup_steps": setup_steps("whatsapp"),
    }


def send_whatsapp_message(
    to: str, text: str, *, within_24h_window: bool = True
) -> Dict[str, Any]:
    """给 WhatsApp 发一条文字消息（Twilio 官方 API）。

    ``within_24h_window=False`` 且没配模板 SID 时**拒绝发送**并如实
    返回 ``template_required``（不编“已发送”）。窗口内直接发。
    """
    if not within_24h_window and not _env(WHATSAPP_TEMPLATE_SID_ENV):
        return {
            "sent": False,
            "reason": "template_required",
            "message": (
                "对方 24 小时没回过消息，WhatsApp 只允许发模板消息；"
                "模板还没配（LIUHAO_TWILIO_WHATSAPP_TEMPLATE_SID），"
                "这次没发。请在 Twilio 控制台配好模板再发，或等对方先发消息。"
            ),
        }
    from src.ai.channels import TwilioWhatsAppSender

    sender = TwilioWhatsAppSender()
    result = sender.send_message(to, text)
    return {"sent": True, "sid": str(result.get("sid") or "")}


def handle_owner_task_message(
    message: Any,
    *,
    store: Any = None,
    account_id: str = "",
    chat_sink: Optional[Callable[[Any], str]] = None,
    replier: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """处理一条已归一化的 WhatsApp 入站消息（``InboundMessage``）。

    * 非 Owner：一律只进聊天当参考（复用 ``deliver_inbound``），不建草稿；
    * Owner 且正文以“任务：/记一下：”开头：记待办草稿 + 进聊天，
      回一句“已记成待办草稿，等你在操作台确认后才建任务”；
    * Owner 其他正文：只进聊天（复用 ``deliver_inbound``），不建草稿。

    返回 ``handled/kind``，与 ``deliver_inbound`` 同一套形状；
    草稿 id 在 ``draft_id`` 里（没有草稿时为空）。
    """
    from src.ai.channels import deliver_inbound

    if not getattr(message, "is_owner", False):
        return deliver_inbound(message, replier=replier)
    text = (getattr(message, "text", "") or "").strip()
    for prefix in TASK_PREFIXES:
        if text.startswith(prefix):
            title = text[len(prefix):].strip()
            if not title:
                return deliver_inbound(message, replier=replier)
            draft_store = store
            if draft_store is None:
                from .task_drafts import TaskDraftStore

                draft_store = TaskDraftStore()
            draft = draft_store.create(
                source="whatsapp",
                title=title,
                detail="WhatsApp 里 Owner 亲口说的待办（草稿，等确认后才建任务）。",
                external_ref=f"whatsapp:{getattr(message, 'message_id', '') or ''}",
                from_label=str(getattr(message, "sender_label", "") or ""),
                account_id=account_id,
            )
            outcome = deliver_inbound(
                message, chat_sink=chat_sink, replier=None
            )
            if replier is not None:
                try:
                    replier(
                        "收到，已记成待办草稿（等你在操作台确认后才正式建任务），"
                        "也已记进聊天。"
                    )
                except Exception:  # noqa: BLE001 - 回执发不出去不影响草稿本身
                    pass
            return {**outcome, "kind": "task_draft_created", "draft_id": draft["draft_id"]}
    return deliver_inbound(message, chat_sink=chat_sink, replier=replier)
