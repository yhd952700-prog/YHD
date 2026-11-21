"""A2a：后端第二批（已批准项）的实现。

本模块集中实现 0.4.6-big 第二批后端能力，避免把 19 项改动散落到
19 个文件里。每一项都只在已有机制上接线，不重写认证/审计/备份恢复
核心；凡是“没配好、没接通、没数据”的情况一律返回老实空状态，绝不
编数字、绝不把“没检查”写成“通过”。

包含（与交卷清单一一对应）：
1.  记忆导入（JSON/Markdown）→ 主线记忆（``src/gateway/account_memory.py``）
2.  Muse Wonder 预设（在 ``src/gateway/models.py`` 里加预设，本模块只给价目说明）
3.  备份可见性＋一键恢复＋恢复演练＋异地入口（接线 ``BackupManager``）
4.  语义搜索空壳决定（删 ``src/api/knowledge_api.py`` 的空壳端点，见 docs）
5.  总览/小队状态推送 WebSocket 事件合同
6.  设置：语言＋时区（默认中文 / America/Tijuana）
7.  文件两人同改排队（文件级写锁）
8.  定时任务错过规则（实现在 ``src/tasks/routines.py``，本模块给规则文本）
9.  邀请链接有效期＋一次性
10. 模型失败自动换下一个＋按真实 token 的用量统计＋每月上限
11. 插件按需加载（注册表状态层）
12. 知识库/记忆主线（文档＋废弃标记，见 docs/knowledge-memory-mainline.md）
13. 派活排队＋限流
14. 失败主动报（只推三类：做完了 / 卡住了 / 要他拍板）
15. 启动自检接口
16. 报错说人话（映射层；accounts.py:324 与 local_proof.py:161 的 403 文案不动）
17. 恢复码提醒＋恢复入口（接线已有恢复码机制，不重写认证核心）
18. 多设备同步一套数据（同一账号一份数据，后到为准＋冲突留记录）
19. 每天早上 8 点推今天待办（走现有例程机制，见 ``src/tasks/routines.py``）

持久化：除记忆（主线 sqlite）、例程（routines sqlite）、用量
（account_usage sqlite）、备份（BackupManager）各自原有存储外，
本批新增的小状态（设置/通知/邀请/异地/演练记录/同步记录/上限/
插件加载状态/派活溢出队列）统一存在一份 ``a2a_state.json``
（``LIUHAO_A2A_STATE_FILE`` 可覆盖，默认走数据根目录），写时用
``atomic_write_text`` + 文件锁，与 ``account_settings.py`` 同款做法。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import socket
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_LANGUAGE = "zh"
DEFAULT_TIMEZONE = "America/Tijuana"
DEFAULT_INVITE_TTL_DAYS = 7
FILE_WRITE_TIMEOUT_SECONDS = 60.0
DISPATCH_MAX_CONCURRENCY = 3
DISPATCH_MAX_QUEUE = 50

#: Muse Wonder 价目（任务书给定，写进预设提示；不是实测价目）。
WONDER_PRICING = {
    "input_per_million_tokens_usd": 4.0,
    "output_per_million_tokens_usd": 20.0,
    "cached_input_per_million_tokens_usd": 0.20,
    "note": "比 DeepSeek 贵不少；Key 由 Joseph 自己去 dev.meta.ai 申请来填。",
}

#: 推送事件合同（第 5 项；前端订阅由 B 组接线）。
PUSH_CONTRACT = {
    "endpoint": "/v1/a2a/ws/status",
    "transport": "WebSocket",
    "events": [
        {
            "name": "overview.snapshot",
            "when": "连接建立后立即发一次；重连后也发一次",
            "payload": {
                "type": "overview.snapshot",
                "at": "ISO 时间",
                "data": {"tasks": "对象或 null（未配好就是 null，不编）",
                         "bots": "列表（没数据就是 []）",
                         "source": "snapshot"},
            },
        },
        {
            "name": "overview.updated",
            "when": "总览状态变化时（任务/ Bot 状态变了才推，不变不推）",
            "payload": {"type": "overview.updated", "at": "ISO 时间",
                        "data": {"changed": ["变了的字段名"], "tasks": "对象或 null",
                                 "bots": "列表"}},
        },
        {
            "name": "team.snapshot",
            "when": "连接建立后立即发一次；重连后也发一次",
            "payload": {"type": "team.snapshot", "at": "ISO 时间",
                        "data": {"members": "列表（没数据就是 []）"}},
        },
        {
            "name": "team.updated",
            "when": "小队成员状态变化时",
            "payload": {"type": "team.updated", "at": "ISO 时间",
                        "data": {"changed": ["变了的字段名"], "members": "列表"}},
        },
        {
            "name": "notice",
            "when": "第 14 项的三类通知之一产生时（做完了/卡住了/要他拍板）",
            "payload": {"type": "notice", "at": "ISO 时间",
                        "data": {"kind": "done|stuck|needs_decision",
                                 "title": "人话标题", "body": "人话内容"}},
        },
    ],
    "fallback": (
        "推送断了回落轮询：客户端收到 close/error 后等 5 秒重连；"
        "重连 3 次还不通就改回每 30 秒轮询 GET /v1/a2a/overview（快照接口），"
        "轮询时同样只拿快照、不编数据；WebSocket 一旦恢复，停掉轮询、"
        "重新订阅并先拿一次 snapshot 再继续。心跳：客户端每 30 秒发 "
        '{"ping": true}，服务端回 {"pong": true}。'
    ),
}

# A2b 第 16 项：本批新增的推送事件（任务生命周期/审批/提醒/待办/
# Bot 干活状态；定义见 src/gateway/a2b.py 的 A2B_PUSH_EVENTS）。
# 没接好的事件不发：这里只扩合同，发送点在 a2b 里按真实状态接线。
try:
    from .a2b import A2B_PUSH_EVENTS as _A2B_PUSH_EVENTS

    PUSH_CONTRACT["events"].extend(_A2B_PUSH_EVENTS)
except Exception:  # noqa: BLE001 - a2b 未就绪时合同只含原有事件，不阻断启动
    logger.warning("a2a push contract could not extend with a2b events", exc_info=True)

#: 人话报错映射（第 16 项；只做文案映射，不改任何鉴权/网关逻辑）。
HUMAN_ERROR_MAP: List[Tuple[str, str]] = [
    ("ssrf", "连不上模型服务：先检查网络能不能上网，再检查模型 Key 填得对不对；都对还不行就换一个模型试试。"),
    ("403", "模型那边拒绝了这次请求：一般是 Key 不对、没权限或被服务方拦了，先检查 Key，再看模型服务的状态页。"),
    ("401", "模型 Key 没通过验证：请检查 Key 是不是填错了、过期了，或者复制时多带了空格。"),
    ("429", "模型那边说请求太频繁了：等一会儿再试；一直这样就是用量到了上限，去设置里看看用量。"),
    ("timeout", "模型那边响应太慢、超时了：先检查网络，网络没问题就等一下再试，或者换一个模型。"),
    ("timed out", "模型那边响应太慢、超时了：先检查网络，网络没问题就等一下再试，或者换一个模型。"),
    ("dns", "域名解析失败：网络可能断了，先检查能不能上网，再试一次。"),
    ("connection refused", "连不上模型服务：服务可能没启动或地址填错了，先检查地址和网络。"),
    ("certificate", "网络证书校验失败：可能是时间不对或网络被拦截，先检查电脑时间，再检查网络。"),
    ("empty_response", "模型回了一个空回复：再试一次；一直空就换一个模型。"),
    ("not_configured", "这个功能还没配好：先去设置里配好再用，没配好的不会假装能用。"),
    ("rate_limit", "请求太频繁了：等一会儿再试。"),
]

_MISSED_RULE_TEXT = (
    "定时任务错过规则：错过一次、且还没过下一次到点，就补做一次，运行记录里注明“补做”；"
    "过期了（已过下一次到点）或连续错过多次的，跳过并记一条“已跳过，不补做”，"
    "绝不悄悄攒一堆补做；全局暂停期间到点的也记跳过、不补跑。"
)

_STATE_ENV = "LIUHAO_A2A_STATE_FILE"
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 状态文件
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from ..common.data_paths import resolve_store_path

    return resolve_store_path(
        "a2a_state.json",
        os.path.join("data", "a2a_state.json"),
        env_var=_STATE_ENV,
    )


def _default_state() -> Dict[str, Any]:
    return {
        "settings": {},
        "notifications": [],
        "invites": {},
        "offsite": {"target": None, "last_triggered_at": None,
                    "last_result": None, "automatic": False},
        "drills": [],
        "sync": {},
        "caps": {},
        "plugin_load": {},
        "dispatch_overflow": [],
        "import_log": [],
        "recovery_ack": {},
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
        with exclusive_file_lock(f"{path}.lock", description="A2a 状态"):
            atomic_write_text(
                path,
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 第 6 项：语言＋时区设置
# ---------------------------------------------------------------------------
# 通知已读 + 主题多设备同步（2026-11-15）：已读键与主题按账号存在
# settings 里，换设备拉 GET 就能看到同一份；localStorage 只留作离线缓存。
# 每个账号一份，子账号各存各的；读/写都走 _principal，不改任何权限。
_READ_KEYS_LIMIT = 800
_READ_KEY_MAX_LEN = 512


class SettingsConflictError(ValueError):
    """乐观锁冲突：调用方带的 expected_version 已不是最新，这次没写进去。

    调用方不要谎报“已同步”：拿回 current（最新的一份）合并/重做一次。
    """

    def __init__(self, current: Dict[str, Any]):
        super().__init__("设置已被另一台设备改过（版本变了），这次没写进去")
        self.current = current


def _clean_read_keys(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    keys = [str(k) for k in value if isinstance(k, str) and str(k).strip()]
    return [k[:_READ_KEY_MAX_LEN] for k in keys[-_READ_KEYS_LIMIT:]]


def get_account_settings(account_id: str) -> Dict[str, Any]:
    """账号设置；没存过就返回默认值（语言中文、时区 America/Tijuana）。

    theme 没设过返回 None（前端按设备形态默认，不谎称用户选过）；
    version/updated_at 是同步用的版本号与最后写入时间。
    """
    stored = load_state().get("settings", {}).get(str(account_id) or "", {})
    theme = stored.get("theme")
    return {
        "language": str(stored.get("language") or DEFAULT_LANGUAGE),
        "timezone": str(stored.get("timezone") or DEFAULT_TIMEZONE),
        "daily_digest_enabled": bool(stored.get("daily_digest_enabled", True)),
        "cost_routing_enabled": bool(stored.get("cost_routing_enabled", False)),
        "theme": theme if theme in ("light", "dark") else None,
        "notifications_read": _clean_read_keys(stored.get("notifications_read")),
        "version": int(stored.get("version") or 0),
        "updated_at": stored.get("updated_at"),
    }


def set_account_settings(account_id: str, **fields: Any) -> Dict[str, Any]:
    state = load_state()
    current = dict(state.get("settings", {}).get(str(account_id) or "", {}))
    # 乐观锁：带了 expected_version 且对不上，说明另一台设备先写了——
    # 这次一个字段都不写，调用方拿 current 回去合并/重做，别谎报已同步。
    expected = fields.get("expected_version")
    if expected is not None:
        if int(current.get("version") or 0) != int(expected):
            raise SettingsConflictError(get_account_settings(account_id))
    if "language" in fields and fields["language"] is not None:
        lang = str(fields["language"]).strip()
        if lang not in ("zh", "en"):
            raise ValueError("语言只支持 zh（中文）或 en（英文）")
        current["language"] = lang
    if "timezone" in fields and fields["timezone"] is not None:
        current["timezone"] = str(fields["timezone"]).strip() or DEFAULT_TIMEZONE
    if "daily_digest_enabled" in fields and fields["daily_digest_enabled"] is not None:
        current["daily_digest_enabled"] = bool(fields["daily_digest_enabled"])
    if "cost_routing_enabled" in fields and fields["cost_routing_enabled"] is not None:
        # 第二季 V9：成本智能路由开关，默认关（与 daily_digest 的默认开相反——
        # 省档会改变应答模型，必须机主显式打开才生效）。
        current["cost_routing_enabled"] = bool(fields["cost_routing_enabled"])
    if "theme" in fields:
        # None = 清掉手动主题（回到跟随形态）；其它值只认 light/dark。
        if fields["theme"] is None:
            current.pop("theme", None)
        elif str(fields["theme"]) in ("light", "dark"):
            current["theme"] = str(fields["theme"])
        else:
            raise ValueError("主题只支持 light（浅色）或 dark（深色）")
    if "notifications_read" in fields and fields["notifications_read"] is not None:
        if not isinstance(fields["notifications_read"], list):
            raise ValueError("notifications_read 必须是一个列表")
        current["notifications_read"] = _clean_read_keys(fields["notifications_read"])
    current["version"] = int(current.get("version") or 0) + 1
    current["updated_at"] = _now_iso()
    state.setdefault("settings", {})[str(account_id)] = current
    save_state(state)
    return get_account_settings(account_id)


# ---------------------------------------------------------------------------
# 第 1 项：记忆导入
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def parse_memory_import(content: bytes, filename: str = "") -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """解析记忆导入文件，支持 JSON 与 Markdown 两种格式。

    JSON：``[{"text": "...", "source": "...", "tags": [...]}]``，
    或 ``{"entries": [...]}`` / ``{"memories": [...]}``；``content`` 可作
    ``text`` 的别名。Markdown：``# 标题`` / ``- 条目`` / ``* 条目`` 每行
    一条记忆，标题行记为分类标签。返回 (条目, 格式, 错误)。
    """
    errors: List[str] = []
    text = content.decode("utf-8", errors="replace")
    name = (filename or "").lower()
    if name.endswith(".json") or text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text)
        except ValueError as exc:
            return [], "json", [f"JSON 解析失败：{exc}"]
        items = data.get("entries", data.get("memories", data)) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return [], "json", ["JSON 里没有找到条目列表（要的是一个列表，或带 entries/memories 的对象）"]
        entries: List[Dict[str, Any]] = []
        for idx, item in enumerate(items):
            if isinstance(item, str):
                entries.append({"text": item, "source": "import", "tags": []})
                continue
            if not isinstance(item, dict):
                errors.append(f"第 {idx + 1} 条不是对象，已跳过")
                continue
            body = str(item.get("text") or item.get("content") or "").strip()
            if not body:
                errors.append(f"第 {idx + 1} 条没有内容，已跳过")
                continue
            entries.append({
                "text": body,
                "source": str(item.get("source") or "import"),
                "tags": [str(t) for t in (item.get("tags") or []) if str(t).strip()],
            })
        return entries, "json", errors
    entries = []
    category = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            category = stripped.lstrip("#").strip()
            continue
        if stripped.startswith(("- ", "* ")):
            body = stripped[2:].strip()
            if body:
                entries.append({"text": body, "source": "import",
                                "tags": [category] if category else []})
    if not entries:
        errors.append("Markdown 里没有找到可导入的条目（用“- 内容”每行一条，# 标题可作分类）")
    return entries, "markdown", errors


def dedup_entries(
    new_entries: Sequence[Dict[str, Any]],
    existing_texts: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按正文归一化去重：已有的跳过并记为重复，不静默丢弃（调用方会报数）。"""
    seen = {_normalize_text(t) for t in existing_texts if _normalize_text(t)}
    to_add, duplicates = [], []
    for entry in new_entries:
        key = _normalize_text(entry.get("text", ""))
        if not key:
            continue
        if key in seen:
            duplicates.append(entry)
        else:
            seen.add(key)
            to_add.append(entry)
    return to_add, duplicates


