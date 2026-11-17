"""本机日历：SQLite 存日程 + icalendar 读写 .ics + caldav 接外部日历。

提醒：日程开始前 N 分钟算\"到点\"，由调用方（定时 tick）查
:func:`CalendarStore.due_events` 后通知；本模块不自己起调度器，
不另引 APScheduler——网关已有自己的定时机制，提醒走它。
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Collection, List, Optional

from src.common.data_paths import resolve_store_path
from src.common.engines import EngineUnavailableError, data_dir

#: caldav 双许可（GPL-3.0-or-later / Apache-2.0）——本项目**按 Apache-2.0 使用**。
CALDAV_LICENSE_ELECTION = "Apache-2.0"

_ICALENDAR_HINT = (
    "日历导入导出还没装好：先装 icalendar（pip install icalendar）。"
)
_CALDAV_HINT = (
    "接外部日历还没装好：先装 caldav（pip install caldav）。"
    "本项目按 caldav 双许可中的 Apache-2.0 使用（见 THIRD-PARTY-NOTICES）。"
)


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: Optional[datetime] = None
    location: str = ""
    description: str = ""
    source: str = "local"  # local | ics-import | caldav
    uid: str = ""
    # 归属账号（2026-11-12 权限统一补）：空串 = 分账前的旧数据，默认归根账号
    # （只根可见）；新数据一律写创建者的 principal。
    account_id: str = ""


def _store_path() -> Path:
    return Path(
        resolve_store_path(
            "calendar.db",
            data_dir() / "calendar.db",
            env_var="LIUHAO_CALENDAR_DB",
        )
    )


class CalendarStore:
    """日程的本机存储：一个 SQLite 文件，无云端。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else _store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    start TEXT NOT NULL, end TEXT, location TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'local', uid TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL)"""
            )
            # 旧库迁移：补列（旧行 account_id 留空串 = 分账前数据，默认归根）。
            cols = [r[1] for r in conn.execute("PRAGMA table_info(calendar_events)")]
            if "account_id" not in cols:
                conn.execute("ALTER TABLE calendar_events ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _to_row(event: CalendarEvent) -> tuple:
        return (
            event.id, event.title, event.start.isoformat(),
            event.end.isoformat() if event.end else None,
            event.location, event.description, event.source, event.uid,
            event.account_id,
            datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CalendarEvent:
        return CalendarEvent(
            id=row["id"], title=row["title"],
            start=datetime.fromisoformat(row["start"]),
            end=datetime.fromisoformat(row["end"]) if row["end"] else None,
            location=row["location"] or "", description=row["description"] or "",
            source=row["source"] or "local", uid=row["uid"] or "",
            account_id=row["account_id"] if "account_id" in row.keys() else "",
        )

    def add(self, event: CalendarEvent) -> CalendarEvent:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO calendar_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                self._to_row(event),
            )
        return event

    def create(self, title: str, start: datetime, end: Optional[datetime] = None,
               location: str = "", description: str = "",
               source: str = "local", uid: str = "",
               account_id: str = "") -> CalendarEvent:
        return self.add(CalendarEvent(
            id=uuid.uuid4().hex[:16], title=title.strip(), start=start, end=end,
            location=location, description=description, source=source,
            uid=uid or uuid.uuid4().hex, account_id=account_id,
        ))

    def list(self, start: Optional[datetime] = None,
             end: Optional[datetime] = None,
             account_ids: Optional[Collection[str]] = None) -> List[CalendarEvent]:
        """列日程。``account_ids`` 给了就只返回这些账号的行（SQL 层过滤）；
        不给则不过滤（仅内部调用/旧测试用；网关一律传显式集合）。"""
        sql = "SELECT * FROM calendar_events"
        args: List[object] = []
        where: List[str] = []
        if start is not None:
            where.append("start >= ?")
            args.append(start.isoformat())
        if end is not None:
            where.append("start < ?")
            args.append(end.isoformat())
        if account_ids is not None:
            ids = list(account_ids)
            if not ids:
                return []
            where.append("account_id IN (%s)" % ",".join("?" * len(ids)))
            args.extend(ids)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY start ASC"
        with self._connect() as conn:
            return [self._from_row(r) for r in conn.execute(sql, args)]

    def get(self, event_id: str) -> Optional[CalendarEvent]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def delete(self, event_id: str,
               account_ids: Optional[Collection[str]] = None) -> bool:
        with self._connect() as conn:
            if account_ids is None:
                cur = conn.execute(
                    "DELETE FROM calendar_events WHERE id = ?", (event_id,)
                )
            else:
                ids = list(account_ids)
                if not ids:
                    return False
                cur = conn.execute(
                    "DELETE FROM calendar_events WHERE id = ? AND account_id IN (%s)"
                    % ",".join("?" * len(ids)),
                    (event_id, *ids),
                )
        return cur.rowcount > 0

    def due_events(self, now: Optional[datetime] = None,
                   within_minutes: int = 60,
                   account_ids: Optional[Collection[str]] = None) -> List[CalendarEvent]:
        """``now`` 起 ``within_minutes`` 分钟内开始的日程（提醒用）。"""
        now = now or datetime.now(timezone.utc)
        return self.list(start=now, end=now + timedelta(minutes=within_minutes),
                         account_ids=account_ids)

    # -- .ics 导入导出（icalendar，BSD-2） ---------------------------------

    def export_ics(self, account_ids: Optional[Collection[str]] = None) -> bytes:
        try:
            from icalendar import Calendar, Event as IcsEvent
        except Exception as exc:
            raise EngineUnavailableError("导出 .ics 需要 icalendar。", _ICALENDAR_HINT) from exc
        cal = Calendar()
        cal.add("prodid", "-//Jarvis//Jarvis Calendar//CN")
        cal.add("version", "2.0")
        for event in self.list(account_ids=account_ids):
            item = IcsEvent()
            item.add("uid", event.uid or event.id)
            item.add("summary", event.title)
            item.add("dtstart", event.start)
            if event.end:
                item.add("dtend", event.end)
            if event.location:
                item.add("location", event.location)
            if event.description:
                item.add("description", event.description)
            cal.add_component(item)
        return cal.to_ical()

    def import_ics(self, data: bytes, source: str = "ics-import",
                   account_id: str = "") -> int:
        """解析 .ics 字节并入库，返回导入条数。格式坏了抛 ValueError。"""
        try:
            from icalendar import Calendar
        except Exception as exc:
            raise EngineUnavailableError("导入 .ics 需要 icalendar。", _ICALENDAR_HINT) from exc
        try:
            cal = Calendar.from_ical(data)
        except Exception as exc:
            raise ValueError(f".ics 文件解析失败：{exc}") from exc
        count = 0
        for component in cal.walk("VEVENT"):
            summary = str(component.get("summary") or "").strip()
            dtstart = component.get("dtstart")
            if not summary or dtstart is None:
                continue
            start = dtstart.dt
            if isinstance(start, datetime):
                pass
            else:  # 全天日程只有日期：按当天 00:00（UTC）记
                start = datetime(start.year, start.month, start.day,
                                 tzinfo=timezone.utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            dtend = component.get("dtend")
            end = None
            if dtend is not None:
                raw_end = dtend.dt
                if isinstance(raw_end, datetime):
                    end = raw_end if raw_end.tzinfo else raw_end.replace(tzinfo=timezone.utc)
            self.create(
                title=summary, start=start, end=end,
                location=str(component.get("location") or ""),
                description=str(component.get("description") or ""),
                source=source, uid=str(component.get("uid") or ""),
                account_id=account_id,
            )
            count += 1
        return count


def sync_caldav(url: str, username: str, password: str,
                store: Optional[CalendarStore] = None,
                account_id: str = "") -> int:
    """从 CalDAV 服务拉日程进本机库，返回导入条数。

    ``password`` 只用于本次连接，**不存储**。这是用户自己选开的可选项：
    默认不连任何外部日历；连自建/Nextcloud 这类 CalDAV 服务时数据只在
    用户自己的服务器与本机之间走，不经过 Jarvis 的云（Jarvis 没有云）。
    Google / Apple 日历不在此列：它们要走各自的云 API，本轮不接。
    """
    try:
        import caldav
    except Exception as exc:
        raise EngineUnavailableError(
            "接外部日历需要 caldav。", _CALDAV_HINT,
        ) from exc
    store = store or CalendarStore()
    client = caldav.DAVClient(url=url, username=username, password=password)
    principal = client.principal()
    calendars = principal.calendars()
    imported = 0
    for calendar in calendars:
        try:
            events = calendar.events()
        except Exception:
            continue
        for event in events:
            try:
                data = event.data
            except Exception:
                continue
            if not data:
                continue
            imported += store.import_ics(
                data.encode("utf-8") if isinstance(data, str) else data,
                source="caldav", account_id=account_id,
            )
    return imported
