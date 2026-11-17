"""日历与记账 HTTP 面（0.4.6 F 组，6-5）。

日历（``/v1/calendar``）：本机日程 CRUD + ``.ics`` 导入导出 + 到点提醒查询。
外部日历只接 CalDAV（用户自己选开，密码只用于本次连接、不存储）；
Google / Apple 云日历不作默认、不接线。

记账（``/v1/ledger``）：手动记一笔、拍照记一笔（图片 -> OCR -> 解析草稿，
调用方核对金额后确认入账）、按月汇总、删一笔。金额单位是分。

两个存储都在本机 SQLite，不上云。挂载时由 ``main.py`` 加人类主权闸门。

账号过滤（2026-11-12 权限统一补）：每个端点都挂 ``require_account_principal``，
读只见自己子树（根额外可见分账前旧数据），写只作用于自己；跨账号读/删一律
中性 404。旧数据（``account_id`` 为空）默认归根账号。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .account_context import AccountContext, require_account_principal

from src.calendar_ledger.calendar_store import (
    CalendarStore,
    sync_caldav,
)
from src.calendar_ledger.ledger import (
    Expense,
    LedgerStore,
    parse_invoice_text,
)
from src.common.engines import EngineUnavailableError
from src.ocr import ocr_image_bytes

calendar_router = APIRouter(prefix="/v1/calendar", tags=["calendar"])
ledger_router = APIRouter(prefix="/v1/ledger", tags=["ledger"])


def _calendar_store() -> CalendarStore:
    return CalendarStore()


def _ledger_store() -> LedgerStore:
    return LedgerStore()


def _visible_accounts(context: AccountContext) -> list:
    """读可见的账号集合：自己子树；根额外可见分账前旧数据（account_id=''）。"""
    ids = list(context.subtree)
    if context.is_root:
        ids.append("")
    return ids


def _writable_accounts(context: AccountContext) -> list:
    """写/删可作用的账号集合：只自己；根可管整树（含旧数据）。"""
    if context.is_root:
        return _visible_accounts(context)
    return [context.principal]


def _event_out(event) -> Dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "location": event.location,
        "description": event.description,
        "source": event.source,
        "uid": event.uid,
    }


# ---------------------------------------------------------------- 日历

class CalendarCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    start: datetime
    end: Optional[datetime] = None
    location: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)


class CaldavSyncRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=500)
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)


@calendar_router.get("/events")
def list_events(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    events = _calendar_store().list(
        start=start, end=end, account_ids=_visible_accounts(context)
    )
    return {"events": [_event_out(e) for e in events], "count": len(events)}


@calendar_router.post("/events", status_code=201)
def create_event(
    body: CalendarCreate,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
    end = body.end
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    event = _calendar_store().create(
        title=body.title, start=start, end=end,
        location=body.location, description=body.description,
        account_id=context.principal,
    )
    return _event_out(event)


@calendar_router.delete("/events/{event_id}")
def delete_event(
    event_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    if not _calendar_store().delete(
        event_id, account_ids=_writable_accounts(context)
    ):
        raise HTTPException(status_code=404, detail="没有这条日程")
    return {"deleted": True, "id": event_id}


@calendar_router.get("/due")
def due_events(
    within_minutes: int = Query(default=60, ge=1, le=1440),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """未来 ``within_minutes`` 分钟内开始的日程（提醒 tick 查它）。"""
    events = _calendar_store().due_events(
        within_minutes=within_minutes, account_ids=_visible_accounts(context)
    )
    return {"events": [_event_out(e) for e in events], "count": len(events)}


@calendar_router.get("/export.ics")
def export_ics(
    context: AccountContext = Depends(require_account_principal),
) -> Response:
    try:
        data = _calendar_store().export_ics(account_ids=_visible_accounts(context))
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    return Response(
        content=data, media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="jarvis-calendar.ics"'},
    )


@calendar_router.post("/import.ics")
async def import_ics(
    file: UploadFile = File(...),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail=".ics 文件太大（上限 5MB）。")
    try:
        count = await asyncio.to_thread(
            _calendar_store().import_ics, data, "ics-import", context.principal
        )
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"imported": count}


@calendar_router.post("/caldav/sync")
async def caldav_sync(
    body: CaldavSyncRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """从用户自己的 CalDAV 服务拉日程（密码只用于本次连接，不存储）。"""
    try:
        count = await asyncio.to_thread(
            sync_caldav, body.url, body.username, body.password,
            None, context.principal,
        )
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 外部服务错误如实转 502
        raise HTTPException(status_code=502, detail=f"CalDAV 同步失败：{exc}") from exc
    return {"imported": count, "license_note": "caldav 按 Apache-2.0 使用"}


# ---------------------------------------------------------------- 记账

class ExpenseCreate(BaseModel):
    occurred_on: date
    amount_cents: int = Field(..., gt=0)
    category: str = Field(default="其他", max_length=20)
    merchant: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)


class ExpenseConfirm(BaseModel):
    """拍照记账第二步：OCR 草稿经人工核对后确认入账（字段都可改）。"""

    amount_cents: int = Field(..., gt=0)
    occurred_on: date
    category: str = Field(default="其他", max_length=20)
    merchant: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)
    ocr_text: str = Field(default="", max_length=20000)


def _expense_out(expense: Expense) -> Dict[str, Any]:
    return {
        "id": expense.id,
        "occurred_on": expense.occurred_on.isoformat(),
        "amount_cents": expense.amount_cents,
        "category": expense.category,
        "merchant": expense.merchant,
        "note": expense.note,
        "source": expense.source,
    }


@ledger_router.post("/expenses/from-image")
async def expense_from_image(
    file: UploadFile = File(...),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """拍照记一笔第 1 步：图片 -> OCR -> 解析草稿（不入账，等确认）。"""
    data = await file.read()
    try:
        ocr = await asyncio.to_thread(ocr_image_bytes, data, file.filename or "receipt.jpg")
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    parsed = parse_invoice_text(ocr.text)
    # 金额不确定的标“待确认”，不编数：amount_cents 为 None 时前端必须让
    # 用户手动填金额才能点确认（ExpenseConfirm 要求 amount_cents > 0）。
    amount_missing = parsed.amount_cents is None
    return {
        "draft": {
            "amount_cents": parsed.amount_cents,
            "occurred_on": parsed.occurred_on.isoformat() if parsed.occurred_on else None,
            "merchant": parsed.merchant,
            "category": parsed.category,
        },
        "ocr_text": ocr.text,
        "needs_review": True,
        "amount_missing": amount_missing,
        "status": "needs_confirmation" if amount_missing else "ready_to_confirm",
        "note": (
            "金额没识别出来：请手动填写金额（不要按猜的数记），"
            "日期、商家确认无误后再点「确认记账」。"
            if amount_missing
            else "识别结果请核对：金额、日期、商家确认无误后再点「确认记账」。"
        ),
    }


@ledger_router.post("/expenses", status_code=201)
def create_expense(
    body: ExpenseCreate,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    import uuid

    expense = _ledger_store().add(Expense(
        id=uuid.uuid4().hex[:16], occurred_on=body.occurred_on,
        amount_cents=body.amount_cents, category=body.category,
        merchant=body.merchant, note=body.note, source="manual",
        account_id=context.principal,
    ))
    return _expense_out(expense)


@ledger_router.post("/expenses/confirm", status_code=201)
def confirm_expense(
    body: ExpenseConfirm,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """拍照记一笔第 2 步：人工核对后的草稿入账。"""
    import uuid

    expense = _ledger_store().add(Expense(
        id=uuid.uuid4().hex[:16], occurred_on=body.occurred_on,
        amount_cents=body.amount_cents, category=body.category,
        merchant=body.merchant, note=body.note, source="ocr",
        ocr_text=body.ocr_text, account_id=context.principal,
    ))
    return _expense_out(expense)


@ledger_router.get("/expenses")
def list_expenses(
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    expenses = _ledger_store().list(
        year=year, month=month, account_ids=_visible_accounts(context)
    )
    return {"expenses": [_expense_out(e) for e in expenses], "count": len(expenses)}


@ledger_router.delete("/expenses/{expense_id}")
def delete_expense(
    expense_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    if not _ledger_store().delete(
        expense_id, account_ids=_writable_accounts(context)
    ):
        raise HTTPException(status_code=404, detail="没有这笔账")
    return {"deleted": True, "id": expense_id}


@ledger_router.get("/summary")
def monthly_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    summary = _ledger_store().monthly_summary(
        year, month, account_ids=_visible_accounts(context)
    )
    return {**summary, "currency": "CNY"}
