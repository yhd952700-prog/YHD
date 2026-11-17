"""A2b：后端第二批（追加十点＋日常十条后端＋规矩＋A1 遗留＋A2a 缺口）。

与 ``a2a.py`` 同款做法：本批新增的小状态统一存在一份 ``a2b_state.json``
（``LIUHAO_A2B_STATE_FILE`` 可覆盖），写时用 ``atomic_write_text`` +
文件锁；凡是“没配好、没接通、没数据”的情况一律返回老实空状态，绝不
编数字、绝不把“没检查”写成“通过”。

包含（与任务书一一对应）：
A1. 多人对话理解记录（群聊/多人会话：谁参与了什么、谁说了什么、谁负责哪部分）
A2. 搁置任务/缺失材料主动承接（台账检测＋通知 Joseph，缺材料不瞎猜）
A3. 任意 Bot 协调别的 Bot（向下分派子任务，仍走审批流程）
A4. 人类判断拉人进流程（带着上下文问，等答复再继续；等时标“等你确认”）
A5. 网页/电脑操作更快更可靠（重试上限 3 次、超时、失败说人话、每步留记录）
A6. 动真格前核查审批记录（查了什么、结论是什么，记进核查记录）
A7. 只报结果规矩落到通知逻辑（过程事件不推，只推做完了/卡住了/要他拍板）
B8. 听不清/拿不准先问一句再干（可配置信度阈值，低于阈值走确认问句）
B9. 一句话找东西（跨聊天/文件/任务召回，标明来源，没找到老实说）
B10. 换电脑一键搬家（导出/导入迁移；Key 绝不随包走；冲突重命名或跳过并记一条）
B11. 文件自动归类（规则归类，标明是规则分的；分错了可改）
B12. 到点提醒休息（熬夜、饭点轻声提醒，走现有例程机制，设置里能关，默认开）
B13. 多设备只响一边（选一个在线设备响，别的设备静默记一条）
B14. 晚上今日小结（每天晚上发一条，走现有例程机制；数字只从真实台账出）
C15. 删文件审批人（提交人不是 Owner 时须 Owner 批；Owner 删自己文件可自批）
     —— 落在 ``src/gateway/files.py``＋``decision_approval.py`` 的 approvers 参数
D16. 推送真实发送点接线（总览/小队真实状态＋任务生命周期/审批/提醒/待办/
     Bot 干活状态事件；没接好的事件不发）

持久化：除例程（routines sqlite）、记忆（主线 sqlite）、会话
（conversation store）、审批（workflow store）各自原有存储外，本批
新增的小状态（多人记录、承接记录、分派记录、等人记录、操作日志、
核查记录、置信度阈值、搬家记录、归类覆盖、设备在线、Bot 状态）
统一存在 ``a2b_state.json``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 操作重试上限（第 5 项）：失败最多重试 3 次（含第一次），退避 1s/2s/4s。
OP_MAX_ATTEMPTS = 3
#: 本机命令默认超时（秒）：超过就停掉这次尝试、按失败重试，不无限等。
OP_SHELL_TIMEOUT_SECONDS = 60.0
#: 电脑/浏览器操作默认超时（秒）。
OP_COMPUTER_TIMEOUT_SECONDS = 30.0
#: 搁置判定：超过这么久没进展就判搁置（可配，默认 48 小时）。
STALLED_DEFAULT_HOURS = 48.0
#: 语音/文字置信度阈值默认值（第 8 项）：低于它先问一句再干。
CLARIFY_DEFAULT_THRESHOLD = 0.6
#: 搬家导出格式（第 10 项；格式说明见 docs/a2b-migration-format.md）。
MIGRATION_FORMAT = "jarvis-migration"
MIGRATION_VERSION = 1

#: Bot 干活状态（第 16 项；桌宠订阅用）。
BOT_STATUSES = ("thinking", "working", "researching", "waiting_confirmation", "done", "error")
BOT_STATUS_LABELS = {
    "thinking": "思考中",
    "working": "在办",
    "researching": "在研究",
    "waiting_confirmation": "等确认",
    "done": "干完",
    "error": "出错",
}

#: 本批新增的推送事件（第 16 项；合同机器可读版见 GET /v1/a2b/push/events）。
A2B_PUSH_EVENTS = [
    {"name": "task.lifecycle", "when": "任务状态变化时（创建/开始/完成/卡住/接管/等人）",
     "payload": {"type": "task.lifecycle", "at": "ISO 时间",
                 "data": {"task_id": "任务 id", "event": "created|started|done|stuck|taken_over|waiting_human",
                          "status": "任务当前状态", "title": "任务标题（有才给）"}}},
    {"name": "approval.changed", "when": "审批结论变化时（通过/拒绝/过期）",
     "payload": {"type": "approval.changed", "at": "ISO 时间",
                 "data": {"approval_id": "审批 id", "operation": "操作名", "status": "approved|rejected|expired|pending"}}},
    {"name": "reminder.due", "when": "到点提醒（饭点/熬夜）发到聊天时",
     "payload": {"type": "reminder.due", "at": "ISO 时间",
                 "data": {"kind": "lunch|late_night", "text": "提醒内容"}}},
    {"name": "digest.ready", "when": "早上待办汇总 / 晚上今日小结发到聊天时",
     "payload": {"type": "digest.ready", "at": "ISO 时间",
                 "data": {"kind": "morning|evening", "text": "汇总内容"}}},
    {"name": "bot.status", "when": "Bot 干活状态变化时（思考/在办/在研究/等确认/干完/出错＋进度）",
     "payload": {"type": "bot.status", "at": "ISO 时间",
                 "data": {"bot_id": "Bot id", "status": "thinking|working|researching|waiting_confirmation|done|error",
                          "progress": "0-100 的进度，没进度就是 null", "label": "人话状态"}}},
]

#: 动真格的操作清单（第 6 项核查的范围）。
SENSITIVE_OPERATIONS = ("send_message", "delete_file", "spend_money", "modify_code", "system_change")

_STATE_ENV = "LIUHAO_A2B_STATE_FILE"
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 状态文件
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from ..common.data_paths import resolve_store_path

    return resolve_store_path(
        "a2b_state.json",
        os.path.join("data", "a2b_state.json"),
        env_var=_STATE_ENV,
    )


def _default_state() -> Dict[str, Any]:
    return {
        "participants": {},
        "stalled": [],
        "delegations": [],
        "human_waits": {},
        "op_log": [],
        "approval_checks": [],
        "clarify_threshold": CLARIFY_DEFAULT_THRESHOLD,
        "migration_log": [],
        "file_groups": {},
        "devices": {},
        "bot_status": {},
        "rest_reminders_enabled": True,
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
        with exclusive_file_lock(f"{path}.lock", description="A2b 状态"):
            atomic_write_text(
                path,
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event_type: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """往 A2a 的推送总线发一个事件；没接好就记日志、不让调用方失败。"""
    try:
        from .a2a import publish_push

        return publish_push(event_type, data=data)
    except Exception:  # noqa: BLE001 - 推送失败不影响业务动作
        logger.warning("a2b push %s failed", event_type, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# A1. 多人对话理解记录
# ---------------------------------------------------------------------------

def record_turn(
    session_id: str,
    speaker: str,
    text: str,
    *,
    responsible: Optional[Sequence[str]] = None,
    owner: str = "",
) -> Dict[str, Any]:
    """记一轮多人对话：谁说了什么、负责哪部分。

    只记调用方传进来的真实内容：没记下来的参与者和分工就是空的，
    绝不反推、绝不编造“谁负责哪部分”。
    """
    session = str(session_id or "").strip()
    who = str(speaker or "").strip()
    if not session or not who:
        raise ValueError("会话 id 和说话人不能为空")
    body = str(text or "").strip()
    parts = [str(p).strip() for p in (responsible or []) if str(p).strip()]
    state = load_state()
    sessions = state.setdefault("participants", {})
    record = sessions.setdefault(session, {
        "session_id": session, "owner": str(owner or ""),
        "participants": {}, "updated_at": _now_iso(),
    })
    people = record.setdefault("participants", {})
    person = people.setdefault(who, {"name": who, "said": [], "responsible": []})
    if body:
        said = person.setdefault("said", [])
        said.append({"text": body, "at": _now_iso()})
        del said[: max(0, len(said) - 50)]  # 每人只留最近 50 句，状态文件不无限长
    for part in parts:
        if part not in person.setdefault("responsible", []):
            person["responsible"].append(part)
    record["updated_at"] = _now_iso()
    save_state(state)
    return {"session_id": session, "speaker": who,
            "said_count": len(person.get("said", [])),
            "responsible": list(person.get("responsible", []))}


def session_participants(session_id: str) -> Dict[str, Any]:
    """某会话的参与者记录：每人说了几句、负责哪部分；没记过就是空列表。"""
    record = load_state().get("participants", {}).get(str(session_id or ""))
    if not record:
        return {"session_id": str(session_id or ""), "participants": [],
                "message": "这个会话还没记过多人对话，谁参与了什么还没记下来。"}
    people = []
    for name, person in (record.get("participants") or {}).items():
        people.append({
            "name": name,
            "said_count": len(person.get("said", [])),
            "last_said": (person.get("said") or [{}])[-1].get("text", "") if person.get("said") else "",
            "responsible": list(person.get("responsible") or []),
        })
    people.sort(key=lambda p: p["said_count"], reverse=True)
    return {"session_id": record.get("session_id"), "participants": people}


# ---------------------------------------------------------------------------
# A2. 搁置任务 / 缺失材料主动承接
# ---------------------------------------------------------------------------

#: 终态：这些状态的任务不算搁置（已经完了，不用再推）。
_TERMINAL_TASK_STATUSES = ("completed", "cancelled")


def detect_stalled_tasks(
    ledger: Any,
    *,
    stale_hours: float = STALLED_DEFAULT_HOURS,
    owner: str = "",
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """从任务台账里找出被搁置的任务：长时间无进展，或失败了没人处理。

    判定只看台账里的真实字段（状态＋最后更新时间）：没更新时间就
    不判搁置（不猜），只列进 ``unknown`` 备注，不编一个“搁置”结论。
    """
    moment = float(now if now is not None else time.time())
    threshold = float(stale_hours) * 3600.0
    try:
        page = ledger.list_tasks(owners=[owner] if owner else None, limit=500)
    except Exception as exc:  # noqa: BLE001 - 台账读不到时返回空，不编搁置清单
        logger.warning("a2b stalled detection could not read ledger: %s", exc)
        return []
    items = page.get("items", []) if isinstance(page, dict) else list(page or [])
    stalled: List[Dict[str, Any]] = []
    for task in items:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "")
        if status in _TERMINAL_TASK_STATUSES:
            continue
        task_id = str(task.get("task_id") or "")
        title = str(task.get("title") or task_id)
        updated_raw = task.get("updated_at") or ""
        updated_epoch: Optional[float] = None
        if updated_raw:
            try:
                updated_epoch = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00")).timestamp()
            except ValueError:
                updated_epoch = None
        if status == "failed":
            stalled.append({"task_id": task_id, "title": title, "status": status,
                            "reason": "任务失败了，还没人处理",
                            "stalled_hours": None})
            continue
        if updated_epoch is None:
            continue  # 没更新时间：不判，别猜
        idle = moment - updated_epoch
        if idle >= threshold:
            stalled.append({"task_id": task_id, "title": title, "status": status,
                            "reason": f"已经 {idle / 3600:.1f} 小时没有进展",
                            "stalled_hours": round(idle / 3600, 1)})
    return stalled


def takeover_stalled(task_id: str, bot_id: str, *, owner: str = "") -> Dict[str, Any]:
    """Bot 主动把搁置任务接过来推进：记一条承接记录，并推一条“卡住了”
    的结果通知给 Joseph（只报结果，不推过程）。

    注意：这里只记“谁接了、什么时候接的”，不碰台账的负责人字段——
    台账接管（``TaskLedger.takeover``）是把任务转给人类操作员用的，
    语义不对，不能借用；真正的推进走派活/小队流程。
    """
    from .a2a import report_stuck

    record = {"task_id": str(task_id), "bot_id": str(bot_id), "owner": str(owner or ""),
              "taken_over_at": _now_iso(), "status": "taken_over"}
    state = load_state()
    state.setdefault("stalled", []).append(record)
    save_state(state)
    _emit("task.lifecycle", {"task_id": str(task_id), "event": "taken_over",
                             "status": "taken_over", "bot_id": str(bot_id)})
    try:
        report_stuck(f"搁置的任务 {task_id}", f"已经很久没进展，{bot_id} 先接过来推进了，缺材料会再来问你。", owner=str(owner or ""))
    except Exception:  # noqa: BLE001 - 通知失败不影响承接记录
        logger.warning("a2b takeover notice failed", exc_info=True)
    return record


def report_missing_material(task_id: str, missing: str, *, owner: str = "") -> Dict[str, Any]:
    """缺材料先通知 Joseph：缺什么、缺了就停下来等他补，补上了再继续，
    不要闷头瞎猜。走 A2a 的结果通知（要他拍板那类）。"""
    from .a2a import report_needs_decision

    what = str(missing or "").strip()
    if not what:
        raise ValueError("请先写清楚缺的是什么材料")
    notice = report_needs_decision(
        f"任务 {task_id} 缺材料，停下来等你：缺的是 {what}。补上之后我再继续，不会自己猜着干。",
        owner=str(owner or ""),
    )
    state = load_state()
    state.setdefault("stalled", []).append({
        "task_id": str(task_id), "missing": what, "owner": str(owner or ""),
        "reported_at": _now_iso(), "status": "waiting_material",
        "notice_id": notice.get("id"),
    })
    save_state(state)
    return {"task_id": str(task_id), "missing": what, "notice_id": notice.get("id"),
            "message": "已经告诉 Joseph 缺什么了，等他补上再继续，不会自己猜着干。"}


# ---------------------------------------------------------------------------
# A3. 任意 Bot 协调别的 Bot
# ---------------------------------------------------------------------------

def delegate_subtask(
    *,
    from_bot: str,
    to_bot: str,
    title: str,
    reason: str = "",
    submitted_by: str,
    owner: str,
) -> Dict[str, Any]:
    """成员 Bot 协调别的 Bot：向下分派子任务，仍走审批流程。

    队长派活之外，成员 Bot 也能分派——但分派本身要先进审批中心：
    建一条 ``team.subtask`` 审批待办，等 Joseph 点了通过再真正派下去，
    不批不派。执行仍走小队派活，不在这里另起一套执行器。
    """
    from .decision_approval import TEAM_DEF_ID, create_decision_approval

    title_clean = str(title or "").strip()
    if not title_clean:
        raise ValueError("子任务标题不能为空")
    if str(from_bot or "").strip() == str(to_bot or "").strip():
        raise ValueError("不能把子任务派给自己")
    created = create_decision_approval(
        title=f"Bot 分派子任务：{title_clean}",
        operation="team.subtask",
        reason=(
            f"{from_bot} 想把子任务「{title_clean}」派给 {to_bot}。"
            + (f"原因：{reason}" if str(reason or "").strip() else "（没写原因）")
            + "通过后才真正派下去。"
        ),
        submitted_by=str(submitted_by or from_bot),
        owner_account_id=str(owner),
        def_id=TEAM_DEF_ID,
        extra_inputs={"from_bot": str(from_bot), "to_bot": str(to_bot), "title": title_clean},
    )
    record = {"delegation_id": str(uuid.uuid4()), "from_bot": str(from_bot),
              "to_bot": str(to_bot), "title": title_clean,
              "approval_id": created["approval_id"], "run_id": created["run_id"],
              "status": "pending_approval", "created_at": _now_iso()}
    state = load_state()
    state.setdefault("delegations", []).append(record)
    save_state(state)
    _emit("approval.changed", {"approval_id": created["approval_id"],
                               "operation": "team.subtask", "status": "pending"})
    return record


def delegation_status(approval_id: str) -> Dict[str, Any]:
    """查一条 Bot 分派的审批结论；不存在返回老实的 not_found。"""
    from .decision_approval import decision_status

    approval = decision_status(approval_id)
    if approval is None:
        return {"approval_id": str(approval_id), "status": "not_found",
                "message": "没有这条分派审批。"}
    return {"approval_id": str(approval_id),
            "status": str(approval.get("status") or ""),
            "operation": str(approval.get("operation") or ""),
            "message": {"pending": "还在等 Joseph 点确认，通过了才真正派下去。",
                        "approved": "已经通过，可以派下去了。",
                        "rejected": "被拒绝了，这次不派。",
                        "expired": "过期了，没批就作废了，这次不派。"}.get(
                            str(approval.get("status") or ""), "状态未知。")}


# ---------------------------------------------------------------------------
# A4. 人类判断拉人进流程
# ---------------------------------------------------------------------------

def request_human_decision(
    *,
    task_id: str,
    question: str,
    context_text: str = "",
    submitted_by: str,
    owner: str,
    person: str = "",
) -> Dict[str, Any]:
    """Bot 判断不了、要人拍板时：停下来，带着上下文问，等答复再继续。

    建一条 ``team.decision`` 审批待办（走已有审批机制），任务在等待
    记录里标“等你确认”；不要自己猜着干，也不要无限等——审批本身有
    7 天过期，过期自动失效（见 decision_approval）。
    """
    from .decision_approval import TEAM_DEF_ID, create_decision_approval

    question_clean = str(question or "").strip()
    if not question_clean:
        raise ValueError("要问的问题不能为空")
    who = str(person or owner or "").strip()
    created = create_decision_approval(
        title=f"要人拍板：{question_clean[:80]}",
        operation="team.decision",
        reason=(
            f"任务 {task_id} 做到一半，Bot 判断不了，停下来问你：{question_clean}"
            + (f"\n上下文：{context_text}" if str(context_text or "").strip() else "")
            + "\n等你答复了再继续，不会自己猜着干。"
        ),
        submitted_by=str(submitted_by),
        owner_account_id=str(owner),
        def_id=TEAM_DEF_ID,
        extra_inputs={"task_id": str(task_id), "question": question_clean,
                      "context": str(context_text or ""), "person": who},
    )
    record = {"task_id": str(task_id), "question": question_clean,
              "context": str(context_text or ""), "person": who,
              "approval_id": created["approval_id"], "run_id": created["run_id"],
              "status": "等你确认", "created_at": _now_iso()}
    state = load_state()
    state.setdefault("human_waits", {})[str(task_id)] = record
    save_state(state)
    _emit("task.lifecycle", {"task_id": str(task_id), "event": "waiting_human",
                             "status": "等你确认"})
    return record


def human_wait_status(task_id: str) -> Dict[str, Any]:
    """查某任务等人的状态：审批结论同步回来——批了就是“已批复，可继续”，
    拒绝/过期都老实说；没问过人就是“没在等”。"""
    record = load_state().get("human_waits", {}).get(str(task_id))
    if not record:
        return {"task_id": str(task_id), "status": "not_waiting",
                "message": "这个任务没在等人拍板。"}
    from .decision_approval import decision_status

    approval = decision_status(str(record.get("approval_id") or ""))
    if approval is None:
        return {**record, "status": "等你确认",
                "message": "还在等你拍板（审批记录找不到了，先按“在等”算，别自己猜着干）。"}
    status = str(approval.get("status") or "")
    label = {"pending": "等你确认", "approved": "已批复，可继续",
             "rejected": "被拒绝了", "expired": "过期了，没批就作废了"}.get(status, status)
    return {**record, "status": label, "approval_status": status,
            "message": {"pending": "还在等你拍板，任务停着，不会自己猜着干。",
                        "approved": "你已经批了，可以继续往下干。",
                        "rejected": "你拒绝了，按拒绝的意思办，别再往下干。",
                        "expired": "等太久过期了，别再按原来的意思干，要干重新问一次。"}.get(status, "")}


def list_human_waits(owner: str = "") -> List[Dict[str, Any]]:
    """所有在等人的任务（只列本账号的；没在等的就是空列表）。"""
    waits = load_state().get("human_waits", {})
    items = [human_wait_status(task_id) for task_id in sorted(waits)]
    if owner:
        items = [w for w in items if not w.get("person") or w.get("person") == owner
                 or str(w.get("person") or "") == ""]
    return items


# ---------------------------------------------------------------------------
# A5. 网页/电脑操作更快更可靠
# ---------------------------------------------------------------------------

def _log_operation(kind: str, label: str, attempt: int, ok: bool, detail: str) -> None:
    """每步操作留一条记录（最近 200 条）：什么时候、哪一步、成没成、为什么。"""
    try:
        state = load_state()
        log = state.setdefault("op_log", [])
        log.append({"at": _now_iso(), "kind": str(kind), "label": str(label)[:200],
                    "attempt": int(attempt), "ok": bool(ok), "detail": str(detail)[:500]})
        del log[: max(0, len(log) - 200)]
        save_state(state)
    except Exception:  # noqa: BLE001 - 日志写失败不影响操作本身
        logger.warning("a2b op log failed", exc_info=True)


def run_reliable_operation(
    kind: str,
    label: str,
    fn: Callable[[], Any],
    *,
    max_attempts: int = OP_MAX_ATTEMPTS,
    timeout_seconds: float = OP_SHELL_TIMEOUT_SECONDS,
    backoff_base_seconds: float = 1.0,
    retry_on_timeout: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """带重试/超时/人话失败提示的操作执行器。

    落点：本机命令（``automation._run_shell_command`` 的 ShellAdapter 执行）
    与电脑操作（``automation_action`` 的 backend.execute，含打开浏览器）。
    只在“没跑成”时重试：抛异常或超时才重试；命令本身跑完了、返回了
    非零退出码，那是“跑完了但没成”，不重试（重试会把副作用跑两遍）。
    ``retry_on_timeout=False`` 时超时只失败不重试——本机命令超时后
    底层的进程可能还在跑，重试等于把同一条命令跑两遍，所以 shell
    走这个口子。安全检查一律在调用方（闸门/审批/allowlist）先过，
    本函数不绕过任何检查。
    """
    from .a2a import humanize_error

    attempts = max(1, int(max_attempts))
    last_error = ""
    attempts_made = 0
    for attempt in range(1, attempts + 1):
        attempts_made = attempt
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(fn)
                result = future.result(timeout=float(timeout_seconds))
            _log_operation(kind, label, attempt, True, "成功。")
            return {"ok": True, "attempts": attempt, "result": result,
                    "message": "操作成功。"}
        except FuturesTimeoutError:
            last_error = f"操作超过 {timeout_seconds:g} 秒没回来，超时了"
            _log_operation(kind, label, attempt, False, last_error)
            if not retry_on_timeout:
                break
        except Exception as exc:  # noqa: BLE001 - 失败要记下来再决定要不要重试
            last_error = str(exc) or type(exc).__name__
            _log_operation(kind, label, attempt, False, last_error)
        if attempt < attempts:
            sleep(float(backoff_base_seconds) * (2 ** (attempt - 1)))
    human = humanize_error(last_error)
    return {"ok": False, "attempts": attempts_made, "error": last_error,
            "message": human.get("message") or f"操作失败：{last_error}。",
            "hint": f"试了 {attempts_made} 次都没成；先检查网络和目标在不在，再重试一次。"}


def list_operation_log(kind: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """操作记录（最近的在前）；没操作过就是空列表。"""
    log = load_state().get("op_log", [])
    if kind:
        log = [e for e in log if str(e.get("kind") or "") == kind]
    return list(reversed(log))[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# A6. 动真格前核查审批记录
# ---------------------------------------------------------------------------

def verify_approval_before_action(
    approval_id: str,
    operation: str,
    *,
    actor: str = "",
) -> Dict[str, Any]:
    """动真格的动作之前，先查审批记录：这件事在什么时候、被谁批过。

    没批过、批过期、被撤回（拒绝）、所属的 run 已取消，都算没批，就不动。
    查了什么、结论是什么，都记进核查记录（``approval_checks``）并写日志，
    供审计链回查。只在已有审批机制（workflow store 的审批表）上读，
    不重写认证/权限/审计核心。
    """
    from src.workflow.store import get_workflow_store

    store = get_workflow_store()
    checked = [f"审批表 workflow_approvals：查 approval_id={approval_id}",
               f"期望操作：{operation}"]
    verdict = "not_approved"
    conclusion = ""
    approval = store.get_approval(str(approval_id))
    if approval is None:
        conclusion = "没有这条审批记录：没批过，不能动。"
    elif str(approval.get("operation") or "") != str(operation):
        checked.append(f"实际操作：{approval.get('operation') or ''}（与期望的不符）")
        conclusion = "这条审批是别的操作，不是这件事：不能动。"
    else:
        status = str(approval.get("status") or "")
        checked.append(f"审批状态：{status}")
        checked.append(f"提交人：{approval.get('submitted_by') or ''}；"
                       f"审批人：{', '.join(approval.get('approvers') or []) or '未指定'}")
        created = approval.get("created_at")
        if created:
            checked.append("创建时间："
                           + datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat())
        expires = approval.get("expires_at")
        if expires:
            checked.append("过期时间："
                           + datetime.fromtimestamp(float(expires), tz=timezone.utc).isoformat())
        if status != "approved":
            conclusion = {"pending": "还在等批复：没批，不能动。",
                          "rejected": "已经被拒绝了：算没批，不能动。",
                          "expired": "已经过期失效了：算没批，不能动。"}.get(
                              status, f"审批状态是 {status}：不是“通过”，不能动。")
        elif expires is not None and time.time() > float(expires):
            conclusion = "审批虽然显示通过，但已经过了有效期：算没批，不能动。"
        else:
            run = store.get_run(str(approval.get("run_id") or ""))
            if run is not None and str(run.get("status") or "") == "cancelled":
                checked.append("所属工作流 run 已取消")
                conclusion = "审批所属的流程已经被取消了：算没批，不能动。"
            else:
                verdict = "approved"
                conclusion = "核查通过：这件事批过、在有效期内、流程没取消，可以动。"
    record = {"at": _now_iso(), "approval_id": str(approval_id),
              "operation": str(operation), "actor": str(actor or ""),
              "verdict": verdict, "checked": checked, "conclusion": conclusion}
    state = load_state()
    checks = state.setdefault("approval_checks", [])
    checks.append(record)
    del checks[: max(0, len(checks) - 200)]
    save_state(state)
    logger.info("a2b approval check %s %s: %s", approval_id, operation, verdict)
    return record


# ---------------------------------------------------------------------------
# A7. 只报结果规矩落到通知逻辑
# ---------------------------------------------------------------------------

def audit_result_only_notices() -> Dict[str, Any]:
    """查通知收件箱：是不是只推三类结果（做完了/卡住了/要他拍板）。

    ``a2a.push_notice`` 本身只接受这三类、别的直接拒绝，所以正常应
    该是 0 条违规；这里是兜底审计——真有过程事件混进来就列出来、
    由调用方收掉，而不是假装没看见。
    """
    from .a2a import NOTICE_KINDS, load_state as _a2a_load

    allowed = set(NOTICE_KINDS)
    items = _a2a_load().get("notifications", [])
    violations = [n for n in items if str(n.get("kind") or "") not in allowed]
    by_kind: Dict[str, int] = {}
    for notice in items:
        by_kind[str(notice.get("kind") or "")] = by_kind.get(str(notice.get("kind") or ""), 0) + 1
    return {"total": len(items), "by_kind": by_kind,
            "violations": [{"id": v.get("id"), "kind": v.get("kind"),
                            "title": v.get("title")} for v in violations],
            "message": ("通知收件箱干净：只推三类结果，没有过程流水。"
                        if not violations else
                        f"发现 {len(violations)} 条过程事件混进了通知，需要收掉。")}


# ---------------------------------------------------------------------------
# B8. 听不清 / 拿不准先问一句再干
# ---------------------------------------------------------------------------

def get_clarify_threshold() -> float:
    """当前置信度阈值（默认 0.6，可配）。"""
    try:
        return float(load_state().get("clarify_threshold") or CLARIFY_DEFAULT_THRESHOLD)
    except (TypeError, ValueError):
        return CLARIFY_DEFAULT_THRESHOLD


def set_clarify_threshold(value: float) -> Dict[str, Any]:
    """改置信度阈值：只能在 0 到 1 之间。"""
    threshold = float(value)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("置信度阈值只能在 0 到 1 之间")
    state = load_state()
    state["clarify_threshold"] = threshold
    save_state(state)
    return {"threshold": threshold,
            "message": f"已设好：置信度低于 {threshold} 就先问一句再干。"}


def should_clarify(
    text: str,
    *,
    confidence: Optional[float] = None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """听不清/拿不准时先问一句再执行，不要猜错了就干。

    注意：当前语音转写（``src/voice/stt.py`` 的 TranscribeResult）不
    返回置信度分数，所以语音场景下调用方（前端/B 组）要把识别的置信
    度传进来；没传分数时只按文字判断：空指令先问，别的按文字直接执行，
    绝不假装“测过置信度”。
    """
    limit = float(threshold if threshold is not None else get_clarify_threshold())
    body = str(text or "").strip()
    if not body:
        return {"needs_clarify": True, "threshold": limit, "confidence": confidence,
                "question": "刚才那句我没听清，能再说一遍吗？",
                "reason": "指令是空的，没法猜你要干什么。"}
    if confidence is not None:
        score = float(confidence)
        if score < limit:
            return {"needs_clarify": True, "threshold": limit, "confidence": score,
                    "question": f"我没太听清，你是想让我“{body}”吗？是的话说“对”，不是就再说一遍。",
                    "reason": f"置信度 {score} 低于阈值 {limit}，先问一句再干。"}
        return {"needs_clarify": False, "threshold": limit, "confidence": score,
                "question": "", "reason": f"置信度 {score} 够了，直接执行。"}
    return {"needs_clarify": False, "threshold": limit, "confidence": None,
            "question": "",
            "reason": "这次没传置信度分数，按文字直接执行；语音识别的置信度由调用方传入后才参与判断。"}


# ---------------------------------------------------------------------------
# B9. 一句话找东西
# ---------------------------------------------------------------------------

def universal_search(
    owner: str,
    query: str,
    *,
    context: Any = None,
    limit_per_source: int = 5,
) -> Dict[str, Any]:
    """自然语言跨聊天/文件/任务召回：返回结果标明来源，没找到老实说没找到。

    来源：聊天（会话全文搜索）/ 文件（A1 的文件内容搜索）/ 任务（任务台账）/
    记忆（主线记忆关键词召回）。知识库检索走 ``/v1/knowledge`` 的 RAG
    接口，这里不重复接线，知识来源如实标“未接线”。
    """
    from .a2a import recall_memories

    q = str(query or "").strip()
    if not q:
        return {"query": q, "results": [], "by_source": {},
                "message": "请先说要找什么（比如“上次那个报价发我”）。"}
    results: List[Dict[str, Any]] = []
    notes: List[str] = []

    # 聊天：会话全文搜索（只搜本账号的）。
    try:
        from src.ai.conversation_store import get_conversation_store

        found = get_conversation_store().search(
            principal=str(owner), query=q, limit=max(1, int(limit_per_source)),
        )
        for hit in (found.get("results") or [])[: int(limit_per_source)]:
            if not isinstance(hit, dict):
                continue
            content = str(hit.get("content") or hit.get("snippet") or "")
            results.append({"source": "聊天",
                            "title": str(hit.get("session_id") or hit.get("principal_id") or "聊天记录"),
                            "snippet": content[:200],
                            "ref": str(hit.get("session_id") or "")})
    except Exception as exc:  # noqa: BLE001 - 一路查不到不拖垮别的来源
        notes.append(f"聊天记录没查成（{exc}），这一路先空着。")

    # 文件：A1 的文件内容搜索（文件名/路径/内容），需要账号视角。
    if context is not None:
        try:
            from .files import _account_space, _content_snippet, _logical_prefix, _STORAGE_ARTIFACT_RE

            physical_root, file_owner, mode = _account_space(context, None)
            prefix = _logical_prefix(file_owner, mode)
            needle = q.lower()
            hits = 0
            if os.path.isdir(physical_root):
                for dirpath, dirnames, filenames in os.walk(physical_root):
                    dirnames.sort()
                    for name in sorted(dirnames) + sorted(filenames):
                        if hits >= int(limit_per_source):
                            break
                        if _STORAGE_ARTIFACT_RE.search(name):
                            continue
                        abs_child = os.path.join(dirpath, name)
                        rel = os.path.relpath(abs_child, physical_root).replace(os.sep, "/")
                        logical = f"{prefix}/{rel}".strip("/") if prefix else rel
                        match = None
                        snippet = None
                        if needle in name.lower():
                            match = "文件名命中"
                        elif needle in logical.lower():
                            match = "路径命中"
                        elif os.path.isfile(abs_child):
                            snippet = _content_snippet(abs_child, needle)
                            if snippet is not None:
                                match = "内容命中"
                        if match is None:
                            continue
                        hits += 1
                        results.append({"source": "文件", "title": logical,
                                        "snippet": (snippet or match)[:200],
                                        "ref": logical})
                    if hits >= int(limit_per_source):
                        break
        except Exception as exc:  # noqa: BLE001
            notes.append(f"文件没查成（{exc}），这一路先空着。")
    else:
        notes.append("文件这一路需要账号视角，本次没传，跳过。")

    # 任务：任务台账按标题/描述匹配。
    try:
        from .task_ledger import TaskLedger

        page = TaskLedger().list_tasks(owners=[str(owner)], limit=200)
        items = page.get("items", []) if isinstance(page, dict) else []
        tokens = [t for t in re.split(r"\W+", q.lower()) if len(t) > 1]
        found_tasks = 0
        for task in items:
            if found_tasks >= int(limit_per_source):
                break
            if not isinstance(task, dict):
                continue
            hay = f"{task.get('title') or ''} {task.get('description') or ''}".lower()
            if q.lower() in hay or (tokens and any(t in hay for t in tokens)):
                found_tasks += 1
                results.append({"source": "任务",
                                "title": str(task.get("title") or task.get("task_id") or ""),
                                "snippet": f"状态：{task.get('status') or ''}；"
                                           f"{str(task.get('description') or '')[:120]}",
                                "ref": str(task.get("task_id") or "")})
    except Exception as exc:  # noqa: BLE001
        notes.append(f"任务台账没查成（{exc}），这一路先空着。")

    # 记忆：主线记忆关键词召回。
    try:
        for entry in recall_memories(str(owner), q, limit=int(limit_per_source)):
            results.append({"source": "记忆",
                            "title": str(entry.get("text") or "")[:60],
                            "snippet": str(entry.get("text") or "")[:200],
                            "ref": str(entry.get("id") or "")})
    except Exception as exc:  # noqa: BLE001
        notes.append(f"记忆没查成（{exc}），这一路先空着。")

    notes.append("知识库检索走 /v1/knowledge 的 RAG 接口，这里不重复接线。")
    by_source: Dict[str, int] = {}
    for hit in results:
        by_source[hit["source"]] = by_source.get(hit["source"], 0) + 1
    return {"query": q, "results": results, "by_source": by_source, "notes": notes,
            "message": ("找到了，分来源列在上面。" if results else
                        "没找到：聊天、文件、任务、记忆里都没有匹配的东西。")}


# ---------------------------------------------------------------------------
# B10. 换电脑一键搬家
# ---------------------------------------------------------------------------

_KEY_LIKE_RE = re.compile(r"(api[_-]?key|secret|token|password|credential)", re.IGNORECASE)


def _scrub_keys(value: Any) -> Any:
    """搬家包里绝不能有 Key：递归删掉名字像 Key/口令/凭证的字段。"""
    if isinstance(value, dict):
        return {k: _scrub_keys(v) for k, v in value.items()
                if not _KEY_LIKE_RE.search(str(k))}
    if isinstance(value, list):
        return [_scrub_keys(v) for v in value]
    return value


def build_migration_export(owner: str, *, context: Any = None) -> Dict[str, Any]:
    """导出迁移包：聊天、Bot、任务、记忆全搬过去。

    Key 出于安全绝不随包走（导出后提醒重新填一次）；包里只有本账号
    自己的东西。任务只导出（台账没有公开的新建接口，导入时如实记
    “未导入”，见 ``import_migration``）。
    """
    from .data_portability import collect_account_settings, collect_memories

    payload: Dict[str, Any] = {
        "format": MIGRATION_FORMAT,
        "version": MIGRATION_VERSION,
        "exported_at": _now_iso(),
        "owner": str(owner),
        "chats": [],
        "bots": [],
        "tasks": [],
        "memories": [],
        "settings": {},
        "notes": [],
    }
    # 聊天：原始轮次（不是 markdown），导入时能原样写回会话库。
    try:
        from src.ai.conversation_store import get_conversation_store

        from .chat import list_sessions

        store = get_conversation_store()
        sessions = list_sessions(str(owner), known_principals=[str(owner)], allow_legacy=False)
        for session_id in sessions:
            key = None
            for candidate in (f"liuhao-{owner}-{session_id}", f"liuhao-{session_id}"):
                try:
                    if store.has_rows(candidate):
                        key = candidate
                        break
                except Exception:  # noqa: BLE001
                    continue
            if key is None:
                continue
            turns = store.load_turns(key)
            if turns:
                payload["chats"].append({"session_id": str(session_id), "turns": turns})
    except Exception as exc:  # noqa: BLE001
        payload["notes"].append(f"聊天没导出来（{exc}），这一路是空的。")
    # Bot：小队成员（含模型配置 id，不含 Key）。
    try:
        from src.team import get_team_store

        payload["bots"] = [
            {"kind": str(m.get("kind") or ""), "name": str(m.get("name") or ""),
             "model_id": m.get("model_id"), "active": bool(m.get("active"))}
            for m in get_team_store().list_members(str(owner))
        ]
    except Exception as exc:  # noqa: BLE001
        payload["notes"].append(f"Bot 没导出来（{exc}），这一路是空的。")
    # 任务：任务台账。
    try:
        from .task_ledger import TaskLedger

        page = TaskLedger().list_tasks(owners=[str(owner)], limit=500)
        payload["tasks"] = page.get("items", []) if isinstance(page, dict) else []
    except Exception as exc:  # noqa: BLE001
        payload["notes"].append(f"任务没导出来（{exc}），这一路是空的。")
    # 记忆：主线记忆。
    try:
        payload["memories"] = [
            {"text": str(m.get("text") or ""), "source": str(m.get("source") or "import"),
             "tags": list(m.get("tags") or [])}
            for m in collect_memories(str(owner))
        ]
    except Exception as exc:  # noqa: BLE001
        payload["notes"].append(f"记忆没导出来（{exc}），这一路是空的。")
    # 设置：只有账号标识与服务端设置，不含 Key。
    try:
        payload["settings"] = collect_account_settings(str(owner))
    except Exception as exc:  # noqa: BLE001
        payload["notes"].append(f"设置没导出来（{exc}）。")
    payload = _scrub_keys(payload)
    payload["notes"].append(
        "模型 Key 不在包里：导入后请去设置 → 模型与 Key 里重新填一次 Key。")
    return payload


def import_migration(payload: Dict[str, Any], owner: str) -> Dict[str, Any]:
    """导入迁移包：校验格式，不覆盖现有数据。

    记忆按正文去重（已有的跳过并记数）；Bot 同名冲突时重命名（加“（2）”）；
    聊天写回会话库（已导过的会话跳过）；任务台账没有公开的新建接口，
    本版只记“未导入”，不伪造导入成功。导入全程不碰 Key。
    """
    if not isinstance(payload, dict):
        raise ValueError("迁移包不是一个对象，导不进来")
    if payload.get("format") != MIGRATION_FORMAT:
        raise ValueError(f"这不是 Jarvis 迁移包（format 应为 {MIGRATION_FORMAT}）")
    if int(payload.get("version") or 0) != MIGRATION_VERSION:
        raise ValueError(f"迁移包版本不对：要的是 v{MIGRATION_VERSION}，拿到的是 {payload.get('version')}")
    result: Dict[str, Any] = {"imported": {}, "skipped": {}, "notes": []}
    # 记忆：去重后写主线记忆。
    try:
        from .a2a import dedup_entries
        from .account_memory import _query_entries, record_entry_for_owner

        entries = payload.get("memories") or []
        existing = [e.get("text", "") for e in _query_entries((str(owner),), "")]
        to_add, duplicates = dedup_entries(
            [{"text": str(e.get("text") or ""), "source": str(e.get("source") or "import"),
              "tags": list(e.get("tags") or [])} for e in entries
             if isinstance(e, dict) and str(e.get("text") or "").strip()],
            existing,
        )
        for entry in to_add:
            record_entry_for_owner(str(owner), entry["text"], source=entry.get("source") or "import",
                                   tags=entry.get("tags") or [])
        result["imported"]["memories"] = len(to_add)
        result["skipped"]["memories_duplicates"] = len(duplicates)
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"记忆没导进来（{exc}）。")
        result["imported"]["memories"] = 0
    # Bot：小队成员，同名冲突重命名，不覆盖。
    try:
        from src.team import get_team_store

        store = get_team_store()
        existing_names = {(str(m.get("kind") or ""), str(m.get("name") or ""))
                          for m in store.list_members(str(owner))}
        added = 0
        renamed = 0
        for bot in (payload.get("bots") or []):
            if not isinstance(bot, dict):
                continue
            kind = str(bot.get("kind") or "").strip()
            name = str(bot.get("name") or "").strip()
            if not kind or not name:
                continue
            if (kind, name) in existing_names:
                base = name
                suffix = 2
                while (kind, f"{base}（{suffix}）") in existing_names:
                    suffix += 1
                name = f"{base}（{suffix}）"
                renamed += 1
            store.create_member(str(owner), kind, name, bot.get("model_id"))
            existing_names.add((kind, name))
            added += 1
        result["imported"]["bots"] = added
        result["skipped"]["bots_renamed"] = renamed
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"Bot 没导进来（{exc}）。")
        result["imported"]["bots"] = 0
    # 聊天：写回会话库，已导过的会话跳过。
    try:
        from src.ai.conversation_store import get_conversation_store

        store = get_conversation_store()
        added_sessions = 0
        skipped_sessions = 0
        for chat in (payload.get("chats") or []):
            if not isinstance(chat, dict):
                continue
            session_id = str(chat.get("session_id") or "").strip()
            turns = chat.get("turns") or []
            if not session_id or not turns:
                continue
            key = f"liuhao-{owner}-migrated-{session_id}"
            try:
                if store.has_rows(key):
                    skipped_sessions += 1
                    continue
            except Exception:  # noqa: BLE001
                pass
            for idx, turn in enumerate(turns):
                if not isinstance(turn, dict):
                    continue
                store.append(key, int(turn.get("turn") or idx + 1),
                             str(turn.get("role") or "user"),
                             str(turn.get("content") or ""),
                             owner_principal=str(owner))
            added_sessions += 1
        result["imported"]["chats"] = added_sessions
        result["skipped"]["chats_already_imported"] = skipped_sessions
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"聊天没导进来（{exc}）。")
        result["imported"]["chats"] = 0
    # 任务：本版导不进台账，如实记，不伪造。
    task_count = len(payload.get("tasks") or [])
    result["skipped"]["tasks_not_imported"] = task_count
    if task_count:
        result["notes"].append(
            f"任务 {task_count} 条没有导入：任务台账没有公开的新建接口，本版只导出不导入，"
            "下版接好接口再导；导出的任务内容都在包里，没丢。")
    result["notes"].append("模型 Key 不在包里：请去设置 → 模型与 Key 里重新填一次 Key。")
    state = load_state()
    state.setdefault("migration_log", []).append(
        {"at": _now_iso(), "owner": str(owner), **result})
    save_state(state)
    return result


# ---------------------------------------------------------------------------
# B11. 文件自动归类
# ---------------------------------------------------------------------------

#: 规则归类：先看文件名关键词，再看扩展名。方法永远是“规则”，不假装 AI 识别。
_CLASSIFY_KEYWORDS = [
    ("发票", ("发票", "invoice", "报销")),
    ("合同", ("合同", "协议", "contract", "agreement")),
]
_CLASSIFY_EXTENSIONS = {
    "图片": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"),
    "表格": (".xls", ".xlsx", ".csv"),
    "文档": (".doc", ".docx", ".pdf", ".txt", ".md", ".ppt", ".pptx"),
    "音频": (".mp3", ".wav", ".m4a"),
    "视频": (".mp4", ".mov", ".mkv"),
    "压缩包": (".zip", ".rar", ".7z", ".tar", ".gz"),
}


def classify_file(name: str, path_hint: str = "") -> Dict[str, Any]:
    """按规则给文件分组：发票/合同/图片/表格/文档/音频/视频/压缩包/其他。

    只看文件名关键词、扩展名、路径片段；分组结果明确标 ``method="rule"``，
    不假装是 AI 识别出来的。分错了用户能在前端改（见 ``set_file_group``）。
    """
    text = f"{name} {path_hint}".lower()
    for group, keywords in _CLASSIFY_KEYWORDS:
        if any(kw.lower() in text for kw in keywords):
            return {"group": group, "method": "rule",
                    "reason": f"文件名/路径里有{keywords[0]}相关的词，按规则分的。"}
    lowered = str(name or "").lower()
    for group, exts in _CLASSIFY_EXTENSIONS.items():
        if any(lowered.endswith(ext) for ext in exts):
            return {"group": group, "method": "rule",
                    "reason": f"扩展名是 {exts[0]} 这一类，按规则分的。"}
    return {"group": "其他", "method": "rule", "reason": "没匹配上任何规则，先放“其他”。"}


def set_file_group(rel_path: str, group: str, *, owner: str = "") -> Dict[str, Any]:
    """用户改分组：人工改的优先于规则（前端改入口调这个）。"""
    path = str(rel_path or "").strip()
    if not path:
        raise ValueError("文件路径不能为空")
    clean = str(group or "").strip()
    if not clean:
        raise ValueError("分组名不能为空")
    state = load_state()
    state.setdefault("file_groups", {})[path] = {
        "group": clean, "method": "user", "owner": str(owner or ""), "at": _now_iso()}
    save_state(state)
    return {"path": path, "group": clean, "method": "user",
            "message": "已按你改的分组记下来了，规则不会再把它分回去。"}


def get_file_group(name: str, rel_path: str = "", *, owner: str = "") -> Dict[str, Any]:
    """某文件的分组：人工改过就用人改的，否则按规则分，并标明方法。"""
    override = load_state().get("file_groups", {}).get(str(rel_path or ""))
    if override:
        return {"path": str(rel_path or ""), "group": str(override.get("group") or ""),
                "method": "user"}
    result = classify_file(name, rel_path)
    return {"path": str(rel_path or ""), **result}


# ---------------------------------------------------------------------------
# B12. 到点提醒休息（走现有例程机制）
# ---------------------------------------------------------------------------

def ensure_rest_reminders(owner: str, *, created_by: Optional[str] = None) -> Dict[str, Any]:
    """确保“到点提醒休息”例程在：午饭 12:00、熬夜 23:00 各一条，默认开。

    走现有例程机制（``src/tasks/routines.py``），设置里关就是把这两条
    例程停用；关了就不提醒。
    """
    from ..tasks.routines import ensure_rest_reminder_routines

    routines = ensure_rest_reminder_routines(
        owner_account_id=str(owner), created_by=str(created_by or owner))
    return {"routines": [
        {"routine_id": r.get("routine_id"), "name": r.get("name"),
         "schedule": r.get("schedule"), "enabled": bool(r.get("enabled"))}
        for r in routines],
        "message": "到点提醒休息已开着（午饭 12:00、熬夜 23:00，轻声提醒）；"
                   "设置里关掉就能停。", "enabled": True}


def set_rest_reminders_enabled(owner: str, enabled: bool) -> Dict[str, Any]:
    """开关到点提醒休息（默认开）：停用/启用那两条例程。"""
    from ..tasks.routines import get_routine_store

    store = get_routine_store()
    changed = []
    for routine in store.list_routines(owners=[str(owner)], limit=500):
        if str(routine.get("action_kind") or "") == "rest_reminder":
            updated = store.update_routine(
                str(routine.get("routine_id")), enabled=1 if enabled else 0)
            changed.append({"routine_id": updated.get("routine_id"),
                            "name": updated.get("name"),
                            "enabled": bool(updated.get("enabled"))})
    state = load_state()
    state["rest_reminders_enabled"] = bool(enabled)
    save_state(state)
    return {"enabled": bool(enabled), "routines": changed,
            "message": "到点提醒休息已打开。" if enabled else "到点提醒休息已关掉，不会再提醒。"}


# ---------------------------------------------------------------------------
# B13. 多设备只响一边
# ---------------------------------------------------------------------------

def register_device(account: str, device_id: str, *, active: bool = False) -> Dict[str, Any]:
    """登记一台设备在线（电脑/手机/App 调这个报到）：记下最后一次见到
    它、是不是正在用。以后到为准的设备在线协调都读这张表。"""
    device = str(device_id or "").strip()
    if not device:
        raise ValueError("设备 id 不能为空")
    state = load_state()
    devices = state.setdefault("devices", {}).setdefault(str(account), {})
    record = devices.get(device, {})
    record.update({"device_id": device, "active": bool(active),
                   "last_seen": _now_iso()})
    if active:
        record["last_active_at"] = _now_iso()
    devices[device] = record
    save_state(state)
    return record


def route_notification(account: str, title: str, body: str = "") -> Dict[str, Any]:
    """消息只在一个设备上响：选一个在线设备响，别的设备静默记一条。

    优先当前正在用的那台（active 且最近活跃）；没有正在用的，就选
    最近见过的那台；一台都没登记过，就只记进收件箱、哪台都不响。
    两边一起响是不允许的——本函数永远只返回一台 ring 设备。
    """
    from .a2a import push_notice

    devices = load_state().get("devices", {}).get(str(account), {})
    online = [d for d in devices.values() if d.get("last_seen")]
    if not online:
        notice = push_notice("done", str(title), str(body), owner=str(account))
        return {"ring_device": None, "silent_devices": [],
                "notice_id": notice.get("id"),
                "message": "没有登记的设备：通知只记进收件箱，哪台都没响。"}
    def _sort_key(device: Dict[str, Any]) -> Tuple[int, str]:
        return (1 if device.get("active") else 0,
                str(device.get("last_active_at") or device.get("last_seen") or ""))

    chosen = max(online, key=_sort_key)
    ring = str(chosen.get("device_id") or "")
    silent = sorted(str(d.get("device_id") or "") for d in online
                    if str(d.get("device_id") or "") != ring)
    notice = push_notice("done", str(title), str(body), owner=str(account))
    state = load_state()
    state.setdefault("devices", {}).setdefault(str(account), {}).setdefault(
        ring, {}).update({"last_rang_at": _now_iso()})
    save_state(state)
    return {"ring_device": ring, "silent_devices": silent,
            "notice_id": notice.get("id"),
            "message": f"只在 {ring} 上响了；"
                       + (f"{'、'.join(silent)} 上静默记了一条，没响。" if silent else "没有别的设备在线。")}


# ---------------------------------------------------------------------------
# B14. 晚上今日小结（走现有例程机制）
# ---------------------------------------------------------------------------

def ensure_evening_digest(owner: str, *, created_by: Optional[str] = None) -> Dict[str, Any]:
    """确保“每天晚上 9 点今日小结”例程存在（走现有例程机制）。"""
    from ..tasks.routines import ensure_evening_digest_routine

    routine = ensure_evening_digest_routine(
        owner_account_id=str(owner), created_by=str(created_by or owner))
    return {"routine_id": routine.get("routine_id"), "name": routine.get("name"),
            "schedule": routine.get("schedule"), "timezone": routine.get("timezone"),
            "enabled": routine.get("enabled"),
            "message": "每天晚上 9 点的今日小结已开着（走例程机制）；设置里关掉就能停。"}


def evening_digest_text(owner: str) -> Dict[str, Any]:
    """现在算一条晚上小结（预览用）：数字只从真实台账出，没办的就是 0。"""
    from ..tasks.routines import build_evening_digest_text, get_routine_store

    return {"text": build_evening_digest_text(get_routine_store(), str(owner))}


# ---------------------------------------------------------------------------
# D16. 推送真实发送点
# ---------------------------------------------------------------------------

def collect_overview(owner: str) -> Dict[str, Any]:
    """总览真实状态：任务台账聚合＋小队成员＋未处理通知数。

    台账读不到时 ``tasks`` 为 null（不编）；成员没有就是 []。
    """
    tasks: Optional[Dict[str, Any]] = None
    try:
        from .task_ledger import TaskLedger

        page = TaskLedger().list_tasks(owners=[str(owner)], limit=500)
        items = page.get("items", []) if isinstance(page, dict) else []
        by_status: Dict[str, int] = {}
        done_today = 0
        stuck = 0
        today = datetime.now(timezone.utc).date()
        for task in items:
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "")
            by_status[status] = by_status.get(status, 0) + 1
            completed = task.get("completed_at") or ""
            if completed:
                try:
                    if datetime.fromisoformat(str(completed).replace("Z", "+00:00")).date() == today:
                        done_today += 1
                except ValueError:
                    pass
            if status == "failed" or task.get("human_hold"):
                stuck += 1
        tasks = {"total": len(items), "by_status": by_status,
                 "done_today": done_today, "stuck": stuck}
    except Exception as exc:  # noqa: BLE001 - 台账读不到就不编总览数字
        logger.warning("a2b overview ledger failed: %s", exc)
    bots = collect_team(owner).get("members", [])
    try:
        from .a2a import list_notices

        pending = len(list_notices(str(owner), only_unacked=True))
    except Exception:  # noqa: BLE001
        pending = 0
    return {"tasks": tasks, "bots": bots, "pending_notices": pending}


def collect_team(owner: str) -> Dict[str, Any]:
    """小队真实状态：小队成员表＋本批记的 Bot 干活状态叠加。"""
    members: List[Dict[str, Any]] = []
    try:
        from src.team import get_team_store

        status_map = load_state().get("bot_status", {})
        for member in get_team_store().list_members(str(owner)):
            member_id = str(member.get("member_id") or "")
            item = {"member_id": member_id, "kind": str(member.get("kind") or ""),
                    "name": str(member.get("name") or ""),
                    "model_id": member.get("model_id"),
                    "active": bool(member.get("active"))}
            live = status_map.get(member_id) or status_map.get(str(member.get("name") or ""))
            if live:
                item["bot_status"] = live.get("status")
                item["bot_status_label"] = live.get("label")
                item["progress"] = live.get("progress")
            members.append(item)
    except Exception as exc:  # noqa: BLE001 - 成员表读不到就返回空列表
        logger.warning("a2b team members failed: %s", exc)
    return {"members": members}


def publish_overview_snapshot(owner: str) -> Dict[str, Any]:
    """把真实总览状态推上 WS（snapshot 版）；推之前先现算，不推缓存旧数。"""
    from .a2a import push_overview

    data = collect_overview(owner)
    return push_overview({**data, "source": "snapshot"}, snapshot=True)


def publish_team_snapshot(owner: str) -> Dict[str, Any]:
    """把真实小队状态推上 WS（snapshot 版）。"""
    from .a2a import push_team

    data = collect_team(owner)
    return push_team({**data, "source": "snapshot"}, snapshot=True)


def publish_push_refresh(owner: str) -> Dict[str, Any]:
    """总览＋小队各推一次真实快照（状态变化处的发送点调这个）。"""
    overview = publish_overview_snapshot(owner)
    team = publish_team_snapshot(owner)
    return {"overview": overview, "team": team,
            "message": "已把真实总览和小队状态推上去了（没变化不推，下次变化再推）。"}


def set_bot_status(bot_id: str, status: str, *, progress: Optional[int] = None,
                   owner: str = "") -> Dict[str, Any]:
    """Bot 干活状态变化：记下来并推 ``bot.status`` 事件（桌宠订阅用）。

    状态只有六种：思考/在办/在研究/等确认/干完/出错；进度 0–100，
    没进度就是 null，不编一个假进度。
    """
    clean = str(status or "").strip()
    if clean not in BOT_STATUSES:
        raise ValueError(f"Bot 状态只认这几种：{', '.join(BOT_STATUSES)}")
    pct: Optional[int] = None
    if progress is not None:
        pct = max(0, min(100, int(progress)))
    record = {"bot_id": str(bot_id), "status": clean,
              "label": BOT_STATUS_LABELS[clean], "progress": pct,
              "owner": str(owner or ""), "at": _now_iso()}
    state = load_state()
    state.setdefault("bot_status", {})[str(bot_id)] = record
    save_state(state)
    _emit("bot.status", {"bot_id": str(bot_id), "status": clean,
                         "progress": pct, "label": BOT_STATUS_LABELS[clean]})
    return record


def emit_task_event(task_id: str, event: str, *, status: str = "", title: str = "") -> Optional[Dict[str, Any]]:
    """任务生命周期事件（创建/开始/完成/卡住/接管/等人）：状态变化处调这个。"""
    return _emit("task.lifecycle", {"task_id": str(task_id), "event": str(event),
                                    "status": str(status), "title": str(title)})


def emit_approval_event(approval_id: str, operation: str, status: str) -> Optional[Dict[str, Any]]:
    """审批结论变化事件：审批中心批了/拒了/过期了，调这个推一声。"""
    return _emit("approval.changed", {"approval_id": str(approval_id),
                                      "operation": str(operation), "status": str(status)})


def emit_digest_event(kind: str, text: str) -> Optional[Dict[str, Any]]:
    """待办汇总/今日小结发到聊天时推一声（桌宠订阅用）。"""
    return _emit("digest.ready", {"kind": str(kind), "text": str(text)})


def emit_reminder_event(kind: str, text: str) -> Optional[Dict[str, Any]]:
    """到点提醒发到聊天时推一声（桌宠订阅用）。"""
    return _emit("reminder.due", {"kind": str(kind), "text": str(text)})


# ---------------------------------------------------------------------------
# HTTP 路由（/v1/a2b；挂载见 src/gateway/main.py）
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .account_context import require_account_principal

router = APIRouter(prefix="/v1/a2b", tags=["a2b"])


def _principal(context=Depends(require_account_principal)):
    return context


class RecordTurnBody(BaseModel):
    session_id: str = ""
    speaker: str = ""
    text: str = ""
    responsible: List[str] = Field(default_factory=list)


class TakeoverBody(BaseModel):
    bot_id: str = ""


class MissingMaterialBody(BaseModel):
    missing: str = ""


class DelegateBody(BaseModel):
    from_bot: str = ""
    to_bot: str = ""
    title: str = ""
    reason: str = ""


class HumanDecisionBody(BaseModel):
    task_id: str = ""
    question: str = ""
    context: str = ""
    person: str = ""


class VerifyApprovalBody(BaseModel):
    approval_id: str = ""
    operation: str = ""


class ThresholdBody(BaseModel):
    threshold: float = CLARIFY_DEFAULT_THRESHOLD


class ClarifyBody(BaseModel):
    text: str = ""
    confidence: Optional[float] = None


class MigrationImportBody(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


class ClassifyBody(BaseModel):
    name: str = ""
    path: str = ""


class FileGroupBody(BaseModel):
    path: str = ""
    group: str = ""


class RestEnabledBody(BaseModel):
    enabled: bool = True


class DeviceBody(BaseModel):
    device_id: str = ""
    active: bool = False


class RouteBody(BaseModel):
    title: str = ""
    body: str = ""


class BotStatusBody(BaseModel):
    bot_id: str = ""
    status: str = ""
    progress: Optional[int] = None


@router.post("/participants/record")
def post_record_turn(body: RecordTurnBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return record_turn(body.session_id, body.speaker, body.text,
                           responsible=body.responsible, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/participants/{session_id}")
def get_participants(session_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    return session_participants(session_id)


@router.get("/tasks/stalled")
def get_stalled(stale_hours: float = Query(STALLED_DEFAULT_HOURS, ge=1, le=720),
                context=Depends(_principal)) -> Dict[str, Any]:
    from .task_ledger import TaskLedger

    found = detect_stalled_tasks(TaskLedger(), stale_hours=stale_hours, owner=context.principal)
    return {"stalled": found, "count": len(found),
            "message": ("没有搁置的任务。" if not found else
                        f"有 {len(found)} 个任务搁置了（长时间没进展或失败没人处理）。")}


@router.post("/tasks/{task_id}/takeover")
def post_takeover(task_id: str, body: TakeoverBody,
                  context=Depends(_principal)) -> Dict[str, Any]:
    if not str(body.bot_id or "").strip():
        raise HTTPException(status_code=422, detail="请先写是哪个 Bot 接过来")
    return takeover_stalled(task_id, body.bot_id, owner=context.principal)


@router.post("/tasks/{task_id}/missing-material")
def post_missing_material(task_id: str, body: MissingMaterialBody,
                          context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return report_missing_material(task_id, body.missing, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/delegate")
def post_delegate(body: DelegateBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return delegate_subtask(from_bot=body.from_bot, to_bot=body.to_bot,
                                title=body.title, reason=body.reason,
                                submitted_by=context.principal,
                                owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/delegations/{approval_id}")
def get_delegation(approval_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    return delegation_status(approval_id)


@router.post("/human-decision")
def post_human_decision(body: HumanDecisionBody,
                        context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return request_human_decision(task_id=body.task_id, question=body.question,
                                      context_text=body.context,
                                      submitted_by=context.principal,
                                      owner=context.principal, person=body.person)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/human-waits")
def get_human_waits(context=Depends(_principal)) -> Dict[str, Any]:
    return {"waits": list_human_waits(context.principal)}


@router.get("/human-waits/{task_id}")
def get_human_wait(task_id: str, context=Depends(_principal)) -> Dict[str, Any]:
    return human_wait_status(task_id)


@router.get("/ops/log")
def get_op_log(kind: str = Query(""), limit: int = Query(50, ge=1, le=200),
               context=Depends(_principal)) -> Dict[str, Any]:
    return {"operations": list_operation_log(kind, limit),
            "note": "每步操作都记了：什么时候、哪一步、成没成、为什么。"}


@router.post("/approvals/verify")
def post_verify_approval(body: VerifyApprovalBody,
                         context=Depends(_principal)) -> Dict[str, Any]:
    if not str(body.approval_id or "").strip() or not str(body.operation or "").strip():
        raise HTTPException(status_code=422, detail="审批 id 和操作名都要填")
    return verify_approval_before_action(body.approval_id, body.operation,
                                         actor=context.principal)


@router.get("/notices/audit")
def get_notices_audit(context=Depends(_principal)) -> Dict[str, Any]:
    return audit_result_only_notices()


@router.get("/clarify/threshold")
def get_threshold(context=Depends(_principal)) -> Dict[str, Any]:
    return {"threshold": get_clarify_threshold()}


@router.post("/clarify/threshold")
def post_threshold(body: ThresholdBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_clarify_threshold(body.threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/clarify/check")
def post_clarify_check(body: ClarifyBody, context=Depends(_principal)) -> Dict[str, Any]:
    return should_clarify(body.text, confidence=body.confidence)


@router.get("/search")
def get_universal_search(q: str = Query(""), context=Depends(_principal)) -> Dict[str, Any]:
    return universal_search(context.principal, q, context=context)


@router.post("/migration/export")
def post_migration_export(context=Depends(_principal)) -> Dict[str, Any]:
    return build_migration_export(context.principal, context=context)


@router.post("/migration/import")
def post_migration_import(body: MigrationImportBody,
                          context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return import_migration(body.payload, context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/migration/format")
def get_migration_format() -> Dict[str, Any]:
    return {"format": MIGRATION_FORMAT, "version": MIGRATION_VERSION,
            "keys_included": False,
            "note": "模型 Key 绝不随包走：导入后去设置 → 模型与 Key 里重新填一次 Key。"
                    "格式说明见 docs/a2b-migration-format.md。",
            "sections": ["chats（聊天轮次）", "bots（小队成员）", "tasks（任务台账，只导出）",
                         "memories（主线记忆）", "settings（账号设置，不含 Key）"]}


@router.post("/files/classify")
def post_classify(body: ClassifyBody, context=Depends(_principal)) -> Dict[str, Any]:
    return classify_file(body.name, body.path)


@router.post("/files/group")
def post_file_group(body: FileGroupBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_file_group(body.path, body.group, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/files/group")
def get_file_group_endpoint(name: str = Query(""), path: str = Query(""),
                            context=Depends(_principal)) -> Dict[str, Any]:
    return get_file_group(name, path, owner=context.principal)


@router.post("/reminders/rest/ensure")
def post_rest_ensure(context=Depends(_principal)) -> Dict[str, Any]:
    return ensure_rest_reminders(context.principal, created_by=context.principal)


@router.post("/reminders/rest/enabled")
def post_rest_enabled(body: RestEnabledBody, context=Depends(_principal)) -> Dict[str, Any]:
    return set_rest_reminders_enabled(context.principal, body.enabled)


@router.get("/reminders/rest")
def get_rest(context=Depends(_principal)) -> Dict[str, Any]:
    state = load_state()
    return {"enabled": bool(state.get("rest_reminders_enabled", True)),
            "message": "到点提醒休息：午饭 12:00、熬夜 23:00，轻声提醒，默认开。"}


@router.post("/devices/register")
def post_device_register(body: DeviceBody, context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return register_device(context.principal, body.device_id, active=body.active)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/devices/route")
def post_device_route(body: RouteBody, context=Depends(_principal)) -> Dict[str, Any]:
    return route_notification(context.principal, body.title, body.body)


@router.post("/digest/evening/ensure")
def post_evening_ensure(context=Depends(_principal)) -> Dict[str, Any]:
    return ensure_evening_digest(context.principal, created_by=context.principal)


@router.get("/digest/evening")
def get_evening_digest(context=Depends(_principal)) -> Dict[str, Any]:
    return evening_digest_text(context.principal)


@router.post("/push/refresh")
def post_push_refresh(context=Depends(_principal)) -> Dict[str, Any]:
    return publish_push_refresh(context.principal)


@router.get("/push/events")
def get_push_events() -> Dict[str, Any]:
    return {"events": A2B_PUSH_EVENTS,
            "note": "本批新增的推送事件（A2a 合同的扩展）：只推真实状态变化，"
                    "没接好的事件不发；总览/小队快照仍走 /v1/a2a/ws/status。"}


@router.post("/bots/{bot_id}/status")
def post_bot_status(bot_id: str, body: BotStatusBody,
                    context=Depends(_principal)) -> Dict[str, Any]:
    try:
        return set_bot_status(body.bot_id or bot_id, body.status,
                              progress=body.progress, owner=context.principal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
