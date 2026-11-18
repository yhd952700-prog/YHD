"""来邮件自动处理：规则引擎 + 开任务 + 摘要链路（C-额外任务 1）。

链路（全部可离线测）：
- 规则：发件人/主题/正文关键词或主题正则命中 -> 自动开任务；
- 开任务：经 ``src.workflow.store`` 建一份**草稿计划**（等 Joseph 批了
  再执行，和例程建计划同一条路），标题带邮件主题，步骤里留痕；
- 摘要：规则要求回摘要时才需要模型。没有模型（``summarizer=None``）
  就老实记 ``summary_status="unavailable_no_model"``，不编摘要；
  有模型时由调用方把真模型传进来，本模块不自己猜 Key 在哪。

邮箱接入（完善第二轮 A4 已接线）：轮询器 :func:`poll_gmail_once` 经
``src.integrations.google_workspace.GmailClient``（Gmail 官方 API，
OAuth token 只认 ``LIUHAO_GOOGLE_ACCESS_TOKEN`` 环境变量）拉未读邮件
喂给 :func:`process_incoming_email`；本地排程器任务 ``liuhao.email.poll``
周期调用它。两道开关缺一不动：

- Google token 没配 -> 如实报未接通，不拉邮件、不编状态；
- ``LIUHAO_EMAIL_RULES_ENABLED`` 没开（默认关）-> 轮询直接跳过。

接通状态见 :func:`mailbox_status`：它只转述 Google 接入层的真实状态，
没接通之前，任何地方都不得写“已接通邮箱”。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.common.atomic_write import atomic_write_text, read_text_with_retry
from src.common.data_paths import resolve_store_path
from src.common.engines import data_dir

logger = logging.getLogger(__name__)

#: 规则落盘：环境变量可覆盖位置；默认 data/email_rules.json。
RULES_FILE_ENV = "LIUHAO_EMAIL_RULES_FILE"
_DEFAULT_RULES_PATH = data_dir() / "email_rules.json"

#: 邮箱还没接：配置入口只报状态，不编“已接通”。
MAILBOX_NOT_CONNECTED = (
    "还没有接任何邮箱：要 Joseph 在设置里接自己的邮箱（Gmail 或 IMAP），"
    "见 docs/email-auto-processing.md。接通前不会收任何邮件。"
)

#: 规则轮询开关的环境变量：默认关，开了才真的拉邮件建草稿计划。
EMAIL_RULES_ENABLED_ENV = "LIUHAO_EMAIL_RULES_ENABLED"


def email_rules_enabled() -> bool:
    """自动处理开关：默认关；只有显式设 1/true 才开（建的是草稿也要他点头才开）。"""
    return (os.environ.get(EMAIL_RULES_ENABLED_ENV) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def mailbox_status() -> Dict[str, Any]:
    """邮箱接入状态：转述 Google 接入层的真实接通状态，不自己编。

    token 配了才算接通（provider=gmail，走官方 API）；规则轮询是否
    在跑另看 ``rules_enabled``——接通邮箱不等于开了自动处理。
    """
    try:
        from src.integrations.google_workspace import google_status

        google = google_status()
        connected = bool(google.get("configured"))
    except Exception:  # noqa: BLE001 - 接入层导入失败按未接通报，不许假装接通
        google, connected = {}, False
    if not connected:
        return {
            "connected": False,
            "provider": None,
            "note": MAILBOX_NOT_CONNECTED,
            "config_entry": "设置 → 邮件接入（待接：Gmail API / IMAP 二选一）",
            "rules_enabled": email_rules_enabled(),
        }
    return {
        "connected": True,
        "provider": "gmail",
        "note": (
            "已接通 Gmail（官方 API）；未读邮件由轮询器喂给规则引擎，"
            "命中规则只建草稿计划、等审批，不自动执行、不自动回信。"
            if email_rules_enabled()
            else "已接通 Gmail（官方 API），但自动处理开关没开：不会拉邮件建计划。"
        ),
        "config_entry": "设置 → 连接（Google 授权）",
        "rules_enabled": email_rules_enabled(),
        "google_status": google.get("status"),
    }


def _rules_path() -> Path:
    return Path(
        resolve_store_path(
            "email_rules.json",
            _DEFAULT_RULES_PATH,
            env_var=RULES_FILE_ENV,
        )
    )


@dataclass
class EmailMessage:
    """一封待处理的新邮件（由邮箱轮询器喂进来，本模块不收邮件）。"""

    message_id: str
    sender: str
    subject: str
    snippet: str = ""
    body: str = ""
    received_at: str = ""


@dataclass
class EmailRule:
    """一条自动处理规则：命中条件全是“包含/正则”，不做语义猜测。"""

    id: str = ""
    name: str = ""
    sender_contains: str = ""
    subject_contains: str = ""
    subject_regex: str = ""
    body_contains: str = ""
    action: str = "create_task"  # create_task | create_task_and_summarize
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "EmailRule":
        return cls(
            id=str(raw.get("id") or ""),
            name=str(raw.get("name") or ""),
            sender_contains=str(raw.get("sender_contains") or ""),
            subject_contains=str(raw.get("subject_contains") or ""),
            subject_regex=str(raw.get("subject_regex") or ""),
            body_contains=str(raw.get("body_contains") or ""),
            action=str(raw.get("action") or "create_task"),
            enabled=bool(raw.get("enabled", True)),
        )


def load_rules(path: Optional[Path] = None) -> List[EmailRule]:
    """读规则文件：没有/损坏都当作“没有规则”，不抛错、不编规则。"""
    target = Path(path) if path else _rules_path()
    try:
        payload = json.loads(read_text_with_retry(target))
    except (FileNotFoundError, OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    rules = []
    for item in payload:
        if isinstance(item, dict):
            rules.append(EmailRule.from_dict(item))
    return rules


def save_rules(rules: List[EmailRule], path: Optional[Path] = None) -> Path:
    """写规则文件（原子写）。返回实际落盘路径。"""
    target = Path(path) if path else _rules_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps([r.to_dict() for r in rules], ensure_ascii=False, indent=2),
    )
    return target


def add_rule(rule: EmailRule, path: Optional[Path] = None) -> EmailRule:
    """追加一条规则（没给 id 就生成一个）。"""
    rules = load_rules(path)
    if not rule.id:
        rule = EmailRule(**{**rule.to_dict(), "id": "emr-" + uuid.uuid4().hex[:12]})
    if rule.action not in ("create_task", "create_task_and_summarize"):
        raise ValueError(f"不认识的邮件动作：{rule.action!r}")
    if rule.subject_regex:
        re.compile(rule.subject_regex)  # 正则写错了现在就报错，别等邮件来了
    rules.append(rule)
    save_rules(rules, path)
    return rule


def match_rule(email: EmailMessage, rule: EmailRule) -> bool:
    """一条规则是否命中：所有写了的条件都要满足（AND），没写条件不算命中。"""
    if not rule.enabled:
        return False
    conditions = 0
    if rule.sender_contains:
        conditions += 1
        if rule.sender_contains.lower() not in (email.sender or "").lower():
            return False
    if rule.subject_contains:
        conditions += 1
        if rule.subject_contains.lower() not in (email.subject or "").lower():
            return False
    if rule.subject_regex:
        conditions += 1
        try:
            if not re.search(rule.subject_regex, email.subject or ""):
                return False
        except re.error:
            return False
    if rule.body_contains:
        conditions += 1
        haystack = f"{email.snippet or ''}\n{email.body or ''}".lower()
        if rule.body_contains.lower() not in haystack:
            return False
    return conditions > 0


def _plan_steps(email: EmailMessage, summary_status: str) -> List[Dict[str, Any]]:
    steps = [
        {
            "title": "读这封邮件",
            "what": f"读发件人 {email.sender} 的邮件「{email.subject}」，确认要办什么。",
            "assignee": "organizer",
            "risk": "low",
        },
    ]
    if summary_status == "unavailable_no_model":
        steps.append(
            {
                "title": "等模型配好再回摘要",
                "what": "现在没配模型，摘要生成不了，先别编；等模型配好再回来生成摘要。",
                "assignee": "organizer",
                "risk": "low",
            }
        )
    else:
        steps.append(
            {
                "title": "按邮件内容跟进",
                "what": "按邮件里说的事去办；要发钱、删东西、对外发消息先走审批。",
                "assignee": "organizer",
                "risk": "medium",
                "approval_required": True,
            }
        )
    return steps


def process_incoming_email(
    email: EmailMessage,
    rules: List[EmailRule],
    *,
    plan_store: Any,
    summarizer: Optional[Callable[[EmailMessage], str]] = None,
    owner_account_id: str = "root",
) -> Dict[str, Any]:
    """处理一封新邮件：命中规则 -> 建草稿计划（任务）；按需生成摘要。

    摘要需要模型：调用方把真模型包成 ``summarizer`` 传进来；没传就
    老实记 ``unavailable_no_model``，绝不编一段摘要充数。
    """
    matched: Optional[EmailRule] = None
    for rule in rules:
        if match_rule(email, rule):
            matched = rule
            break
    if matched is None:
        return {
            "matched": False,
            "plan_id": None,
            "rule_id": None,
            "summary_status": "not_applicable",
            "summary": None,
            "note": "没有规则命中这封邮件，没建任务。",
        }
    needs_summary = matched.action == "create_task_and_summarize"
    summary: Optional[str] = None
    summary_status = "not_requested"
    if needs_summary:
        if summarizer is None:
            summary_status = "unavailable_no_model"
        else:
            try:
                summary = (summarizer(email) or "").strip() or None
                summary_status = "ready" if summary else "failed_empty"
            except Exception as exc:  # noqa: BLE001 - 摘要失败不能丢了任务
                summary_status = "failed"
                logger.warning("email summary failed: %s", exc)
    goal = (
        f"处理新邮件：发件人 {email.sender}，主题「{email.subject}」。"
        + (f"摘要：{summary}" if summary else "")
        + (
            "（摘要待生成：未配置模型，不要凭空编。）"
            if summary_status == "unavailable_no_model"
            else ""
        )
    )
    plan = plan_store.create_plan(
        title=f"处理邮件：{email.subject[:80] or '(无主题)'}",
        goal=goal,
        created_by="email_rule:" + (matched.id or "manual"),
        owner_account_id=owner_account_id,
        steps=_plan_steps(email, summary_status),
    )
    _audit_email(email, matched, plan.get("plan_id", ""), summary_status)
    return {
        "matched": True,
        "plan_id": plan.get("plan_id"),
        "rule_id": matched.id,
        "summary_status": summary_status,
        "summary": summary,
        "note": "已按规则建草稿计划，等你批了再执行。",
    }


def _audit_email(
    email: EmailMessage, rule: EmailRule, plan_id: str, summary_status: str
) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            "system",
            AuditScope.L1,
            "success",
            {
                "action": "email.rule_matched",
                "rule_id": rule.id,
                "plan_id": plan_id,
                "summary_status": summary_status,
                "sender": email.sender,
                "subject": email.subject,
            },
            correlation_id=plan_id or email.message_id,
        )
    except Exception:  # noqa: BLE001 - 审计写不进去不能丢了任务
        logger.warning("email rule audit failed: %s", rule.id)


# ---------------------------------------------------------------------------
# Gmail 轮询接线（完善第二轮 A4）：排程器周期调用 poll_gmail_once。
# ---------------------------------------------------------------------------

#: 已处理过的 Gmail 消息 id 落盘：同一封未读邮件不能每轮都重建一遍计划。
SEEN_FILE_ENV = "LIUHAO_EMAIL_RULES_SEEN_FILE"
_DEFAULT_SEEN_PATH = data_dir() / "email_rules_seen.json"
_SEEN_CAP = 500


def _seen_path() -> Path:
    return Path(
        resolve_store_path(
            "email_rules_seen.json",
            _DEFAULT_SEEN_PATH,
            env_var=SEEN_FILE_ENV,
        )
    )


def _load_seen_ids() -> List[str]:
    try:
        raw = read_text_with_retry(_seen_path())
        loaded = json.loads(raw) if raw else []
        return [str(x) for x in loaded] if isinstance(loaded, list) else []
    except (OSError, ValueError):
        return []


def _save_seen_ids(ids: List[str]) -> None:
    path = _seen_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(ids[-_SEEN_CAP:], ensure_ascii=False))


def poll_gmail_once(
    *,
    transport: Any = None,
    plan_store: Any = None,
    summarizer: Optional[Callable[[EmailMessage], str]] = None,
    owner_account_id: str = "root",
    max_results: int = 10,
) -> Dict[str, Any]:
    """轮询一次 Gmail 未读并按规则处理（排程器 ``liuhao.email.poll`` 调用）。

    两道开关缺一不动，且都如实回报原因，不假装跑过：
    ``LIUHAO_EMAIL_RULES_ENABLED`` 没开 -> ``reason="disabled"``；
    Google token 没配 -> ``reason="not_configured"``。

    只拉邮件头 + 摘要（GmailClient 的既有口径，不全文拉正文）；处理过
    的消息 id 落盘去重。命中规则也只建**草稿计划**，不执行、不回信。
    """
    if not email_rules_enabled():
        return {
            "ran": False, "reason": "disabled",
            "note": f"自动处理开关没开（{EMAIL_RULES_ENABLED_ENV}），本轮跳过。",
        }
    from src.integrations.google_workspace import GmailClient, is_google_configured

    if not is_google_configured():
        return {
            "ran": False, "reason": "not_configured",
            "note": "Google 账号还没接通（没有 OAuth token），本轮没拉邮件。",
        }
    if plan_store is None:
        from src.workflow.store import get_workflow_store

        plan_store = get_workflow_store()
    rules = load_rules()
    client = GmailClient(transport=transport)
    messages = client.list_recent_unread(max_results=max_results)
    seen = _load_seen_ids()
    seen_set = set(seen)
    processed = 0
    matched = 0
    plan_ids: List[str] = []
    for message in messages:
        message_id = str(message.get("id") or "")
        if not message_id or message_id in seen_set:
            continue
        email = EmailMessage(
            message_id=message_id,
            sender=str(message.get("from") or ""),
            subject=str(message.get("subject") or ""),
            snippet=str(message.get("snippet") or ""),
            body="",
            received_at=str(message.get("date") or ""),
        )
        result = process_incoming_email(
            email, rules,
            plan_store=plan_store, summarizer=summarizer,
            owner_account_id=owner_account_id,
        )
        processed += 1
        if result.get("matched"):
            matched += 1
            if result.get("plan_id"):
                plan_ids.append(str(result["plan_id"]))
        seen.append(message_id)
        seen_set.add(message_id)
    if processed:
        _save_seen_ids(seen)
    return {
        "ran": True,
        "fetched": len(messages),
        "processed": processed,
        "matched": matched,
        "plan_ids": plan_ids,
        "note": (
            f"本轮拉到 {len(messages)} 封未读，新处理 {processed} 封，"
            f"命中规则 {matched} 封（只建草稿计划，等审批）。"
        ),
    }
