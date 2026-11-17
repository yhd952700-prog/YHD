"""多渠道接入专属硬化测试（补缺口轮，2026-11-17）。

覆盖此前零专属测试的 ``src/ai/channels.py``：

* 候选渠道占位与开关：默认全关、没接线的开不了、没配凭证的开不了，
  开关落盘后仍要凭证配齐才算 enabled；
* 入站归一化：Telegram / Twilio WhatsApp 的 Owner 判定、审批回调与
  关键词、看不懂的推送返回 None；
* webhook 鉴权：Telegram Secret 与 Twilio 签名的正/误/缺失路径；
* 入站投递安全线：非 Owner 点审批一律拒绝且不碰审批决定函数，
  非 Owner 消息只作参考、Owner 消息才回执。

凭证只用 monkeypatch 环境变量（假值），不联网、不读真实 Token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from src.ai import channels
from src.ai.channels import (
    ChannelNotConfiguredError,
    ChannelNotReadyError,
    InboundMessage,
)

_ENV_KEYS = (
    "LIUHAO_TELEGRAM_BOT_TOKEN",
    "LIUHAO_TELEGRAM_WEBHOOK_SECRET",
    "LIUHAO_TELEGRAM_OWNER_CHAT_ID",
    "LIUHAO_TWILIO_ACCOUNT_SID",
    "LIUHAO_TWILIO_AUTH_TOKEN",
    "LIUHAO_TWILIO_WHATSAPP_NUMBER",
    "LIUHAO_WHATSAPP_OWNER_NUMBER",
    "LIUHAO_CHANNELS_FILE",
)


@pytest.fixture(autouse=True)
def _clean_channel_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def channels_file(tmp_path):
    return tmp_path / "channels.json"


def _configure_telegram(monkeypatch):
    monkeypatch.setenv("LIUHAO_TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("LIUHAO_TELEGRAM_OWNER_CHAT_ID", "4242")


def _configure_whatsapp(monkeypatch):
    monkeypatch.setenv("LIUHAO_TWILIO_ACCOUNT_SID", "AC-fake")
    monkeypatch.setenv("LIUHAO_TWILIO_AUTH_TOKEN", "fake-auth-token")
    monkeypatch.setenv("LIUHAO_TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    monkeypatch.setenv("LIUHAO_WHATSAPP_OWNER_NUMBER", "whatsapp:+8613800000000")


# --------------------------------------------------------------------------- #
# 占位列表与状态
# --------------------------------------------------------------------------- #


def test_list_channels_defaults_and_copy_isolation():
    items = channels.list_channels()
    assert {c["name"] for c in items} == {
        "telegram", "whatsapp", "wechat", "slack", "discord",
    }
    assert all(c["enabled"] is False for c in items)
    assert all(c["status"] == "not_connected" for c in items)

    # 返回的是拷贝：改了返回值不能污染模块常量
    items[0]["enabled"] = True
    assert channels.list_channels()[0]["enabled"] is False

    assert channels.get_channel(" Telegram ")["name"] == "telegram"
    with pytest.raises(KeyError):
        channels.get_channel("myspace")


def test_channel_statuses_with_no_file_and_corrupt_file(channels_file):
    statuses = {s["name"]: s for s in channels.channel_statuses(channels_file)}
    assert statuses["telegram"]["wired"] is True
    assert statuses["whatsapp"]["wired"] is True
    assert statuses["wechat"]["wired"] is False
    assert statuses["telegram"]["configured"] is False
    assert statuses["telegram"]["status"] == "not_connected"
    assert statuses["telegram"]["setup_steps"]  # 给了接通步骤，不编状态

    # 开关文件坏了（非法 JSON / 非 dict）时按全关处理，不抛错
    channels_file.write_text("{not json", encoding="utf-8")
    assert all(
        s["enabled"] is False for s in channels.channel_statuses(channels_file)
    )
    channels_file.write_text(json.dumps(["enabled"]), encoding="utf-8")
    assert all(
        s["enabled"] is False for s in channels.channel_statuses(channels_file)
    )


def test_setup_steps_mention_env_vars_and_unknown_channel():
    telegram_steps = "\n".join(channels.setup_steps("telegram"))
    assert "LIUHAO_TELEGRAM_BOT_TOKEN" in telegram_steps
    assert "LIUHAO_TELEGRAM_OWNER_CHAT_ID" in telegram_steps
    whatsapp_steps = "\n".join(channels.setup_steps("whatsapp"))
    assert "LIUHAO_TWILIO_AUTH_TOKEN" in whatsapp_steps
    wechat_steps = "\n".join(channels.setup_steps("wechat"))
    assert "个人微信" in wechat_steps  # 老实话：个人号接不上
    assert len(channels.setup_steps("slack")) == 1  # 未接线只给一句方向


# --------------------------------------------------------------------------- #
# 开关：接线 / 凭证 / 落盘三道门
# --------------------------------------------------------------------------- #


def test_enable_channel_gates(channels_file):
    with pytest.raises(KeyError):
        channels.enable_channel("nope", channels_file)
    with pytest.raises(ChannelNotReadyError):
        channels.enable_channel("wechat", channels_file)  # 没接线
    with pytest.raises(ChannelNotConfiguredError):
        channels.enable_channel("telegram", channels_file)  # 没配凭证
    # 拒绝后不能留下假开关
    assert channels.is_enabled("telegram", channels_file) is False
    assert not channels_file.exists()


def test_enable_disable_roundtrip_requires_config(channels_file, monkeypatch):
    _configure_telegram(monkeypatch)
    result = channels.enable_channel("telegram", channels_file)
    assert result == {
        "name": "telegram", "enabled": True,
        "configured": True, "status": "ready",
    }
    assert channels.is_enabled("telegram", channels_file) is True
    status = {s["name"]: s for s in channels.channel_statuses(channels_file)}
    assert status["telegram"]["status"] == "ready"

    # 开关还在盘上，但凭证被撤走后就不算 enabled（不假装连着）
    monkeypatch.delenv("LIUHAO_TELEGRAM_BOT_TOKEN")
    assert channels.is_enabled("telegram", channels_file) is False
    status = {s["name"]: s for s in channels.channel_statuses(channels_file)}
    assert status["telegram"]["status"] == "not_connected"

    _configure_telegram(monkeypatch)
    disabled = channels.disable_channel("telegram", channels_file)
    assert disabled == {"name": "telegram", "enabled": False}
    assert channels.is_enabled("telegram", channels_file) is False
    # 关一个从没开过的已接线渠道也合法；未知渠道抛 KeyError
    assert channels.disable_channel("whatsapp", channels_file)["enabled"] is False
    with pytest.raises(KeyError):
        channels.disable_channel("nope", channels_file)


def test_whatsapp_configuration_needs_all_three_values(monkeypatch):
    assert channels.is_configured("whatsapp") is False
    monkeypatch.setenv("LIUHAO_TWILIO_ACCOUNT_SID", "AC-fake")
    monkeypatch.setenv("LIUHAO_TWILIO_AUTH_TOKEN", "fake")
    assert channels.is_configured("whatsapp") is False  # 还差发信号码
    monkeypatch.setenv("LIUHAO_TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    assert channels.is_configured("whatsapp") is True
    assert channels.is_configured("wechat") is False  # 未接线恒 False
    assert channels.is_configured("nope") is False


# --------------------------------------------------------------------------- #
# 入站归一化
# --------------------------------------------------------------------------- #


def test_normalize_telegram_message_owner_and_keyword(monkeypatch):
    _configure_telegram(monkeypatch)
    update = {
        "message": {
            "message_id": 7,
            "chat": {"id": 4242},
            "from": {"username": "joseph"},
            "text": "允许 appr-9",
        }
    }
    msg = channels.normalize_telegram_update(update)
    assert msg is not None
    assert msg.channel == "telegram"
    assert msg.is_owner is True
    assert msg.approval_action == ("approve", "appr-9")
    assert msg.sender_label == "joseph"

    update["message"]["chat"]["id"] = 9999  # 别人发来的
    msg = channels.normalize_telegram_update(update)
    assert msg is not None and msg.is_owner is False
    assert msg.approval_action == ("approve", "appr-9")  # 动作解析了，但投递层会拒


def test_normalize_telegram_callback_and_garbage(monkeypatch):
    _configure_telegram(monkeypatch)
    callback = {
        "callback_query": {
            "id": "cb-1",
            "data": "reject:appr-3",
            "from": {"first_name": "Joseph"},
            "message": {"chat": {"id": 4242}},
        }
    }
    msg = channels.normalize_telegram_update(callback)
    assert msg is not None
    assert msg.approval_action == ("reject", "appr-3")
    assert msg.is_owner is True
    assert msg.text == ""

    callback["callback_query"]["data"] = "something-else"
    assert channels.normalize_telegram_update(callback) is None
    assert channels.normalize_telegram_update({"message": {"chat": {"id": 1}}}) is None
    assert channels.normalize_telegram_update({"edited_message": {}}) is None
    assert channels.normalize_telegram_update("not-a-dict") is None
    assert channels.normalize_telegram_update(None) is None


def test_normalize_twilio_whatsapp(monkeypatch):
    _configure_whatsapp(monkeypatch)
    form = {
        "From": "WhatsApp:+8613800000000",  # 大小写不敏感地认 Owner
        "Body": "拒绝 appr-5",
        "MessageSid": "SM-1",
    }
    msg = channels.normalize_twilio_whatsapp(form)
    assert msg is not None
    assert msg.is_owner is True
    assert msg.approval_action == ("reject", "appr-5")
    assert msg.message_id == "SM-1"

    form["From"] = "whatsapp:+8613900000000"
    form["Body"] = "随便聊聊"
    msg = channels.normalize_twilio_whatsapp(form)
    assert msg is not None and msg.is_owner is False
    assert msg.approval_action is None

    assert channels.normalize_twilio_whatsapp({"From": "whatsapp:+1"}) is None
    assert channels.normalize_twilio_whatsapp({"Body": "hi"}) is None
    assert channels.normalize_twilio_whatsapp("nope") is None


def test_inbound_audit_payload_never_contains_text():
    msg = InboundMessage(
        channel="telegram", external_sender_id="1", sender_label="someone",
        text="绝密正文", is_owner=False,
    )
    audit = msg.to_audit()
    assert audit["has_text"] is True
    assert "绝密正文" not in json.dumps(audit, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# webhook 鉴权
# --------------------------------------------------------------------------- #


def test_verify_telegram_webhook(monkeypatch):
    # 没配 Secret 时拒绝一切推送，不猜
    assert channels.verify_telegram_webhook(
        {"x-telegram-bot-api-secret-token": "s3cret"}
    ) is False
    monkeypatch.setenv("LIUHAO_TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert channels.verify_telegram_webhook(
        {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}  # 头名大小写不敏感
    ) is True
    assert channels.verify_telegram_webhook(
        {"x-telegram-bot-api-secret-token": "wrong"}
    ) is False
    assert channels.verify_telegram_webhook({}) is False
    # 显式传 secret 时以传入为准
    assert channels.verify_telegram_webhook(
        {"x-telegram-bot-api-secret-token": "abc"}, secret="abc"
    ) is True


def _twilio_signature(url: str, params: dict, token: str) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_verify_twilio_signature(monkeypatch):
    url = "https://example.com/v1/channels/webhook/whatsapp"
    params = {"From": "whatsapp:+1", "Body": "hi"}
    good = _twilio_signature(url, params, "auth-token-1")
    assert channels.verify_twilio_signature(
        url, params, good, auth_token="auth-token-1"
    ) is True
    assert channels.verify_twilio_signature(
        url, params, good, auth_token="other-token"
    ) is False
    assert channels.verify_twilio_signature(
        url, {**params, "Body": "tampered"}, good, auth_token="auth-token-1"
    ) is False
    assert channels.verify_twilio_signature(url, params, "") is False
    # 没配 Auth Token 时拒绝（环境里也没有）
    assert channels.verify_twilio_signature(url, params, good) is False
    monkeypatch.setenv("LIUHAO_TWILIO_AUTH_TOKEN", "auth-token-1")
    assert channels.verify_twilio_signature(url, params, good) is True


# --------------------------------------------------------------------------- #
# 外发器构造（不联网）
# --------------------------------------------------------------------------- #


def test_make_sender_gates(monkeypatch):
    with pytest.raises(ChannelNotReadyError):
        channels.make_sender("wechat")
    with pytest.raises(ChannelNotConfiguredError):
        channels.make_sender("telegram")  # 没 Token 建不出外发器
    with pytest.raises(ChannelNotConfiguredError):
        channels.make_sender("whatsapp")

    _configure_telegram(monkeypatch)
    assert isinstance(channels.make_sender("telegram"), channels.TelegramBotSender)
    monkeypatch.setenv("LIUHAO_TWILIO_ACCOUNT_SID", "AC-fake")
    with pytest.raises(ChannelNotConfiguredError):
        channels.make_sender("whatsapp")  # 只配一部分仍不行
    _configure_whatsapp(monkeypatch)
    assert isinstance(channels.make_sender("whatsapp"), channels.TwilioWhatsAppSender)


# --------------------------------------------------------------------------- #
# 入站投递：审批只认 Owner
# --------------------------------------------------------------------------- #


def _msg(**overrides) -> InboundMessage:
    base = dict(
        channel="whatsapp", external_sender_id="whatsapp:+8613800000000",
        sender_label="owner", text="你好", is_owner=True,
    )
    base.update(overrides)
    return InboundMessage(**base)


def test_deliver_non_owner_approval_is_refused_without_touching_decider():
    decided = []
    result = channels.deliver_inbound(
        _msg(is_owner=False, approval_action=("approve", "appr-1")),
        chat_sink=lambda m: "should-not-run",
        approval_decider=lambda *a: decided.append(a) or {},
    )
    assert result["kind"] == "approval_refused"
    assert decided == []  # 审批决定函数一次都没碰


def test_deliver_owner_approval_decides_and_receipts():
    decided = []
    receipts = []
    result = channels.deliver_inbound(
        _msg(approval_action=("reject", "appr-2")),
        approval_decider=lambda aid, decision, by: decided.append(
            (aid, decision, by)
        ) or {"ok": True},
        replier=receipts.append,
    )
    assert result == {"handled": True, "kind": "approval_decided", "decision": "reject"}
    assert decided[0][0] == "appr-2"
    assert decided[0][1] == "reject"
    assert decided[0][2].startswith("channel:whatsapp:")
    assert receipts and "appr-2" in receipts[0]


def test_deliver_approval_failure_is_reported_not_raised():
    def boom(*_a):
        raise RuntimeError("审批不存在")

    result = channels.deliver_inbound(
        _msg(approval_action=("approve", "appr-x")), approval_decider=boom,
    )
    assert result["kind"] == "approval_failed"
    assert "审批不存在" in result["error"]


def test_deliver_plain_messages_owner_vs_non_owner():
    sunk = []
    receipts = []

    def sink(message):
        sunk.append(message)
        return "owner_message_appended" if message.is_owner else "non_owner_noted"

    owner_result = channels.deliver_inbound(
        _msg(), chat_sink=sink, replier=receipts.append
    )
    assert owner_result["kind"] == "owner_message_appended"
    assert len(receipts) == 1  # Owner 消息给回执

    receipts.clear()
    guest_result = channels.deliver_inbound(
        _msg(is_owner=False, sender_label="陌生人"),
        chat_sink=sink, replier=receipts.append,
    )
    assert guest_result["kind"] == "non_owner_noted"
    assert receipts == []  # 非 Owner 不回执、不互动
    assert len(sunk) == 2
