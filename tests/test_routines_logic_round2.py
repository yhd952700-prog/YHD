"""例程引擎逻辑专属测试（完善第二轮 A4）。

覆盖：cron 解析/校验/下一次触发计算、RoutineStore 的建/改/删/所有者
过滤、运行记录与失败聚合、全局暂停开关、错过执行决策。临时 SQLite。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.tasks.routines import (
    CronSchedule,
    RoutineError,
    RoutineStore,
    decide_missed_run,
)

UTC = ZoneInfo("UTC")


def test_cron_daily_next_after():
    sched = CronSchedule("0 9 * * *")
    nxt = sched.next_after(datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc), UTC)
    assert (nxt.day, nxt.hour, nxt.minute) == (17, 9, 0)
    after = sched.next_after(datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc), UTC)
    assert after.day == 18


def test_cron_weekday_names_skip_weekend():
    sched = CronSchedule("30 8 * * mon-fri")
    saturday = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)  # 2026-11-21 周六
    nxt = sched.next_after(saturday, UTC)
    assert nxt.weekday() == 0  # 跳到周一


def test_cron_step_values():
    sched = CronSchedule("*/15 * * * *")
    nxt = sched.next_after(datetime(2026, 11, 17, 10, 7, tzinfo=timezone.utc), UTC)
    assert nxt.minute == 15


def test_cron_matches_dom_dow_or_rule():
    sched = CronSchedule("0 9 1 * mon")  # 日与周都写：任一满足即触发（cron 老规矩）
    assert sched.matches(datetime(2026, 11, 1, 9, 0)) is True   # 1 号（周日也无妨）
    assert sched.matches(datetime(2026, 11, 2, 9, 0)) is True   # 周一
    assert sched.matches(datetime(2026, 11, 3, 9, 0)) is False  # 既非 1 号也非周一


@pytest.mark.parametrize("bad", ["61 * * * *", "* 25 * * *", "* * 32 * *", "* * * 13 *", "* * * * 8", "x * * * *", "0 9 * *"])
def test_cron_rejects_invalid(bad):
    with pytest.raises(RoutineError):
        CronSchedule(bad)


@pytest.fixture()
def store(tmp_path):
    s = RoutineStore(db_path=str(tmp_path / "routines.db"))
    yield s
    s.close()


def _mk(store, name="晨报", owner="boss"):
    return store.create_routine(
        name=name, owner_account_id=owner, created_by=owner,
        trigger_type="schedule", schedule="0 9 * * *", timezone_name="UTC",
        action_kind="chat", action_text="汇总待办", plan_title=name,
        plan_goal="每日汇总", plan_steps=[],
    )


def test_routine_store_crud_and_owner_filter(store):
    r1 = _mk(store)
    _mk(store, name="晚报", owner="alice")
    rid = r1["routine_id"]
    got = store.get_routine(rid)
    assert got is not None and got["name"] == "晨报"
    assert got["next_run_at"] is not None  # 定时例程建时就算好下一次
    assert len(store.list_routines(owners=["boss"])) == 1
    assert len(store.list_routines(owners=["alice"])) == 1
    assert len(store.list_routines()) == 2
    store.update_routine(rid, name="晨报改")
    assert store.get_routine(rid)["name"] == "晨报改"
    store.delete_routine(rid)
    assert store.get_routine(rid) is None


def test_routine_run_records_and_failures(store):
    rid = _mk(store)["routine_id"]
    now = time.time()
    store.record_run(routine_id=rid, trigger="cron", reason="", status="ok",
                     started_at=now - 10, ended_at=now - 9)
    store.record_run(routine_id=rid, trigger="cron", reason="", status="failed",
                     error="boom", started_at=now - 5, ended_at=now - 4)
    runs = store.list_runs(rid)
    assert len(runs) == 2
    failures = store.recent_failures(owners=["boss"])
    assert len(failures) == 1
    assert failures[0]["routine_id"] == rid


def test_routine_paused_all_switch(store):
    assert store.get_paused_all() is False
    assert store.set_paused_all(True) is True
    assert store.get_paused_all() is True
    assert store.set_paused_all(False) is False
    assert store.get_paused_all() is False


def test_decide_missed_run_branches():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc).timestamp()
    daily = {"schedule": "0 9 * * *", "next_run_at": now + 3600}
    assert decide_missed_run(daily, now) == "due"
    just_missed = {"schedule": "0 9 * * *", "next_run_at": now - 3600}
    assert decide_missed_run(just_missed, now) == "catch_up_once"
    long_missed = {"schedule": "0 9 * * *", "next_run_at": now - 3 * 24 * 3600}
    assert decide_missed_run(long_missed, now) == "skip"
    no_next = {"schedule": "0 9 * * *", "next_run_at": None}
    assert decide_missed_run(no_next, now) == "due"