def import_memories(
    content: bytes,
    filename: str,
    owner: str,
    *,
    existing_texts: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """导入记忆到主线记忆（account_memory sqlite），返回真实计数。

    去重依据是主线记忆里已有的正文；导入的条目 ``source="import"``。
    """
    from .account_memory import _query_entries  # 内部查询：只读已有正文做去重

    entries, fmt, errors = parse_memory_import(content, filename)
    if existing_texts is None:
        try:
            existing_texts = [e.get("text", "") for e in _query_entries((str(owner),), "")]
        except Exception:  # noqa: BLE001 - 读不到已有记忆时按“无重复”处理并如实记错
            existing_texts = []
            errors.append("读已有记忆失败，本次未做去重（导入仍继续）")
    to_add, duplicates = dedup_entries(entries, list(existing_texts))
    from .account_memory import record_entry_for_owner

    added_ids: List[str] = []
    for entry in to_add:
        record = record_entry_for_owner(
            str(owner), entry["text"], source=entry.get("source") or "import",
            tags=entry.get("tags") or [],
        )
        added_ids.append(str(record["id"]))
    result = {
        "format": fmt,
        "found": len(entries),
        "imported": len(added_ids),
        "duplicates_skipped": len(duplicates),
        "errors": errors,
        "imported_ids": added_ids,
    }
    state = load_state()
    state.setdefault("import_log", []).append(
        {"at": _now_iso(), "owner": str(owner), "filename": filename, **{k: v for k, v in result.items() if k != "imported_ids"}}
    )
    save_state(state)
    return result


def recall_memories(owner: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """聊天时调记忆：主线记忆里按关键词命中计分，返回最相关的几条。

    没命中就是空列表，不编内容；调用方（聊天）把结果拼进上下文时要标明
    来源是“记忆”，不要假装是模型现想的。
    """
    from .account_memory import _query_entries

    query = (query or "").strip()
    if not query:
        return []
    tokens = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
    try:
        entries = _query_entries((str(owner),), "")
    except Exception:  # noqa: BLE001
        return []
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for entry in entries:
        hay = _normalize_text(
            str(entry.get("text", "")) + " " + " ".join(entry.get("tags") or [])
        )
        score = sum(1 for t in tokens if t in hay)
        if query.lower() in hay:
            score += 2
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[: max(1, int(limit))]]


# ---------------------------------------------------------------------------
# 第 14 项：只报三类结果的通知
# ---------------------------------------------------------------------------

NOTICE_KINDS = ("done", "stuck", "needs_decision")


def push_notice(kind: str, title: str, body: str, *, owner: str = "") -> Dict[str, Any]:
    """记一条结果级通知。只允许三类：做完了 / 卡住了 / 要他拍板。"""
    if kind not in NOTICE_KINDS:
        raise ValueError(f"通知只分三类：{', '.join(NOTICE_KINDS)}，不推过程流水")
    notice = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "title": str(title),
        "body": str(body),
        "owner": str(owner or ""),
        "created_at": _now_iso(),
        "acked": False,
    }
    state = load_state()
    state.setdefault("notifications", []).append(notice)
    save_state(state)
    try:
        publish_push("notice", {"type": "notice", "at": notice["created_at"], "data": notice})
    except Exception:  # noqa: BLE001 - 推送失败不影响记通知
        logger.warning("push notice failed", exc_info=True)
    return notice


