"""视频会议 HTTP 面（A3 集成与会议组，默认 Google Meet）。

会议从日历来：本机日历（``/v1/calendar`` 同库）+ Google 日历
（``/v1/meetings/google``，没配 Google 账号时 409、不编会议）。

会前带他进会：``GET /v1/meetings/upcoming`` 给提醒 + 进会入口数据
（时间、Meet 链接、议程=日程备注）；提醒 tick 仍走既有的
``/v1/calendar/due``。

会中 Bot 参与——**先核实平台允不允许，再动手**：
Google Meet 没有官方 Bot 进会聊天的接口，Teams/Zoom 的 Bot 也要
另走各自的官方 Bot 框架注册、且不能假装“进了会聊天”。所以本模块
**没有“Bot 进会”这个能力**：不伪造“我进了会”的记录、不伪造
会中发言。替代路：会后按 Joseph 给的记录/纪要整理决策、落跟进任务
（``POST /v1/meetings/{id}/wrap-up``），跟进任务是真实的小队任务。

平台限制说明见 ``docs/meeting-platform-limits.md``，
``GET /v1/meetings/platforms`` 返回同一份口径的机器版。
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .account_context import AccountContext, require_account_principal
from src.calendar_ledger.calendar_store import CalendarStore

router = APIRouter(prefix="/v1/meetings", tags=["meetings"])

_MEET_RE = re.compile(r"https?://meet\.google\.com/[a-z0-9\-]+", re.IGNORECASE)
_TEAMS_RE = re.compile(r"https?://teams\.microsoft\.com/[^\s\"']+", re.IGNORECASE)
_ZOOM_RE = re.compile(r"https?://(?:[a-z0-9\-]+\.)?zoom\.us/j/[^\s\"']+", re.IGNORECASE)


def _calendar_store() -> CalendarStore:
    return CalendarStore()


def _visible_accounts(context: AccountContext) -> list:
    ids = list(context.subtree)
    if context.is_root:
        ids.append("")
    return ids


def _meeting_url(location: str, description: str) -> Dict[str, str]:
    """从地点/备注里抠会议链接，并认出是哪个平台（抠不到就是空串，不编）。"""
    text = f"{location or ''}\n{description or ''}"
    for pattern, platform in (
        (_MEET_RE, "meet"),
        (_TEAMS_RE, "teams"),
        (_ZOOM_RE, "zoom"),
    ):
        match = pattern.search(text)
        if match:
            return {"url": match.group(0), "platform": platform}
    return {"url": "", "platform": ""}


def _event_out(event: Any) -> Dict[str, Any]:
    link = _meeting_url(event.location, event.description)
    return {
        "id": event.id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "location": event.location,
        "description": event.description,
        "source": event.source,
        "meeting_url": link["url"],
        "meeting_platform": link["platform"],
        "bot_attended": False,
        "bot_note": "Bot 不进会：Meet 没有官方 Bot 进会聊天的接口，会后按记录整理决策。",
    }


def _join_info(event: Any) -> Dict[str, Any]:
    """会前进会入口数据：时间、链接、议程（议程就是日程备注，不编）。"""
    base = _event_out(event)
    return {
        **base,
        "agenda": event.description or "",
        "join_hint": (
            "点 meeting_url 进会（浏览器或 Meet 应用打开）。"
            if base["meeting_url"]
            else "这条日程里没有会议链接：进会链接请找会议组织者要，Jarvis 不编链接。"
        ),
    }


# ---------------------------------------------------------------- 会议平台限制（与 docs/meeting-platform-limits.md 同口径）

PLATFORM_LIMITS: List[Dict[str, Any]] = [
    {
        "platform": "meet",
        "display_name": "Google Meet",
        "bot_can_join_chat": False,
        "why": (
            "Google Meet 没有官方 Bot 进会聊天的接口：没有“让机器人进会、"
            "在会里发言/听会”的公开 API。有的只是日历上的会议链接"
            "（Calendar API 可读）和会后录制/转录（需另开录制，且要参会人同意）。"
        ),
        "what_we_do": [
            "会前：从日历读会议，给提醒、进会链接、议程。",
            "会后：按 Joseph 给的记录/纪要整理决策、落跟进任务。",
        ],
        "alternative": "会后整理（本模块已做）；实时转录要另接 Meet 录制 + 转录服务，本轮不做。",
    },
    {
        "platform": "teams",
        "display_name": "Microsoft Teams",
        "bot_can_join_chat": False,
        "why": (
            "Teams 有官方 Bot 框架（Bot Framework），但那是“在 Teams 里"
            "加一个机器人应用、走企业租户注册与管理员审批”，不是“随便一个"
            "Bot 就能进任意会议聊天”。Joseph 的 Teams 租户、管理员审批"
            "都没走过，本轮不假装接通。"
        ),
        "what_we_do": ["会前：日程里的 Teams 链接能识别、能给进会入口。"],
        "alternative": "以后要走：企业租户注册 Teams Bot 应用 + 管理员审批 + 会议策略放行。",
    },
    {
        "platform": "zoom",
        "display_name": "Zoom",
        "bot_can_join_chat": False,
        "why": (
            "Zoom 有官方 Meeting SDK / Bot 方案，但同样要开发者账号、"
            "应用审核与会议主持人放行，且录制/转录要参会人知情同意。"
            "本轮没注册、没审核，不假装接通。"
        ),
        "what_we_do": ["会前：日程里的 Zoom 链接能识别、能给进会入口。"],
        "alternative": "以后要走：Zoom 开发者账号注册应用、过审、主持人放行后接 Meeting SDK。",
    },
]


# ---------------------------------------------------------------- 会后整理存储


def _wrapups_path() -> str:
    from src.common.data_paths import resolve_store_path

    repo_root = Path(__file__).resolve().parents[2]
    return resolve_store_path(
        "meeting_wrapups.db",
        repo_root / "data" / "meeting_wrapups.db",
        env_var="LIUHAO_MEETING_WRAPUPS_DB",
    )


class WrapupStore:
    """会后整理记录库（本机 SQLite，不落仓库）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _wrapups_path()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS meeting_wrapups (
                    event_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    decisions TEXT NOT NULL DEFAULT '[]',
                    follow_ups TEXT NOT NULL DEFAULT '[]',
                    task_ids TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL DEFAULT '',
                    bot_attended INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL)"""
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def save(
        self,
        event_id: str,
        *,
        title: str,
        decisions: List[str],
        follow_ups: List[Dict[str, Any]],
        task_ids: List[str],
        notes: str,
        account_id: str,
    ) -> Dict[str, Any]:
        import json

        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO meeting_wrapups(event_id, title, decisions,"
                " follow_ups, task_ids, notes, account_id, bot_attended,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?,0,?,?)"
                " ON CONFLICT(event_id) DO UPDATE SET title=excluded.title,"
                " decisions=excluded.decisions, follow_ups=excluded.follow_ups,"
                " task_ids=excluded.task_ids, notes=excluded.notes,"
                " updated_at=excluded.updated_at",
                (
                    event_id, title, json.dumps(decisions, ensure_ascii=False),
                    json.dumps(follow_ups, ensure_ascii=False),
                    json.dumps(task_ids), notes, account_id, now, now,
                ),
            )
        return self.get(event_id) or {"event_id": event_id}

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        import json

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM meeting_wrapups WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in ("decisions", "follow_ups", "task_ids"):
            try:
                data[key] = json.loads(data[key] or "[]")
            except ValueError:
                data[key] = []
        data["bot_attended"] = False
        return data


def _wrapup_store() -> WrapupStore:
    return WrapupStore()


# ---------------------------------------------------------------- 端点


@router.get("/platforms")
def list_platforms() -> Dict[str, Any]:
    """会议平台限制（机器版，与 docs/meeting-platform-limits.md 同口径）。"""
    return {"platforms": PLATFORM_LIMITS}


@router.get("")
def list_meetings(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """会议从日历来：本机日历里的日程（含 Meet/Teams/Zoom 链接识别）。"""
    events = _calendar_store().list(
        start=start, end=end, account_ids=_visible_accounts(context)
    )
    return {"meetings": [_event_out(e) for e in events], "count": len(events)}


@router.get("/upcoming")
def upcoming_meetings(
    within_minutes: int = Query(default=60, ge=1, le=1440),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """会前进会入口：未来 N 分钟内开始的会议（时间、链接、议程）。"""
    events = _calendar_store().due_events(
        within_minutes=within_minutes, account_ids=_visible_accounts(context)
    )
    return {"meetings": [_join_info(e) for e in events], "count": len(events)}


@router.get("/google")
def google_meetings(
    time_min: str = "",
    time_max: str = "",
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """Google 日历上的会议（含 Meet 链接）。没配 Google 账号时 409，不编会议。"""
    from src.integrations.google_workspace import GoogleCalendarClient, NotConfiguredError

    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_configured", "message": str(exc)},
        ) from exc
    try:
        events = client.list_events(time_min=time_min, time_max=time_max)
    except Exception as exc:  # noqa: BLE001 - 上游错误如实转 502
        raise HTTPException(status_code=502, detail=f"Google 日历读取失败：{exc}") from exc
    return {
        "meetings": [
            {**e, "bot_attended": False,
             "bot_note": "Bot 不进会：Meet 没有官方 Bot 进会聊天的接口，会后按记录整理决策。"}
            for e in events
        ],
        "count": len(events),
        "source": "google-calendar",
    }


@router.get("/{event_id}")
def get_meeting(
    event_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """一条会议 + 会前进会入口数据（时间、链接、议程）。"""
    event = _calendar_store().get(event_id)
    if event is None or event.account_id not in _visible_accounts(context):
        raise HTTPException(status_code=404, detail="没有这场会议")
    return _join_info(event)


class FollowUpIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    assignee: str = Field(default="", max_length=100)


class WrapUpIn(BaseModel):
    decisions: List[str] = Field(default_factory=list, max_length=50)
    follow_ups: List[FollowUpIn] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=5000)


@router.post("/{event_id}/wrap-up", status_code=201)
def wrap_up_meeting(
    event_id: str,
    body: WrapUpIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """会后整理：Joseph 给的决策 + 跟进任务（跟进任务是真实的小队任务）。

    ``bot_attended`` 恒为 False：Bot 没进会，整理依据是 Joseph 这次
    提交的记录/纪要，不是会里听来的——绝不伪造“我进了会”。
    """
    event = _calendar_store().get(event_id)
    if event is None or event.account_id not in _visible_accounts(context):
        raise HTTPException(status_code=404, detail="没有这场会议")
    from src.team.store import TeamStore

    team = TeamStore()
    task_ids: List[str] = []
    follow_ups: List[Dict[str, Any]] = []
    for item in body.follow_ups:
        title = item.title.strip()
        assignee = item.assignee.strip()
        label = f"[跟进] {title}" + (f"（负责人：{assignee}）" if assignee else "")
        task = team.create_task(label, context.principal)
        task_ids.append(str(task.get("task_id") or ""))
        follow_ups.append({"title": title, "assignee": assignee, "task_id": task_ids[-1]})
    decisions = [d.strip() for d in body.decisions if d.strip()]
    saved = _wrapup_store().save(
        event_id,
        title=event.title,
        decisions=decisions,
        follow_ups=follow_ups,
        task_ids=task_ids,
        notes=body.notes.strip(),
        account_id=context.principal,
    )
    return {
        **saved,
        "bot_attended": False,
        "bot_note": "Bot 没进会：这份整理依据是你这次提交的记录/纪要，不是会里听来的。",
    }


@router.get("/{event_id}/wrap-up")
def get_wrap_up(
    event_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """取一场的会后整理；还没整理过时 404 + 老实说明（不编一份）。"""
    event = _calendar_store().get(event_id)
    if event is None or event.account_id not in _visible_accounts(context):
        raise HTTPException(status_code=404, detail="没有这场会议")
    saved = _wrapup_store().get(event_id)
    if saved is None:
        raise HTTPException(
            status_code=404,
            detail="这场会还没整理过：会后把记录/纪要发给 Jarvis 再整理。",
        )
    return saved
