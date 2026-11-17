"""连接 HTTP 面（A3 集成与会议组）。

* ``GET /v1/connections`` — 六个平台的接通状态（B 组设置页直接用）：
  Gmail / Google 日历 / GitHub / WhatsApp / 视频会议 / 杰夫AI。
  没接通就是 ``not_configured``，不返回假数据。
* Gmail：``POST /gmail/sync`` 读未读 → 按规则记待办**草稿**
  （等确认才建任务）；``POST /gmail/send`` 发邮件；草稿确认/驳回。
* Google 日历：读/建/改/删（官方 API），``POST /calendar/google/import``
  单向同步进本机日历；改期走 ``PATCH``。
* GitHub：``GET /github/digest`` 只读摘要（仓库/issue/PR 状态）。
* WhatsApp：``POST /whatsapp/send``（Twilio 官方 API；24 小时窗口外
  没配模板时拒绝发送、不假装发了）。

所有写操作只在 Joseph 明确调用时发生；没配凭证一律 409。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .account_context import AccountContext, require_account_principal
from src.integrations.connections import connection_status, connection_statuses
from src.integrations.github_digest import github_digest
from src.integrations.google_workspace import (
    GoogleCalendarClient,
    GmailClient,
    NotConfiguredError,
    apply_mail_rules,
)
from src.integrations.task_drafts import TaskDraftStore, draft_out
from src.integrations.whatsapp_business import send_whatsapp_message

router = APIRouter(prefix="/v1/connections", tags=["connections"])


def _visible_accounts(context: AccountContext) -> list:
    ids = list(context.subtree)
    if context.is_root:
        ids.append("")
    return ids


def _draft_store() -> TaskDraftStore:
    return TaskDraftStore()


def _not_configured(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "not_configured", "message": str(exc)},
    )


# ---------------------------------------------------------------- 状态


@router.get("")
def list_connections() -> Dict[str, Any]:
    """六个平台的接通状态（固定顺序：gmail / calendar / github / whatsapp / meet / jiefuai）。"""
    return {"connections": connection_statuses()}


@router.get("/{platform}")
def get_connection(platform: str) -> Dict[str, Any]:
    try:
        return connection_status(platform)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------- Gmail


class GmailSyncIn(BaseModel):
    max_results: int = Field(default=10, ge=1, le=50)
    rules: Optional[List[Dict[str, Any]]] = None


@router.post("/gmail/sync")
def sync_gmail(
    body: GmailSyncIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """读未读邮件 → 按规则记待办草稿（只记草稿，不建任务、不回信、不动原邮件）。"""
    try:
        client = GmailClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        messages = client.list_recent_unread(max_results=body.max_results)
    except Exception as exc:  # noqa: BLE001 - 上游错误如实转 502
        raise HTTPException(status_code=502, detail=f"Gmail 读取失败：{exc}") from exc
    store = _draft_store()
    drafts: List[Dict[str, Any]] = []
    for message in messages:
        matched = apply_mail_rules(message, body.rules)
        if matched is None:
            continue
        drafts.append(
            draft_out(
                store.create(
                    source="gmail",
                    title=matched["title"],
                    detail=matched["detail"],
                    external_ref=matched["external_ref"],
                    from_label=matched["from_label"],
                    account_id=context.principal,
                )
            )
        )
    return {
        "read": len(messages),
        "drafts_created": len(drafts),
        "drafts": drafts,
        "note": "草稿等你在操作台确认后才正式建任务；没命中的邮件只读不处理。",
    }


class GmailSendIn(BaseModel):
    to: str = Field(..., min_length=1, max_length=200)
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)


@router.post("/gmail/send")
def send_gmail(
    body: GmailSendIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """发一封邮件（Gmail 官方 API；没配 Google 账号时 409）。"""
    try:
        client = GmailClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        return client.send_message(body.to, body.subject, body.body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Gmail 发送失败：{exc}") from exc


@router.get("/gmail/drafts")
def list_gmail_drafts(
    status: str = Query(default="draft"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """Gmail 来的待办草稿（按账号过滤；默认只看等确认的）。"""
    drafts = [
        draft_out(d)
        for d in _draft_store().list(
            account_ids=_visible_accounts(context), status=status
        )
        if d["source"] == "gmail"
    ]
    return {"drafts": drafts, "count": len(drafts)}


@router.post("/gmail/drafts/{draft_id}/confirm")
def confirm_gmail_draft(
    draft_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """确认一条 Gmail 待办草稿 → 正式建成小队任务（Joseph 点了才建）。"""
    from src.team.store import TeamStore

    store = _draft_store()
    draft = store.confirm(draft_id, account_ids=_visible_accounts(context))
    if draft is None:
        raise HTTPException(status_code=404, detail="没有这条待办草稿")
    if draft["status"] != "confirmed":
        return {"draft": draft_out(draft), "task_id": None, "note": "这条草稿已经处理过了。"}
    task = TeamStore().create_task(
        f"[邮件待办] {draft['title']}", context.principal
    )
    return {
        "draft": draft_out(draft),
        "task_id": str(task.get("task_id") or ""),
        "note": "已正式建任务。",
    }


@router.post("/gmail/drafts/{draft_id}/dismiss")
def dismiss_gmail_draft(
    draft_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """驳回一条 Gmail 待办草稿（不建任务）。"""
    draft = _draft_store().dismiss(draft_id, account_ids=_visible_accounts(context))
    if draft is None:
        raise HTTPException(status_code=404, detail="没有这条待办草稿")
    return {"draft": draft_out(draft)}


# ---------------------------------------------------------------- Google 日历


@router.get("/calendar/google/events")
def list_google_events(
    time_min: str = "",
    time_max: str = "",
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """读 Google 日历上的日程（没配 Google 账号时 409）。"""
    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        events = client.list_events(time_min=time_min, time_max=time_max)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Google 日历读取失败：{exc}") from exc
    return {"events": events, "count": len(events), "source": "google-calendar"}


class GoogleEventIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    start: str = Field(..., min_length=1, max_length=100)
    end: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)


@router.post("/calendar/google/events", status_code=201)
def create_google_event(
    body: GoogleEventIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """在 Google 日历上新建日程（官方 API；没配账号时 409）。"""
    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        return client.create_event(
            body.title, body.start, body.end, body.location, body.description
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Google 日历新建失败：{exc}") from exc


class GoogleEventPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    start: Optional[str] = Field(default=None, max_length=100)
    end: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


@router.patch("/calendar/google/events/{event_id}")
def update_google_event(
    event_id: str,
    body: GoogleEventPatch,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """改期/改 Google 日程（只传要改的字段；没配账号时 409）。"""
    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="没给要改的字段")
    try:
        return client.update_event(event_id, **fields)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Google 日历修改失败：{exc}") from exc


@router.delete("/calendar/google/events/{event_id}")
def delete_google_event(
    event_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """删 Google 日程（官方 API；没配账号时 409；没有这条时 404）。"""
    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        deleted = client.delete_event(event_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Google 日历删除失败：{exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Google 日历上没有这条日程")
    return {"deleted": True, "id": event_id}


class GoogleImportIn(BaseModel):
    time_min: str = Field(default="", max_length=100)
    time_max: str = Field(default="", max_length=100)


@router.post("/calendar/google/import")
def import_google_calendar(
    body: GoogleImportIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """把 Google 日历同步进本机日历（单向拉取、按 Google 事件 id 去重；本机改的不回写）。"""
    from src.calendar_ledger.calendar_store import CalendarStore

    try:
        client = GoogleCalendarClient()
    except NotConfiguredError as exc:
        raise _not_configured(exc) from exc
    try:
        count = client.import_to_local(
            CalendarStore(), time_min=body.time_min, time_max=body.time_max
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Google 日历同步失败：{exc}") from exc
    return {
        "imported": count,
        "note": "单向拉取：Google → 本机。本机改的日程不回写 Google（双向同步以后再做）。",
    }


# ---------------------------------------------------------------- GitHub


@router.get("/github/digest")
def get_github_digest(
    repos: List[str] = Query(default=[]),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """GitHub 只读摘要：仓库动态、open issue / PR 状态（没配令牌时 409）。"""
    from src.integrations.github_digest import is_github_configured

    if not is_github_configured():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_configured",
                "message": "GitHub 还没接通：请先把个人访问令牌填进 LIUHAO_GITHUB_TOKEN。",
            },
        )
    return github_digest(repos or None)


# ---------------------------------------------------------------- WhatsApp


@router.get("/whatsapp/drafts")
def list_whatsapp_drafts(
    status: str = Query(default="draft"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """WhatsApp 里 Owner 说“任务：…”记下的待办草稿（等确认才建任务）。"""
    drafts = [
        draft_out(d)
        for d in _draft_store().list(
            account_ids=_visible_accounts(context), status=status
        )
        if d["source"] == "whatsapp"
    ]
    return {"drafts": drafts, "count": len(drafts)}


class WhatsAppSendIn(BaseModel):
    to: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=4000)
    within_24h_window: bool = Field(default=True)


@router.post("/whatsapp/send")
def send_whatsapp(
    body: WhatsAppSendIn,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """给 WhatsApp 发消息（Twilio 官方 API）。

    24 小时窗口外且没配模板 SID 时返回 ``sent: false`` +
    ``reason: template_required``（不假装发了）。
    """
    from src.ai.channels import ChannelNotConfiguredError, is_configured

    if not is_configured("whatsapp"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_configured",
                "message": "WhatsApp 还没接通：请先按设置 → 连接里的步骤配好 Twilio 账号信息。",
            },
        )
    try:
        result = send_whatsapp_message(
            body.to, body.text, within_24h_window=body.within_24h_window
        )
    except ChannelNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_configured", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"WhatsApp 发送失败：{exc}") from exc
    return result