def report_done(what: str, where: str, *, owner: str = "") -> Dict[str, Any]:
    return push_notice("done", f"搞定了：{what}", f"结果在{where}。", owner=owner)


def report_stuck(where: str, missing: str, *, owner: str = "") -> Dict[str, Any]:
    return push_notice("stuck", f"卡住了：{where}", f"缺的是：{missing}。", owner=owner)


def report_needs_decision(what: str, *, owner: str = "") -> Dict[str, Any]:
    return push_notice("needs_decision", "要你拍板", str(what), owner=owner)


def list_notices(owner: str = "", *, only_unacked: bool = False) -> List[Dict[str, Any]]:
    items = load_state().get("notifications", [])
    if owner:
        items = [n for n in items if not n.get("owner") or n.get("owner") == owner]
    if only_unacked:
        items = [n for n in items if not n.get("acked")]
    return sorted(items, key=lambda n: n.get("created_at", ""), reverse=True)


def ack_notice(notice_id: str) -> bool:
    state = load_state()
    for notice in state.get("notifications", []):
        if str(notice.get("id")) == str(notice_id):
            notice["acked"] = True
            save_state(state)
            return True
    return False


# ---------------------------------------------------------------------------
# 第 5 项：推送（WebSocket 事件合同）
# ---------------------------------------------------------------------------

