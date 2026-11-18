"""D-R4 croniter 引入的回归证据（完善第二轮）。

钉死差分探针实测的三处手搓分歧，防退回：
1. `5/15` 单值带步长语义（手搓只触发 :05，标准是 :05/:20/:35/:50）；
2. 闰日表达式在下一次到点 >366 天时被手搓扫描上限误拒；
3. workers 调度器此前对 cron 配置直接报错，现在能算到点延迟。
外加契约不变项：不可能的日期仍是 bad_cron、恰好落在到点时刻算下一次。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.tasks.routines import CronSchedule, RoutineError
from src.workers.scheduler import _cron_delay_seconds

TZ = ZoneInfo("America/Los_Angeles")


def _next(expr: str, after_iso: str) -> str:
    after = datetime.fromisoformat(after_iso).replace(tzinfo=TZ)
    return (
        CronSchedule(expr)
        .next_after(after.astimezone(timezone.utc), TZ)
        .astimezone(TZ)
        .strftime("%Y-%m-%d %H:%M")
    )


def test_stepped_single_value_follows_standard_semantics():
    assert _next("5/15 * * * *", "2026-11-17T10:00:00") == "2026-11-17 10:05"
    assert _next("5/15 * * * *", "2026-11-17T10:05:00") == "2026-11-17 10:20"
    assert _next("5/15 * * * *", "2026-11-17T10:20:00") == "2026-11-17 10:35"
    sched = CronSchedule("5/15 * * * *")
    assert sched.matches(datetime(2026, 11, 17, 10, 20, tzinfo=TZ))
    assert not sched.matches(datetime(2026, 11, 17, 10, 7, tzinfo=TZ))


def test_leap_day_expression_not_rejected_beyond_one_year():
    assert _next("0 0 29 2 *", "2025-03-01T00:00:00") == "2028-02-29 00:00"


def test_sparse_expression_next_is_fast():
    start = time.perf_counter()
    assert _next("0 0 29 2 *", "2027-06-01T00:00:00") == "2028-02-29 00:00"
    # 手搓分钟扫描约 0.76s；给 croniter 留足余量，只钉「数量级变快」。
    assert time.perf_counter() - start < 0.3


def test_impossible_date_still_bad_cron():
    with pytest.raises(RoutineError) as excinfo:
        _next("0 0 30 2 *", "2026-01-01T00:00:00")
    assert excinfo.value.code == "bad_cron"


def test_exact_boundary_counts_as_next_occurrence():
    assert _next("0 8 * * *", "2026-11-17T08:00:00") == "2026-11-18 08:00"
    assert _next("0 8 * * *", "2026-11-17T07:59:00") == "2026-11-17 08:00"


def test_standard_expressions_unchanged():
    assert _next("0 9-17 * * mon-fri", "2026-11-14T18:00:00") == "2026-11-16 09:00"
    assert _next("0 0 1 * 1", "2026-11-17T00:00:00") == "2026-11-23 00:00"
    assert _next("0 8 * jan mon", "2026-11-17T00:00:00") == "2027-01-04 08:00"
    assert _next("0 9 31 * *", "2026-02-01T00:00:00") == "2026-03-31 09:00"


def test_worker_scheduler_cron_delay():
    now = datetime(2026, 11, 17, 10, 7, 30, tzinfo=timezone.utc)
    assert _cron_delay_seconds("* * * * *", now=now) == pytest.approx(30.0)
    delay = _cron_delay_seconds("0 8 * * *", now=now)
    assert 0 < delay <= 86400
    assert _cron_delay_seconds("* * * * *", now=now, offset_seconds=90) == pytest.approx(120.0)
    with pytest.raises(Exception):
        _cron_delay_seconds("not a cron", now=now)
