"""Google 官方接口：Gmail + Google Calendar（A3 集成与会议组）。

走的路（只走官方接口，不走任何野路子）：
* Gmail REST API v1（``https://gmail.googleapis.com/gmail/v1``），
  OAuth 2.0 Bearer 鉴权；
* Google Calendar API v3（``https://www.googleapis.com/calendar/v3``），
  同一个 OAuth 令牌、同一套 Google 账号（跟 Joseph 的 Gmail/日历一套）。

凭证只认环境变量（由 Joseph 在部署环境里填，见 ``docs/connections-routes.md``）：
* ``LIUHAO_GOOGLE_ACCESS_TOKEN`` — Google OAuth access token
  （access token 会过期；长期用要配 refresh token 走 OAuth 刷新，
  那一步在设置页里由 Joseph 自己走 Google 授权完成，本模块不存
  refresh token、不替他刷新、不代他登录）。
* ``LIUHAO_GOOGLE_CALENDAR_ID`` — 日历 ID，默认 ``primary``。

诚实规则（全模块通用）：
* 没配 token 时一切读/写都返回 ``not_configured``，**不返回假数据**；
  HTTP 层对应 409（没配好），不是 200 配一段编出来的邮件/日程。
* 客户端构造时不联网；只有调用方法时才发请求。测试用 ``transport``
  注入假的 httpx transport，不碰真网、不碰真 Key。
* 邮件/日程内容只在内存里流转，不写日志、不进审计正文。
"""

from __future__ import annotations

import base64
import os
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

#: 凭证环境变量（只判断“配了没有”，永远不读出、不记录、不返回 token 本身）。
GOOGLE_ACCESS_TOKEN_ENV = "LIUHAO_GOOGLE_ACCESS_TOKEN"
GOOGLE_CALENDAR_ID_ENV = "LIUHAO_GOOGLE_CALENDAR_ID"


class NotConfiguredError(RuntimeError):
    """Google 账号还没接通：token 没配。"""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_google_configured() -> bool:
    """Google OAuth token 配了没有（只返回布尔值）。"""
    return bool(_env(GOOGLE_ACCESS_TOKEN_ENV))


def google_calendar_id() -> str:
    return _env(GOOGLE_CALENDAR_ID_ENV) or "primary"


def google_status() -> Dict[str, Any]:
    """Gmail / Calendar 共用的接通状态（给设置页与 ``/v1/connections`` 用）。"""
    configured = is_google_configured()
    return {
        "configured": configured,
        "status": "configured" if configured else "not_configured",
        "route": "official_api",
        "route_label": "Google 官方 API（Gmail API + Calendar API v3，OAuth 2.0）",
        "risk": "low",
        "missing": [] if configured else [GOOGLE_ACCESS_TOKEN_ENV],
        "setup_steps": [
            "在 Google Cloud 控制台建一个 OAuth 客户端（桌面应用或 Web 应用），"
            "打开 Gmail API 与 Google Calendar API。",
            "走一次 Google 授权（Joseph 本人在浏览器里点“允许”），拿到 access token；"
            "长期使用请在设置页里完成 OAuth 授权、由系统保管 refresh token，"
            "不要把 token 贴进聊天、不要写进代码。",
            "把 access token 填进部署环境的 LIUHAO_GOOGLE_ACCESS_TOKEN"
            "（或在设置 → 连接里完成授权）。",
            "（可选）要读的不是主日历时，把日历 ID 填进 LIUHAO_GOOGLE_CALENDAR_ID，默认 primary。",
        ],
    }


def _require_token(token: Optional[str]) -> str:
    value = (token if token is not None else _env(GOOGLE_ACCESS_TOKEN_ENV)).strip()
    if not value:
        raise NotConfiguredError(
            "Google 账号还没接通：请先在设置 → 连接里完成 Google 授权"
            "（或填 LIUHAO_GOOGLE_ACCESS_TOKEN），再读邮件/日历。"
        )
    return value


def _client(transport: Any = None):
    import httpx

    kwargs: Dict[str, Any] = {"timeout": 20.0}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


# ---------------------------------------------------------------- Gmail