class PushHub:
    """进程内推送总线：总览/小队状态变化时发布，WS 连接订阅。

    只有“状态变了”才推；没变化不推空心跳之外的东西。断线后客户端按
    ``PUSH_CONTRACT["fallback"]`` 回落轮询，再连上先拿 snapshot。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: List["queue.Queue[Dict[str, Any]]"] = []
        self._last: Dict[str, Dict[str, Any]] = {}

    def subscribe(self) -> "queue.Queue[Dict[str, Any]]":
        q: queue.Queue[Dict[str, Any]] = queue.Queue()
        with self._lock:
            self._queues.append(q)
            # 只重放快照（overview/team snapshot）：刚连上要先知道当前状态。
            # notice / updated 是瞬时事件，重放旧的会让客户端看到过时的提醒。
            for event in self._last.values():
                if str(event.get("type", "")).endswith(".snapshot"):
                    q.put_nowait(event)
        return q

    def unsubscribe(self, q: "queue.Queue[Dict[str, Any]]") -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def publish(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        event = {"type": event_type, "at": _now_iso(), "data": data}
        with self._lock:
            self._last[event_type] = event
            queues = list(self._queues)
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        return event


_HUB = PushHub()


def publish_push(event_type: str, event: Optional[Dict[str, Any]] = None,
                 data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if event is not None and data is None:
        payload = dict(event.get("data") or {})
        return _HUB.publish(str(event.get("type") or event_type), payload)
    return _HUB.publish(event_type, data or {})


def push_overview(data: Dict[str, Any], *, snapshot: bool = False) -> Dict[str, Any]:
    return _HUB.publish("overview.snapshot" if snapshot else "overview.updated", data)


def push_team(data: Dict[str, Any], *, snapshot: bool = False) -> Dict[str, Any]:
    return _HUB.publish("team.snapshot" if snapshot else "team.updated", data)


# ---------------------------------------------------------------------------
# 第 7 项：文件级写锁/排队
# ---------------------------------------------------------------------------

class FileWriteQueue:
    """两个 Bot 同时改一个文件时排队：后到的等前一个写完再写。

    同一路径一把锁；超时（默认 60 秒）与死锁都有上限：等不到就报人话
    “有人正在改这个文件，等了 60 秒还没轮到你，先别改，稍后再试”，
    绝不无限等、绝不互相覆盖。写完后下一个自动接手。
    """

    def __init__(self, timeout_seconds: float = FILE_WRITE_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self._guard = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}
        self._waiting: Dict[str, int] = {}

    def _lock_for(self, path: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(str(path), threading.Lock())

    def waiting_count(self, path: str) -> int:
        with self._guard:
            return int(self._waiting.get(str(path), 0))

    def write_queued(self, path: str, writer: Callable[[], Any], *,
                     owner: str = "") -> Dict[str, Any]:
        """排队写一次。返回 {ok, waited_seconds, message}；超时返回 ok=False。"""
        lock = self._lock_for(path)
        with self._guard:
            self._waiting[str(path)] = self._waiting.get(str(path), 0) + 1
        started = time.monotonic()
        try:
            acquired = lock.acquire(timeout=self.timeout_seconds)
        finally:
            with self._guard:
                self._waiting[str(path)] = max(0, self._waiting.get(str(path), 0) - 1)
        if not acquired:
            return {
                "ok": False,
                "waited_seconds": round(time.monotonic() - started, 1),
                "message": (
                    f"有人正在改这个文件，等了 {int(self.timeout_seconds)} 秒还没轮到你，"
                    "先别改，稍后再试——你的改动没有被覆盖，也没有丢。"
                ),
            }
        try:
            result = writer()
            return {
                "ok": True,
                "waited_seconds": round(time.monotonic() - started, 1),
                "result": result,
                "message": "写好了，排在你后面的已经可以接着写了。",
            }
        finally:
            lock.release()


FILE_WRITE_QUEUE = FileWriteQueue()


# ---------------------------------------------------------------------------
# 第 9 项：邀请链接（有效期＋一次性）
# ---------------------------------------------------------------------------

def create_invite(principal: str, *, created_by: str, ttl_days: int = DEFAULT_INVITE_TTL_DAYS,
                  role: str = "viewer") -> Dict[str, Any]:
    """建一条邀请链接记录：默认 7 天有效，可配；用过即失效。"""
    days = int(ttl_days)
    if days < 1 or days > 90:
        raise ValueError("邀请链接有效期只能在 1 到 90 天之间")
    now = time.time()
    token = uuid.uuid4().hex
    invite = {
        "token": token,
        "principal": str(principal),
        "role": str(role),
        "created_by": str(created_by),
        "created_at": _now_iso(),
        "expires_at": datetime.fromtimestamp(now + days * 86400, tz=timezone.utc).isoformat(),
        "used": False,
        "used_at": None,
    }
    state = load_state()
    state.setdefault("invites", {})[token] = invite
    save_state(state)
    return invite


def invite_status(token: str) -> Dict[str, Any]:
    """查一条邀请链接：过期/用过都给人话，不返回“能用”的假象。"""
    invite = load_state().get("invites", {}).get(str(token))
    if invite is None:
        return {"ok": False, "state": "not_found",
                "message": "这个邀请链接不存在，可能是链接复制错了，请让 Owner 重新发一个。"}
    if invite.get("used"):
        return {"ok": False, "state": "used",
                "message": "这个邀请链接已经用过了，用一次就失效，请让 Owner 重新发一个。"}
    try:
        expires = datetime.fromisoformat(str(invite["expires_at"])).timestamp()
    except ValueError:
        return {"ok": False, "state": "invalid",
                "message": "这个邀请链接坏了，请让 Owner 重新发一个。"}
    if time.time() > expires:
        return {"ok": False, "state": "expired",
                "message": "这个邀请链接已经过期了，请让 Owner 重新发一个。"}
    return {"ok": True, "state": "valid", "invite": invite,
            "message": "邀请链接有效，可以继续。", "expires_at": invite["expires_at"]}


def redeem_invite(token: str) -> Dict[str, Any]:
    """核销邀请链接：只在有效时核销一次；核销后立即失效。

    注意：核销只管“链接本身一次性”，账号创建仍走 Owner 邀请
    （``owner_invite``）的激活码流程，不在这里另写一份认证。
    """
    status = invite_status(token)
    if not status["ok"]:
        return status
    state = load_state()
    invite = state.get("invites", {}).get(str(token))
    if invite is None:  # 并发下被删了也按“不存在”老实说
        return {"ok": False, "state": "not_found",
                "message": "这个邀请链接不存在，可能是链接复制错了，请让 Owner 重新发一个。"}
    invite["used"] = True
    invite["used_at"] = _now_iso()
    save_state(state)
    return {"ok": True, "state": "redeemed", "invite": invite,
            "message": "邀请链接已核销（用过即失效）；接下来请 Owner 用激活码给你开账号。"}


# ---------------------------------------------------------------------------
# 第 10 项：模型 fallback＋用量统计＋每月上限
# ---------------------------------------------------------------------------

#: 用量预警阈值（B 组）：到上限的 80% 就提前说一声。
WARNING_RATIO = 0.8

def try_models_in_order(
    candidates: Sequence[str],
    call_fn: Callable[[str], Any],
) -> Tuple[Any, List[Dict[str, Any]]]:
    """按顺序试模型：失败就记一条、换下一个；全失败就把最后一次失败原样抛出。

    绝不编造成功：返回的 attempts 里每一次失败都有真实原因。
    """
    attempts: List[Dict[str, Any]] = []
    last_error: Optional[BaseException] = None
    for model in candidates:
        try:
            result = call_fn(str(model))
        except Exception as exc:  # noqa: BLE001 - 失败要记下来再换下一个
            last_error = exc
            attempts.append({"model": str(model), "ok": False, "error": str(exc) or type(exc).__name__})
            logger.warning("model %s failed, trying next: %s", model, exc)
            continue
        attempts.append({"model": str(model), "ok": True})
        return result, attempts
    if last_error is not None:
        raise last_error
    raise RuntimeError("没有可用的模型（候选列表是空的）")


def resolve_provider_with_fallback(
    *,
    model_id: Optional[str] = None,
    key_scope: Any = None,
    extra_model_ids: Optional[Sequence[str]] = None,
    resolver: Optional[Callable[..., Any]] = None,
):
    """聊天用的 provider 解析：指定模型失败时，按“默认→其余已配模型”的
    顺序换下一个；每次失败都记日志，全部失败才把原始错误抛回。

    ``resolver`` 是调用方的 provider 解析函数（默认直连
    ``model_router.provider_for_task``）；``src/gateway/chat.py`` 会传自己
    的 ``provider_for_task`` 包装器进来，这样 Key 归属视角与测试接缝都
    不会被绕过——解析失败仍按原样 409，不留空会话。
    """
    from ..ai.model_router import provider_for_task as _router

    def _call(mid: Optional[str]):
        if resolver is not None:
            return resolver("chat", model_id=mid, key_scope=key_scope)
        if key_scope is None:
            return _router("chat", model_id=mid)
        return _router(
            "chat",
            model_id=mid,
            visible_to=list(key_scope.visible_to),
            allow_unowned=key_scope.allow_unowned,
            allow_owners=list(key_scope.allow_owners),
            allow_env_fallback=key_scope.is_root,
        )

    candidates: List[Optional[str]] = []
    for mid in [model_id, None, *(extra_model_ids or [])]:
        if mid not in candidates:
            candidates.append(mid)
    result, attempts = try_models_in_order(
        [c or "" for c in candidates],
        lambda mid: _call(mid or None),
    )
    logger.info("model fallback attempts: %s", attempts)
    return result


def _usage_db_path() -> str:
    from .account_usage import db_path

    return db_path()


def bot_usage_summary(bot_id: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """某 Bot 的真实用量：按实际 token 与厂商价目算；没调用就是 0。

    未定价的模型 ``cost`` 为 null 且明确列出，绝不按 0 元充数。
    ``month`` 为 ``YYYY-MM``，默认本月（UTC）。
    """
    from .account_usage import _billing_report, _price_table

    bot = str(bot_id or "").strip()
    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    path = _usage_db_path()
    rows: List[Tuple[str, int, int]] = []
    calls = 0
    if bot and Path(path).exists():
        with sqlite3.connect(path) as conn:
            # 归属按两条算：actor = Bot 本人，或会话是这个 Bot 的工作台会话
            # （``chat.py`` 记账时 actor 是聊天的那个人、session_id 才是
            # ``workbench-<bot_id>``；只按 actor 算，Bot 聊天的用量永远是 0，
            # 上限也就永远拦不住 —— 本轮 item 6 的归属修复）。
            cur = conn.execute(
                "SELECT model, tokens_in, tokens_out FROM account_usage_events "
                "WHERE (actor = ? OR session_id = ?) AND substr(created_at, 1, 7) = ?",
                (bot, f"workbench-{bot}", target_month),
            )
            rows = [(str(r[0] or "unknown"), int(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()]
            calls = len(rows)
    models = sorted({model for model, _, _ in rows})
    _price_table(models)  # 与用量页同一口径；未知模型不进表
    report = _billing_report(models, rows)
    return {
        "bot_id": bot,
        "month": target_month,
        "calls": calls,
        "tokens_in": sum(t[1] for t in rows),
        "tokens_out": sum(t[2] for t in rows),
        "total_tokens": sum(t[1] + t[2] for t in rows),
        "cost": report.get("total_cost"),
        "cost_complete": report.get("total_cost_complete"),
        "unknown_models": report.get("unknown_models", []),
        "message": report.get("message", ""),
        "by_model": report.get("models", {}),
        "note": "没调用就是 0；没定价的模型费用显示为 null，不按 0 元算。",
    }


def set_monthly_cap(bot_id: str, cap_usd: Optional[float], *, owner: str = "") -> Dict[str, Any]:
    """设某 Bot 每月花费上限（美元）；None 表示不设上限。"""
    state = load_state()
    caps = state.setdefault("caps", {})
    if cap_usd is None:
        caps.pop(str(bot_id), None)
    else:
        value = float(cap_usd)
        if value < 0:
            raise ValueError("每月上限不能是负数")
        caps[str(bot_id)] = {"cap_usd": value, "owner": str(owner), "updated_at": _now_iso()}
    save_state(state)
    return {"bot_id": str(bot_id), "cap_usd": cap_usd}


def bot_cap_status(bot_id: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """查某 Bot 本月是否到上限：纯计算，**不发任何通知**。

    聊天热路径的门禁专用（``chat.py`` 每通聊天都调一次）：``check_monthly_cap``
    到了上限会调 ``report_stuck`` 发一条“到上限停了”的通知，热路径里调它，
    被拦住的每一通聊天都会往收件箱里再塞一条，刷爆。通知只由设置 / 用量页
    的显式检查（``check_monthly_cap``）发一次；热路径里 429 本身就是告诉
    用户的那句话。返回结构与 ``check_monthly_cap`` 完全一致。
    """
    cap = load_state().get("caps", {}).get(str(bot_id))
    summary = bot_usage_summary(bot_id, month=month)
    if cap is None:
        return {"bot_id": str(bot_id), "capped": False, "cap_usd": None,
                "warning": False, "usage_ratio": None, "warning_ratio": WARNING_RATIO, **summary}
    cost = summary.get("cost")
    reached = cost is not None and float(cost) >= float(cap["cap_usd"])
    ratio = None
    if cost is not None and float(cap["cap_usd"]) > 0:
        ratio = float(cost) / float(cap["cap_usd"])
    warning = bool(ratio is not None and ratio >= WARNING_RATIO and not reached)
    return {"bot_id": str(bot_id), "capped": bool(reached), "cap_usd": cap["cap_usd"],
            "warning": warning, "usage_ratio": ratio, "warning_ratio": WARNING_RATIO, **summary}


def check_monthly_cap(bot_id: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """查某 Bot 本月是否到上限：到了就停，并给 Joseph 一条“到上限停了”的通知。

    B 组补预警：到上限 80% 就提前说一声（``warning=True`` + ``usage_ratio``），
    别等月底才吓一跳。预警阈值 ``WARNING_RATIO = 0.8``。

    判断复用无副作用的 ``bot_cap_status``；本函数只在“到了”时多发一次通知。
    显式检查（设置 / 用量页）调本函数；聊天热路径调 ``bot_cap_status``。
    """
    status = bot_cap_status(bot_id, month=month)
    if status.get("capped"):
        cap_usd = status.get("cap_usd")
        cost = status.get("cost")
        report_stuck(
            f"Bot {bot_id} 本月花费",
            f"已经到了你设的每月上限 ${cap_usd}（本月已花 ${cost}），我先帮它停了，"
            "要继续用就去设置里调高上限。",
            owner=str(load_state().get("caps", {}).get(str(bot_id), {}).get("owner") or ""),
        )
    return status


# ---------------------------------------------------------------------------
# 第 11 项：插件按需加载
# ---------------------------------------------------------------------------

CORE_PLUGINS = ("core",)

def plugin_lazy_status(plugin_id: str) -> Dict[str, Any]:
    """某插件的加载状态：核心常驻，其余默认未加载、用时再加载。"""
    record = load_state().get("plugin_load", {}).get(str(plugin_id), {})
    if str(plugin_id) in CORE_PLUGINS:
        return {"plugin_id": str(plugin_id), "state": "loaded", "core": True,
                "message": "核心插件，启动时已加载。"}
    return {
        "plugin_id": str(plugin_id),
        "state": str(record.get("state") or "not_loaded"),
        "core": False,
        "last_error": record.get("last_error"),
        "loaded_at": record.get("loaded_at"),
        "message": {
            "loaded": "已加载。",
            "not_loaded": "还没加载，用到时才加载，不占启动时间。",
            "failed": f"加载失败，没有拖垮启动：{record.get('last_error') or '原因未知'}。",
        }.get(str(record.get("state") or "not_loaded"), "还没加载。"),
    }


def ensure_plugin_loaded(plugin_id: str, loader: Callable[[], Any]) -> Dict[str, Any]:
    """用时加载一个插件：失败只记一条、界面老实显示未加载，绝不拖垮启动。"""
    current = plugin_lazy_status(plugin_id)
    if current["state"] == "loaded":
        return current
    try:
        loader()
    except Exception as exc:  # noqa: BLE001 - 加载失败是预期内的分支
        state = load_state()
        state.setdefault("plugin_load", {})[str(plugin_id)] = {
            "state": "failed", "last_error": str(exc) or type(exc).__name__,
            "loaded_at": None,
        }
        save_state(state)
        logger.warning("plugin %s failed to load on demand: %s", plugin_id, exc)
        return plugin_lazy_status(plugin_id)
    state = load_state()
    state.setdefault("plugin_load", {})[str(plugin_id)] = {
        "state": "loaded", "last_error": None, "loaded_at": _now_iso(),
    }
    save_state(state)
    return plugin_lazy_status(plugin_id)


def list_plugin_lazy_status(plugin_ids: Sequence[str]) -> List[Dict[str, Any]]:
    return [plugin_lazy_status(pid) for pid in plugin_ids]


# ---------------------------------------------------------------------------
# 第 13 项：派活排队＋限流
# ---------------------------------------------------------------------------

class DispatchQueue:
    """派活排队＋限流：并发上限 3，队列上限 50。

    队列满了也不丢任务：多出来的进“溢出区”（落盘在 a2a 状态里），
    前面空出位置就接着派；状态页如实显示排队数与溢出数，并给人话提示。
    """

    def __init__(self, max_concurrency: int = DISPATCH_MAX_CONCURRENCY,
                 max_queue: int = DISPATCH_MAX_QUEUE) -> None:
        self.max_concurrency = int(max_concurrency)
        self.max_queue = int(max_queue)
        self._lock = threading.Lock()
        self._running: Dict[str, Dict[str, Any]] = {}
        self._queued: "deque[Dict[str, Any]]" = deque()

    def submit(self, task: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(task)
        item.setdefault("id", str(uuid.uuid4()))
        item["submitted_at"] = _now_iso()
        with self._lock:
            if len(self._queued) >= self.max_queue:
                state = load_state()
                overflow = state.setdefault("dispatch_overflow", [])
                overflow.append(item)
                save_state(state)
                return {
                    "id": item["id"], "state": "overflow",
                    "message": "派活太多了，你的任务已排进溢出区等着，不会丢；前面空出来就接着派。",
                }
            self._queued.append(item)
            position = len(self._queued)
        return {"id": item["id"], "state": "queued", "position": position,
                "message": f"已排队，前面还有 {position - 1} 个任务。" if position > 1 else "已排队，马上就派。"}

    def start_next(self) -> Optional[Dict[str, Any]]:
        """取下一个任务开始跑（调用方执行完要调 finish）。并发满了返回 None。"""
        with self._lock:
            if len(self._running) >= self.max_concurrency or not self._queued:
                return None
            item = self._queued.popleft()
            self._running[item["id"]] = item
            return item

    def finish(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            done = self._running.pop(str(task_id), None)
            if done is None:
                return None
            # 溢出区有任务就补进主队列，顺序不变。
            state = load_state()
            overflow = state.get("dispatch_overflow", [])
            if overflow and len(self._queued) < self.max_queue:
                self._queued.append(overflow.pop(0))
                save_state(state)
            return done

    def status(self) -> Dict[str, Any]:
        state = load_state()
        with self._lock:
            return {
                "max_concurrency": self.max_concurrency,
                "max_queue": self.max_queue,
                "running": len(self._running),
                "queued": len(self._queued),
                "overflow": len(state.get("dispatch_overflow", [])),
                "running_ids": sorted(self._running),
                "message": "派活排队正常。" if len(self._queued) < self.max_queue else "派活队列已满，新任务先进溢出区等着，不会丢。",
            }


DISPATCH_QUEUE = DispatchQueue()


# ---------------------------------------------------------------------------
# 第 15 项：启动自检
# ---------------------------------------------------------------------------

def run_startup_checks() -> List[Dict[str, Any]]:
    """启动自检：端口 / 模型 / 磁盘 / 网络。

    每项只给三种老实状态：pass（通过）/ fail（失败）/ not_configured
    （没配好）；“没检查”绝不写成“通过”。
    """
    from ..common.data_paths import data_root

    checks: List[Dict[str, Any]] = []

    # 磁盘：数据根剩余空间。
    try:
        root = data_root() or Path("data")
        usage = shutil.disk_usage(str(root))
        free_gb = usage.free / (1024 ** 3)
        checks.append({
            "name": "disk", "label": "磁盘空间",
            "status": "pass" if free_gb >= 1 else "fail",
            "detail": f"数据目录剩余 {free_gb:.1f} GB（少于 1 GB 会失败）。",
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "disk", "label": "磁盘空间", "status": "fail",
                       "detail": f"磁盘检查没跑成：{exc}"})

    # 模型：有没有配好的模型记录（只计数，不读 Key、不调接口）。
    try:
        from ..ai.model_config import ModelConfigStore

        store = ModelConfigStore()
        models = store.list_models()
        configured = [m for m in models if (m.get("api_key_status") or "") == "ok"]
        if not models:
            checks.append({"name": "models", "label": "模型配置", "status": "not_configured",
                           "detail": "还没配任何模型：去设置 → 模型与 Key 里填一个 Key 才能聊天。"})
        elif not configured:
            checks.append({"name": "models", "label": "模型配置", "status": "not_configured",
                           "detail": f"配了 {len(models)} 个模型，但没有一个 Key 是可用的，请检查 Key。"})
        else:
            checks.append({"name": "models", "label": "模型配置", "status": "pass",
                           "detail": f"配了 {len(models)} 个模型，其中 {len(configured)} 个 Key 可用。"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "models", "label": "模型配置", "status": "fail",
                       "detail": f"模型配置读不出来：{exc}"})

    # 端口：本机网关端口能不能连上（默认 8765，LIUHAO_PORT 可覆盖）。
    port = int(os.environ.get("LIUHAO_PORT") or os.environ.get("PORT") or 8765)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            checks.append({"name": "port", "label": "本机网关端口", "status": "pass",
                           "detail": f"127.0.0.1:{port} 能连上。"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "port", "label": "本机网关端口", "status": "fail",
                       "detail": f"127.0.0.1:{port} 连不上（{exc}）：网关可能还没启动。"})

    # 网络：只做 DNS 解析，不调任何模型接口、不用 Key。
    try:
        socket.getaddrinfo("api.deepseek.com", 443)
        checks.append({"name": "network", "label": "网络（DNS）", "status": "pass",
                       "detail": "域名能解析，网络是通的。"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "network", "label": "网络（DNS）", "status": "fail",
                       "detail": f"域名解析失败（{exc}）：先检查能不能上网。"})

    return checks


# ---------------------------------------------------------------------------
# 第 16 项：报错说人话
# ---------------------------------------------------------------------------

def humanize_error(text: str) -> Dict[str, Any]:
    """把面向 Joseph 的报错翻成人话。只做文案映射，不改鉴权逻辑。

    注意：src/gateway/accounts.py:324 与 src/gateway/local_proof.py:161
    的 403 文案是禁区，本函数不碰它们；这里只翻译调用方传进来的文本。
    """
    raw = str(text or "")
    lowered = raw.lower()
    for needle, human in HUMAN_ERROR_MAP:
        if needle in lowered:
            return {"original": raw, "message": human, "matched": needle}
    if not raw.strip():
        return {"original": raw, "message": "出了一点问题，但没说清原因：先重试一次，还不行就把这句话原样发给 Muse 看。", "matched": None}
    return {"original": raw, "message": f"出问题了：{raw}。先重试一次，还不行就换一个模型试试。", "matched": None}


# ---------------------------------------------------------------------------
# 第 3 项：备份可见性＋一键恢复＋演练＋异地入口
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    from ..common.data_paths import data_root

    root = data_root()
    return Path(root) if root else Path("data")


def backup_status() -> Dict[str, Any]:
    """备份页要看得到的东西：每日自动备份跑没跑、最近一次什么时候、多大。

    从来没备过就老实说没备过，不编一个假的“最近一次”。
    """
    from ..backup.auto_backup import auto_backup_status

    status = auto_backup_status(_data_dir())
    latest = status.get("latest")
    return {
        "auto_backup_hour": 3,
        "retention_days": 7,
        "backup_dir": status.get("backup_dir"),
        "key_exists": status.get("key_exists"),
        "backup_count": status.get("backup_count", 0),
        "latest": latest,
        "ran": latest is not None,
        "message": (
            f"最近一次自动备份在 {latest.get('timestamp_text') or latest['timestamp']}，大小 {latest['size_bytes']} 字节。"
            if latest else
            "每日自动备份还没跑过一次（每天本地时间 03:00 跑，跑完这里会显示时间）。"
        ),
        "offsite": load_state().get("offsite", {}),
    }


def list_local_backups() -> Dict[str, Any]:
    """本机备份文件清单（自动备份 + 恢复前快照），给备份页挑一份恢复用。"""
    from ..backup.auto_backup import list_local_backups as _list_local_backups

    data = _list_local_backups(_data_dir())
    return {
        "backups": data["backups"],
        "snapshots": data["snapshots"],
        "backup_count": len(data["backups"]),
        "snapshot_count": len(data["snapshots"]),
    }


def _local_backup_manager(data_dir: Path, *, subdir: str, backup_id: str):
    """恢复用的 manager：必须带备份时的源映射；是否加密按目标备份来。

    之前这里只传 ``backup_dir``：加密归档解不开（恢复永远失败），就算
    解开也因没有 ``source_paths`` 一个文件都写不回去还报成功。
    加密的备份要密钥；明文备份（老版本/手工备份）不需要密钥，别硬要。
    """
    from ..backup.auto_backup import (
        AUTO_BACKUP_EXCLUDE_NAMES,
        auto_backup_source_paths,
        load_backup_key,
    )
    from ..backup.backup_manager import BackupManager

    meta = _light_manager(subdir).get_backup(backup_id)
    encrypted = bool(meta is not None and meta.encrypted)
    key = load_backup_key(data_dir) if encrypted else None
    return BackupManager(
        backup_dir=data_dir / "backups" / subdir,
        source_paths=auto_backup_source_paths(data_dir),
        encryption_enabled=encrypted,
        encryption_key=key,
        exclude_names=AUTO_BACKUP_EXCLUDE_NAMES,
    )


def _latest_usable_backup(manager):
    """最近一份能恢复的备份：COMPLETED 与 VERIFIED 都算（校验会把内存态
    status 改成 VERIFIED，重启前再取 latest 不能把它漏掉）。"""
    from ..backup.backup_manager import BackupStatus

    usable = (BackupStatus.COMPLETED, BackupStatus.VERIFIED)
    for meta in manager.list_backups():
        if meta.status in usable:
            return meta
    return None


def _restore_with_snapshot(data_dir: Path, *, manager, backup_id: str,
                           dry_run: bool = False) -> Dict[str, Any]:
    """校验 → 恢复前快照 → 动手恢复 → 失败自动回滚到快照。

    dry_run=True 时只校验（给恢复演练用），不写任何文件、不备快照。
    恢复是大事：快照备不成就别动手；恢复失败必须回到恢复前的样子，
    回不去也要把快照 id 留给用户手动恢复，不能含糊过去。
    """
    from ..backup.auto_backup import format_backup_time, run_pre_restore_snapshot

    meta = manager.get_backup(backup_id)
    when = format_backup_time(meta.timestamp) if meta is not None else ""
    label = f"备份（{when}）" if when else f"备份 {backup_id}"
    # 1. 先校验：校验不过直接拒，现有数据不动，也没必要备快照。
    if not manager.verify_backup(backup_id):
        return {"ok": False, "backup_id": backup_id,
                "message": f"{label}校验没通过（文件不完整或解不开），没敢用它恢复，现有数据没动。先重新备一份再说。"}
    if dry_run:
        return {"ok": True, "backup_id": backup_id,
                "message": "演练校验通过：备份文件完整、能解开。"}
    # 2. 恢复前快照：备不成就别动手。
    try:
        snapshot = run_pre_restore_snapshot(data_dir)
    except Exception as exc:  # noqa: BLE001 - 快照失败的原因要原样说
        return {"ok": False, "backup_id": backup_id,
                "message": f"恢复前给当前数据备一份没备成（{exc}），没敢动手，现有数据没动。"}
    # 3. 动手恢复。
    result = manager.restore_backup(backup_id)
    if getattr(result, "success", False):
        detail = result.to_dict() if hasattr(result, "to_dict") else {}
        files = detail.get("total_files_restored") or 0
        return {"ok": True, "backup_id": backup_id,
                "snapshot_backup_id": snapshot.backup_id,
                "restored_files": files,
                "restored_bytes": detail.get("total_bytes_restored"),
                "restored_sources": detail.get("restored_sources"),
                "message": (
                    f"已恢复到{label}：恢复了 {files} 个文件。"
                    f"恢复前已自动备一份当前数据（快照 {snapshot.backup_id}），"
                    "恢复得不对可以在备份页把它恢复回来。"
                ), "test": True}
    # 4. 恢复失败：用快照回到恢复前的样子。
    rolled_back = False
    try:
        from ..backup.auto_backup import PRE_RESTORE_SUBDIR

        snap_manager = _local_backup_manager(
            data_dir, subdir=PRE_RESTORE_SUBDIR, backup_id=snapshot.backup_id
        )
        rollback = snap_manager.restore_backup(snapshot.backup_id)
        rolled_back = bool(getattr(rollback, "success", False))
    except Exception:  # noqa: BLE001 - 回滚失败也要如实报，不吞掉
        rolled_back = False
    if rolled_back:
        return {"ok": False, "backup_id": backup_id,
                "snapshot_backup_id": snapshot.backup_id,
                "message": "恢复没成功，但已自动回到恢复前的样子（恢复前快照已恢复），现有数据就是恢复前的数据。"}
    return {"ok": False, "backup_id": backup_id,
            "snapshot_backup_id": snapshot.backup_id,
            "message": (
                "恢复没成功，而且自动回滚也没跑成；恢复前快照还在"
                f"（{snapshot.backup_id}），可以在备份页手动把它恢复回来。"
            )}


def _light_manager(subdir: str):
    """只读元数据的 manager：找“有没有备份”不需要密钥。

    恢复前必须先用它确认备份存在：空数据目录里既没有备份也没有密钥，
    先报“没有可恢复的备份”才对，而不是先报密钥不在了。
    """
    from ..backup.backup_manager import BackupManager

    return BackupManager(backup_dir=_data_dir() / "backups" / subdir)


def restore_latest_backup(*, dry_run: bool = False) -> Dict[str, Any]:
    """一键恢复到上一个好的版本：本机自动备份库里最近一份。

    真恢复，不是摆设：带解密密钥 + 备份时的源映射；先校验、再给当前
    数据备一份恢复前快照、再动手；恢复失败自动用快照回到恢复前的样子。
    dry_run=True 时只校验（给恢复演练用），不写任何文件、不备快照。
    """
    from ..backup.auto_backup import AUTO_BACKUP_SUBDIR

    data_dir = _data_dir()
    latest = _latest_usable_backup(_light_manager(AUTO_BACKUP_SUBDIR))
    if latest is None:
        return {"ok": False, "message": "没有可恢复的备份：先等每天 03:00 的自动备份跑一次，或者手动备一份。"}
    try:
        manager = _local_backup_manager(
            data_dir, subdir=AUTO_BACKUP_SUBDIR, backup_id=latest.backup_id
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return _restore_with_snapshot(
        data_dir, manager=manager, backup_id=latest.backup_id, dry_run=dry_run
    )


def restore_backup_by_id(backup_id: str, *, collection: str = "auto") -> Dict[str, Any]:
    """恢复本机备份库里指定的一份（自动备份或恢复前快照）。

    最近一份不一定是好的那份（坏数据也可能已被自动备份进去）：灾难恢复
    要能挑一份更早的好备份。流程与一键恢复相同：先校验、再备快照、再动手，
    失败自动回滚到快照。
    """
    from ..backup.auto_backup import AUTO_BACKUP_SUBDIR, PRE_RESTORE_SUBDIR

    cleaned = str(backup_id or "").strip()
    if not cleaned:
        return {"ok": False, "message": "请先选一份备份。"}
    if collection not in ("auto", "pre-restore"):
        return {"ok": False, "message": "恢复来源不对：只能从本机自动备份或恢复前快照里选。"}
    subdir = PRE_RESTORE_SUBDIR if collection == "pre-restore" else AUTO_BACKUP_SUBDIR
    if _light_manager(subdir).get_backup(cleaned) is None:
        return {"ok": False, "message": "没找到这份备份：可能已被自动清理（自动备份只留 7 天，快照只留最近 3 份）。"}
    data_dir = _data_dir()
    try:
        manager = _local_backup_manager(data_dir, subdir=subdir, backup_id=cleaned)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return _restore_with_snapshot(data_dir, manager=manager, backup_id=cleaned)


def run_restore_drill() -> Dict[str, Any]:
    """恢复演练：校验最近备份（校验和＋解开），跑通/跑不通都如实记一条。"""
    outcome = restore_latest_backup(dry_run=True)
    record = {"at": _now_iso(), "ok": bool(outcome.get("ok")),
              "backup_id": outcome.get("backup_id"),
              "message": outcome.get("message", "")}
    state = load_state()
    state.setdefault("drills", []).append(record)
    save_state(state)
    return record


def set_offsite_target(target: str) -> Dict[str, Any]:
    """配异地/外盘备份目标位置（只配位置，不假装已自动异地）。"""
    cleaned = str(target or "").strip()
    if not cleaned:
        raise ValueError("请先填异地备份的目标位置（比如另一个盘的文件夹或 U 盘路径）")
    state = load_state()
    state["offsite"] = {"target": cleaned, "last_triggered_at": None,
                        "last_result": None, "automatic": False}
    save_state(state)
    return state["offsite"]


def trigger_offsite_backup() -> Dict[str, Any]:
    """手动触发一次异地备份：把最近一次自动备份拷到目标位置，并记状态。"""
    from ..backup.auto_backup import AUTO_BACKUP_SUBDIR
    from ..backup.backup_manager import BackupManager

    offsite = load_state().get("offsite", {})
    target = str(offsite.get("target") or "").strip()
    if not target:
        return {"ok": False, "message": "还没配异地备份的目标位置：先在备份页填一个目标位置（另一个盘或 U 盘），再点手动备份。"}
    manager = BackupManager(backup_dir=_data_dir() / "backups" / AUTO_BACKUP_SUBDIR)
    latest = manager.latest_backup()
    if latest is None or not latest.file_path:
        return {"ok": False, "message": "没有可拷的备份：先等自动备份跑一次。"}
    try:
        dest_dir = Path(target)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(str(latest.file_path)).name
        shutil.copy2(str(latest.file_path), str(dest))
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "message": f"异地备份没拷过去：{exc}。请检查目标位置写得对不对、盘在不在。"}
        state = load_state()
        state["offsite"] = {**offsite, "last_triggered_at": _now_iso(), "last_result": result}
        save_state(state)
        return result
    result = {"ok": True, "message": f"已把最近一次备份拷到异地位置：{dest}。注意：这只是手动触发的一次，自动异地还没做。"}
    state = load_state()
    state["offsite"] = {**offsite, "target": target, "last_triggered_at": _now_iso(),
                        "last_result": result, "automatic": False}
    save_state(state)
    return result


# ---------------------------------------------------------------------------
# 第 18 项：多设备同步一套数据
# ---------------------------------------------------------------------------

def record_sync(account_id: str, device_id: str, *, base_version: Optional[int] = None) -> Dict[str, Any]:
    """多设备同步：同一账号只存一份数据（本来就是一份 sqlite，不按设备分）。

    后到的写覆盖先到的（last-write-wins），冲突留一条记录：调用方传
    ``base_version``，与当前版本对不上就记“冲突：后到的为准”。
    """
    account = str(account_id)
    device = str(device_id or "unknown")
    state = load_state()
    sync = state.setdefault("sync", {})
    record = sync.get(account, {"version": 0, "conflicts": [], "devices": []})
    conflict = None
    if base_version is not None and int(base_version) != int(record.get("version", 0)):
        conflict = {
            "at": _now_iso(), "device_id": device, "base_version": int(base_version),
            "current_version": int(record.get("version", 0)),
            "resolution": "后到的为准（last-write-wins），先到的那份被覆盖了",
        }
        record.setdefault("conflicts", []).append(conflict)
    record["version"] = int(record.get("version", 0)) + 1
    record["last_device"] = device
    record["last_at"] = _now_iso()
    if device not in record.setdefault("devices", []):
        record["devices"].append(device)
    sync[account] = record
    save_state(state)
    return {
        "account_id": account, "version": record["version"],
        "conflict": conflict,
        "message": (
            "同步冲突：你这台设备的数据是旧版本，我按后到的为准覆盖了，冲突已记下来。"
            if conflict else "已同步：同一账号所有设备看到的都是同一份数据。"
        ),
    }


def sync_state(account_id: str) -> Dict[str, Any]:
    record = load_state().get("sync", {}).get(str(account_id))
    if record is None:
        return {"account_id": str(account_id), "version": 0, "devices": [],
                "conflicts": [], "message": "这个账号还没在多设备上同步过。"}
    return {"account_id": str(account_id), **record}


# ---------------------------------------------------------------------------
# 第 17 项：恢复码提醒＋恢复入口
# ---------------------------------------------------------------------------

RECOVERY_REMINDER_TEXT = (
    "请把恢复码记好（抄在纸上或存在你信任的地方）：它是忘记密码时唯一能"
    "重置密码的东西，只在建账号成功时显示这一次，丢了就找不回来。"
)

def recovery_entry() -> Dict[str, Any]:
    """恢复入口说明：接线已有恢复码重置机制（登录页“忘记密码 → 恢复码重置”），
    不重写认证核心。远程重置只在服务端显式允许时才放行，见 accounts.py。"""
    return {
        "entry": "登录页 → “忘记密码” → “用恢复码重置”",
        "local_only_by_default": True,
        "reminder": RECOVERY_REMINDER_TEXT,
        "message": "恢复码只在建账号成功时显示一次；重置密码成功后旧恢复码即失效，请记好新码。",
    }


def ack_recovery_reminder(account_id: str) -> Dict[str, Any]:
    state = load_state()
    state.setdefault("recovery_ack", {})[str(account_id)] = {
        "acked_at": _now_iso(), "reminder": RECOVERY_REMINDER_TEXT,
    }
    save_state(state)
    return {"account_id": str(account_id), "acked": True,
            "message": "已记下你保存好了恢复码，下次不再提醒这一条。"}


# ---------------------------------------------------------------------------
# 第 19 项：每天早上 8 点推今天待办（走现有例程机制）
# ---------------------------------------------------------------------------

def build_daily_digest_text(owner: str) -> str:
    """今天待办的结果汇总（只报结果，不推过程）。

    内容来自真实记录：今天到点的例程＋未处理的三类通知；没有就是老实
    的“今天没待办”，不编。
    """
    lines = ["今天的待办汇总："]
    try:
        from ..tasks.routines import get_routine_store

        store = get_routine_store()
        routines = store.list_routines(owners=[str(owner)], limit=100)
        today = datetime.now(timezone.utc).date()
        due = []
        for routine in routines:
            nxt = routine.get("next_run_at")
            if nxt and datetime.fromtimestamp(float(nxt), tz=timezone.utc).date() == today:
                due.append(str(routine.get("name") or routine.get("routine_id")))
        if due:
            lines.append("今天要跑的例程：" + "、".join(due))
    except Exception:  # noqa: BLE001 - 例程库读不到时不让汇总整个失败
        logger.warning("daily digest could not read routines", exc_info=True)
    pending = [n for n in list_notices(owner, only_unacked=True) if n["kind"] in ("stuck", "needs_decision")]
    if pending:
        lines.append("还需要你处理的：")
        for notice in pending[:10]:
            lines.append(f"· {notice['title']}——{notice['body']}")
    if len(lines) == 1:
        lines.append("今天没有待办。")
    return "\n".join(lines)


def ensure_morning_digest(owner: str, *, created_by: Optional[str] = None) -> Dict[str, Any]:
    """确保“每天早上 8 点待办汇总”例程存在（走现有例程机制，不新造定时系统）。"""
    from ..tasks.routines import ensure_morning_digest_routine

    routine = ensure_morning_digest_routine(
        owner_account_id=str(owner), created_by=str(created_by or owner),
    )
    return {
        "routine_id": routine.get("routine_id"),
        "name": routine.get("name"),
        "schedule": routine.get("schedule"),
        "timezone": routine.get("timezone"),
        "enabled": routine.get("enabled"),
        "message": "每天早上 8 点的待办汇总已开着（走例程机制）；设置里关掉“每天待办汇总”就能停。",
    }


# ---------------------------------------------------------------------------
# 第 8 项：错过规则文本（实现在 src/tasks/routines.py）
# ---------------------------------------------------------------------------

MISSED_RUN_RULE = _MISSED_RULE_TEXT


# ---------------------------------------------------------------------------
# 第 12 项：主线说明（文档见 docs/knowledge-memory-mainline.md）
# ---------------------------------------------------------------------------

MAINLINE = {
    "knowledge": {
        "mainline": "src/api/routes/knowledge.py（网关挂载的 /v1/knowledge，真实 RAG 管线）",
        "deprecated": [
            "src/api/knowledge_api.py：未挂进网关的旧壳，语义搜索端点已删，只留文档说明；"
            "不要再往里面加功能。",
        ],
    },
    "memory": {
        "mainline": "src/gateway/account_memory.py（按账号隔离的 sqlite 记忆，主线）",
        "deprecated": [
            "src/api/memory_api.py：LEGACY 内存脚手架，未挂进网关，只留给旧测试；不要再往里面加功能。",
            "src/knowledge/memory.py / memory_system.py：知识库内部的记忆组件，"
            "只作为 RAG 内部使用，不再作为面向用户的第二套记忆入口；新记忆一律走主线。",
        ],
    },
}


# ---------------------------------------------------------------------------
# HTTP 路由（/v1/a2a；挂载见 src/gateway/main.py）
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, WebSocket
from pydantic import BaseModel, Field

from .account_context import require_account_principal

router = APIRouter(prefix="/v1/a2a", tags=["a2a"])


def _principal(context=Depends(require_account_principal)):
    return context


# 哨兵：区分 PUT 里“没传 theme”（不动）与“传了 null”（清掉手动主题）。
_SETTING_UNSET = object()


class SettingsBody(BaseModel):
    language: Optional[str] = None
    timezone: Optional[str] = None
    daily_digest_enabled: Optional[bool] = None
    cost_routing_enabled: Optional[bool] = None
    theme: Any = _SETTING_UNSET
    notifications_read: Optional[List[str]] = None
    expected_version: Optional[int] = None


class MemoryImportJsonBody(BaseModel):
    filename: str = "import.json"
    content: str = ""


class InviteBody(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    ttl_days: int = DEFAULT_INVITE_TTL_DAYS
    role: str = "viewer"


class OffsiteBody(BaseModel):
    target: str = ""


class SyncBody(BaseModel):
    device_id: str = ""
    base_version: Optional[int] = None


class DispatchBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    bot_id: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class NoticeBody(BaseModel):
    kind: str = ""
    title: str = ""
    body: str = ""


class CapBody(BaseModel):
    bot_id: str = ""
    cap_usd: Optional[float] = None


@router.get("/settings")
def get_settings(context=Depends(_principal)) -> Dict[str, Any]:
    return get_account_settings(context.principal)


@router.put("/settings")
def put_settings(body: SettingsBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_account_settings(
            context.principal,
            language=body.language,
            timezone=body.timezone,
            daily_digest_enabled=body.daily_digest_enabled,
            cost_routing_enabled=body.cost_routing_enabled,
            **({"theme": body.theme} if body.theme is not _SETTING_UNSET else {}),
            notifications_read=body.notifications_read,
            expected_version=body.expected_version,
        )
    except SettingsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SETTINGS_CONFLICT",
                "message": "另一台设备刚才改了设置，这次没写进去——已按最新的来，没悄悄说已同步。",
                "current": exc.current,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/memory/import")
async def import_memory_file(file: UploadFile = File(...),
                             context=Depends(_principal)) -> Dict[str, Any]:
    content = await file.read()
    return import_memories(content, file.filename or "import", context.principal)


@router.post("/memory/import-json")
def import_memory_json(body: MemoryImportJsonBody,
                       context=Depends(_principal)) -> Dict[str, Any]:
    return import_memories(body.content.encode("utf-8"), body.filename, context.principal)


@router.get("/memory/recall")
def recall_memory(q: str = Query("", description="聊天关键词"),
                  limit: int = Query(5, ge=1, le=20),
                  context=Depends(_principal)) -> Dict[str, Any]:
    return {"query": q, "entries": recall_memories(context.principal, q, limit)}


@router.get("/push/contract")
def get_push_contract() -> Dict[str, Any]:
    return PUSH_CONTRACT


@router.get("/overview")
def get_overview_snapshot() -> Dict[str, Any]:
    """推送断线后的轮询回落口：返回最后一次推送的快照；没有就老实说没有。"""
    last = dict(_HUB._last)
    return {
        "snapshot_available": bool(last),
        "overview": last.get("overview.snapshot") or last.get("overview.updated"),
        "team": last.get("team.snapshot") or last.get("team.updated"),
        "note": "没有快照时 tasks/bots 均为 null/[]，不等同于“没有任务”；请以总览/小队页的真实接口为准。",
    }


@router.websocket("/ws/status")
async def status_websocket(websocket: WebSocket) -> None:
    """总览/小队状态推送。先发 snapshot，重连也一样；断了按合同回落轮询。

    注意（D 组 2026-11-14 如实记录，未改）：本端点目前不验 Origin、不收令牌，
    只靠外层的 HostGuard（已覆盖 websocket scope，伪造 Host 会被 close 1008
    拒绝）+ 网关默认只绑回环。跨站页面直连回环端口仍可读快照——后续要么在端点
    加 Origin 校验、要么走 ?token= 鉴权（桌宠已发 Bearer，浏览器端订阅要改），
    由总协调定，不在本轮悄悄改掉订阅契约。
    """
    await websocket.accept()
    q = _HUB.subscribe()
    try:
        push_overview({"tasks": None, "bots": [], "source": "snapshot"}, snapshot=True)
        push_team({"members": [], "source": "snapshot"}, snapshot=True)
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(None, q.get, True, 30)
            except Exception:  # noqa: BLE001 - 30 秒没新事件就发一次心跳
                try:
                    await websocket.send_json({"type": "heartbeat", "at": _now_iso()})
                except Exception:  # noqa: BLE001
                    break
                continue
            try:
                await websocket.send_json(event)
            except Exception:  # noqa: BLE001
                break
            try:
                incoming = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                if isinstance(incoming, dict) and incoming.get("ping"):
                    await websocket.send_json({"pong": True})
            except Exception:  # noqa: BLE001 - 没消息是常态
                pass
    finally:
        _HUB.unsubscribe(q)


@router.get("/backup/status")
def get_backup_status(context=Depends(_principal)) -> Dict[str, Any]:
    return backup_status()


@router.get("/backup/list")
def get_backup_list(context=Depends(_principal)) -> Dict[str, Any]:
    return list_local_backups()


class RestoreBody(BaseModel):
    backup_id: str = ""
    collection: str = "auto"


@router.post("/backup/restore")
def post_restore_by_id(body: RestoreBody, context=Depends(_principal)) -> Dict[str, Any]:
    outcome = restore_backup_by_id(body.backup_id, collection=body.collection or "auto")
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("message", "恢复没成功"))
    return outcome


@router.post("/backup/restore-latest")
def post_restore_latest(context=Depends(_principal)) -> Dict[str, Any]:
    outcome = restore_latest_backup()
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("message", "恢复没成功"))
    return outcome


@router.post("/backup/drill")
def post_restore_drill(context=Depends(_principal)) -> Dict[str, Any]:
    return run_restore_drill()


def run_backup_now() -> Dict[str, Any]:
    """手动立刻备一份：跟每天 03:00 的自动备份同一份代码、同一位置。

    备份页一直写着“或者手动备一份”，但以前根本没有这个口子——自动备份
    又要等半夜，页上永远是“最近还没备份过”。失败时抛异常，由调用方
    转成 409 人话，不静默吞掉。
    """
    from ..backup.auto_backup import run_daily_auto_backup

    meta = run_daily_auto_backup(_data_dir(), description="手动备份（备份页“立即备份一次”）")
    return {
        "ok": True,
        "backup_id": meta.backup_id,
        "timestamp": meta.timestamp,
        "size_bytes": meta.size_bytes,
        "message": f"已备好一份备份（{meta.backup_id}），大小 {meta.size_bytes} 字节。",
    }


@router.post("/backup/run-now")
def post_run_backup_now(context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return run_backup_now()
    except Exception as exc:  # noqa: BLE001 - 失败原因要原样告诉页面
        raise HTTPException(status_code=409, detail=f"备份没跑成：{exc}") from exc


@router.get("/backup/drills")
def get_restore_drills(context=Depends(_principal)) -> Dict[str, Any]:
    return {"drills": load_state().get("drills", [])}


@router.post("/backup/offsite")
def post_offsite(body: OffsiteBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_offsite_target(body.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/backup/offsite/trigger")
def post_offsite_trigger(context=Depends(_principal)) -> Dict[str, Any]:
    outcome = trigger_offsite_backup()
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("message", "异地备份没成功"))
    return outcome


@router.post("/invites")
def post_invite(body: InviteBody, context=Depends(_principal)) -> Dict[str, Any]:
    """邀请链接入口已停用（Joseph 2026-11-14 新规矩：注册改走自助注册＋
    主账号批准，不再发邀请链接）。

    只关 HTTP 入口：底层的 ``create_invite`` / ``invite_status`` /
    ``redeem_invite`` 函数保留不动（历史数据与单测仍可用），随时可恢复。
    """
    raise HTTPException(
        status_code=410,
        detail={"code": "INVITE_DISABLED", "message": "邀请链接已停用，请走注册＋批准。"},
    )


@router.get("/invites/{token}")
def get_invite(token: str) -> Dict[str, Any]:
    """邀请链接查询入口已停用（同上，只关入口，不删底层函数）。"""
    raise HTTPException(
        status_code=410,
        detail={"code": "INVITE_DISABLED", "message": "邀请链接已停用，请走注册＋批准。"},
    )


@router.post("/invites/{token}/redeem")
def post_redeem_invite(token: str) -> Dict[str, Any]:
    """邀请链接兑换入口已停用（同上，只关入口，不删底层函数）。"""
    raise HTTPException(
        status_code=410,
        detail={"code": "INVITE_DISABLED", "message": "邀请链接已停用，请走注册＋批准。"},
    )


@router.get("/usage/bot/{bot_id}")
def get_bot_usage(bot_id: str, month: Optional[str] = Query(None),
                  context=Depends(_principal)) -> Dict[str, Any]:
    return bot_usage_summary(bot_id, month=month)


@router.post("/usage/caps")
def post_cap(body: CapBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_monthly_cap(body.bot_id, body.cap_usd, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/usage/caps/{bot_id}")
def get_cap(bot_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    return check_monthly_cap(bot_id)


@router.get("/plugins/lazy-status")
def get_plugins_lazy_status(ids: str = Query("", description="逗号分隔的插件 id"),
                            context=Depends(_principal)) -> Dict[str, Any]:
    wanted = [p.strip() for p in ids.split(",") if p.strip()]
    return {"plugins": list_plugin_lazy_status(wanted),
            "note": "核心插件启动时加载，其余用时再加载；加载失败不拖垮启动，界面会老实显示未加载。"}


@router.post("/plugins/{plugin_id}/ensure-loaded")
def post_ensure_plugin(plugin_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    def _loader() -> None:
        from ..plugins.registry import get_plugin_registry

        if not get_plugin_registry().plugin_exists(plugin_id):
            raise RuntimeError(f"注册表里没有这个插件：{plugin_id}")

    return ensure_plugin_loaded(plugin_id, _loader)


@router.get("/dispatch/status")
def get_dispatch_status(context=Depends(_principal)) -> Dict[str, Any]:
    return DISPATCH_QUEUE.status()


@router.post("/dispatch/submit")
def post_dispatch(body: DispatchBody, context=Depends(_principal)) -> Dict[str, Any]:
    return DISPATCH_QUEUE.submit({"title": body.title, "bot_id": body.bot_id,
                                  "payload": body.payload, "owner": context.principal})


@router.get("/notifications")
def get_notifications(only_unacked: bool = Query(False),
                      context=Depends(_principal)) -> Dict[str, Any]:
    return {"notifications": list_notices(context.principal, only_unacked=only_unacked),
            "note": "只推三类：做完了 / 卡住了 / 要他拍板；过程流水不推。"}


@router.post("/notifications")
def post_notice(body: NoticeBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return push_notice(body.kind, body.title, body.body, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/notifications/{notice_id}/ack")
def post_ack_notice(notice_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    return {"acked": ack_notice(notice_id)}


@router.get("/startup-check")
def get_startup_check(context=Depends(_principal)) -> Dict[str, Any]:
    checks = run_startup_checks()
    return {
        "checks": checks,
        "overall": "fail" if any(c["status"] == "fail" for c in checks)
                   else ("not_configured" if any(c["status"] == "not_configured" for c in checks) else "pass"),
        "note": "每项只有三种老实状态：通过 / 失败 / 未配；“没检查”不会写成“通过”。",
    }


@router.get("/errors/humanize")
def get_human_error(text: str = Query(""), context=Depends(_principal)) -> Dict[str, Any]:
    return humanize_error(text)


@router.get("/recovery/entry")
def get_recovery_entry(context=Depends(_principal)) -> Dict[str, Any]:
    return recovery_entry()


@router.post("/recovery/reminder-ack")
def post_recovery_ack(context=Depends(_principal)) -> Dict[str, Any]:
    return ack_recovery_reminder(context.principal)


@router.get("/sync/state")
def get_sync_state(context=Depends(_principal)) -> Dict[str, Any]:
    return sync_state(context.principal)


@router.post("/sync/record")
def post_sync_record(body: SyncBody, context=Depends(_principal)) -> Dict[str, Any]:
    return record_sync(context.principal, body.device_id, base_version=body.base_version)


@router.post("/digest/ensure")
def post_digest_ensure(context=Depends(_principal)) -> Dict[str, Any]:
    return ensure_morning_digest(context.principal, created_by=context.principal)


@router.get("/digest/today")
def get_digest_today(context=Depends(_principal)) -> Dict[str, Any]:
    return {"text": build_daily_digest_text(context.principal)}


@router.get("/routines/missed-rule")
def get_missed_rule() -> Dict[str, Any]:
    return {"rule": MISSED_RUN_RULE}


@router.get("/mainline")
def get_mainline() -> Dict[str, Any]:
    return MAINLINE
