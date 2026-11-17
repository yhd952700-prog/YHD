"""A3s：补齐清单七件的后端。

本模块集中实现 0.4.6-big 补齐七件的后端能力（B 组随后做界面），与
``a2a.py`` / ``a2b.py`` 同款做法：新增的小状态统一存在一份
``a3s_state.json``（``LIUHAO_A3S_STATE_FILE`` 可覆盖，默认走数据根目录），
写时用 ``atomic_write_text`` + 文件锁；凡是“没配好、没接通、没数据”的
情况一律返回老实空状态，绝不编数字。

七件（与任务书一一对应）：
1. 自定义第三方模型地址：待确认 → Joseph 亲手确认 → 才写进模型配置库。
   自定义地址只活在 Joseph 自己的配置里，绝不写进
   ``TRUSTED_VENDOR_HOSTS`` / 出站登记表代码（本模块不碰那两处）；
   Key 用模型配置库同款 AES-256-GCM 加密存放，永不显示全；
   备份/迁移导出时自定义端点整条剔除（见 ``custom_endpoints_backup_view``）。
2. 群聊不漏秘密：发往群聊的消息先过敏感内容检查，审批卡 / 密钥 / 草稿类
   内容拒绝进群并记一条原因（返回人话说明，不静默丢）；1:1 不受影响。
3. 说错了能纠正：纠正记进记忆主线（``account_memory``，source=correction），
   原说法打上 superseded 标记；召回时纠正过的条目优先，旧说法不再出现。
4. 升级坏了自动回滚：只接线已有机制——A2a 启动自检 + ``BackupManager``
   一键恢复（``a2a.restore_latest_backup``）。自检 fail 时触发回滚并留记录。
   本轮已接进开机流程：``main._warm_chat_stack`` 的后台步调
   ``startup_self_check_and_rollback()``（端口按真实监听端口重算，10 分钟
   内已有回滚记录就跳过）；进程根本起不来时仍需启动器调端点，见
   ``AUTO_ROLLBACK_GAP``。
5. 每人用量封顶（已改口径）：子账号不设任何上限；只落主账号自己的总上限。
   复用 A2a 的用量口径（``account_usage`` 真实账本 + ``BillingEngine`` 定价），
   超了就停并经 ``report_stuck`` 告诉 Joseph。本轮已接进聊天热路径：
   ``chat.py._enforce_usage_caps`` 在生成前调 ``owner_cap_allows_call``
   （主账号总上限）与 ``a2a.bot_cap_status``（per-Bot 上限），到上限返回
   429 拦截；判断一律用无副作用的状态函数，通知只由显式检查发。6. 声音认人：声纹已落地（见 ``VOICEPRINT_STATUS``：本机 sherpa-onnx
   + 3D-Speaker 模型，不把语音传上云）；落地的是第二条：语音入口的危险命令分级，
   危险的一律进“等你确认”收件箱，屏幕上点了才执行，语音里不直接执行。
7. 数据通用格式导出：聊天 / 任务 / 记忆 / 文件清单按类型导出（文字类易读
   文本、结构用 JSON），一次导出是一份清单；Key 一律剔除（复用
   ``a2b._scrub_keys``，再加一道断言式检查）。

底线：不碰登录/权限/审计链/备份恢复核心逻辑，只在已有机制上接线；
``accounts.py:324`` 与 ``local_proof.py:161`` 的 403 文案不动；
``api.x.ai`` 不进信任名单与出站登记表；LGPL 法务书面确认仍缺，
本模块不宣称许可证已闭环。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..common.input_guard import normalize_required_text
from .account_context import AccountContext, deny, require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/a3s", tags=["a3s"])

_STATE_ENV = "LIUHAO_A3S_STATE_FILE"
_state_lock = threading.Lock()

#: 备份/迁移导出里自定义端点的处理：整条剔除（Key 与地址都不进包），
#: 换机后 Joseph 在设置里重新添加、重新确认一次。
CUSTOM_ENDPOINT_EXPORT_NOTE = (
    "自定义模型地址与 Key 不进备份/迁移包（整条剔除）：换机后请在设置 → "
    "模型与 Key 里重新添加一次地址和 Key，并点确认生效。"
)

#: 第 4 件的缺口（诚实写清：进程内已接进开机流程，进程起不来那层仍缺）。
AUTO_ROLLBACK_GAP = {
    "wired_into_boot": True,
    "gap": (
        "已接进开机流程：网关后台预热链（main._warm_chat_stack，首屏发出去后）"
        "跑 a3s.startup_self_check_and_rollback()，自检 fail 就自动回滚并留记录；"
        "10 分钟内已有回滚记录就跳过，防回滚循环。仍缺的一层在进程之外：网关"
        "进程自身起不来（端口被占、uvicorn 没起来）时，进程内自检跑不起来，"
        "仍需启动器或运维调 POST /v1/a3s/upgrade/auto-rollback 手动触发。"
    ),
    "what_exists": (
        "evaluate_startup_and_rollback()：跑 A2a 启动自检，fail 项触发 "
        "a2a.restore_latest_backup()（BackupManager.restore_backup），"
        "回滚记录写进 a3s_state.json，可用 GET /v1/a3s/upgrade/rollback-records 查；"
        "startup_self_check_and_rollback()：开机自检 + 自动回滚的后台步，"
        "端口项按真实监听端口重算，10 分钟内已有回滚记录就跳过。"
    ),
    "what_is_missing": "进程根本起不来时的启动器层钩子（不在本轮）。",
}

#: 第 6 件声纹那条已落地（2026-11-15 第 4 段）：本机合规的本地声纹引擎
#: 是 sherpa-onnx（Apache-2.0）+ 3D-Speaker ERes2Net-Base 模型（约 38MB，
#: 打开声纹时按需下载一次，不随包带走）；注册、验证、标定、删除都在
#: ``/v1/voice/voiceprint/*``，声纹只存本机加密特征、不进备份。
#: 语音绝不传上云比对。危险语音命令仍须屏幕点确认（第二条）才执行。
VOICEPRINT_STATUS = {
    "landed": True,
    "method_landed": "voiceprint_matching",
    "method_not_landed": None,
    "reason": (
        "声纹认人已落地：本机 sherpa-onnx（Apache-2.0）+ 3D-Speaker "
        "ERes2Net-Base 模型提特征，注册 2–3 句、余弦相似度比对、阈值自动"
        "标定；特征 AES-GCM 加密只存本机、不进备份。语音绝不传上云比对。"
        "危险语音命令仍须在屏幕上点确认才执行。"
    ),
}

#: 第 5 件：子账号不设上限的书面规矩（Joseph 2026-11-14 ~09:26 拍板）。
CAP_POLICY = {
    "owner_total_cap": (
        "只对主账号自己设每月总花费上限（他自己的 Key、他自己的调用）。"
        "超了就停，并告诉他。"
    ),
    "subaccounts": (
        "子账号不设任何上限：Bot 数量、存储、并发、花费都不封顶——"
        "子账号用自己的 Key、花自己的钱。本模块没有任何给子账号加封顶的"
        "接口，set_owner_monthly_cap 只接受主账号本人。"
    ),
    "unpriced_models": (
        "没定价的模型费用算不进合计（cost_complete=false），上限拦不住它："
        "检查结果会如实写 enforceable=false，不假装拦住了。"
    ),
}


def _principal(context=Depends(require_account_principal)):
    return context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path() -> str:
    from ..common.data_paths import resolve_store_path

    return resolve_store_path(
        "a3s_state.json",
        os.path.join("data", "a3s_state.json"),
        env_var=_STATE_ENV,
    )


def _default_state() -> Dict[str, Any]:
    return {
        "custom_endpoints": [],
        "rollback_records": [],
        "owner_cap": {},
        "voice_pending": [],
    }


def load_state() -> Dict[str, Any]:
    """读状态文件；不存在/损坏时返回默认空状态（不编数据）。"""
    from ..common.atomic_write import read_text_with_retry

    try:
        raw = json.loads(read_text_with_retry(_state_path()))
    except (OSError, ValueError):
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    merged = _default_state()
    merged.update(raw)
    return merged


def save_state(state: Dict[str, Any]) -> None:
    from ..common.atomic_write import atomic_write_text, exclusive_file_lock

    path = _state_path()
    with _state_lock:
        with exclusive_file_lock(f"{path}.lock", description="A3s 状态"):
            atomic_write_text(
                path,
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            )


def _scrub_for_export(payload: Any) -> Any:
    """导出前的最后一道剔除：复用 A2b 搬家导出的 Key 剔除规则。"""
    from .a2b import _scrub_keys

    return _scrub_keys(payload)


def _assert_no_key_material(payload: Any, where: str) -> None:
    """断言式检查：导出内容里不许出现任何 Key 形态的字段。"""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    lowered = text.lower()
    for needle in ("api_key", "api-key", "apikey", "secret", "password",
                   "credential", "api_key_enc", "enc:v1:"):
        if needle in lowered:
            raise ValueError(f"导出内容里不许出现 {needle}（{where}）")


# --------------------------------------------------------------------------- #
# 第 1 件：自定义第三方模型地址
# --------------------------------------------------------------------------- #

def _model_store():
    from ..ai.model_config import ModelConfigStore

    return ModelConfigStore()


def _mask_key(plain: str) -> Optional[str]:
    from ..ai.model_config import mask_secret

    return mask_secret(plain)


def _validate_custom_endpoint(name: str, base_url: str, model: str,
                              api_key: str) -> Tuple[str, str, str, str]:
    from ..ai.model_config import is_masked_placeholder, normalize_api_key

    clean_name = normalize_required_text(name, "名称", max_chars=64)
    clean_model = normalize_required_text(model, "模型名", max_chars=128)
    url = str(base_url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("自定义地址必须以 http:// 或 https:// 开头")
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        raise ValueError("自定义地址里没有写主机名（例如 https://api.example.com/v1）")
    raw_key = str(api_key or "").strip()
    if not raw_key:
        raise ValueError("请先填 Key：自定义地址没有 Key 连不上")
    if is_masked_placeholder(raw_key):
        raise ValueError("请填真实的 Key，不要把掩码（****）当 Key 提交")
    normalize_api_key(raw_key)  # 只校验形态，不调接口、不花钱
    return clean_name, url, clean_model, raw_key


def _public_endpoint(record: Dict[str, Any]) -> Dict[str, Any]:
    """对外视图：永远不含 Key 明文与密文，只留掩码。"""
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or ""),
        "base_url": str(record.get("base_url") or ""),
        "host": str(record.get("host") or ""),
        "model": str(record.get("model") or ""),
        "provider": str(record.get("provider") or "openai"),
        "status": str(record.get("status") or "pending"),
        "api_key_masked": record.get("api_key_masked"),
        "model_config_id": record.get("model_config_id"),
        "owner": str(record.get("owner") or ""),
        "created_at": str(record.get("created_at") or ""),
        "confirmed_at": record.get("confirmed_at"),
    }


def add_custom_endpoint(owner: str, name: str, base_url: str, model: str,
                        api_key: str, *, provider: str = "openai") -> Dict[str, Any]:
    """新增自定义地址：先是“待确认”，Joseph 点确认才生效。

    Key 用模型配置库同款 AES-256-GCM（``ModelConfigStore._encrypt_secret``，
    AAD 绑本次记录 id）加密后才落盘；明文只在内存里停留这一调用。
    自定义地址只活在 Joseph 自己的配置里：本函数不写
    ``TRUSTED_VENDOR_HOSTS``、不出站登记表，什么代码名单都不动。
    """
    clean_name, url, clean_model, raw_key = _validate_custom_endpoint(
        name, base_url, model, api_key
    )
    proto = str(provider or "openai").strip().lower()
    if proto not in ("openai",):
        # 目前只接 OpenAI 兼容协议的聚合地址；别的协议以后再加，不静默认领。
        raise ValueError("自定义地址目前只支持 OpenAI 兼容协议（provider=openai）")
    endpoint_id = str(uuid.uuid4())
    store = _model_store()
    encrypted = store._encrypt_secret(endpoint_id, raw_key)
    record = {
        "id": endpoint_id,
        "owner": str(owner),
        "name": clean_name,
        "base_url": url,
        "host": (urlparse(url).hostname or "").lower(),
        "model": clean_model,
        "provider": proto,
        "api_key_enc": encrypted,
        "api_key_masked": _mask_key(raw_key),
        "status": "pending",
        "model_config_id": None,
        "created_at": _now_iso(),
        "confirmed_at": None,
    }
    state = load_state()
    state.setdefault("custom_endpoints", []).append(record)
    save_state(state)
    return {
        **_public_endpoint(record),
        "message": (
            "已记下这个自定义地址，但还没生效：请你再点一次“确认”它才生效。"
            "确认之前它不会被用来聊天。"
        ),
    }


def list_custom_endpoints(owner: str) -> List[Dict[str, Any]]:
    """只列本人的自定义地址（待确认 + 已确认），一律掩码。"""
    records = [
        rec for rec in load_state().get("custom_endpoints", [])
        if str(rec.get("owner") or "") == str(owner)
    ]
    return [_public_endpoint(rec) for rec in records]


def confirm_custom_endpoint(owner: str, endpoint_id: str) -> Dict[str, Any]:
    """Joseph 亲手确认一次：才把地址 + Key 写进模型配置库，正式生效。

    确认成功后，待确认记录里的 Key 密文立即删掉（Key 只留在模型配置库
    的加密字段里，不在两处各存一份）；确认失败（例如 Key 形态不对）
    时记录保持待确认，并返回人话原因。
    """
    from ..ai.model_config import ModelConfigError

    state = load_state()
    record = next(
        (rec for rec in state.get("custom_endpoints", [])
         if str(rec.get("id")) == str(endpoint_id)
         and str(rec.get("owner") or "") == str(owner)),
        None,
    )
    if record is None:
        raise LookupError("找不到这个自定义地址（可能已被删除，或不是你的）")
    if str(record.get("status")) == "confirmed":
        return {**_public_endpoint(record), "message": "这个地址已经确认生效了，不用再点一次。"}
    store = _model_store()
    try:
        plain = store._decrypt_secret(str(record["id"]), str(record.get("api_key_enc") or ""))
    except ModelConfigError as exc:
        return {"ok": False, "id": str(endpoint_id), "status": "pending",
                "message": f"确认没成：存着的 Key 解不开了（{exc}）。请删掉这条，重新添加一次。"}
    if not plain:
        return {"ok": False, "id": str(endpoint_id), "status": "pending",
                "message": "确认没成：这条记录里没有存下 Key，请删掉重加。"}
    try:
        created = store.create(
            {
                "name": str(record["name"]),
                "provider": str(record.get("provider") or "openai"),
                "model": str(record["model"]),
                "base_url": str(record["base_url"]),
                "api_key": plain,
            },
            actor=str(owner),
            owner_principal=str(owner),
        )
    except ModelConfigError as exc:
        return {"ok": False, "id": str(endpoint_id), "status": "pending",
                "message": f"确认没成：{exc}。记录还留在待确认里，改好再点确认。"}
    finally:
        plain = ""
    record["status"] = "confirmed"
    record["model_config_id"] = str(created.get("id") or "")
    record["confirmed_at"] = _now_iso()
    record.pop("api_key_enc", None)
    save_state(state)
    return {
        **_public_endpoint(record),
        "ok": True,
        "message": (
            "已确认生效：这个自定义地址现在可以用来聊天了。"
            "注意：它只活在你自己的模型配置里，没有写进任何厂商白名单代码；"
            "换机后要重新添加、重新确认一次。"
        ),
    }


def delete_custom_endpoint(owner: str, endpoint_id: str) -> Dict[str, Any]:
    """删一条自定义地址：待确认的只删记录；已确认的连模型配置库里的行一起删。"""
    from ..ai.model_config import ModelConfigError

    state = load_state()
    records = state.get("custom_endpoints", [])
    record = next(
        (rec for rec in records
         if str(rec.get("id")) == str(endpoint_id)
         and str(rec.get("owner") or "") == str(owner)),
        None,
    )
    if record is None:
        raise LookupError("找不到这个自定义地址（可能已被删除，或不是你的）")
    model_config_id = str(record.get("model_config_id") or "")
    if model_config_id:
        try:
            _model_store().delete(model_config_id, actor=str(owner))
        except (ModelConfigError, LookupError, KeyError) as exc:
            logger.warning("custom endpoint model row delete failed: %s", exc)
    state["custom_endpoints"] = [
        rec for rec in records if str(rec.get("id")) != str(endpoint_id)
    ]
    save_state(state)
    return {"ok": True, "id": str(endpoint_id), "deleted": True,
            "message": "已删除：这个自定义地址不再生效，模型配置库里的对应行也一起删了。"}


def custom_endpoints_backup_view(owner: str) -> Dict[str, Any]:
    """备份/迁移导出用的视图：自定义端点整条剔除，只留条数与说明。

    迁移包是明文随身带的，Key 与自定义地址（主机 + 模型名 + 掩码）都不进包；
    加密整机备份里模型库密文本就随口令保护的归档走，那是加密备份本身的
    设计（见模块文档），这里不宣称“备份里绝对没有密文”。
    """
    records = [
        rec for rec in load_state().get("custom_endpoints", [])
        if str(rec.get("owner") or "") == str(owner)
    ]
    return {
        "included": [],
        "excluded_count": len(records),
        "note": CUSTOM_ENDPOINT_EXPORT_NOTE,
        "pending_count": sum(1 for rec in records if rec.get("status") == "pending"),
        "confirmed_count": sum(1 for rec in records if rec.get("status") == "confirmed"),
    }


# --------------------------------------------------------------------------- #
# 第 2 件：群聊不漏秘密
# --------------------------------------------------------------------------- #

#: 群聊里一律不许出现的消息类型（写死在这里，前端/调用方不许绕过）。
GROUP_BLOCKED_KINDS = {
    "approval": "审批卡：审批只能在 1:1 里点，群里只告诉大家结果。",
    "secret": "密钥/口令：Key、Token、密码、恢复码绝不能发进群。",
    "draft": "草稿：没定稿的东西只在 1:1 里改，群里只发定稿的结果。",
    "sensitive": "敏感内容：含个人隐私、账单、内部地址的信息不进群。",
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key|apikey)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(password|passwd|密码)\s*[:：=]\s*\S+"),
    re.compile(r"恢复码\s*[:：]?\s*\S+"),
    re.compile(r"(token|令牌)\s*[:：=]\s*\S+", re.IGNORECASE),
)


def classify_group_message(kind: str, text: str) -> Tuple[bool, str]:
    """判一条消息能不能进群。返回 (allowed, reason)。

    ``kind`` 是调用方声明的类型；即使声明成普通文本，正文里扫到
    Key/口令/恢复码形态也一样拦住（不信任调用方的分类）。
    """
    clean_kind = str(kind or "text").strip().lower()
    if clean_kind in GROUP_BLOCKED_KINDS:
        return False, GROUP_BLOCKED_KINDS[clean_kind]
    body = str(text or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.search(body):
            return False, "正文里扫到了密钥/口令形态的东西（Key、Token、密码或恢复码），不能发进群。"
    return True, "可以发进群。"


def check_group_message(channel: str, kind: str, text: str, *,
                        sender: str = "") -> Dict[str, Any]:
    """群聊发送闸：所有要发进群聊通道的消息都先过这里。

    ``channel`` 为 ``"group"`` 时强制检查；``"dm"`` / ``"1:1"`` / ``""``
    一律放行（1:1 不受影响）。被拦时返回人话说明，不静默丢——调用方
    必须把这条说明告诉发消息的人（或记进日志），不能假装发出去了。
    """
    chan = str(channel or "").strip().lower()
    if chan in ("dm", "1:1", "direct", "private", ""):
        return {"allowed": True, "channel": chan or "dm",
                "message": "1:1 消息不受群聊检查影响，直接发。"}
    if chan != "group":
        return {"allowed": False, "channel": chan,
                "message": f"不认识的通道类型“{chan}”：只认 group（群聊）与 dm（1:1），先别发。"}
    allowed, reason = classify_group_message(kind, text)
    return {
        "allowed": allowed,
        "channel": "group",
        "kind": str(kind or "text"),
        "sender": str(sender or ""),
        "message": reason if allowed else f"这条消息没发进群：{reason}",
        "reason": reason,
        "blocked_kind": None if allowed else str(kind or "text"),
    }


def group_guard_policy() -> Dict[str, Any]:
    """把“哪些类型被拦、拦了之后调用方收到什么”写成机器可读的策略。"""
    return {
        "applies_to": "channel=group（群聊通道）；1:1（dm）不受影响",
        "blocked": [
            {"kind": kind, "reason": reason}
            for kind, reason in GROUP_BLOCKED_KINDS.items()
        ],
        "also_blocked": "正文里扫到 Key/Token/密码/恢复码形态的普通文本也拦",
        "when_blocked": (
            "返回 allowed=false + 人话 message，调用方必须把这条说明告诉发消息的人，"
            "不能静默丢掉，也不能假装发出去了；被拦的消息不会出现在群里。"
        ),
        "when_allowed": "返回 allowed=true，调用方按原流程发进群。",
    }


# --------------------------------------------------------------------------- #
# 第 3 件：说错了能纠正
# --------------------------------------------------------------------------- #

CORRECTION_SOURCE = "correction"
_CORRECTION_TAG = "correction"


def _correction_tag_for(old_text: str) -> str:
    return f"corrects:{str(old_text or '')[:500]}"


def _is_superseded(entry: Dict[str, Any]) -> bool:
    return any(
        str(tag).startswith("superseded-by:")
        for tag in (entry.get("tags") or [])
    )


def _is_correction(entry: Dict[str, Any]) -> bool:
    return (
        str(entry.get("source") or "") == CORRECTION_SOURCE
        or _CORRECTION_TAG in (entry.get("tags") or [])
    )


def record_correction(owner: str, old_text: str, corrected_text: str) -> Dict[str, Any]:
    """Joseph 说“你刚才说错了，应该是 X”：认错、记进记忆主线。

    纠正本身是一条记忆（source=correction，tags 带 correction 与
    ``corrects:<原说法>``），正文就是纠正后的说法；原来那条旧说法
    （正文逐字相同的本人记忆）打上 ``superseded-by:<纠正 id>``，召回时
    不再出现。认错的回复文案由调用方（聊天）按这条记录组织，本函数只
    负责记下来、记得能查到。
    """
    from .account_memory import _connect, record_entry_for_owner

    old = normalize_required_text(old_text, "原说法", max_chars=8000)
    new = normalize_required_text(corrected_text, "纠正后的说法", max_chars=8000)
    if old == new:
        raise ValueError("原说法和纠正后的说法是一样的，不用记这次纠正")
    entry = record_entry_for_owner(
        str(owner), new, source=CORRECTION_SOURCE,
        tags=[_CORRECTION_TAG, _correction_tag_for(old)],
    )
    superseded = 0
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, tags FROM account_memory_entries WHERE owner = ? AND text = ?",
            (str(owner), old),
        ).fetchall()
        for row in rows:
            if str(row["id"]) == str(entry["id"]):
                continue
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:  # noqa: BLE001
                tags = []
            if not isinstance(tags, list):
                tags = []
            marker = f"superseded-by:{entry['id']}"
            if marker in [str(t) for t in tags]:
                continue
            tags.append(marker)
            connection.execute(
                "UPDATE account_memory_entries SET tags = ? WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), str(row["id"])),
            )
            superseded += 1
        connection.commit()
    return {
        "correction": entry,
        "old_text": old,
        "corrected_text": new,
        "superseded_count": superseded,
        "message": (
            "记下了：你说得对，刚才那个说法是错的，我记住了正确的说法，"
            "下次再问同样的问题就按正确的说。"
            if superseded else
            "记下了：你说得对，刚才那个说法是错的，我记住了正确的说法。"
            "（原来那条旧说法没在记忆里找到，没什么可划掉的。）"
        ),
    }


def list_corrections(owner: str) -> List[Dict[str, Any]]:
    """查本人的纠正记录（只列 correction 条目，不含普通记忆）。"""
    from .account_memory import _query_entries

    entries = _query_entries((str(owner),), "")
    return [e for e in entries if _is_correction(e)]


def recall_with_corrections(owner: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """召回：纠正过的条目优先于被纠正的旧说法；旧说法直接不出现。

    在 A2a ``recall_memories`` 的排序结果上再排一次：correction 条目置顶
    （按时间倒序），被 superseded 的条目剔除，其余保持原顺序。

    另外补一层字符重叠召回：``recall_memories`` 按 ``\\W+`` 切词，中文整句
    （如“公司年会定在什么时候”）切不出词、会漏掉明明相关的条目；这里按
    查询字符与正文/标签的重叠再捞一遍，纠正条目 2 字重叠就捞，普通条目
    3 字重叠或整句命中才捞。
    """
    from .a2a import recall_memories
    from .account_memory import _query_entries

    merged: Dict[str, Dict[str, Any]] = {}
    for entry in recall_memories(str(owner), query, max(10, int(limit) * 2)):
        merged[str(entry["id"])] = entry
    q = str(query or "").strip()
    if q:
        try:
            all_entries = _query_entries((str(owner),), "")
        except Exception:  # noqa: BLE001 - 兜底召回失败时只用第一层结果
            all_entries = []
        chars = {c for c in q if not c.isspace()}
        for entry in all_entries:
            if str(entry["id"]) in merged:
                continue
            hay = str(entry.get("text") or "") + " " + " ".join(entry.get("tags") or [])
            overlap = sum(1 for c in chars if c in hay)
            if (
                q.lower() in hay.lower()
                or overlap >= 3
                or (_is_correction(entry) and overlap >= 2)
            ):
                merged[str(entry["id"])] = entry
    corrections = [e for e in merged.values() if _is_correction(e)]
    corrections.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)
    others = [e for e in merged.values()
              if not _is_correction(e) and not _is_superseded(e)]
    ordered = corrections + others
    out: List[Dict[str, Any]] = []
    for entry in ordered[: max(1, int(limit))]:
        item = dict(entry)
        item["is_correction"] = _is_correction(entry)
        out.append(item)
    return out


def update_correction(owner: str, entry_id: str, corrected_text: str) -> Dict[str, Any]:
    """改一条纠正记录（只改纠正后的说法；归属与纠正标记不可改）。"""
    from .account_memory import _connect

    new = normalize_required_text(corrected_text, "纠正后的说法", max_chars=8000)
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
        if row is None or str(row["owner"]) != str(owner):
            raise LookupError("找不到这条纠正记录（可能已被删除，或不是你的）")
        if str(row["source"]) != CORRECTION_SOURCE:
            raise ValueError("这条不是纠正记录，不能按纠正来改（请用记忆的改入口）")
        connection.execute(
            "UPDATE account_memory_entries SET text = ? WHERE id = ? AND owner = ?",
            (new, str(entry_id), str(owner)),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
    from .account_memory import _row_to_entry

    return {"entry": _row_to_entry(updated), "message": "纠正记录已改，下次召回按改后的说法来。"}


def delete_correction(owner: str, entry_id: str) -> Dict[str, Any]:
    """删一条纠正记录：同时把被它划掉的旧说法恢复（去掉 superseded 标记）。

    删纠正不等于删记忆本身：旧说法只是恢复成“没被纠正过”的状态，
    不会因为删了纠正就跟着消失。
    """
    from .account_memory import _connect

    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner, source FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
        if row is None or str(row["owner"]) != str(owner):
            raise LookupError("找不到这条纠正记录（可能已被删除，或不是你的）")
        if str(row["source"]) != CORRECTION_SOURCE:
            raise ValueError("这条不是纠正记录，不能按纠正来删")
        marker = f"superseded-by:{entry_id}"
        restored = 0
        rows = connection.execute(
            "SELECT id, tags FROM account_memory_entries WHERE owner = ? AND tags LIKE ?",
            (str(owner), f"%{marker}%"),
        ).fetchall()
        for other in rows:
            try:
                tags = json.loads(other["tags"] or "[]")
            except Exception:  # noqa: BLE001
                tags = []
            if not isinstance(tags, list):
                tags = []
            kept = [t for t in tags if str(t) != marker]
            if len(kept) != len(tags):
                connection.execute(
                    "UPDATE account_memory_entries SET tags = ? WHERE id = ?",
                    (json.dumps(kept, ensure_ascii=False), str(other["id"])),
                )
                restored += 1
        connection.execute(
            "DELETE FROM account_memory_entries WHERE id = ? AND owner = ?",
            (str(entry_id), str(owner)),
        )
        connection.commit()
    return {"id": str(entry_id), "deleted": True, "restored_old_statements": restored,
            "message": "纠正记录已删；原来被它划掉的旧说法已恢复（不是跟着删掉）。"}


#: “记一句”的来源：就是普通记忆的 `conversation`（对话），不另起一套
#: 来源值；记忆页（知识中心 → 记忆）按 `对话` 显示，改/删走
#: `PATCH|DELETE /v1/memory/entries/{id}` 同一条路，不跟纠正记录混。
CHAT_NOTE_SOURCE = "conversation"


def remember_chat_note(
    owner: str,
    text: str,
    *,
    chat_name: str = "",
    speaker: str = "",
    is_group: bool = False,
) -> Dict[str, Any]:
    """聊天里一句话值得记住：点“记一句”就写进记忆主线。

    只写**自己**的记忆。来源固定是 `conversation`，标签写清出处——
    哪次聊天（对话名/群名）+ 谁说的（Joseph / Bot 名），不记没出处的
    假记忆；同一句（同账号、同正文、同来源）已经记过就不再造一条，
    老实返回已有的那条并说“这句已经记过了”。

    去重是永久的（不是 `POST /v1/memory/entries` 那个 60 秒幂等窗口）：
    隔多久再记同一句都只会命中已有的那条。删掉之后再记同一句可以
    重新记（删了就是没了，不留影子）。
    """
    from .account_memory import _connect, record_entry_for_owner

    clean = normalize_required_text(text, "要记住的话", max_chars=8000).strip()
    chat = normalize_required_text(chat_name, "对话名", max_chars=128).strip() if str(chat_name or "").strip() else "贾维斯"
    who = normalize_required_text(speaker, "说话人", max_chars=64).strip() if str(speaker or "").strip() else "Joseph"
    kind = "群聊" if is_group else "对话"
    tags = [f"{kind}：{chat}", f"{who}说的"]
    with _connect() as connection:
        existing = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries "
            "WHERE owner = ? AND text = ? AND source = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (str(owner), clean, CHAT_NOTE_SOURCE),
        ).fetchone()
    if existing is not None:
        from .account_memory import _row_to_entry

        return {
            "entry": _row_to_entry(existing),
            "deduplicated": True,
            "message": "这句已经记过了，记忆里已经有同一句了。",
        }
    entry = record_entry_for_owner(
        str(owner), clean, source=CHAT_NOTE_SOURCE, tags=tags,
    )
    return {
        "entry": entry,
        "deduplicated": False,
        "message": "记下了，在知识中心 → 记忆里能看到。",
    }


# --------------------------------------------------------------------------- #
# 第 4 件：升级坏了自动回滚
# --------------------------------------------------------------------------- #

def evaluate_startup_and_rollback(*, checks: Optional[List[Dict[str, Any]]] = None,
                                  dry_run: bool = False) -> Dict[str, Any]:
    """自检不过就回滚到上一个好的版本，并留记录、给人话说明。

    只接线已有机制：A2a ``run_startup_checks``（端口/模型/磁盘/网络，
    每项 pass / fail / not_configured）+ ``a2a.restore_latest_backup``
    （``BackupManager.restore_backup``，不重写恢复逻辑）。
    只有 ``fail`` 才触发回滚；``not_configured``（例如还没配模型）不触发——
    没配好不是“新版本起不来”，回滚也解决不了。
    """
    from .a2a import restore_latest_backup, run_startup_checks

    items = list(checks) if checks is not None else run_startup_checks()
    failed = [c for c in items if str(c.get("status")) == "fail"]
    if not failed:
        return {
            "ok": True, "rolled_back": False, "failed_checks": [],
            "message": "启动自检都过了（没配好的项只是没配好，不算失败），不用回滚。",
        }
    failed_names = [str(c.get("name") or c.get("label") or "?") for c in failed]
    if dry_run:
        return {
            "ok": True, "rolled_back": False, "dry_run": True,
            "failed_checks": failed_names,
            "message": (
                "演练：如果真跑，会因为自检没过而回滚到上一个好的版本。"
                f"没过的项：{', '.join(failed_names)}。本次演练没有动任何数据。"
            ),
        }
    outcome = restore_latest_backup()
    record = {
        "at": _now_iso(),
        "reason": "启动自检没过，自动回滚",
        "failed_checks": failed_names,
        "failed_details": [
            {"name": str(c.get("name") or ""), "label": str(c.get("label") or ""),
             "detail": str(c.get("detail") or "")} for c in failed
        ],
        "backup_id": outcome.get("backup_id"),
        "restore_ok": bool(outcome.get("ok")),
        "restore_message": str(outcome.get("message") or ""),
    }
    if outcome.get("ok"):
        record["message"] = (
            "新版本起不来，已经自动回到上一个好的版本，不用你动手。"
            f"没过的自检：{', '.join(failed_names)}；回到的备份：{outcome.get('backup_id')}。"
        )
    else:
        record["message"] = (
            "新版本起不来，想回滚但没回成："
            f"{outcome.get('message') or '没有可恢复的备份'}。"
            f"没过的自检：{', '.join(failed_names)}。请手动处理，现有数据没动。"
        )
    state = load_state()
    state.setdefault("rollback_records", []).append(record)
    save_state(state)
    return {"ok": bool(outcome.get("ok")), "rolled_back": bool(outcome.get("ok")),
            "record": record, "message": record["message"]}


def list_rollback_records() -> List[Dict[str, Any]]:
    return list(load_state().get("rollback_records", []))


#: 回滚循环保护：10 分钟内已有回滚记录就不再自动回滚一次。
_ROLLBACK_LOOP_GUARD_MINUTES = 10


def _with_real_port_check(checks: List[Dict[str, Any]], listen_port: int) -> List[Dict[str, Any]]:
    """把自检里的端口项换成对真实监听端口的探测。

    ``run_startup_checks`` 只认 ``LIUHAO_PORT`` / ``PORT`` / 8765，不知道
    桌面壳实际监听的 ``LIUHAO_LISTEN_PORT``（如 8137）；开机自检跑在网关
    已经监听之后，用真实端口重算才不会误判“网关没起来”。
    """
    import socket

    try:
        with socket.create_connection(("127.0.0.1", listen_port), timeout=2):
            item = {"name": "port", "label": "本机网关端口", "status": "pass",
                    "detail": f"127.0.0.1:{listen_port} 能连上。"}
    except Exception as exc:  # noqa: BLE001
        item = {"name": "port", "label": "本机网关端口", "status": "fail",
                "detail": f"127.0.0.1:{listen_port} 连不上（{exc}）：网关可能还没启动。"}
    return [c for c in checks if str(c.get("name")) != "port"] + [item]


def _recent_rollback_record(*, minutes: int) -> Optional[str]:
    """最近一次回滚记录的时间（ISO）；没有或超过 ``minutes`` 分钟就返回 None。"""
    records = list_rollback_records()
    if not records:
        return None
    raw = str(records[-1].get("at") or "")
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - at).total_seconds()
    if 0 <= age < minutes * 60:
        return raw
    return None


def startup_self_check_and_rollback(*, listen_port: Optional[int] = None) -> Dict[str, Any]:
    """开机自检 + 坏了自动回滚（接进 ``main._warm_chat_stack`` 的后台步）。

    调用点在网关已经开始监听、首屏发出去之后（后台预热线程），端口自检
    用真实监听端口（main 传进来的 ``LIUHAO_LISTEN_PORT`` 优先）重算，不会
    在“还没开始监听”时误判成网关没起来。

    只有自检 ``fail`` 才回滚；``not_configured``（没配模型）不回滚。
    10 分钟内已有回滚记录就跳过（防回滚循环：回滚完的版本自检还是 fail，
    下一次启动不该立刻再回滚一次）。本函数不抛异常：自检本身跑不起来也
    只记一条原因、返回 ``ok=False``，绝不拖垮启动。
    """
    from .a2a import run_startup_checks

    try:
        checks = run_startup_checks()
    except Exception as exc:  # noqa: BLE001
        logger.warning("开机自检没跑成（不回滚，服务继续）：%s", exc, exc_info=True)
        return {"ok": False, "rolled_back": False, "failed_checks": [],
                "message": f"开机自检没跑成：{exc}。没回滚，服务继续跑。"}
    if listen_port is not None:
        checks = _with_real_port_check(checks, int(listen_port))
    failed = [c for c in checks if str(c.get("status")) == "fail"]
    if not failed:
        return {"ok": True, "rolled_back": False, "failed_checks": [],
                "message": "开机自检都过了，不用回滚。"}
    failed_names = [str(c.get("name") or c.get("label") or "?") for c in failed]
    recent = _recent_rollback_record(minutes=_ROLLBACK_LOOP_GUARD_MINUTES)
    if recent is not None:
        return {"ok": True, "rolled_back": False, "skipped": True,
                "failed_checks": failed_names,
                "message": (
                    f"开机自检没过（{', '.join(failed_names)}），但 {recent} 已经回滚过一次，"
                    "10 分钟内不再重复回滚：请手动看一眼是什么问题。"
                )}
    outcome = evaluate_startup_and_rollback(checks=checks)
    return {"ok": bool(outcome.get("ok")), "rolled_back": bool(outcome.get("rolled_back")),
            "failed_checks": failed_names, "record": outcome.get("record"),
            "message": str(outcome.get("message") or "")}


def auto_rollback_status() -> Dict[str, Any]:
    """自动回滚的现状：能做什么、缺口在哪（见 AUTO_ROLLBACK_GAP）。"""
    return {
        "gap": AUTO_ROLLBACK_GAP,
        "records": list_rollback_records(),
        "trigger": "POST /v1/a3s/upgrade/auto-rollback",
    }


# --------------------------------------------------------------------------- #
# 第 5 件：主账号自己的总上限（子账号不设任何上限）
# --------------------------------------------------------------------------- #

def set_owner_monthly_cap(cap_usd: Optional[float], *, owner: str,
                          is_root: bool) -> Dict[str, Any]:
    """设主账号自己每月的总花费上限（美元）；None 表示取消上限。

    只接受主账号本人：子账号没有“总上限”这个概念（规矩见 CAP_POLICY），
    非主账号调用直接拒绝，不静默收下。
    """
    if not is_root:
        raise PermissionError("每月总上限只能由主账号自己设：子账号没有上限可设，也不用设")
    state = load_state()
    if cap_usd is None:
        state["owner_cap"] = {}
    else:
        value = float(cap_usd)
        if value < 0:
            raise ValueError("每月上限不能是负数")
        state["owner_cap"] = {
            "cap_usd": value, "owner": str(owner), "updated_at": _now_iso(),
        }
    save_state(state)
    return {"owner": str(owner), "cap_usd": cap_usd,
            "message": (
                "主账号每月总上限已取消。"
                if cap_usd is None else
                f"主账号每月总花费上限已设为 ${cap_usd}：到了就停，并告诉你。"
            )}


def owner_monthly_cost(owner: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """主账号自己本月的真实花费：只算他自己的调用、他自己的 Key。

    口径与用量页同一套（``account_usage.usage_summary`` + ``BillingEngine``
    定价）；没定价的模型费用不进合计（``cost_complete=false``），绝不按
    0 元充数——``cost`` 数字本身是“已定价部分的合计”，不是全部花费。
    """
    from .account_usage import usage_summary

    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    summary = usage_summary(str(owner), include_callers=False, owners=(str(owner),))
    # usage_summary 是累计口径（账本没有按月切分）；本月口径用 events 的 since/until 再算一次。
    import calendar as _calendar

    year, mon = int(target_month[:4]), int(target_month[5:7])
    last_day = _calendar.monthrange(year, mon)[1]
    from .account_usage import usage_events

    events = usage_events(
        str(owner), include_callers=False, owners=(str(owner),),
        since=f"{target_month}-01T00:00:00+00:00",
        until=f"{target_month}-{last_day:02d}T23:59:59+00:00",
        limit=200, offset=0,
    )
    return {
        "owner": str(owner),
        "month": target_month,
        "cost": events.get("cost"),
        "cost_complete": events.get("cost_complete"),
        "unknown_models": events.get("unknown_models", []),
        "calls": events.get("total", 0),
        "tokens_in": summary.get("tokens_in", 0),
        "tokens_out": summary.get("tokens_out", 0),
        "note": (
            "cost 是“已定价部分的合计”；cost_complete=false 表示本月有用过"
            "没定价的模型，那部分花费没算进去，上限拦不住（enforceable=false）。"
        ),
    }


def _owner_cap_evaluation(owner: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """主账号本月是否到总上限：纯计算，**不发任何通知**。

    与 ``check_owner_monthly_cap`` 的判断完全一致，只是不调 ``report_stuck``：
    聊天热路径的门禁（``chat.py`` 经 ``owner_cap_allows_call``）每通聊天都调
    一次，调会发通知的那版，被拦住的每一通都会往收件箱再塞一条，刷爆。
    通知只由设置 / 用量页的显式检查（``check_owner_monthly_cap``）发一次。
    """
    cap = load_state().get("owner_cap") or {}
    cost_info = owner_monthly_cost(owner, month=month)
    cap_usd = cap.get("cap_usd")
    if cap_usd is None:
        return {"owner": str(owner), "capped": False, "cap_usd": None,
                "enforceable": bool(cost_info.get("cost_complete")), **cost_info}
    cost = cost_info.get("cost")
    if not cost_info.get("cost_complete"):
        return {"owner": str(owner), "capped": False, "cap_usd": cap_usd,
                "enforceable": False,
                "message": (
                    "主账号本月上限设了，但花费算不全（本月有用过没定价的模型："
                    f"{', '.join(cost_info.get('unknown_models') or []) or '未知模型'}），"
                    "上限这次拦不住：不是没超，是不知道超没超。"
                ), **cost_info}
    reached = float(cost) >= float(cap_usd)
    ratio = float(cost) / float(cap_usd) if float(cap_usd) > 0 else None
    warning = bool(ratio is not None and ratio >= 0.8 and not reached)
    return {"owner": str(owner), "capped": bool(reached), "cap_usd": cap_usd,
            "enforceable": True,
            "warning": warning, "usage_ratio": ratio, "warning_ratio": 0.8,
            "message": (
                f"到上限了，本月已花 ${cost}，上限 ${cap_usd}，已停并通知你。"
                if reached else
                f"快到上限了：本月已花 ${cost}，上限 ${cap_usd}（已用 {round(ratio * 100)}%），提前跟你说一声。"
                if warning else
                f"还没到上限：本月已花 ${cost}，上限 ${cap_usd}。"
            ), **cost_info}


def check_owner_monthly_cap(owner: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """查主账号本月是否到了总上限：到了就停，并给 Joseph 一条“到上限停了”的通知。

    判断复用无副作用的 ``_owner_cap_evaluation``；本函数只在“到了”时多发
    一次通知。显式检查（设置 / 用量页）调本函数；聊天热路径调
    ``owner_cap_allows_call``（它用纯计算那版，不发通知）。
    """
    from .a2a import report_stuck

    result = _owner_cap_evaluation(owner, month=month)
    if result.get("capped"):
        report_stuck(
            "主账号本月总花费",
            f"已经到了你设的每月总上限 ${result.get('cap_usd')}（本月已花 ${result.get('cost')}），我先帮你停了，"
            "要继续用就去设置里调高上限。",
            owner=str(owner),
        )
    return result


def owner_cap_allows_call(owner: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """聊天调用前的门禁 helper：主账号到上限就返回不许调用 + 人话原因。

    本轮 item 6 已接进聊天热路径（``chat.py._enforce_usage_caps`` 在生成前
    调本函数）。判断用无副作用的纯计算：到了上限不发通知，429 本身就是
    告诉用户的那句话；80% 预警只以 ``warning`` 字段带回，由调用方记日志。
    """
    result = _owner_cap_evaluation(owner, month=month)
    if result.get("capped"):
        return {"allowed": False,
                "reason": result.get("message") or "主账号本月总花费已到上限，先停了。",
                "check": result}
    if not result.get("enforceable") and result.get("cap_usd") is not None:
        return {"allowed": True, "warning": result.get("message"), "check": result}
    return {"allowed": True, "check": result}


# --------------------------------------------------------------------------- #
# 第 6 件：语音危险命令须屏幕点确认（声纹已落地，见 VOICEPRINT_STATUS）
# --------------------------------------------------------------------------- #

#: 危险语音命令分级：命中任一类就必须进“等你确认”收件箱。
DANGEROUS_VOICE_RULES = (
    ("delete_file", "删文件", ("删文件", "删除文件", "删掉文件", "清空文件", "删掉这个文件",
                          # D 组走查（2026-11-14）发现：只列“删掉文件”会漏掉日常说法
                          # “把旧报告删掉”“把这份文件删除”——删的宾语不一定是“文件”二字。
                          # 删的动作本身（删掉/删除/删了/清空）就够危险，进待确认；安全
                          # 侧只认“查/看/找”类动作，不靠宾语兜底。
                          "删掉", "删除", "删了", "清空")),
    ("modify_code", "改代码", ("改代码", "修改代码", "改一下代码", "重构代码")),
    ("spend", "花钱", ("花钱", "支付", "付款", "买", "下单", "转账", "扣费")),
    ("account", "动账号", ("改密码", "删账号", "注销账号", "改账号", "换绑")),
    ("system", "动系统", ("关机", "重启电脑", "重启系统", "装软件", "安装软件", "格式化", "改系统设置")),
)


def classify_voice_command(text: str) -> Dict[str, Any]:
    """给一句语音转文字的命令分危险级：命中危险词就必须屏幕点确认。"""
    body = str(text or "").strip()
    if not body:
        return {"danger": False, "needs_confirmation": False,
                "categories": [], "message": "没听到说什么，先不执行。"}
    hit: List[Dict[str, str]] = []
    for code, label, keywords in DANGEROUS_VOICE_RULES:
        for keyword in keywords:
            if keyword in body:
                hit.append({"code": code, "label": label, "keyword": keyword})
                break
    if not hit:
        return {"danger": False, "needs_confirmation": False,
                "categories": [], "message": "这句不危险，可以直接执行。"}
    labels = "、".join(sorted({h["label"] for h in hit}))
    return {
        "danger": True, "needs_confirmation": True,
        "categories": hit,
        "message": (
            f"这句要动{labels}，语音里不直接执行：已经记进“等你确认”，"
            "你在屏幕上点一下确认我再执行。"
        ),
    }


def submit_voice_command(text: str, *, owner: str) -> Dict[str, Any]:
    """语音入口：先分级；危险的进待确认收件箱，安全的放行（执行由调用方完成）。"""
    result = classify_voice_command(text)
    if not result["needs_confirmation"]:
        return {"action": "proceed", "text": str(text or ""),
                "message": result["message"]}
    item = {
        "id": str(uuid.uuid4()),
        "owner": str(owner),
        "text": str(text or ""),
        "categories": result["categories"],
        "status": "pending",
        "created_at": _now_iso(),
        "decided_at": None,
    }
    state = load_state()
    state.setdefault("voice_pending", []).append(item)
    save_state(state)
    return {"action": "needs_confirmation", "confirmation_id": item["id"],
            "item": item, "message": result["message"]}


def list_voice_pending(owner: str) -> List[Dict[str, Any]]:
    return [item for item in load_state().get("voice_pending", [])
            if str(item.get("owner") or "") == str(owner)]


def _decide_voice_command(owner: str, confirmation_id: str, decision: str) -> Dict[str, Any]:
    state = load_state()
    item = next(
        (it for it in state.get("voice_pending", [])
         if str(it.get("id")) == str(confirmation_id)
         and str(it.get("owner") or "") == str(owner)),
        None,
    )
    if item is None:
        raise LookupError("找不到这条待确认的语音命令（可能已处理，或不是你的）")
    if str(item.get("status")) != "pending":
        return {"id": str(confirmation_id), "status": str(item.get("status")),
                "message": f"这条已经处理过了（{item.get('status')}），不用再点。"}
    item["status"] = decision
    item["decided_at"] = _now_iso()
    save_state(state)
    if decision == "confirmed":
        return {"id": str(confirmation_id), "status": "confirmed",
                "text": str(item.get("text") or ""),
                "message": "已确认：调用方现在可以执行这句命令了（执行结果由调用方回填，这里只记确认）。"}
    return {"id": str(confirmation_id), "status": "cancelled",
            "message": "已取消：这句语音命令不会执行。"}


def confirm_voice_command(owner: str, confirmation_id: str) -> Dict[str, Any]:
    """屏幕上点确认：这句危险语音命令才可以执行（执行本身由调用方完成）。"""
    return _decide_voice_command(owner, confirmation_id, "confirmed")


def cancel_voice_command(owner: str, confirmation_id: str) -> Dict[str, Any]:
    return _decide_voice_command(owner, confirmation_id, "cancelled")


# --------------------------------------------------------------------------- #
# 第 7 件：数据通用格式导出
# --------------------------------------------------------------------------- #

def collect_tasks_export(owner: str) -> List[Dict[str, Any]]:
    """本人的任务（台账真实数据），只要 JSON 结构，不含 Key。"""
    from .task_ledger import TaskLedger

    page = TaskLedger().list_tasks(owners=[str(owner)], limit=500)
    items = page.get("items", []) if isinstance(page, dict) else []
    return _scrub_for_export(items)


def collect_file_list(owner: str) -> List[Dict[str, Any]]:
    """本人的文件清单：只要路径与大小（不要内容），疑似凭据的文件不列入。"""
    from ..ai.workspace import workspace_root
    from .ai_management import AIStateManager
    from .data_portability import _looks_like_credential

    root = workspace_root()
    index = AIStateManager().artifact_owner_index()
    out: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [n for n in dirnames if n not in (".git", "__pycache__", "node_modules")]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            rel = str(os.path.relpath(absolute, root)).replace("\\", "/").strip("/")
            if _looks_like_credential(name):
                continue
            if str(owner) not in set(index.get(rel, set())):
                continue
            try:
                size = os.stat(absolute).st_size
            except OSError:
                continue
            out.append({"path": rel, "size_bytes": size})
    out.sort(key=lambda item: item["path"])
    return out


def _tasks_text(tasks: List[Dict[str, Any]]) -> str:
    lines = ["# 我的任务", "", f"- 导出时间（UTC）：{_now_iso()}",
             f"- 任务数：{len(tasks)}", ""]
    for task in tasks:
        lines.append(
            f"- [{task.get('status') or '未知状态'}] {task.get('title') or task.get('task_id') or '未命名任务'}"
            + (f"（{task.get('task_id') or ''}）" if task.get("task_id") else "")
        )
    return "\n".join(lines).rstrip() + "\n"


def _file_list_text(files: List[Dict[str, Any]]) -> str:
    lines = ["# 我的文件清单", "", f"- 导出时间（UTC）：{_now_iso()}",
             f"- 文件数：{len(files)}", "",
             "只含路径与大小，不含文件内容；疑似凭据的文件不在清单里。", ""]
    for item in files:
        lines.append(f"- {item.get('path')}（{item.get('size_bytes')} 字节）")
    return "\n".join(lines).rstrip() + "\n"


def export_by_type(owner: str, types: Sequence[str]) -> Dict[str, Any]:
    """按类型导出成一份清单：聊天（文本＋JSON）、任务（JSON＋文本）、
    记忆（JSON＋文本）、文件清单（JSON＋文本）。Key 一律剔除。

    ``types`` 为空表示四种都要；不认识的类型记进 notes，不让整包失败。
    """
    from .data_portability import (
        _memory_markdown,
        collect_conversations,
        collect_memories,
    )

    wanted = [str(t).strip().lower() for t in (types or [])]
    if not wanted:
        wanted = ["chat", "tasks", "memory", "files"]
    context = SimpleNamespace(principal=str(owner), subtree=(str(owner),),
                              is_root=False, standalone=False)
    sections: Dict[str, Any] = {}
    notes: List[str] = []
    for kind in wanted:
        if kind == "chat":
            try:
                files, manifest = collect_conversations(context)
                sessions = []
                for item in files:
                    sessions.append({
                        "session_id": item["session_id"],
                        "markdown": item["markdown"],
                    })
                sections["chat"] = {
                    "format": "text+JSON（Markdown 易读文本）",
                    "sessions": sessions,
                    "sessions_count": manifest.get("sessions", 0),
                    "messages": manifest.get("messages", 0),
                }
            except Exception as exc:  # noqa: BLE001 - 一路失败不拖垮整包
                sections["chat"] = {"error": f"聊天没导出来：{exc}"}
                notes.append(f"聊天没导出来（{exc}），这一路是空的。")
        elif kind == "tasks":
            try:
                tasks = collect_tasks_export(str(owner))
                sections["tasks"] = {"format": "JSON + 易读文本",
                                     "tasks": tasks, "text": _tasks_text(tasks)}
            except Exception as exc:  # noqa: BLE001
                sections["tasks"] = {"error": f"任务没导出来：{exc}"}
                notes.append(f"任务没导出来（{exc}），这一路是空的。")
        elif kind == "memory":
            try:
                entries = collect_memories(str(owner))
                sections["memory"] = {
                    "format": "JSON + 易读文本",
                    "entries": entries,
                    "text": _memory_markdown(str(owner), entries),
                }
            except Exception as exc:  # noqa: BLE001
                sections["memory"] = {"error": f"记忆没导出来：{exc}"}
                notes.append(f"记忆没导出来（{exc}），这一路是空的。")
        elif kind in ("files", "file_list", "file-list"):
            try:
                listing = collect_file_list(str(owner))
                sections["files"] = {"format": "JSON + 易读文本（只要清单，不要内容）",
                                     "files": listing, "text": _file_list_text(listing)}
            except Exception as exc:  # noqa: BLE001
                sections["files"] = {"error": f"文件清单没导出来：{exc}"}
                notes.append(f"文件清单没导出来（{exc}），这一路是空的。")
        else:
            notes.append(f"不认识的导出类型“{kind}”，已跳过（可选：chat / tasks / memory / files）。")
    payload = {
        "format": "jarvis.generic-export.v1",
        "exported_at": _now_iso(),
        "owner": str(owner),
        "types": wanted,
        "sections": sections,
        "notes": notes + [
            "文字类是易读文本（Markdown），结构是 JSON，不依赖贾维斯自己才能读的格式。",
            "本导出不含任何 Key、口令、恢复码与凭据；自定义模型地址整条剔除，见 "
            "GET /v1/a3s/models/custom/backup-view。",
            "想完整搬到另一台机器（会话、目标、员工、模型 Key）请用命令行加密备份，"
            "不要用这个通用导出。",
        ],
        "keys_included": False,
    }
    scrubbed = _scrub_for_export(payload)
    _assert_no_key_material(scrubbed, "通用导出")
    return scrubbed


# --------------------------------------------------------------------------- #
# HTTP 面
# --------------------------------------------------------------------------- #

class CustomEndpointBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., min_length=1, max_length=512)
    model: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=1, max_length=512)


@router.post("/models/custom")
def http_add_custom_endpoint(body: CustomEndpointBody,
                             context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """新增自定义第三方模型地址（待确认；Joseph 点确认才生效）。"""
    try:
        return add_custom_endpoint(context.principal, body.name, body.base_url,
                                   body.model, body.api_key)
    except ValueError as exc:
        raise deny(400, "A3S_CUSTOM_ENDPOINT_INVALID", str(exc)) from exc


@router.get("/models/custom")
def http_list_custom_endpoints(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    return {"endpoints": list_custom_endpoints(context.principal),
            "note": "待确认的地址还没生效，不会用来聊天；Key 只显示掩码，永不显示全。"}


@router.post("/models/custom/{endpoint_id}/confirm")
def http_confirm_custom_endpoint(endpoint_id: str,
                                 context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return confirm_custom_endpoint(context.principal, endpoint_id)
    except LookupError as exc:
        raise deny(404, "A3S_CUSTOM_ENDPOINT_NOT_FOUND", str(exc)) from exc


@router.delete("/models/custom/{endpoint_id}")
def http_delete_custom_endpoint(endpoint_id: str,
                                context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return delete_custom_endpoint(context.principal, endpoint_id)
    except LookupError as exc:
        raise deny(404, "A3S_CUSTOM_ENDPOINT_NOT_FOUND", str(exc)) from exc


@router.get("/models/custom/backup-view")
def http_custom_endpoints_backup_view(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """备份/迁移导出时的自定义端点视图：整条剔除，只留条数与说明。"""
    return custom_endpoints_backup_view(context.principal)


class GroupGuardBody(BaseModel):
    channel: str = Field("group", description="group（群聊）或 dm（1:1）")
    kind: str = Field("text", description="消息类型：text / approval / secret / draft / sensitive")
    text: str = Field("", max_length=20000)
    sender: str = Field("", max_length=128)


@router.get("/group/guard/policy")
def http_group_guard_policy() -> Dict[str, Any]:
    return group_guard_policy()


@router.post("/group/guard")
def http_group_guard(body: GroupGuardBody,
                     context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """群聊发送闸：发进群之前先过这里；被拦的返回人话原因，不静默丢。"""
    return check_group_message(body.channel, body.kind, body.text, sender=body.sender or context.principal)


class CorrectionBody(BaseModel):
    old_text: str = Field(..., min_length=1, max_length=8000)
    corrected_text: str = Field(..., min_length=1, max_length=8000)


class CorrectionUpdateBody(BaseModel):
    corrected_text: str = Field(..., min_length=1, max_length=8000)


@router.post("/memory/corrections")
def http_record_correction(body: CorrectionBody,
                           context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """记一次纠正：Joseph 说“你刚才说错了，应该是 X”。"""
    try:
        return record_correction(context.principal, body.old_text, body.corrected_text)
    except ValueError as exc:
        raise deny(400, "A3S_CORRECTION_INVALID", str(exc)) from exc


@router.get("/memory/corrections")
def http_list_corrections(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    corrections = list_corrections(context.principal)
    return {"corrections": corrections, "count": len(corrections)}


@router.get("/memory/recall")
def http_recall_with_corrections(q: str = "", limit: int = 5,
                                 context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """召回：纠正过的条目优先，旧说法不出现。"""
    return {"query": q, "entries": recall_with_corrections(context.principal, q, limit)}


@router.patch("/memory/corrections/{entry_id}")
def http_update_correction(entry_id: str, body: CorrectionUpdateBody,
                           context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return update_correction(context.principal, entry_id, body.corrected_text)
    except LookupError as exc:
        raise deny(404, "A3S_CORRECTION_NOT_FOUND", str(exc)) from exc
    except ValueError as exc:
        raise deny(400, "A3S_CORRECTION_INVALID", str(exc)) from exc


@router.delete("/memory/corrections/{entry_id}")
def http_delete_correction(entry_id: str,
                           context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return delete_correction(context.principal, entry_id)
    except LookupError as exc:
        raise deny(404, "A3S_CORRECTION_NOT_FOUND", str(exc)) from exc
    except ValueError as exc:
        raise deny(400, "A3S_CORRECTION_INVALID", str(exc)) from exc


class RememberNoteBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    chat_name: str = Field("", max_length=128)
    speaker: str = Field("", max_length=64)
    is_group: bool = Field(False)


@router.post("/memory/remember")
def http_remember_note(body: RememberNoteBody,
                       context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """聊天里点“记一句”：把这句话写进记忆主线（来源带上哪次聊天、谁说的）。"""
    try:
        return remember_chat_note(
            context.principal,
            body.text,
            chat_name=body.chat_name,
            speaker=body.speaker,
            is_group=bool(body.is_group),
        )
    except ValueError as exc:
        raise deny(400, "A3S_REMEMBER_INVALID", str(exc)) from exc


class AutoRollbackBody(BaseModel):
    dry_run: bool = Field(False, description="只演练，不真回滚")


@router.post("/upgrade/auto-rollback")
def http_auto_rollback(body: AutoRollbackBody,
                       context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """跑一次启动自检：没过就自动回滚到上一个好的版本，并留记录。"""
    return evaluate_startup_and_rollback(dry_run=bool(body.dry_run))


@router.get("/upgrade/auto-rollback/status")
def http_auto_rollback_status() -> Dict[str, Any]:
    return auto_rollback_status()


@router.get("/upgrade/rollback-records")
def http_rollback_records(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    return {"records": list_rollback_records(),
            "count": len(list_rollback_records())}


class OwnerCapBody(BaseModel):
    cap_usd: Optional[float] = Field(None, description="每月总上限（美元）；null=取消")


@router.post("/usage/owner-cap")
def http_set_owner_cap(body: OwnerCapBody,
                        context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """设主账号自己每月的总花费上限（只主账号能设；子账号不设上限）。"""
    try:
        return set_owner_monthly_cap(body.cap_usd, owner=context.principal,
                                     is_root=bool(context.is_root))
    except PermissionError as exc:
        raise deny(403, "A3S_OWNER_CAP_ROOT_ONLY", str(exc)) from exc
    except ValueError as exc:
        raise deny(400, "A3S_OWNER_CAP_INVALID", str(exc)) from exc


@router.get("/usage/owner-cap")
def http_check_owner_cap(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """查主账号本月总花费与上限：到了就停并通知。"""
    return check_owner_monthly_cap(context.principal)


@router.get("/usage/cap-policy")
def http_cap_policy() -> Dict[str, Any]:
    """用量封顶的书面规矩：主账号有总上限，子账号什么都不封。"""
    return CAP_POLICY


class VoiceCommandBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/voice/command")
def http_voice_command(body: VoiceCommandBody,
                       context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """语音入口：先分危险级；危险的进“等你确认”，语音里不直接执行。"""
    return submit_voice_command(body.text, owner=context.principal)


@router.get("/voice/pending")
def http_voice_pending(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    items = list_voice_pending(context.principal)
    return {"pending": items, "count": len(items)}


@router.post("/voice/pending/{confirmation_id}/confirm")
def http_voice_confirm(confirmation_id: str,
                       context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return confirm_voice_command(context.principal, confirmation_id)
    except LookupError as exc:
        raise deny(404, "A3S_VOICE_PENDING_NOT_FOUND", str(exc)) from exc


@router.post("/voice/pending/{confirmation_id}/cancel")
def http_voice_cancel(confirmation_id: str,
                      context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    try:
        return cancel_voice_command(context.principal, confirmation_id)
    except LookupError as exc:
        raise deny(404, "A3S_VOICE_PENDING_NOT_FOUND", str(exc)) from exc


@router.get("/voice/voiceprint-status")
def http_voiceprint_status() -> Dict[str, Any]:
    """声纹那条为什么没落地（诚实说明，不硬凑）。"""
    return VOICEPRINT_STATUS


class ExportByTypeBody(BaseModel):
    types: List[str] = Field(default_factory=list,
                             description="空=四种都要；可选 chat / tasks / memory / files")
    format: str = Field("json", description="json（结构）为主；文本已含在各 section 的 text 里")


@router.post("/export/by-type")
def http_export_by_type(body: ExportByTypeBody,
                        context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """按类型导出：聊天 / 任务 / 记忆 / 文件清单，一份清单，Key 一律剔除。"""
    return export_by_type(context.principal, body.types or [])


@router.get("/export/tasks")
def http_export_tasks(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """只导出任务（JSON），给设置里的一键导出用。"""
    tasks = collect_tasks_export(context.principal)
    return {"format": "jarvis.tasks.export.v1", "tasks": tasks,
            "count": len(tasks), "text": _tasks_text(tasks),
            "keys_included": False}


@router.get("/export/file-list")
def http_export_file_list(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """只导出文件清单（路径＋大小，不要内容），给设置里的一键导出用。"""
    listing = collect_file_list(context.principal)
    return {"format": "jarvis.file-list.export.v1", "files": listing,
            "count": len(listing), "text": _file_list_text(listing),
            "keys_included": False}


__all__ = [
    "router",
    "add_custom_endpoint",
    "confirm_custom_endpoint",
    "delete_custom_endpoint",
    "list_custom_endpoints",
    "custom_endpoints_backup_view",
    "check_group_message",
    "classify_group_message",
    "group_guard_policy",
    "record_correction",
    "list_corrections",
    "recall_with_corrections",
    "update_correction",
    "delete_correction",
    "remember_chat_note",
    "evaluate_startup_and_rollback",
    "startup_self_check_and_rollback",
    "list_rollback_records",
    "auto_rollback_status",
    "set_owner_monthly_cap",
    "owner_monthly_cost",
    "check_owner_monthly_cap",
    "owner_cap_allows_call",
    "classify_voice_command",
    "submit_voice_command",
    "list_voice_pending",
    "confirm_voice_command",
    "cancel_voice_command",
    "collect_tasks_export",
    "collect_file_list",
    "export_by_type",
    "AUTO_ROLLBACK_GAP",
    "VOICEPRINT_STATUS",
    "CAP_POLICY",
]
