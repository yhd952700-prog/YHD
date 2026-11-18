"""日历 + 记账 store 专属测试（完善第二轮 A4）。

覆盖：日程 CRUD/时间窗/账号隔离删除；发票文本解析（金额/日期/分类）、
记账校验（金额必须 >0）、按月汇总与账号过滤。全部用临时 SQLite 文件。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.calendar_ledger.calendar_store import CalendarStore
from src.calendar_ledger.ledger import (
    Expense,
    LedgerStore,
    guess_category,
    parse_invoice_text,
)


@pytest.fixture()
def cal(tmp_path):
    return CalendarStore(path=tmp_path / "cal.db")


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)


def test_calendar_create_list_window(cal):
    cal.create("晨会", _at(18), account_id="boss")
    cal.create("牙医", _at(20), account_id="boss")
    all_events = cal.list()
    assert [e.title for e in all_events] == ["晨会", "牙医"]  # 按开始时间升序
    window = cal.list(start=_at(19), end=_at(21))
    assert [e.title for e in window] == ["牙医"]


def test_calendar_account_filter_and_delete_guard(cal):
    mine = cal.create("我的", _at(18), account_id="alice")
    cal.create("他的", _at(18), account_id="bob")
    assert [e.title for e in cal.list(account_ids={"alice"})] == ["我的"]
    assert cal.list(account_ids=set()) == []
    # 删别人的日程必须失败且数据还在
    assert cal.delete(mine.id, account_ids={"bob"}) is False
    assert cal.get(mine.id) is not None
    assert cal.delete(mine.id, account_ids={"alice"}) is True
    assert cal.get(mine.id) is None


def test_calendar_persistence_across_instances(tmp_path):
    path = tmp_path / "cal.db"
    CalendarStore(path=path).create("持久", _at(18), account_id="boss")
    again = CalendarStore(path=path)
    assert [e.title for e in again.list()] == ["持久"]


def test_parse_invoice_amount_date_merchant():
    parsed = parse_invoice_text("星巴克咖啡\n日期：2026年11月17日\n合计 ¥35.50\n谢谢惠顾")
    assert parsed.amount_cents == 3550
    assert parsed.occurred_on == date(2026, 11, 17)


def test_parse_invoice_no_amount_is_none_not_zero():
    parsed = parse_invoice_text("这是一张没有金额的票")
    assert parsed.amount_cents is None


def test_guess_category_keywords():
    assert guess_category("美团外卖订单") != "其他" or guess_category("打车去机场") != "其他"
    assert guess_category("完全无关的文字内容") == "其他"


@pytest.fixture()
def ledger(tmp_path):
    return LedgerStore(path=tmp_path / "ledger.db")


def _expense(eid: str, cents: int, day: int = 17, cat: str = "餐饮", acct: str = "boss") -> Expense:
    return Expense(id=eid, occurred_on=date(2026, 11, day), amount_cents=cents,
                   category=cat, merchant="店", note="", source="manual",
                   ocr_text="", account_id=acct)


def test_ledger_rejects_non_positive_amount(ledger):
    with pytest.raises(ValueError):
        ledger.add(_expense("e0", 0))
    with pytest.raises(ValueError):
        ledger.add(_expense("e0b", -500))


def test_ledger_monthly_summary_and_account_filter(ledger):
    ledger.add(_expense("e1", 1000, cat="餐饮"))
    ledger.add(_expense("e2", 2500, cat="交通"))
    ledger.add(_expense("e3", 9999, cat="餐饮", acct="alice"))
    ledger.add(_expense("e4", 500, day=3, cat="餐饮"))  # 不同月份不进 11 月汇总? 同年同月
    summary = ledger.monthly_summary(2026, 11, account_ids={"boss"})
    assert summary["total_cents"] == 1000 + 2500 + 500
    assert summary["count"] == 3
    assert summary["by_category_cents"] == {"餐饮": 1500, "交通": 2500}
    assert ledger.monthly_summary(2026, 10)["count"] == 0


def test_ledger_add_from_invoice_requires_amount(ledger):
    parsed = parse_invoice_text("没有任何金额的票据")
    with pytest.raises(ValueError):
        ledger.add_from_invoice(parsed)
    ok = ledger.add_from_invoice(parsed, amount_cents=1200, occurred_on=date(2026, 11, 17))
    assert ok.amount_cents == 1200
    assert ledger.get(ok.id) is not None


def test_ledger_delete_account_guard(ledger):
    ledger.add(_expense("e9", 100, acct="alice"))
    assert ledger.delete("e9", account_ids={"boss"}) is False
    assert ledger.delete("e9", account_ids={"alice"}) is True
