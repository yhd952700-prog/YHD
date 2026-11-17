"""来邮件自动处理：规则引擎 + 开任务 + 摘要链路（C-额外任务 1）。

本沙箱**没有真实邮箱账号**，也没有接通 Gmail/IMAP：本模块只做
“邮件到了之后按规则怎么办”，不假装已经连上邮箱。

链路（全部可离线测）：
- 规则：发件人/主题/正文关键词或主题正则命中 -> 自动开任务；
- 开任务：经 ``src.workflow.store`` 建一份**草稿计划**（等 Joseph 批了
  再执行，和例程建计划同一条路），标题带邮件主题，步骤里留痕；
- 摘要：规则要求回摘要时才需要模型。没有模型（``summarizer=None``）
  就老实记 ``summary_status="unavailable_no_model"``，不编摘要；
  有模型时由调用方把真模型传进来，本模块不自己猜 Key 在哪。

邮箱接入本身只留配置入口：见 :func:`mailbox_status` 与
``docs/email-auto-processing.md``——要 Joseph 在设置里接自己的
邮箱（Gmail API 或 IMAP），接通后由轮询器把新邮件喂给
:func:`process_incoming_email`。没接通之前，任何地方都不得写
“已接通邮箱”。
"""

from __future__ import annotations

import json
import logging
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


def mailbox_status() -> Dict[str, Any]:
    """邮箱接入状态：现在永远是未连接（本沙箱无真实邮箱账号）。"""
    return {
        "connected": False,
        "provider": None,
        "note": MAILBOX_NOT_CONNECTED,
        "config_entry": "设置 → 邮件接入（待接：Gmail API / IMAP 二选一）",
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
