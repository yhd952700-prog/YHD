"""晨间简报 + 例程模板库（大升级 U4：主动管家）。

此前的主动能力是散的：例程有「待办汇总/晚间小结/休息提醒」，提醒有
安静时段与一次投递，日历台账与待审批各在一处。本模块把它们收成
两件用户能直接用的东西：

1. **晨间简报** ``build_morning_briefing``：一份跨源汇总——昨日例程
   结果、今日例程、今日提醒、今日日程、待审批、3 小时内即将到期。
   每个数据源独立降级：读不到就在简报里如实写「暂时读不到」，绝不
   编造、绝不因为一处坏了整份不发。纯函数口径（stores/clock 可注入），
   假数据可完整演练。
2. **例程模板库** ``ROUTINE_TEMPLATES``：开箱模板（晨间简报/晚间
   小结/午饭提醒/熬夜提醒/每周开场），默认**不创建、不启用**；用户在
   聊天里一句话启用才落成真例程（enabled=1），停用即 enabled=0，
   与例程引擎既有语义完全一致，不另起一套定时系统。

简报时区沿用既有汇总口径 ``America/Tijuana``（与待办汇总一致）。
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("liuhao.tasks.briefing")

#: 简报例程动作（routines._execute 按此分发；字符串与模板共用一处定义）。
ACTION_MORNING_BRIEFING = "morning_briefing"

BRIEFING_TIMEZONE = "America/Tijuana"
_WEEKDAYS = "一二三四五六日"


def _day_bounds(day, tz: ZoneInfo):
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _routine_sections(
    owner: str, today, yesterday, tz: ZoneInfo, store: Any
) -> List[str]:
    lines: List[str] = []
    try:
        routines = store.list_routines(owners=[owner], limit=200)
    except Exception:  # noqa: BLE001 - 读不到如实说，不编
        return ["例程：暂时读不到（例程库读取失败）。"]
    # 昨日结果
    y_start, y_end = _day_bounds(yesterday, tz)
    ran_ok = 0
    ran_failed: List[str] = []
    for routine in routines:
        try:
            runs = store.list_runs(str(routine.get("routine_id")), limit=20)
        except Exception:  # noqa: BLE001
            continue
        for run in runs:
            started = run.get("started_at")
            if not isinstance(started, (int, float)):
                continue
            moment = datetime.fromtimestamp(float(started), tz=timezone.utc)
            if not (y_start <= moment.astimezone(tz) < y_end):
                continue
            if run.get("status") == "success":
                ran_ok += 1
            elif run.get("status") == "failed":
                ran_failed.append(
                    f"{routine.get('name') or routine.get('routine_id')}"
                    f"（{run.get('error') or run.get('result_text') or '失败'}）"
                )
    if ran_ok or ran_failed:
        lines.append(f"昨日例程：跑成 {ran_ok} 个" + (f"，没跑成 {len(ran_failed)} 个。" if ran_failed else "。"))
        for item in ran_failed[:5]:
            lines.append(f"· 没跑成：{item}")
    # 今日例程
    t_start, t_end = _day_bounds(today, tz)
    due_today: List[str] = []
    for routine in routines:
        if not routine.get("enabled"):
            continue
        nxt = routine.get("next_run_at")
        if isinstance(nxt, (int, float)):
            moment = datetime.fromtimestamp(float(nxt), tz=timezone.utc).astimezone(tz)
            if t_start <= moment < t_end:
                due_today.append(
                    f"{moment.strftime('%H:%M')} {routine.get('name') or routine.get('routine_id')}"
                )
    if due_today:
        lines.append("今日例程：" + "；".join(sorted(due_today)))
    return lines


def _reminder_section(owner: str, today, now_local: datetime, store: Any) -> List[str]:
    try:
        reminders = store.list(owner, status="active", limit=100)
    except Exception:  # noqa: BLE001
        return ["提醒：暂时读不到（提醒库读取失败）。"]
    items: List[str] = []
    for reminder in reminders:
        title = str(reminder.get("title") or "提醒")
        kind = str(reminder.get("repeat_kind") or "once")
        hour, minute = reminder.get("hour"), reminder.get("minute")
        if kind == "daily" and isinstance(hour, int) and isinstance(minute, int):
            items.append(f"{hour:02d}:{minute:02d} {title}")
        elif kind == "weekly" and isinstance(hour, int) and isinstance(minute, int):
            weekday = reminder.get("weekday")
            if isinstance(weekday, int) and weekday == today.weekday():
                items.append(f"{hour:02d}:{minute:02d} {title}")
        else:
            run_at = str(reminder.get("run_at") or "")
            try:
                moment = datetime.fromisoformat(run_at)
            except ValueError:
                continue
            if moment.date() == today:
                items.append(f"{moment.strftime('%H:%M')} {title}")
    if items:
        return ["今日提醒：" + "；".join(sorted(items))]
    return []


def _calendar_section(owner: str, today, tz: ZoneInfo, store: Any) -> List[str]:
    try:
        start, end = _day_bounds(today, tz)
        events = store.list(start=start, end=end, account_ids=[owner])
    except Exception:  # noqa: BLE001
        return ["日程：暂时读不到（日历台账读取失败）。"]
    items = [
        f"{event.start.astimezone(tz).strftime('%H:%M')} {event.title}"
        for event in events
    ]
    if items:
        return ["今日日程：" + "；".join(items)]
    return []


def _default_approvals_counter(owner: str):
    """待审批计数：工作流存储里 owner 名下 pending 的审批（标题取前 3）。"""
    from src.workflow.store import get_workflow_store

    store = get_workflow_store()
    approvals = store.list_approvals(
        owner_account_id=owner, status="pending", limit=50
    )
    titles = [str(item.get("title") or item.get("id") or "") for item in approvals[:3]]
    return len(approvals), titles


def build_morning_briefing(
    owner_account_id: str,
    *,
    now: Optional[datetime] = None,
    tz_name: str = BRIEFING_TIMEZONE,
    routine_store: Any = None,
    reminder_store: Any = None,
    calendar_store: Any = None,
    approvals_counter: Optional[Callable[[str], Any]] = None,
) -> str:
    """生成一份晨间简报（纯文本）。数据源全部可注入，缺省走真实存储。"""
    tz = ZoneInfo(tz_name)
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = moment.date()
    yesterday = today - timedelta(days=1)
    owner = str(owner_account_id or "")

    if routine_store is None:
        from src.tasks.routines import get_routine_store

        routine_store = get_routine_store()
    if reminder_store is None:
        from src.tasks.reminders import ReminderStore

        reminder_store = ReminderStore()
    if calendar_store is None:
        from src.calendar_ledger.calendar_store import CalendarStore

        calendar_store = CalendarStore()
    if approvals_counter is None:
        approvals_counter = _default_approvals_counter

    lines = [
        f"晨间简报 · {today.month}月{today.day}日 周{_WEEKDAYS[today.weekday()]}",
    ]
    sections: List[str] = []
    sections += _routine_sections(owner, today, yesterday, tz, routine_store)
    sections += _reminder_section(owner, today, moment, reminder_store)
    sections += _calendar_section(owner, today, tz, calendar_store)
    try:
        count, titles = approvals_counter(owner)
        if count:
            suffix = "：".join(["", *titles]) if titles else ""
            sections.append(f"待审批：{count} 件{suffix}")
    except Exception:  # noqa: BLE001 - 审批读不到不写假数字
        sections.append("待审批：暂时读不到（审批库读取失败）。")
    lines += sections
    if not sections:
        lines.append("今天暂时没有安排：没有到点的例程、提醒和日程，也没有待审批。")
    lines.append("需要调整就直接跟我说。")
    return "\n".join(lines)


# ==================== 例程模板库 ====================

ROUTINE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "morning_briefing",
        "name": "晨间简报",
        "schedule": "0 8 * * *",
        "action_kind": ACTION_MORNING_BRIEFING,
        "action_text": "",
        "match_name": "",
        "description": "每天 08:00 把昨日结果、今日例程/提醒/日程、待审批汇总成一份简报发到聊天。",
    },
    {
        "id": "evening_digest",
        "name": "晚间小结",
        "schedule": "0 21 * * *",
        "action_kind": "evening_digest",
        "action_text": "",
        "match_name": "",
        "description": "每天 21:00 汇总今天办完的事与没办成的事，发到聊天。",
    },
    {
        "id": "lunch_rest",
        "name": "午饭提醒",
        "schedule": "0 12 * * *",
        "action_kind": "rest_reminder",
        "action_text": "",
        "match_name": "午饭提醒",
        "description": "每天 12:00 轻声提醒吃午饭、歇一会儿。",
    },
    {
        "id": "late_night_rest",
        "name": "熬夜提醒",
        "schedule": "0 23 * * *",
        "action_kind": "rest_reminder",
        "action_text": "",
        "match_name": "熬夜提醒",
        "description": "每天 23:00 提醒休息，没干完的事贾维斯记着、不会丢。",
    },
    {
        "id": "weekly_kickoff",
        "name": "每周开场",
        "schedule": "0 9 * * 1",
        "action_kind": "chat_message",
        "action_text": "新的一周开始了，跟我说「看看这周的安排」，我把本周例程、提醒和日程一次过给你。",
        "match_name": "每周开场",
        "description": "每周一 09:00 在聊天里提醒过一遍本周安排。",
    },
]

_TEMPLATE_BY_ID = {item["id"]: item for item in ROUTINE_TEMPLATES}


def _find_template_routine(store: Any, owner: str, template: Dict[str, Any]):
    for routine in store.list_routines(owners=[owner], limit=500):
        if str(routine.get("action_kind") or "") != template["action_kind"]:
            continue
        if template.get("match_name"):
            if str(routine.get("name") or "") == template["match_name"]:
                return routine
        else:
            return routine
    return None


def list_template_states(store: Any, owner_account_id: str) -> List[Dict[str, Any]]:
    """每个模板当前状态：enabled / disabled / not_created（默认全是未创建）。"""
    owner = str(owner_account_id or "")
    states: List[Dict[str, Any]] = []
    for template in ROUTINE_TEMPLATES:
        routine = _find_template_routine(store, owner, template)
        states.append({
            "id": template["id"],
            "name": template["name"],
            "schedule": template["schedule"],
            "description": template["description"],
            "state": (
                "not_created" if routine is None
                else "enabled" if routine.get("enabled") else "disabled"
            ),
            "routine_id": (routine or {}).get("routine_id"),
        })
    return states


def set_template_enabled(
    store: Any,
    owner_account_id: str,
    *,
    created_by: str,
    template_id: str,
    enabled: bool,
) -> Dict[str, Any]:
    """启用/停用一个模板。启用且不存在时按模板创建例程；只改 enabled 语义。"""
    template = _TEMPLATE_BY_ID.get(str(template_id or ""))
    if template is None:
        raise ValueError(f"没有这个模板：{template_id}")
    owner = str(owner_account_id or "")
    routine = _find_template_routine(store, owner, template)
    if routine is None:
        if not enabled:
            return {"id": template_id, "state": "not_created"}
        routine = store.create_routine(
            name=template["match_name"] or template["name"],
            owner_account_id=owner,
            created_by=str(created_by or owner),
            trigger_type="schedule",
            schedule=template["schedule"],
            timezone_name=BRIEFING_TIMEZONE,
            action_kind=template["action_kind"],
            action_text=template["action_text"],
            plan_title="",
            plan_goal="",
            plan_steps=[],
        )
    store.update_routine(str(routine["routine_id"]), enabled=1 if enabled else 0)
    return {
        "id": template_id,
        "name": template["name"],
        "state": "enabled" if enabled else "disabled",
        "routine_id": routine["routine_id"],
    }
