"""提醒 HTTP API（5-10 主动提醒一期）。

提醒本身早已存在，不是在这里新造的：

* 存储：``ReminderStore``（SQLite ``reminders.db``，重启不丢）；
* 调度：``LocalScheduler``（SQLite ``local_scheduler.db``，同一份库，
  不是进程内内存态 —— 建提醒只入队不 ``run()`` 的断链已由
  ``scheduler_host`` 在网关进程内托管修好）；
* 投递：到点走托盘气泡 + 控制台出站队列（``reminder_notify``），
  错过的合并成摘要。

此前唯一的入口是聊天里的提醒工具与 ``python -m src.reminders`` 命令行，
操作台里看不到、建不了。本模块只补 HTTP 这一层：列表、创建、取消、
待确认投递（到点弹通知用）、确认、历史 —— 不另起调度器、不另起存储，
提醒仍是同一份 SQLite。

端点（全部走人类主权闸门，``principal`` 只取自令牌）：

* ``GET /v1/reminders`` —— 列表（含下次响的时间 ``run_at_local``）；
* ``POST /v1/reminders`` —— 创建：``{"text": "明天下午3点提醒我开会"}``
  （自然语言，与聊天工具同一解析器）或
  ``{"title": "开会", "run_at_local": "2026-11-13T15:00", "repeat": "once"}``
  （表单）；时间说不清时 422 + 追问，不猜；
* ``DELETE /v1/reminders/{reminder_id}`` —— 取消；
* ``GET /v1/reminders/pending`` —— 最近一条未确认的已响提醒（没有时
  ``{"pending": null}``），操作台每 30 秒轮询它，到点就弹通知；
* ``POST /v1/reminders/deliveries/{delivery_id}/ack`` —— 点「知道了」；
* ``GET /v1/reminders/deliveries?kind=history|missed`` —— 响过/错过的记录。

定时任务不引入 APScheduler：现有的 ``LocalScheduler`` 已经是 SQLite
持久化 + 指数退避 + 死信，再接一套 APScheduler 等于两套调度器抢同一
批提醒；计划里「定时用 APScheduler 3.11.3」的前提（进程内内存态）经核实
不成立。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from src._time import utc_now
from ..tasks.reminder_time import NeedsClarification
from ..tasks.reminders import ReminderService, get_reminder_service
from .a2a import get_account_settings
from .policy import require_human_principal


def _account_tz(principal: str):
    """账号时区（设置里存的 IANA 名，默认 America/Tijuana）。

    提醒解析必须按这个时区读“明天早上 8 点”，而不是按上海硬编码
    （走查 F14）。名字非法/缺 tzdata 时返回 None，让服务层退回本机时区。
    """
    try:
        from zoneinfo import ZoneInfo

        name = str(get_account_settings(principal).get("timezone") or "").strip()
        return ZoneInfo(name) if name else None
    except Exception:  # noqa: BLE001 - 时区名非法时退回服务默认
        return None

router = APIRouter(prefix="/v1/reminders", tags=["reminders"])


class CreateReminderRequest(BaseModel):
    """两种创建方式二选一：自然语言 text，或表单 title + run_at_local。"""

    text: Optional[str] = None
    title: Optional[str] = None
    run_at_local: Optional[str] = None
    repeat: str = "once"


class UpdateReminderRequest(BaseModel):
    """两种改法二选一：自然语言 text，或表单 title/run_at_local/repeat。"""

    text: Optional[str] = None
    title: Optional[str] = None
    run_at_local: Optional[str] = None
    repeat: Optional[str] = None


class AckRequest(BaseModel):
    ack_status: str = "seen"


class CloseDeliveryRequest(BaseModel):
    """提醒闭环（B 组）：做完了 / 再提醒我 / 不用了。

    没点就不算完：done 标记完成，snooze 按已有逻辑重排（默认 10 分钟），
    dismiss 只标记“不用了”、不再追问。复用已有提醒存储与服务方法，
    不另起状态机；错过的提醒不能 snooze（服务层原样返回 noop）。
    """

    action: str = "done"
    minutes: Optional[int] = None


def _service(request: Request) -> ReminderService:
    """提醒服务：优先用网关进程内托管的那份排程器（与触发同一连接）。"""
    host = getattr(request.app.state, "local_scheduler_host", None)
    scheduler = getattr(host, "scheduler", None) if host is not None else None
    if scheduler is not None:
        return get_reminder_service(scheduler=scheduler)
    return get_reminder_service()


def _enrich(service: ReminderService, user_id: str, delivery: Dict[str, Any]) -> Dict[str, Any]:
    """投递记录本身不带标题，从提醒表里补；提醒已删时如实标注。"""
    item = dict(delivery)
    record = service.store.get(str(delivery.get("reminder_id") or ""), user_id=user_id)
    item["title"] = str(record["title"]) if record else "（已删除的提醒）"
    return item


@router.get("", response_model=None)
def list_reminders(
    request: Request,
    include_cancelled: bool = Query(default=False),
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    service = _service(request)
    return {
        "reminders": service.list(
            principal,
            include_cancelled=include_cancelled,
            tz=_account_tz(principal),
        ),
    }


@router.post("", status_code=201, response_model=None)
def create_reminder(
    request: Request,
    body: CreateReminderRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    service = _service(request)
    text = (body.text or "").strip()
    title = (body.title or "").strip()
    try:
        if text:
            created = service.create(principal, text, tz=_account_tz(principal))
        elif title and (body.run_at_local or "").strip():
            created = service.create_explicit(
                principal,
                title=title,
                run_at_local=body.run_at_local or "",
                repeat=body.repeat,
                tz=_account_tz(principal),
            )
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REMINDER_MISSING_INPUT",
                    "message": "请说一句提醒（例如“明天下午3点提醒我开会”），"
                    "或填标题和时间。",
                },
            )
    except NeedsClarification as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_NEEDS_CLARIFICATION", "message": exc.question},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_INVALID", "message": str(exc)},
        ) from exc
    return {"reminder": created}


@router.patch("/{reminder_id}", response_model=None)
def update_reminder(
    request: Request,
    reminder_id: str,
    body: UpdateReminderRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """改一条提醒的时间和/或内容（不用删了重建）。

    * ``text``：自然语言重说一遍（与创建同一解析器）；
    * ``title`` / ``run_at_local`` / ``repeat``：表单式，只改给出的字段。
    已响过/已取消的提醒不能改（服务层如实返回 noop），不存在的 404。
    """
    service = _service(request)
    text = (body.text or "").strip()
    try:
        if text:
            result = service.update(principal, reminder_id, text, tz=_account_tz(principal))
        elif (
            body.title is not None
            or body.run_at_local is not None
            or body.repeat is not None
        ):
            result = service.update_explicit(
                principal,
                reminder_id,
                title=body.title,
                run_at_local=body.run_at_local,
                repeat=body.repeat,
                tz=_account_tz(principal),
            )
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REMINDER_MISSING_INPUT",
                    "message": "请说一句提醒，或填要改的标题、时间。",
                },
            )
    except NeedsClarification as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_NEEDS_CLARIFICATION", "message": exc.question},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_INVALID", "message": str(exc)},
        ) from exc
    if result.get("status") == "not_found":
        raise HTTPException(
            status_code=404,
            detail={"code": "REMINDER_NOT_FOUND", "message": "没有这个提醒。"},
        )
    if result.get("status") == "noop":
        raise HTTPException(
            status_code=409,
            detail={"code": "REMINDER_NOT_EDITABLE", "message": str(result.get("message") or "这条提醒现在不能改。")},
        )
    return result


@router.delete("/{reminder_id}", response_model=None)
def cancel_reminder(
    request: Request,
    reminder_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    service = _service(request)
    result = service.cancel(principal, reminder_id)
    if result.get("status") == "not_found":
        raise HTTPException(
            status_code=404,
            detail={"code": "REMINDER_NOT_FOUND", "message": "没有这个提醒。"},
        )
    return result


@router.get("/pending", response_model=None)
def pending_reminder(
    request: Request,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """最近一条未确认的已响提醒；没有时 pending 为 null（不是空对象）。"""
    service = _service(request)
    delivery = service.store.latest_pending_ack(principal)
    if delivery is None:
        return {"pending": None}
    return {"pending": _enrich(service, principal, delivery)}


@router.post("/deliveries/{delivery_id}/ack", response_model=None)
def ack_delivery(
    request: Request,
    delivery_id: int,
    body: AckRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    service = _service(request)
    acked = service.store.ack_delivery(
        delivery_id, principal, body.ack_status, utc_now().isoformat()
    )
    if not acked:
        raise HTTPException(
            status_code=404,
            detail={"code": "REMINDER_DELIVERY_NOT_FOUND",
                    "message": "这条提醒已经确认过，或不存在。"},
        )
    return {"acked": True, "delivery_id": delivery_id}


@router.post("/deliveries/{delivery_id}/close", response_model=None)
def close_delivery(
    request: Request,
    delivery_id: int,
    body: CloseDeliveryRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """闭环一条已响提醒：done / snooze / dismiss（三选一）。

    ``ack`` 只记“知道了”，这里才是“这事完了”：done 走服务层完成，
    snooze 走服务层重排，dismiss 记 ack_status='dismissed'。
    已处理过的投递返回 404（与 ack 同口径）；错过的提醒 snooze 时
    服务层返回 noop，本端点如实透传，不谎称已重排。
    """
    action = str(body.action or "").strip().lower()
    if action not in ("done", "snooze", "dismiss"):
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_INVALID_CLOSE_ACTION",
                    "message": "action 只能是 done、snooze 或 dismiss。"},
        )
    service = _service(request)
    if action == "done":
        result = service.done(principal, delivery_id=delivery_id)
        if result.get("status") in ("not_found", "noop"):
            # 已处理过（或汇总条目不能单独标记）：与 ack 同口径，404，不谎称又关了一次。
            raise HTTPException(
                status_code=404,
                detail={"code": "REMINDER_DELIVERY_NOT_FOUND",
                        "message": result.get("message") or "这条提醒已经确认过，或不存在。"},
            )
        closed = result.get("status") not in ("error",)
        return {"closed": bool(closed), "action": "done", "result": result}
    if action == "snooze":
        result = service.snooze(principal, delivery_id=delivery_id, minutes=body.minutes)
        if result.get("status") == "not_found":
            raise HTTPException(
                status_code=404,
                detail={"code": "REMINDER_DELIVERY_NOT_FOUND",
                        "message": "这条提醒已经确认过，或不存在。"},
            )
        closed = result.get("status") not in ("noop", "error")
        return {"closed": bool(closed), "action": "snooze", "result": result}
    acked = service.store.ack_delivery(
        delivery_id, principal, "dismissed", utc_now().isoformat()
    )
    if not acked:
        raise HTTPException(
            status_code=404,
            detail={"code": "REMINDER_DELIVERY_NOT_FOUND",
                    "message": "这条提醒已经确认过，或不存在。"},
        )
    return {"closed": True, "action": "dismiss", "result": {"status": "dismissed"}}


@router.get("/deliveries", response_model=None)
def list_deliveries(
    request: Request,
    kind: str = Query(default="history"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    service = _service(request)
    if kind == "missed":
        items = service.store.list_missed(principal, limit=limit)
    elif kind == "history":
        items = service.store.list_history(principal, limit=limit)
    else:
        raise HTTPException(
            status_code=422,
            detail={"code": "REMINDER_INVALID_KIND",
                    "message": "kind 只能是 history 或 missed。"},
        )
    return {
        "deliveries": [_enrich(service, principal, item) for item in items],
    }


__all__ = ["router"]
