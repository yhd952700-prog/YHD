"""中文自然语言 → 提醒时间解析（确定性，算不准就反问）。

设计原则
--------
* **能算的算**：``3 天后``、``2 秒后``、``明天下午三点``、``每天十点``、
  ``每周一早上九点`` 都能解析成绝对时间。
* **算不准的一律反问**：缺时刻（``明天提醒我``）、日期含糊
  （``下周那个会``）、口语模糊（``晚点``/``有空时``）都抛
  :class:`NeedsClarification`，由工具层把问题原样问回用户。
  **不做"猜一个最接近的时间"的兜底** —— 猜错时间比反问更糟。

时区
----
仓库内部统一存 naive UTC（见 ``src/_time.py``）。用户说的"十点"是**本地**
十点，因此这里先按本地时区解析，再换算成 naive UTC。

本地时区优先级：``LIUHAO_TIMEZONE_OFFSET_HOURS`` → ``TZ``（IANA 名，
如 ``Asia/Shanghai``）→ 默认 ``Asia/Shanghai`` → 系统时区 → UTC。
注意 ``LIUHAO_TIMEZONE_OFFSET_HOURS`` 是**固定偏移**，不随夏令时（DST）
变化；需要 DST 语义时请用 ``TZ=America/New_York`` 这类 IANA 时区名。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from src._time import utc_now

WEEKDAY_NAMES = "一二三四五六日"
_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_PERIOD_WORDS = (
    "凌晨",
    "清晨",
    "早上",
    "早晨",
    "上午",
    "中午",
    "午后",
    "下午",
    "傍晚",
    "晚上",
    "晚间",
    "夜里",
    "夜间",
)

_DELAY_RE = re.compile(
    r"(?P<num>(?:\d+(?:\.\d+)?)|[零〇一二两三四五六七八九十百]+|半)"
    r"\s*个?\s*(?P<unit>秒钟|秒|分钟|分|小时|钟头|天|日|周|星期)"
    r"\s*(?:之|以)?后"
)

_UNIT_SECONDS = {
    "秒": 1.0,
    "秒钟": 1.0,
    "分": 60.0,
    "分钟": 60.0,
    "小时": 3600.0,
    "钟头": 3600.0,
    "天": 86400.0,
    "日": 86400.0,
    "周": 604800.0,
    "星期": 604800.0,
}

_REL_DAY_RE = re.compile(r"(?P<day>大后天|后天|明天|明日|今天|今日)")
_REL_OFFSET = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
    "大后天": 3,
}

_REPEAT_DAILY_RE = re.compile(r"(?P<every>每天|每日|天天)")
_REPEAT_WEEKLY_RE = re.compile(
    r"每\s*(?:个\s*)?(?:周|星期|礼拜)\s*(?P<wd>[一二三四五六日天])"
)

_WEEKDAY_RE = re.compile(
    r"(?P<prefix>下下|下|本|这)?\s*(?:周|星期|礼拜)\s*(?P<wd>[一二三四五六日天])"
)

_VAGUE_RE = re.compile(
    r"晚点|晚些|稍后|待会|待会儿|过会|过会儿|有空|改天|回头|改日|等会|等会儿"
)
_DATE_HINT_RE = re.compile(
    r"今天|今日|明天|明日|后天|大后天|下周|下个?星期|这周|本周|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]|"
    r"\d{1,4}\s*[年月日号]|[一二三四五六七八九十]{1,3}\s*月"
)

# 「2026-12-25」「2026/12/25」「2026.12.25」「2026年12月25日」。
_ABS_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*[-/.年]\s*(?P<month>\d{1,2})\s*[-/.月]\s*"
    r"(?P<day>\d{1,2})\s*[日号]?"
)
# 「12月25日」「12月25号」：缺年份，按今年/明年判定；已过则反问。
_CN_MONTH_DAY_RE = re.compile(
    r"(?P<month>\d{1,2}|[一二三四五六七八九十]{1,3})\s*月\s*"
    r"(?P<day>\d{1,2}|[一二三四五六七八九十]{1,3})\s*[日号]"
)
# 「每月5号」：识别出来但明确拒绝，避免静默落成 today/tomorrow。
_MONTHLY_RE = re.compile(
    r"每\s*(?:个\s*)?月\s*(?P<day>\d{1,2}|[一二三四五六七八九十]{1,3})\s*[日号]"
)
# 看起来像日期、但本解析器不认识的形态：必须反问，不许猜。
_DATE_LIKE_RE = re.compile(
    r"(?:下下|下|上|本|这个|下个|上个)\s*月|"
    r"\d{1,2}\s*月|"
    r"月\s*(?:底|初|中)|"
    r"\d{1,2}\s*[日号]|"
    r"\d{4}\s*[-/.年]"
)

_COLON_RE = re.compile(r"(?P<hour>\d{1,2})\s*[:：]\s*(?P<minute>\d{1,2})")
_CLOCK_RE = re.compile(
    rf"(?P<period>{'|'.join(_PERIOD_WORDS)})?\s*"
    r"(?P<hour>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*(?:点|时)"
    r"(?P<minute>半|一刻|三刻|(?:\d{1,2}|[零〇一二三四五六七八九十]{1,3})分)?"
)

_FILLER_RE = re.compile(
    r"(?:提醒我一下|提醒一下我|提醒我|提醒一下|提醒下我|提醒|叫我一声|叫我|"
    r"记得|别忘了|别忘记|麻烦|帮我|请你|请)"
)
_EDGE_RE = re.compile(r"^[\s,，。、;；:：!！?？~～.。的]+|[\s,，。、;；:：!！?？~～.。的]+$")


class NeedsClarification(ValueError):
    """时间无法唯一确定时抛出；``question`` 直接问回用户。"""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


@dataclass(frozen=True)
class ReminderSpec:
    """解析结果（绝对时间用 naive UTC，重复规则里的时分用本地时间）。"""

    title: str
    run_at: datetime
    repeat_kind: str  # once / daily / weekly
    weekday: Optional[int] = None  # 0=周一
    hour: Optional[int] = None
    minute: Optional[int] = None
    time_text: str = ""
    source_text: str = ""


def local_timezone() -> tzinfo:
    """本地时区（优先 IANA ``ZoneInfo``，其次固定偏移，最后系统时区）。

    ``LIUHAO_TIMEZONE_OFFSET_HOURS`` 是**固定偏移**，不随夏令时变化，主要
    给测试/无 tzdata 环境用；需要 DST 语义请设 ``TZ`` 为 IANA 时区名。
    """
    raw = os.environ.get("LIUHAO_TIMEZONE_OFFSET_HOURS")
    if raw:
        try:
            return timezone(timedelta(hours=float(raw)))
        except ValueError as exc:
            raise ValueError(
                "LIUHAO_TIMEZONE_OFFSET_HOURS must be a number, "
                f"got {raw!r}"
            ) from exc
    name = (os.environ.get("TZ") or "").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - 缺 tzdata / 非法 TZ 时退回系统时区
        pass
    tz = datetime.now().astimezone().tzinfo
    return tz or timezone.utc


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _to_utc_naive(local_dt: datetime) -> datetime:
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def _cn_int(token: str) -> int:
    token = token.strip()
    if not token:
        raise ValueError("empty number")
    if token.isdigit():
        return int(token)
    if "百" in token:
        left, _, right = token.partition("百")
        hundreds = _CN_DIGITS[left] if left else 1
        return hundreds * 100 + (_cn_int(right) if right else 0)
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGITS[left] if left else 1
        ones = _CN_DIGITS[right] if right else 0
        return tens * 10 + ones
    if len(token) == 1:
        return _CN_DIGITS[token]
    return int("".join(str(_CN_DIGITS[ch]) for ch in token))


def _number(token: str) -> float:
    token = token.strip()
    if token == "半":
        return 0.5
    try:
        return float(token)
    except ValueError:
        return float(_cn_int(token))


def _delay_seconds(match: re.Match) -> float:
    amount = _number(match.group("num"))
    seconds = amount * _UNIT_SECONDS[match.group("unit")]
    if seconds <= 0:
        raise NeedsClarification("提醒时间必须晚于现在，请重新说一个未来的时间。")
    return seconds


def _apply_period(hour: int, period: Optional[str]) -> int:
    if period in ("下午", "午后", "傍晚", "晚上", "晚间", "夜里", "夜间"):
        if hour == 12 and period in ("晚上", "晚间", "夜里", "夜间"):
            return 0
        return hour + 12 if hour < 12 else hour
    if period == "中午":
        return hour + 12 if hour < 11 else hour
    if period == "凌晨":
        return 0 if hour == 12 else hour
    return hour


def _period_before(text: str, index: int) -> Optional[str]:
    prefix = text[:index].rstrip()
    for word in sorted(_PERIOD_WORDS, key=len, reverse=True):
        if prefix.endswith(word):
            return word
    return None


def _parse_minute(token: Optional[str]) -> int:
    if not token:
        return 0
    if token == "半":
        return 30
    if token == "一刻":
        return 15
    if token == "三刻":
        return 45
    return _cn_int(token.rstrip("分"))


def _overlaps(span: Tuple[int, int], others) -> bool:
    start, end = span
    return any(start < other_end and end > other_start for other_start, other_end in others)


def _extract_clock(
    text: str, skip_spans=()
) -> Optional[Tuple[int, int, Tuple[int, int], str, Optional[str]]]:
    """返回 ``(hour, minute, span, raw, period)``；没有可识别时刻返回 None。

    ``skip_spans`` 是日期/重复词（周一、明天、每天…）的区间。必须跳过它们，
    否则「周一九点」会被当成「一九点」（19 点）——这是实际踩过的坑。
    """
    position = 0
    while True:
        colon = _COLON_RE.search(text, position)
        if colon is None:
            break
        if _overlaps(colon.span(), skip_spans):
            position = colon.start() + 1
            continue
        raw_hour = int(colon.group("hour"))
        minute = int(colon.group("minute"))
        period = _period_before(text, colon.start())
        span = colon.span()
        raw = colon.group(0)
        if period:
            start = colon.start() - len(period)
            while start > 0 and text[start - 1].isspace():
                start -= 1
            span = (start, colon.end())
            raw = text[span[0]:span[1]]
        hour = _apply_period(raw_hour, period)
        _validate_clock(hour, minute, raw)
        return hour, minute, span, raw, period

    position = 0
    while True:
        match = _CLOCK_RE.search(text, position)
        if match is None:
            return None
        if _overlaps(match.span(), skip_spans):
            position = match.start() + 1
            continue
        period = match.group("period") or _period_before(text, match.start())
        raw_hour = _cn_int(match.group("hour"))
        minute = _parse_minute(match.group("minute"))
        hour = _apply_period(raw_hour, period)
        span = match.span()
        if period and not match.group("period"):
            start = match.start() - len(period)
            span = (max(0, start), match.end())
        raw = text[span[0]:span[1]]
        _validate_clock(hour, minute, raw)
        return hour, minute, span, raw, period


def _validate_clock(hour: int, minute: int, raw: str) -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise NeedsClarification(
            f"「{raw}」不是有效时刻。请说一个具体时间，例如「明天上午九点」。"
        )


def _weekday_name(index: int) -> str:
    return f"周{WEEKDAY_NAMES[index]}"


def _first_run_local(
    local_now: datetime,
    hour: int,
    minute: int,
    *,
    repeat_kind: str,
    weekday: Optional[int] = None,
    rel_offset: Optional[int] = None,
    week_prefix: Optional[str] = None,
) -> datetime:
    """把「时分 + 规则」落成第一次本地执行时间。必要时反问而不是猜。"""
    base = local_now.date()
    now_minutes = local_now.hour * 60 + local_now.minute
    target_minutes = hour * 60 + minute

    if repeat_kind == "daily":
        day = base if target_minutes > now_minutes else base + timedelta(days=1)
    elif repeat_kind == "weekly":
        assert weekday is not None
        days = (weekday - base.weekday()) % 7
        if days == 0 and target_minutes <= now_minutes:
            days = 7
        day = base + timedelta(days=days)
    elif rel_offset is not None:
        day = base + timedelta(days=rel_offset)
        if rel_offset == 0 and target_minutes <= now_minutes:
            raise NeedsClarification(
                f"今天 {hour:02d}:{minute:02d} 已经过了，要改成明天吗？"
                "请说「明天」或一个新的时间。"
            )
    elif weekday is not None:
        days = (weekday - base.weekday()) % 7
        if week_prefix == "下下":
            days = days + 7 if days else 14
        elif week_prefix == "下":
            days = days if days else 7
        elif week_prefix in ("本", "这"):
            if days == 0 and target_minutes <= now_minutes:
                raise NeedsClarification(
                    f"本周{_weekday_name(weekday)} {hour:02d}:{minute:02d} 已经过了，"
                    "要改成下周吗？"
                )
        else:
            if days == 0 and target_minutes <= now_minutes:
                days = 7
        day = base + timedelta(days=days)
    else:
        day = base if target_minutes > now_minutes else base + timedelta(days=1)

    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=local_now.tzinfo
    )


def _build_local_datetime(day: date, hour: int, minute: int, zone: tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)


def _resolve_abs_date(match: re.Match, raw: str) -> date:
    """带年份的绝对日期；非法（2 月 30 日等）直接反问。"""
    try:
        year = int(match.group("year"))
        month = _cn_int(match.group("month"))
        day = _cn_int(match.group("day"))
        return date(year, month, day)
    except (KeyError, ValueError):
        raise NeedsClarification(
            f"「{raw}」不是有效日期，请检查年月日（例如「2026-12-25」）。"
        ) from None


def _resolve_month_day(match: re.Match, local_now: datetime, raw: str) -> date:
    """缺年份的 ``M月D日``：按今年算；今年已过则反问，不偷偷滚到明年。"""
    try:
        month = _cn_int(match.group("month"))
        day = _cn_int(match.group("day"))
        candidate = date(local_now.year, month, day)
    except (KeyError, ValueError):
        raise NeedsClarification(
            f"「{raw}」不是有效日期，请检查月份和日期（例如「12月25日」）。"
        ) from None
    if candidate < local_now.date():
        raise NeedsClarification(
            f"「{raw}」在今年（{local_now.year}）已经过去了。是明年吗？"
            f"请补充年份，例如「{local_now.year + 1}年{month}月{day}日」。"
        )
    return candidate


def _extract_title(original: str, spans: list[Tuple[int, int]]) -> str:
    masked = list(original)
    for start, end in spans:
        for index in range(max(0, start), min(len(masked), end)):
            masked[index] = ""
    remaining = "".join(masked)
    remaining = _FILLER_RE.sub("", remaining)
    remaining = re.sub(r"\s{2,}", " ", remaining).strip()
    remaining = re.sub(r"^在\s*", "", remaining)
    remaining = _EDGE_RE.sub("", remaining)
    return remaining or "提醒"


def _missing_time_question(original: str) -> str:
    if _VAGUE_RE.search(original):
        return (
            f"「{original}」里的时间太模糊了，请给我一个具体时间，"
            "例如「明天上午九点」或「三天后」。"
        )
    if _DATE_HINT_RE.search(original):
        return (
            f"「{original}」没有说具体几点。请补充时刻，"
            "例如「明天上午九点」或「下周一早上九点」。"
        )
    return (
        "我没找到具体时间。请说「三天后」「明天上午九点」"
        "或「每周一早上九点」这样的时间。"
    )


def parse_reminder(
    text: str,
    now: Optional[datetime] = None,
    *,
    tz: Optional[tzinfo] = None,
) -> ReminderSpec:
    """把一句中文提醒解析成 :class:`ReminderSpec`。

    Raises:
        NeedsClarification: 时间无法唯一确定（缺时刻 / 日期含糊 / 口语模糊）。
    """
    original = (text or "").strip()
    if not original:
        raise NeedsClarification(
            "要提醒你做什么？请说具体时间与内容，例如「明天上午九点提醒我开会」。"
        )

    now_utc = _naive_utc(now if now is not None else utc_now())
    zone = tz or local_timezone()
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(zone)

    delay_match = _DELAY_RE.search(original)
    daily_match = _REPEAT_DAILY_RE.search(original)
    weekly_match = _REPEAT_WEEKLY_RE.search(original)
    monthly_match = _MONTHLY_RE.search(original)
    rel_match = _REL_DAY_RE.search(original)
    weekday_match = _WEEKDAY_RE.search(original)
    abs_match = _ABS_DATE_RE.search(original)
    month_day_match = None if abs_match else _CN_MONTH_DAY_RE.search(original)
    # 「每月N号」：明确拒绝，绝不静默落成 today/tomorrow。
    if monthly_match:
        raise NeedsClarification(
            f"暂不支持每月重复（「{monthly_match.group(0)}」）。"
            "请改成「每周一上午九点」，或先建一条一次性提醒。"
        )
    # 日期/重复词不能参与时刻解析：否则「周一九点」会被读成「一九点」。
    skip_spans = [
        match.span()
        for match in (
            daily_match,
            weekly_match,
            rel_match,
            weekday_match,
            abs_match,
            month_day_match,
        )
        if match is not None
    ]
    clock = _extract_clock(original, skip_spans)

    # 1) 相对延迟：X 秒/分/小时/天后（一次性）
    if delay_match and not daily_match and not weekly_match:
        seconds = _delay_seconds(delay_match)
        return ReminderSpec(
            title=_extract_title(original, [delay_match.span()]),
            run_at=now_utc + timedelta(seconds=seconds),
            repeat_kind="once",
            time_text=delay_match.group(0),
            source_text=original,
        )

    # 2) 重复规则：每天 / 每周X
    if daily_match or weekly_match:
        marker = daily_match or weekly_match
        kind = "daily" if daily_match else "weekly"
        if clock is None:
            example = "「每天上午九点」" if kind == "daily" else "「每周一上午九点」"
            raise NeedsClarification(
                f"「{marker.group(0)}」要几点提醒？请补充具体时间，例如{example}。"
            )
        hour, minute, clock_span, clock_text, _period = clock
        weekday = _WEEKDAY_INDEX[weekly_match.group("wd")] if weekly_match else None
        run_local = _first_run_local(
            local_now, hour, minute, repeat_kind=kind, weekday=weekday
        )
        return ReminderSpec(
            title=_extract_title(original, [marker.span(), clock_span]),
            run_at=_to_utc_naive(run_local),
            repeat_kind=kind,
            weekday=weekday,
            hour=hour,
            minute=minute,
            time_text=f"{marker.group(0)}{clock_text}",
            source_text=original,
        )

    # 2.5) 绝对日期：「12月25日上午九点」「2026-12-25 09:00」
    #      明确日期一律按给定日期算，绝不兜底到 today/tomorrow。
    date_match = abs_match or month_day_match
    if date_match is not None:
        if clock is None:
            raise NeedsClarification(_missing_time_question(original))
        hour, minute, clock_span, clock_text, _period = clock
        raw_date = date_match.group(0)
        if abs_match is not None:
            target_day = _resolve_abs_date(abs_match, raw_date)
        else:
            target_day = _resolve_month_day(date_match, local_now, raw_date)
        run_local = _build_local_datetime(target_day, hour, minute, zone)
        if run_local <= local_now:
            raise NeedsClarification(
                f"「{raw_date} {clock_text}」已经过去了，请换一个未来的时间。"
            )
        return ReminderSpec(
            title=_extract_title(original, [date_match.span(), clock_span]),
            run_at=_to_utc_naive(run_local),
            repeat_kind="once",
            hour=hour,
            minute=minute,
            time_text=f"{raw_date}{clock_text}",
            source_text=original,
        )

    # 3) 一次性：相对日期 / 星期几 + 时刻，或只有时刻
    if clock is None:
        raise NeedsClarification(_missing_time_question(original))

    hour, minute, clock_span, clock_text, _period = clock
    spans = [clock_span]
    if rel_match:
        offset = _REL_OFFSET[rel_match.group("day")]
        run_local = _first_run_local(
            local_now, hour, minute, repeat_kind="once", rel_offset=offset
        )
        spans.append(rel_match.span())
        time_text = f"{rel_match.group(0)}{clock_text}"
    elif weekday_match:
        weekday = _WEEKDAY_INDEX[weekday_match.group("wd")]
        run_local = _first_run_local(
            local_now,
            hour,
            minute,
            repeat_kind="once",
            weekday=weekday,
            week_prefix=weekday_match.group("prefix"),
        )
        spans.append(weekday_match.span())
        time_text = f"{weekday_match.group(0)}{clock_text}"
    else:
        if _DATE_LIKE_RE.search(original):
            raise NeedsClarification(
                f"「{original}」里的日期我没认出来。请用完整日期，"
                "例如「12月25日上午九点」或「2026-12-25 09:00」。"
            )
        run_local = _first_run_local(local_now, hour, minute, repeat_kind="once")
        time_text = clock_text

    return ReminderSpec(
        title=_extract_title(original, spans),
        run_at=_to_utc_naive(run_local),
        repeat_kind="once",
        hour=hour,
        minute=minute,
        time_text=time_text,
        source_text=original,
    )


__all__ = [
    "NeedsClarification",
    "ReminderSpec",
    "WEEKDAY_NAMES",
    "local_timezone",
    "parse_reminder",
]
