"""大升级 U4 契约：晨间简报跨源汇总 + 例程模板库默认关/一句话开关。

全部假数据 + 假时钟演练：简报每个数据源独立注入，断言内容只来自
真实记录、读不到时如实降级；模板默认不创建，启用才落成真例程，
到点由例程引擎真跑一次并把简报发到聊天（notify_chat 打桩捕获）。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.tasks import briefing
from src.tasks.briefing import (
    build_morning_briefing,
    list_template_states,
    set_template_enabled,
)
from src.tasks.routines import RoutineRunner, RoutineStore

TZ = ZoneInfo("America/Tijuana")
# 2026-11-18 是周三；本地 08:00 作为"现在"。
NOW = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)


def _ts(dt: datetime) -> float:
    return dt.timestamp()


class _FakeRoutineStore:
    def list_routines(self, owners=None, limit=100):
        today_run = datetime.combine(NOW.date(), time(9, 30), tzinfo=TZ)
        return [{
            "routine_id": "r1",
            "name": "每日抓取",
            "enabled": 1,
            "next_run_at": _ts(today_run),
        }]

    def list_runs(self, routine_id, limit=20):
        yesterday = datetime.combine(NOW.date() - timedelta(days=1), time(10, 0), tzinfo=TZ)
        return [
            {"status": "success", "started_at": _ts(yesterday)},
            {"status": "failed", "started_at": _ts(yesterday), "error": "网络超时"},
        ]


class _FakeReminderStore:
    def list(self, user_id, status=None, limit=100):
        return [
            {"title": "吃药", "repeat_kind": "daily", "hour": 9, "minute": 0, "weekday": None},
            {"title": "交报告", "repeat_kind": "once", "hour": None, "minute": None,
             "run_at": datetime(2026, 11, 18, 15, 0, tzinfo=TZ).isoformat()},
            {"title": "明天的事", "repeat_kind": "once", "hour": None, "minute": None,
             "run_at": datetime(2026, 11, 19, 9, 0, tzinfo=TZ).isoformat()},
        ]


class _Event:
    def __init__(self, title, start):
        self.title = title
        self.start = start


class _FakeCalendarStore:
    def list(self, start=None, end=None, account_ids=None):
        assert account_ids == ["alice"], "简报只能看本人日程"
        return [_Event("客户会议", datetime(2026, 11, 18, 14, 0, tzinfo=TZ))]


def _briefing_text(**overrides):
    kwargs = dict(
        now=NOW,
        routine_store=_FakeRoutineStore(),
        reminder_store=_FakeReminderStore(),
        calendar_store=_FakeCalendarStore(),
        approvals_counter=lambda owner: (2, ["采购合同", "报价单"]),
    )
    kwargs.update(overrides)
    return build_morning_briefing("alice", **kwargs)


class TestBriefingContent:
    def test_aggregates_all_sources(self):
        text = _briefing_text()
        assert "晨间简报" in text and "11月18日" in text
        assert "昨日例程：跑成 1 个" in text and "没跑成 1 个" in text
        assert "网络超时" in text
        assert "09:30 每日抓取" in text
        assert "09:00 吃药" in text and "15:00 交报告" in text
        assert "明天的事" not in text
        assert "14:00 客户会议" in text
        assert "待审批：2 件" in text and "采购合同" in text

    def test_broken_source_degrades_honestly(self):
        class _Broken:
            def list(self, *args, **kwargs):
                raise RuntimeError("db gone")

        text = _briefing_text(reminder_store=_Broken())
        assert "提醒：暂时读不到" in text
        assert "客户会议" in text, "一处坏了不许整份简报跟着没"

    def test_empty_day_is_honest(self):
        class _EmptyRoutines:
            def list_routines(self, owners=None, limit=100):
                return []

        class _EmptyReminders:
            def list(self, user_id, status=None, limit=100):
                return []

        class _EmptyCalendar:
            def list(self, start=None, end=None, account_ids=None):
                return []

        text = _briefing_text(
            routine_store=_EmptyRoutines(),
            reminder_store=_EmptyReminders(),
            calendar_store=_EmptyCalendar(),
            approvals_counter=lambda owner: (0, []),
        )
        assert "今天暂时没有安排" in text


@pytest.fixture()
def store(tmp_path):
    return RoutineStore(db_path=str(tmp_path / "routines.db"))


class TestTemplates:
    def test_templates_default_not_created(self, store):
        states = {item["id"]: item["state"] for item in list_template_states(store, "alice")}
        assert states == {
            "morning_briefing": "not_created",
            "evening_digest": "not_created",
            "lunch_rest": "not_created",
            "late_night_rest": "not_created",
            "weekly_kickoff": "not_created",
        }, "模板默认关：没经用户启用，一条都不许偷偷建"

    def test_enable_disable_roundtrip(self, store):
        out = set_template_enabled(
            store, "alice", created_by="alice",
            template_id="morning_briefing", enabled=True,
        )
        assert out["state"] == "enabled"
        states = {i["id"]: i["state"] for i in list_template_states(store, "alice")}
        assert states["morning_briefing"] == "enabled"
        out = set_template_enabled(
            store, "alice", created_by="alice",
            template_id="morning_briefing", enabled=False,
        )
        assert out["state"] == "disabled"
        states = {i["id"]: i["state"] for i in list_template_states(store, "alice")}
        assert states["morning_briefing"] == "disabled"

    def test_unknown_template_rejected(self, store):
        with pytest.raises(ValueError):
            set_template_enabled(
                store, "alice", created_by="alice",
                template_id="nope", enabled=True,
            )

    def test_briefing_routine_actually_fires(self, store, monkeypatch):
        set_template_enabled(
            store, "alice", created_by="alice",
            template_id="morning_briefing", enabled=True,
        )
        routine = next(
            r for r in store.list_routines(owners=["alice"], limit=10)
            if r["action_kind"] == "morning_briefing"
        )
        store.update_routine(routine["routine_id"], next_run_at=_ts(NOW) - 60)

        sent: list[str] = []
        import src.tasks.routines as routines_module

        monkeypatch.setattr(routines_module, "notify_chat", lambda text: sent.append(text) or True)
        # 简报生成时的数据源换成假的，避免碰真实存储
        import src.tasks.briefing as briefing_module

        monkeypatch.setattr(
            briefing_module, "build_morning_briefing",
            lambda owner, **kw: "晨间简报（演练）",
        )
        records = RoutineRunner(store).run_due(now=_ts(NOW))
        assert records and records[0]["status"] == "success"
        assert sent == ["晨间简报（演练）"]
