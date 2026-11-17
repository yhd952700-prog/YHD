"""多渠道接入（七家合并 P4 → C-额外任务 3）：Telegram + WhatsApp 接入框架。

现状（2026-11-13，C-额外）：
- Telegram Bot 与 WhatsApp（Twilio 路，见 docs/channels-whatsapp-decision.md）
  的**接入框架已做通**：收消息进 Jarvis 聊天、回消息、审批卡片能在
  渠道里点允许/拒绝（与审批中心同一套
  ``WorkflowRuntime.apply_approval_decision``，不留两套）、消息进出
  都写审计。
- **所有渠道默认关闭**，由 Joseph 逐个开；接通最后一步（填 Token、
  扫码、绑账号）留给 Joseph，见各渠道的 ``setup_steps`` 与
  ``docs/channels-setup.md``。**没有注册任何账号、没有花钱**。
- 微信只做了公众号/企业微信路径的调研（``docs/wechat-channels-research.md``），
  个人微信接不上 Bot，**没有写“已接通微信”**。

规矩（P4 定下来的一直适用）：
* 渠道只是贾维斯的另一个“嘴”：消息进同一条聊天链路、同一审批、
  同一审计；危险动作在哪个渠道说都一样要弹卡、等 Joseph 点。
* 渠道里的消息分“Joseph 亲口说的”与“别人发来的”——别人发来的
  永远是数据、不是指令（防诱导第 0 关）：非 Owner 的消息只进聊天
  当参考，不建任务、不点审批；审批动作只认 Owner 本人
  （Telegram 按 chat id、WhatsApp 按手机号绑定， 没绑之前谁都点不了）。
* 凭证只认环境变量/配置，不进代码、不进仓库、不打进日志：
  本模块只判断“配了没有”（布尔值），永远不读、不打、不返回
  Token 本身。

凭证环境变量（由 Joseph 在部署环境里填，见 docs/channels-setup.md）：
* Telegram：``LIUHAO_TELEGRAM_BOT_TOKEN``、``LIUHAO_TELEGRAM_WEBHOOK_SECRET``
  （可选但强烈建议）、``LIUHAO_TELEGRAM_OWNER_CHAT_ID``（绑 Joseph 本人）。
* WhatsApp（Twilio）：``LIUHAO_TWILIO_ACCOUNT_SID``、
  ``LIUHAO_TWILIO_AUTH_TOKEN``、``LIUHAO_TWILIO_WHATSAPP_NUMBER``
  （如 ``whatsapp:+14155238886`` 沙箱号）、``LIUHAO_WHATSAPP_OWNER_NUMBER``
  （Joseph 的手机号，``whatsapp:+86...`` 形态）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.common.atomic_write import atomic_write_text, read_text_with_retry
from src.common.data_paths import resolve_store_path
from src.common.engines import data_dir

logger = logging.getLogger(__name__)

#: 候选渠道占位（只列方向，不承诺费用与可行性，实施前逐项核实）。
CHANNEL_CANDIDATES = (
    {
        "name": "telegram",
        "display_name": "Telegram",
        "note": "Bot API 公开，接入门槛低，通常先做它；Joseph 是否用 Telegram 未知。",
        "status": "not_connected",
        "enabled": False,
    },
    {
        "name": "whatsapp",
        "display_name": "WhatsApp",
        "note": "用户基数大，但商业 API 要申请与审核，费用与流程未核实，不承诺。",
        "status": "not_connected",
        "enabled": False,
    },
    {
        "name": "wechat",
        "display_name": "微信",
        "note": "个人号无官方机器人接口，合规做法未核实，不承诺能做；先问 Joseph 用不用微信，再核。",
        "status": "not_connected",
        "enabled": False,
    },
    {
        "name": "slack",
        "display_name": "Slack",
        "note": "已有 MCP / OpenAPI 可走，适合“工作群里使唤”，但 Joseph 是否在用未知。",
        "status": "not_connected",
        "enabled": False,
    },
    {
        "name": "discord",
        "display_name": "Discord",
        "note": "已有公开 API 可走；Joseph 是否在用未知。",
        "status": "not_connected",
        "enabled": False,
    },
)

#: 本轮真正接了框架的渠道（其余三个仍是占位）。
WIRED_CHANNELS = ("telegram", "whatsapp")

#: 渠道开关落盘：环境变量可覆盖位置；默认 data/channels.json。
#: 只存“开/关”，不存任何 Token。
CHANNELS_FILE_ENV = "LIUHAO_CHANNELS_FILE"
_DEFAULT_CHANNELS_PATH = data_dir() / "channels.json"


class ChannelNotReadyError(RuntimeError):
    """渠道还没接入：只有占位，没有可用的适配器。"""


class ChannelNotConfiguredError(ChannelNotReadyError):
    """框架已就绪，但 Joseph 还没填这一渠道的凭证/绑定自己的身份。"""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


# ── 占位列表（P4 原样保留：旧测试与旧接口都依赖它）────────────────────


def list_channels() -> List[Dict[str, Any]]:
    """候选渠道占位列表（全部未连接、全部默认关，不编状态）。"""
    return [dict(item) for item in CHANNEL_CANDIDATES]


def get_channel(name: str) -> Dict[str, Any]:
    """一个渠道的占位信息；没有这个候选渠道时抛 KeyError。"""
    for item in CHANNEL_CANDIDATES:
        if item["name"] == (name or "").strip().lower():
            return dict(item)
    raise KeyError(f"没有这个候选渠道：{name}")


# ── 配置与开关 ────────────────────────────────────────────────────────


def setup_steps(name: str) -> List[str]:
    """Joseph 接通这一渠道要自己做的最后几步（没做之前渠道保持关闭）。"""
    key = (name or "").strip().lower()
    if key == "telegram":
        return [
            "在 Telegram 里找 @BotFather，/newbot 建一个 Bot，拿到 Bot Token（只显示一次）。",
            "把 Token 填进部署环境的 LIUHAO_TELEGRAM_BOT_TOKEN（不要发给任何人、不要写进代码）。",
            "（建议）在 @BotFather 里给 webhook 设一个 Secret Token，填进 LIUHAO_TELEGRAM_WEBHOOK_SECRET；Jarvis 只认带这个 Secret 的推送。",
            "在 Telegram 里给 Bot 发一句话，从收到的更新里找到自己的 chat id，填进 LIUHAO_TELEGRAM_OWNER_CHAT_ID——只有这个 id 能点审批、能让 Jarvis 干活。",
            "把 Jarvis 的公网地址告诉 Telegram：POST https://api.telegram.org/bot<Token>/setWebhook，url 填 https://<你的 Jarvis 地址>/v1/channels/webhook/telegram。",
            "最后在设置 → 渠道里打开 Telegram。",
        ]
    if key == "whatsapp":
        return [
            "注册一个 Twilio 账号（https://www.twilio.com），在控制台开通 WhatsApp 沙箱（先免费试）或正式的 WhatsApp 号码。",
            "把 Account SID、Auth Token、WhatsApp 发信号码（如 whatsapp:+14155238886 沙箱号）分别填进 LIUHAO_TWILIO_ACCOUNT_SID、LIUHAO_TWILIO_AUTH_TOKEN、LIUHAO_TWILIO_WHATSAPP_NUMBER。",
            "把自己的手机号（whatsapp:+86... 形态）填进 LIUHAO_WHATSAPP_OWNER_NUMBER——只有这个号码能点审批、能让 Jarvis 干活。",
            "沙箱试用：用自己的手机给沙箱号发 Twilio 给的那句加入口令（如 join <口令>），加入后再发消息才收得到。",
            "在 Twilio 控制台把 WhatsApp 号码的收消息 webhook 指向 https://<你的 Jarvis 地址>/v1/channels/webhook/whatsapp（Twilio 用签名验推送，不用另外设 Secret）。",
            "最后在设置 → 渠道里打开 WhatsApp。正式商用要另外走 WhatsApp 模板消息审核，见 docs/channels-whatsapp-decision.md。",
        ]
    if key == "wechat":
        return [
            "个人微信接不上 Bot：不要信任何“个人号机器人”教程（会被封号）。",
            "合规路只有两条，见 docs/wechat-channels-research.md：微信公众号（需注册公众号）或企业微信（需注册企业微信并建应用）。",
            "先定用哪条路，再按那篇文档里的步骤注册、拿 Token。没定之前微信保持关闭。",
        ]
    return ["该渠道还没接线，先定 Joseph 每天用的那一个。"]


def is_configured(name: str) -> bool:
    """这一渠道的凭证配齐没有（只返回布尔值，不读出、不记录凭证本身）。"""
    key = (name or "").strip().lower()
    if key == "telegram":
        return bool(_env("LIUHAO_TELEGRAM_BOT_TOKEN"))
    if key == "whatsapp":
        return bool(
            _env("LIUHAO_TWILIO_ACCOUNT_SID")
            and _env("LIUHAO_TWILIO_AUTH_TOKEN")
            and _env("LIUHAO_TWILIO_WHATSAPP_NUMBER")
        )
    return False


def _channels_path() -> Path:
    return Path(
        resolve_store_path(
            "channels.json",
            _DEFAULT_CHANNELS_PATH,
            env_var=CHANNELS_FILE_ENV,
        )
    )


def _load_enabled(path: Optional[Path] = None) -> Dict[str, bool]:
    target = Path(path) if path else _channels_path()
    try:
        payload = json.loads(read_text_with_retry(target))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, dict):
        return {}
    return {str(k): bool(v) for k, v in enabled.items()}


def _save_enabled(enabled: Dict[str, bool], path: Optional[Path] = None) -> Path:
    target = Path(path) if path else _channels_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps({"enabled": enabled}, ensure_ascii=False, indent=2),
    )
    return target


def channel_statuses(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """每个渠道的真实状态：占位信息 + 是否配好凭证 + 开关（默认全关）。"""
    enabled = _load_enabled(path)
    out: List[Dict[str, Any]] = []
    for item in CHANNEL_CANDIDATES:
        name = item["name"]
        wired = name in WIRED_CHANNELS
        configured = is_configured(name) if wired else False
        out.append(
            {
                **dict(item),
                "wired": wired,
                "configured": configured,
                "enabled": bool(enabled.get(name, False)),
                "status": (
                    "ready"
                    if (configured and enabled.get(name))
                    else "configured" if configured
                    else "not_connected"
                ),
                "setup_steps": setup_steps(name),
            }
        )
    return out


def enable_channel(name: str, path: Optional[Path] = None) -> Dict[str, Any]:
    """开一个渠道：没接线的一律拒绝；接了线但没配凭证的也拒绝（不假装连上了）。

    凭证配齐后才真正打开开关并落盘。
    """
    item = get_channel(name)  # 先校验名字在不在候选里，不在抛 KeyError。
    key = item["name"]
    if key not in WIRED_CHANNELS:
        raise ChannelNotReadyError(
            f"渠道「{key}」还没接入：先定第一个渠道（Joseph 定他每天用的），"
            "当前只有占位，没有注册任何账号、没有连任何服务器"
        )
    if not is_configured(key):
        raise ChannelNotConfiguredError(
            f"渠道「{key}」的凭证还没配：按设置页里的步骤填好 "
            f"{'Bot Token' if key == 'telegram' else 'Twilio 账号信息'} 再开。"
        )
    enabled = _load_enabled(path)
    enabled[key] = True
    _save_enabled(enabled, path)
    return {
        "name": key,
        "enabled": True,
        "configured": True,
        "status": "ready",
    }


def disable_channel(name: str, path: Optional[Path] = None) -> Dict[str, Any]:
    """关一个渠道（开关落盘；凭证本身在环境变量里，不在这里动）。"""
    item = get_channel(name)
    enabled = _load_enabled(path)
    enabled[item["name"]] = False
    _save_enabled(enabled, path)
    return {"name": item["name"], "enabled": False}


def is_enabled(name: str, path: Optional[Path] = None) -> bool:
    """渠道开着没有（开关 + 凭证配齐，两个条件缺一不可）。"""
    key = (name or "").strip().lower()
    if key not in WIRED_CHANNELS:
        return False
    return bool(_load_enabled(path).get(key, False)) and is_configured(key)


# ── 审计（消息进出都要写；凭证、消息正文不进审计）────────────────────


def _audit(action: str, *, details: Dict[str, Any], correlation_id: str = "") -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            "system",
            AuditScope.L1,
            "success",
            {"action": action, **details},
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮渠道
        logger.warning("channel audit failed: %s", action)


# ── 入站消息归一化 ────────────────────────────────────────────────────


@dataclass
class InboundMessage:
    """一条从渠道收到的消息（归一化后；正文只在内存里流转）。"""

    channel: str
    external_sender_id: str
    sender_label: str
    text: str
    message_id: str = ""
    is_owner: bool = False
    #: 审批动作（点卡片/回关键词时才有）：("approve"|"reject", approval_id)。
    approval_action: Optional[tuple] = None

    def to_audit(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "sender": self.sender_label,
            "is_owner": self.is_owner,
            "has_text": bool(self.text),
            "approval_action": (
                f"{self.approval_action[0]}:{self.approval_action[1]}"
                if self.approval_action
                else None
            ),
        }


_APPROVE_RE = re.compile(r"^(允许|同意|approve|allow)\s+(\S+)\s*$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^(拒绝|不同意|reject|deny)\s+(\S+)\s*$", re.IGNORECASE)


def _parse_approval_keyword(text: str) -> Optional[tuple]:
    """WhatsApp 文字版审批：“允许 <审批号>”/“拒绝 <审批号>”。"""
    clean = (text or "").strip()
    match = _APPROVE_RE.match(clean)
    if match:
        return ("approve", match.group(2))
    match = _REJECT_RE.match(clean)
    if match:
        return ("reject", match.group(2))
    return None


def _is_telegram_owner(chat_id: Any) -> bool:
    owner = _env("LIUHAO_TELEGRAM_OWNER_CHAT_ID")
    return bool(owner) and str(chat_id) == owner


def _is_whatsapp_owner(from_number: str) -> bool:
    owner = _env("LIUHAO_WHATSAPP_OWNER_NUMBER")
    return bool(owner) and (from_number or "").strip().lower() == owner.lower()


def normalize_telegram_update(update: Dict[str, Any]) -> Optional[InboundMessage]:
    """Telegram webhook 的 update -> 归一化消息；看不懂的 update 返回 None。"""
    if not isinstance(update, dict):
        return None
    callback = update.get("callback_query") or {}
    if isinstance(callback, dict) and callback.get("data"):
        data = str(callback.get("data") or "").strip()
        action = None
        if data.startswith("approve:"):
            action = ("approve", data[len("approve:"):])
        elif data.startswith("reject:"):
            action = ("reject", data[len("reject:"):])
        if action is None:
            return None
        chat = callback.get("message") or {}
        chat_id = (chat.get("chat") or {}).get("id", "")
        sender = callback.get("from") or {}
        return InboundMessage(
            channel="telegram",
            external_sender_id=str(chat_id),
            sender_label=str(sender.get("username") or sender.get("first_name") or chat_id),
            text="",
            message_id=str(callback.get("id") or ""),
            is_owner=_is_telegram_owner(chat_id),
            approval_action=action,
        )
    message = update.get("message") or {}
    if not isinstance(message, dict) or not message.get("text"):
        return None
    chat_id = (message.get("chat") or {}).get("id", "")
    sender = message.get("from") or {}
    text = str(message.get("text") or "").strip()
    return InboundMessage(
        channel="telegram",
        external_sender_id=str(chat_id),
        sender_label=str(sender.get("username") or sender.get("first_name") or chat_id),
        text=text,
        message_id=str(message.get("message_id") or ""),
        is_owner=_is_telegram_owner(chat_id),
        approval_action=_parse_approval_keyword(text),
    )


def normalize_twilio_whatsapp(form: Dict[str, Any]) -> Optional[InboundMessage]:
    """Twilio WhatsApp webhook 的表单 -> 归一化消息；看不懂的返回 None。"""
    if not isinstance(form, dict):
        return None
    from_number = str(form.get("From") or "").strip()
    body = str(form.get("Body") or "").strip()
    if not from_number or not body:
        return None
    return InboundMessage(
        channel="whatsapp",
        external_sender_id=from_number,
        sender_label=from_number,
        text=body,
        message_id=str(form.get("MessageSid") or ""),
        is_owner=_is_whatsapp_owner(from_number),
        approval_action=_parse_approval_keyword(body),
    )


# ── webhook 鉴权 ──────────────────────────────────────────────────────


def verify_telegram_webhook(headers: Dict[str, Any], secret: Optional[str] = None) -> bool:
    """Telegram Secret Token 校验（没配 Secret 时拒绝一切推送，不猜）。"""
    expected = secret if secret is not None else _env("LIUHAO_TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        return False
    got = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == "x-telegram-bot-api-secret-token":
            got = str(value or "")
            break
    if not got:
        return False
    return hmac.compare_digest(got, expected)


def verify_twilio_signature(
    url: str, params: Dict[str, Any], signature: str, auth_token: Optional[str] = None
) -> bool:
    """Twilio 请求签名校验（官方算法：URL + 按键排序的参数拼接后 HMAC-SHA1）。

    官方文档：https://www.twilio.com/docs/usage/security#validating-requests。
    没配 Auth Token 时拒绝一切推送，不猜。
    """
    token = auth_token if auth_token is not None else _env("LIUHAO_TWILIO_AUTH_TOKEN")
    if not token or not signature:
        return False
    data = str(url or "")
    for key in sorted(params.keys()):
        data += str(key) + str(params[key])
    digest = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(str(signature), expected)


# ── 外发（回消息 / 发审批卡）──────────────────────────────────────────


class TelegramBotSender:
    """给 Telegram 发消息（Bot API）。只在调用时联网；构造时不联网。"""

    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token if token is not None else _env("LIUHAO_TELEGRAM_BOT_TOKEN")
        if not self._token:
            raise ChannelNotConfiguredError("Telegram Bot Token 还没配，无法发消息。")

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx

        url = f"https://api.telegram.org/bot{self._token}/{method}"
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram 回错：{data.get('description') or 'unknown'}")
        return data

    def send_message(self, chat_id: str, text: str) -> Dict[str, Any]:
        result = self._post("sendMessage", {"chat_id": chat_id, "text": text})
        _audit("channel.outbound", details={"channel": "telegram", "kind": "message"})
        return result

    def send_approval_card(self, chat_id: str, approval: Dict[str, Any]) -> Dict[str, Any]:
        """审批卡片：标题 + 动作 + 允许/拒绝两个按钮（点按钮走 callback）。"""
        approval_id = str(approval.get("approval_id") or "")
        text = (
            f"需要你拍板：{approval.get('title') or approval_id}\n"
            f"{approval.get('action_text') or ''}\n"
            f"风险：{approval.get('risk_note') or '见审批中心'}\n"
            f"审批号：{approval_id}"
        )
        result = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "允许", "callback_data": f"approve:{approval_id}"},
                            {"text": "拒绝", "callback_data": f"reject:{approval_id}"},
                        ]
                    ]
                },
            },
        )
        _audit(
            "channel.outbound",
            details={"channel": "telegram", "kind": "approval_card"},
            correlation_id=approval_id,
        )
        return result


class TwilioWhatsAppSender:
    """给 WhatsApp 发消息（Twilio Messages API）。只在调用时联网。"""

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
    ) -> None:
        self._sid = account_sid if account_sid is not None else _env("LIUHAO_TWILIO_ACCOUNT_SID")
        self._token = auth_token if auth_token is not None else _env("LIUHAO_TWILIO_AUTH_TOKEN")
        self._from = from_number if from_number is not None else _env("LIUHAO_TWILIO_WHATSAPP_NUMBER")
        if not (self._sid and self._token and self._from):
            raise ChannelNotConfiguredError("Twilio 账号信息还没配齐，无法发消息。")

    def send_message(self, to: str, text: str) -> Dict[str, Any]:
        import httpx

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        with httpx.Client(timeout=15.0, auth=(self._sid, self._token)) as client:
            resp = client.post(url, data={"From": self._from, "To": to, "Body": text})
        resp.raise_for_status()
        _audit("channel.outbound", details={"channel": "whatsapp", "kind": "message"})
        return resp.json()

    def send_approval_card(self, to: str, approval: Dict[str, Any]) -> Dict[str, Any]:
        """WhatsApp 审批卡：文字版（按钮模板要另外在 Twilio 配 Content SID，
        本轮不假装有按钮；回“允许 <审批号>”/“拒绝 <审批号>”同样生效）。"""
        approval_id = str(approval.get("approval_id") or "")
        text = (
            f"需要你拍板：{approval.get('title') or approval_id}\n"
            f"{approval.get('action_text') or ''}\n"
            f"风险：{approval.get('risk_note') or '见审批中心'}\n"
            f"回“允许 {approval_id}”通过，回“拒绝 {approval_id}”拒绝。"
        )
        result = self.send_message(to, text)
        _audit(
            "channel.outbound",
            details={"channel": "whatsapp", "kind": "approval_card"},
            correlation_id=approval_id,
        )
        return result


def make_sender(name: str) -> Any:
    """按渠道名建一个外发器；没配凭证时抛 ChannelNotConfiguredError。"""
    key = (name or "").strip().lower()
    if key == "telegram":
        return TelegramBotSender()
    if key == "whatsapp":
        return TwilioWhatsAppSender()
    raise ChannelNotReadyError(f"渠道「{key}」还没接入，没有外发器。")


# ── 入站投递：进聊天 + 审批（与审批中心同一套）───────────────────────


def _default_chat_sink(message: InboundMessage) -> str:
    """默认聊天落点：写进 Joseph 的默认聊天会话。

    * Owner 亲口说的 -> 按 user 的话追加进会话（渠道只是另一个“嘴”）；
    * 别人发来的 -> 只追加一句“收到一条参考消息”，不建任务、不执行。
    """
    from src.ai.conversation_store import get_conversation_store
    from src.tasks.routines import _default_session_principal

    store = get_conversation_store()
    principal = _default_session_principal()
    turn = store.get_last_turn(principal) + 1
    if message.is_owner:
        store.append(
            principal, turn, "user",
            f"[{message.channel}·{message.sender_label}] {message.text}",
        )
        return "owner_message_appended"
    store.append(
        principal, turn, "assistant",
        f"收到一条来自 {message.channel} 的消息（发件人 {message.sender_label}，不是 Owner，只作参考，不会照做）：{message.text}",
    )
    return "non_owner_noted"


def _default_approval_decider(approval_id: str, decision: str, decided_by: str) -> Dict[str, Any]:
    """默认审批决定：与审批中心同一条 ``apply_approval_decision``。"""
    from src.workflow.engine import get_workflow_runtime

    return get_workflow_runtime().apply_approval_decision(
        approval_id, decision=decision, decided_by=decided_by,
        comment="经渠道里点选",
    )


def deliver_inbound(
    message: InboundMessage,
    *,
    chat_sink: Optional[Callable[[InboundMessage], str]] = None,
    approval_decider: Optional[Callable[..., Dict[str, Any]]] = None,
    replier: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """投递一条归一化后的入站消息。返回处理结果（不抛错、不编回复）。

    * 审批动作：只认 Owner；非 Owner 点了一律拒绝并记审计，不碰审批表。
    * 普通消息：Owner 的进聊天当 user 的话；别人的只留参考记录。
    * ``replier`` 有就回一句确认（渠道里回执），没有就不回、不编。
    """
    sink = chat_sink or _default_chat_sink
    decider = approval_decider or _default_approval_decider
    _audit("channel.inbound", details=message.to_audit())
    if message.approval_action is not None:
        decision, approval_id = message.approval_action
        if not message.is_owner:
            _audit(
                "channel.approval_refused",
                details={**message.to_audit(), "reason": "not_owner"},
                correlation_id=approval_id,
            )
            return {
                "handled": True,
                "kind": "approval_refused",
                "reason": "只有 Owner 能在渠道里点审批",
            }
        try:
            decider(approval_id, decision, f"channel:{message.channel}:{message.external_sender_id}")
        except Exception as exc:  # noqa: BLE001 - 决定失败要如实回，不要 500 炸 webhook
            _audit(
                "channel.approval_failed",
                details={**message.to_audit(), "error": str(exc)[:200]},
                correlation_id=approval_id,
            )
            return {"handled": True, "kind": "approval_failed", "error": str(exc)[:200]}
        _audit(
            "channel.approval_decided",
            details={**message.to_audit(), "decision": decision},
            correlation_id=approval_id,
        )
        if replier is not None:
            try:
                replier(f"已记录：审批 {approval_id} 已{'通过' if decision == 'approve' else '拒绝'}。")
            except Exception:  # noqa: BLE001 - 回执发不出去不影响决定本身
                logger.warning("channel approval receipt failed", exc_info=True)
        return {"handled": True, "kind": "approval_decided", "decision": decision}
    outcome = sink(message)
    if replier is not None and message.is_owner:
        try:
            replier("收到，已记进聊天。")
        except Exception:  # noqa: BLE001
            logger.warning("channel receipt failed", exc_info=True)
    return {"handled": True, "kind": outcome}