class GmailClient:
    """Gmail API v1 的最小客户端：读新邮件、发邮件（都走官方接口）。"""

    def __init__(self, token: Optional[str] = None, transport: Any = None) -> None:
        self._token = _require_token(token)
        self._transport = transport

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def list_messages(
        self, query: str = "", max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """按 Gmail 搜索语法列邮件（默认只看未读：调用方传 ``q=is:unread``）。"""
        with _client(self._transport) as client:
            resp = client.get(
                f"{GMAIL_BASE}/users/me/messages",
                headers=self._headers(),
                params={"q": query, "maxResults": max(1, min(max_results, 50))},
            )
        resp.raise_for_status()
        return list(resp.json().get("messages") or [])

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """取一封邮件的头（发件人/主题/日期）与摘要；正文不全文拉取。"""
        with _client(self._transport) as client:
            resp = client.get(
                f"{GMAIL_BASE}/users/me/messages/{message_id}",
                headers=self._headers(),
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
        resp.raise_for_status()
        data = resp.json()
        headers = {
            str(h.get("name") or "").lower(): str(h.get("value") or "")
            for h in (data.get("payload") or {}).get("headers") or []
        }
        return {
            "id": str(data.get("id") or message_id),
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": str(data.get("snippet") or ""),
        }

    def list_recent_unread(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """读新邮件：先列未读，再逐封取头（没配 token 时直接抛未接通）。"""
        return [
            self.get_message(str(item.get("id") or ""))
            for item in self.list_messages("is:unread", max_results=max_results)
            if item.get("id")
        ]

    def send_message(self, to: str, subject: str, body: str) -> Dict[str, Any]:
        """发一封纯文本邮件（RFC 822 → base64url，走官方 ``/messages/send``）。"""
        raw = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        with _client(self._transport) as client:
            resp = client.post(
                f"{GMAIL_BASE}/users/me/messages/send",
                headers=self._headers(),
                json={"raw": encoded},
            )
        resp.raise_for_status()
        data = resp.json()
        return {"id": str(data.get("id") or ""), "sent": True}


# ---------------------------------------------------------------- 邮件规则


#: 默认进待办规则：主题或发件人命中关键词 → 记一条待办草稿（等确认才建任务）。
#: 规则只“认出来”，不自动执行、不自动回信；回信永远要 Joseph 自己点。
DEFAULT_MAIL_RULES: List[Dict[str, Any]] = [
    {
        "id": "todo-keywords",
        "name": "主题含“待办/请处理”",
        "match_subject_contains": ["待办", "请处理", "待处理", "TODO", "请确认"],
        "match_from_contains": [],
        "action": "task_draft",
    },
    {
        "id": "invoice",
        "name": "发票/付款类",
        "match_subject_contains": ["发票", "付款", "账单", "报销"],
        "match_from_contains": [],
        "action": "task_draft",
    },
]


def apply_mail_rules(
    message: Dict[str, Any],
    rules: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """把一封邮件按规则转成待办草稿（命中才返回，否则 None）。

    只做“认出来”：草稿标题取自主题，发件人只记名字部分（不记完整地址
    以外的隐私）；不回信、不建任务、不动原邮件（不标已读、不归档——
    批量自动操作不在本轮做，见 docs/connections-routes.md）。
    """
    subject = str(message.get("subject") or "")
    sender = str(message.get("from") or "")
    sender_name = parseaddr(sender)[0] or sender
    for rule in rules if rules is not None else DEFAULT_MAIL_RULES:
        hit_subject = any(
            kw and kw in subject for kw in rule.get("match_subject_contains") or []
        )
        hit_from = any(
            kw and kw in sender for kw in rule.get("match_from_contains") or []
        )
        if hit_subject or hit_from:
            title = subject.strip() or "（无主题邮件）"
            return {
                "title": title,
                "detail": (
                    f"来自 {sender_name} 的邮件（规则：{rule.get('name') or rule.get('id')}）。"
                    f"摘要：{str(message.get('snippet') or '')[:200]}"
                ),
                "external_ref": f"gmail:{message.get('id') or ''}",
                "from_label": sender_name,
                "rule_id": str(rule.get("id") or ""),
            }
    return None


# ---------------------------------------------------------------- Google 日历


class GoogleCalendarClient:
    """Google Calendar API v3 的最小客户端：读/建/改/删日程（官方接口）。"""

    def __init__(
        self,
        token: Optional[str] = None,
        calendar_id: Optional[str] = None,
        transport: Any = None,
    ) -> None:
        self._token = _require_token(token)
        self._calendar_id = (calendar_id or google_calendar_id()).strip() or "primary"
        self._transport = transport

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _base(self) -> str:
        from urllib.parse import quote

        return f"{CALENDAR_BASE}/calendars/{quote(self._calendar_id, safe='')}"

    @staticmethod
    def _event_out(data: Dict[str, Any]) -> Dict[str, Any]:
        start = (data.get("start") or {}).get("dateTime") or (data.get("start") or {}).get("date") or ""
        end = (data.get("end") or {}).get("dateTime") or (data.get("end") or {}).get("date") or ""
        location = str(data.get("location") or "")
        description = str(data.get("description") or "")
        hangout = str((data.get("conferenceData") or {}).get("entryPoints") or "")
        meet_url = ""
        for point in (data.get("conferenceData") or {}).get("entryPoints") or []:
            if str(point.get("entryPointType") or "") == "video":
                meet_url = str(point.get("uri") or "")
                break
        return {
            "id": str(data.get("id") or ""),
            "title": str(data.get("summary") or ""),
            "start": start,
            "end": end,
            "location": location,
            "description": description,
            "meet_url": meet_url or _find_meet_url(location + "\n" + description),
            "html_link": str(data.get("htmlLink") or ""),
        }

    def list_events(
        self, time_min: str = "", time_max: str = "", max_results: int = 20
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(max_results, 50)),
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        with _client(self._transport) as client:
            resp = client.get(f"{self._base()}/events", headers=self._headers(), params=params)
        resp.raise_for_status()
        return [self._event_out(item) for item in resp.json().get("items") or []]

    def create_event(
        self,
        title: str,
        start: str,
        end: str = "",
        location: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start},
            "location": location,
            "description": description,
        }
        if end:
            body["end"] = {"dateTime": end}
        with _client(self._transport) as client:
            resp = client.post(f"{self._base()}/events", headers=self._headers(), json=body)
        resp.raise_for_status()
        return self._event_out(resp.json())

    def update_event(self, event_id: str, **fields: Any) -> Dict[str, Any]:
        """改期/改标题：只传要改的字段（start/end/summary/location/description）。"""
        mapping = {
            "title": "summary",
            "summary": "summary",
            "location": "location",
            "description": "description",
        }
        body: Dict[str, Any] = {}
        for key, value in fields.items():
            if key in ("start", "end"):
                body[key] = {"dateTime": value}
            elif key in mapping:
                body[mapping[key]] = value
        with _client(self._transport) as client:
            resp = client.patch(
                f"{self._base()}/events/{event_id}", headers=self._headers(), json=body
            )
        resp.raise_for_status()
        return self._event_out(resp.json())

    def delete_event(self, event_id: str) -> bool:
        with _client(self._transport) as client:
            resp = client.delete(
                f"{self._base()}/events/{event_id}", headers=self._headers()
            )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def import_to_local(self, store: Any, time_min: str = "", time_max: str = "") -> int:
        """把 Google 日历上的日程同步进本机日历（写 ``source=google-calendar``）。

        同步是“拉取快照”：按 Google 事件 id 去重（uid 字段），已有的不重复写。
        本机改的不回写 Google（回写要另做双向同步，本轮只做单向拉取，
        见 docs/connections-routes.md）。
        """
        from datetime import datetime, timezone

        count = 0
        for event in self.list_events(time_min=time_min, time_max=time_max, max_results=50):
            uid = f"google:{event['id']}"
            existing = [e for e in store.list() if e.uid == uid]
            if existing:
                continue
            start_raw = event["start"]
            try:
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end = None
            if event["end"]:
                try:
                    end = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                except ValueError:
                    end = None
            store.create(
                title=event["title"] or "（无标题）",
                start=start,
                end=end,
                location=event["location"],
                description=event["description"],
                source="google-calendar",
                uid=uid,
                account_id="",
            )
            count += 1
        return count


def _find_meet_url(text: str) -> str:
    """从地点/备注里抠 Meet 链接（``meet.google.com/xxx-yyyy-zzz``）。"""
    import re

    match = re.search(r"https?://meet\.google\.com/[a-z0-9\-]+", text or "")
    return match.group(0) if match else ""
