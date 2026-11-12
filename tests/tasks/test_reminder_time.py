"""自然语言时间解析：能算的算，算不准必须反问（不猜）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.tasks.reminder_time import NeedsClarification, parse_reminder

TZ = timezone(timedelta(hours=8))
# 2026-01-01 00:00 UTC = 2026-01-01 08:00 +08:00（周四）
NOW = datetime(2026, 1, 1, 0, 0, 0)


def _parse(text: str, now: datetime = NOW):
    return parse_reminder(text, now, tz=TZ)


def test_delay_days_is_computed_exactly():
    spec = _parse("三天后提醒我交材料")
    assert spec.title == "交材料"
    assert spec.repeat_kind == "once"
    assert spec.run_at == NOW + timedelta(days=3)
    assert spec.time_text == "三天后"


def test_delay_seconds_minutes_and_half_hour():
    assert _parse("2秒后提醒我测试").run_at == NOW + timedelta(seconds=2)
    assert _parse("5分钟后提醒我喝水").run_at == NOW + timedelta(minutes=5)
    assert _parse("半小时后提醒我休息").run_at == NOW + timedelta(minutes=30)


def test_daily_fixed_clock_and_repeat_rule():
    spec = _parse("每天十点提醒我喝水")
    assert spec.repeat_kind == "daily"
    assert (spec.hour, spec.minute) == (10, 0)
    # 本地 10:00 = UTC 02:00
    assert spec.run_at == datetime(2026, 1, 1, 2, 0)
    assert spec.title == "喝水"


def test_daily_half_past():
    spec = _parse("每天十点半提醒我起身")
    assert (spec.hour, spec.minute) == (10, 30)
    assert spec.run_at == datetime(2026, 1, 1, 2, 30)


def test_weekly_with_period():
    spec = _parse("每周一早上九点提醒我开会")
    assert spec.repeat_kind == "weekly"
    assert spec.weekday == 0
    assert (spec.hour, spec.minute) == (9, 0)
    # 2026-01-01 是周四 → 下周一 2026-01-05 09:00 本地 = 01:00 UTC
    assert spec.run_at == datetime(2026, 1, 5, 1, 0)
    assert spec.title == "开会"


def test_tomorrow_afternoon():
    spec = _parse("明天下午三点提醒我打电话")
    assert spec.run_at == datetime(2026, 1, 2, 7, 0)  # 15:00 +08:00
    assert spec.title == "打电话"


def test_tomorrow_colon_time():
    spec = _parse("明天上午9:30提醒我发邮件")
    assert spec.run_at == datetime(2026, 1, 2, 1, 30)


def test_evening_period_maps_to_20():
    spec = _parse("晚上八点提醒我散步")
    assert spec.run_at == datetime(2026, 1, 1, 12, 0)  # 20:00 +08:00


def test_quarter_and_quarter_past_minutes():
    assert _parse("十点一刻提醒我").minute == 15
    assert _parse("十点三刻提醒我").minute == 45


def test_weekday_without_prefix_goes_to_next_occurrence():
    spec = _parse("周一九点提醒我开会")
    assert spec.run_at == datetime(2026, 1, 5, 1, 0)


def test_today_time_already_passed_must_ask():
    # 本地 12:00，用户说"今天十点" —— 已经过了，不能擅自安排到明天。
    with pytest.raises(NeedsClarification) as excinfo:
        parse_reminder(
            "今天十点提醒我开会",
            datetime(2026, 1, 1, 4, 0),
            tz=TZ,
        )
    assert "已经过了" in excinfo.value.question


@pytest.mark.parametrize(
    "text",
    [
        "下周那个会",
        "明天提醒我",
        "晚点提醒我",
        "有空时提醒我",
        "提醒我交材料",
        "每周一提醒我开会",
        "每天提醒我喝水",
    ],
)
def test_ambiguous_time_asks_instead_of_guessing(text):
    with pytest.raises(NeedsClarification) as excinfo:
        _parse(text)
    question = excinfo.value.question
    assert "具体" in question or "几点" in question or "时刻" in question


def test_empty_text_asks():
    with pytest.raises(NeedsClarification):
        _parse("   ")


def test_title_strips_reminder_filler():
    spec = _parse("三天后的会议要提醒我参加")
    assert "提醒我" not in spec.title
    assert spec.title == "会议要参加"


def test_ascii_digits_are_accepted():
    # 回归：_cn_int 曾把 ASCII "9" 当汉字查表 → KeyError: '9'
    assert _parse("明天上午9点提醒我开会").run_at == datetime(2026, 1, 2, 1, 0)
    assert _parse("明天09点提醒我开会").run_at == datetime(2026, 1, 2, 1, 0)
    assert _parse("明天上午9点15分提醒我开会").run_at == datetime(2026, 1, 2, 1, 15)
    assert _parse("明天10:30提醒我开会").run_at == datetime(2026, 1, 2, 2, 30)


@pytest.mark.parametrize(
    "text",
    [
        "12月25日上午九点提醒我开会",
        "2026-12-25 09:00 提醒我开会",
        "2026/12/25 09:00 提醒我开会",
        "2026年12月25日上午九点提醒我开会",
        "12月25号上午九点提醒我开会",
    ],
)
def test_absolute_date_lands_on_that_day_not_tomorrow(text):
    spec = _parse(text)
    # 本地 2026-12-25 09:00 = UTC 01:00，而不是 NOW+1 天
    assert spec.run_at == datetime(2026, 12, 25, 1, 0)
    assert spec.repeat_kind == "once"


def test_invalid_absolute_date_asks_instead_of_guessing():
    with pytest.raises(NeedsClarification):
        _parse("2026-02-30 09:00 提醒我开会")
    with pytest.raises(NeedsClarification):
        _parse("2月30日上午九点提醒我开会")


def test_past_absolute_date_asks_instead_of_silently_rescheduling():
    # 本地现在 2026-01-01 08:00；"2025-12-25" 已经过去，不能偷偷改到明天
    with pytest.raises(NeedsClarification) as excinfo:
        _parse("2025-12-25 09:00 提醒我开会")
    assert "已经过去" in excinfo.value.question


def test_unrecognised_date_shape_asks_instead_of_today_or_tomorrow():
    # 这些写法本解析器不认识，必须反问；绝不能落到 today/tomorrow
    for text in ("25号上午九点提醒我开会", "下个月5号上午九点提醒我开会", "月底上午九点提醒我"):
        with pytest.raises(NeedsClarification):
            _parse(text)


def test_monthly_repeat_is_explicitly_rejected():
    with pytest.raises(NeedsClarification) as excinfo:
        _parse("每月5号上午九点提醒我开会")
    assert "暂不支持每月重复" in excinfo.value.question

