"""后台派活结束后的用户通知（完成 / 未完成）。

为什么需要它
------------
``POST /v1/goals/assign`` 默认 ``background=true``：接口立刻返回 running，任务
在网关线程里跑。用户关掉页面去做别的事，过去**没有任何通道**会告诉他"跑完了"
或"失败了" —— 只有下次打开「目标与任务」才发现。

这里补上两条真实通道（复用提醒那套，不另造基础设施）：

* Windows 托盘气泡（从网关进程弹，页面关掉也能看到）；
* 落地出站队列 ``reminders_outbox.jsonl`` + 控制台横幅（无桌面时兜底、可追溯）。

文案必须是**中性中文**：只说"完成/没有完成"和下一步去哪看，不出现
``failed`` / 异常类名 / 内部任务名。重复抑制用 ``goal:<id>:<state>`` 幂等键。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .reminder_notify import Notifier, NotifyResult, build_default_notifier

logger = logging.getLogger(__name__)

#: 与提醒共用同一套出口；标题保持产品口径，不带内部术语。
GOAL_NOTIFY_TITLE = "贾维斯任务通知"
_GOAL_TEXT_LIMIT = 60


def _shorten(text: str, limit: int = _GOAL_TEXT_LIMIT) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _failed_task_count(entry: Dict[str, Any]) -> int:
    """失败子任务数：优先读汇总字段，否则从 ``tasks`` 里数真实状态。"""
    raw = entry.get("failed_tasks")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    tasks = entry.get("tasks")
    if not isinstance(tasks, list):
        return 0
    return sum(
        1
        for item in tasks
        if isinstance(item, dict) and str(item.get("status") or "") == "failed"
    )


def _artifact_count(entry: Dict[str, Any]) -> int:
    """真实产物数：只数有路径（且未被标为不存在）的产物条目。"""
    artifacts = entry.get("artifacts")
    if not isinstance(artifacts, list):
        return 0
    count = 0
    for item in artifacts:
        if isinstance(item, str) and item.strip():
            count += 1
        elif isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            if path and item.get("exists") is not False:
                count += 1
    return count


def goal_notification_text(entry: Dict[str, Any]) -> Tuple[str, str]:
    """把一条 goal 终态记录翻译成用户看得懂的一句话通知。

    没有内部状态码、异常文本或任务 id；失败时只给下一步动作。
    ``completed`` 不等于「交付」：目标要求文件却没有任何产物时，不能说
    「可以查看结果」。
    """
    goal_text = _shorten(entry.get("natural_language") or "你派出的任务")
    state = str(entry.get("state") or "").strip().lower()
    failed_tasks = _failed_task_count(entry)
    artifacts = _artifact_count(entry)
    try:
        from src.ai.llm_support import goal_requires_artifact

        needs_artifact = goal_requires_artifact(
            entry.get("natural_language") or ""
        )
    except Exception:  # noqa: BLE001 - 通知不能因导入失败而消失
        needs_artifact = False
    if state == "completed":
        if failed_tasks > 0:
            return GOAL_NOTIFY_TITLE, (
                f"「{goal_text}」跑完了，但其中 {failed_tasks} 项没有成功。"
                "打开「目标与任务」可以查看原因并重试。"
            )
        if needs_artifact and artifacts == 0:
            return GOAL_NOTIFY_TITLE, (
                f"「{goal_text}」跑完了，但没有产出任何文件，不算完成。"
                "打开「目标与任务」可以查看原因并重试。"
            )
        body = f"「{goal_text}」已完成。打开「目标与任务」可以查看结果。"
    elif state == "cancelled":
        body = f"「{goal_text}」已停止，没有继续执行。"
    else:
        hint = f"其中 {failed_tasks} 项任务没有成功。" if failed_tasks > 0 else ""
        body = (
            f"「{goal_text}」没有完成。{hint}"
            "打开「目标与任务」可以查看原因并重试。"
        )
    return GOAL_NOTIFY_TITLE, body


def notify_goal_outcome(
    entry: Dict[str, Any],
    *,
    notifier: Optional[Notifier] = None,
) -> List[NotifyResult]:
    """后台任务结束时发一条通知；任何失败都只记日志，绝不影响任务结果。"""
    try:
        key = f"goal:{entry.get('goal_id') or ''}:{entry.get('state') or ''}"
        if not entry.get("goal_id"):
            return []
        title, body = goal_notification_text(entry)
        results = list(
            (notifier or build_default_notifier()).notify(
                title, body, idempotency_key=key
            )
        )
        if not any(item.ok for item in results):
            logger.warning(
                "任务结束通知全部通道失败（goal=%s）：%s",
                entry.get("goal_id"),
                "; ".join(
                    f"{item.channel}: {item.detail}"
                    for item in results
                    if not item.ok
                ),
            )
        return results
    except Exception:  # noqa: BLE001 - 通知是增强，不能拖垮任务线程
        logger.exception(
            "任务结束通知失败（goal=%s）", entry.get("goal_id") if entry else None
        )
        return []


__all__ = [
    "GOAL_NOTIFY_TITLE",
    "goal_notification_text",
    "notify_goal_outcome",
]
