"""把定时提醒暴露成 LLM 可调用的工具（注册到 ``make_tools``）。

工具动作：
* ``create`` —— 自然语言创建，如「每天十点提醒我喝水」
* ``list`` —— 查看当前提醒
* ``cancel`` —— 取消，之后不会再响
* ``update`` —— 修改时间/内容（每天/每周可改）
* ``failures`` —— 查触发失败及原因
* ``history`` —— 查投递记录

**含糊时间必须反问**：解析器抛 ``NeedsClarification`` 时，本工具返回
``{"status": "needs_clarification", "question": ...}``，提示词要求模型把
``question`` 原样问给用户，**不许自己猜时间**。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from src.ai.tool_registry import Tool
from src.tasks.reminders import ReminderService, get_reminder_service
from src.tasks.reminder_time import NeedsClarification

_ACTIONS = (
    "create",
    "list",
    "cancel",
    "update",
    "failures",
    "history",
    "missed",
    "done",
    "snooze",
    "settings",
)
_ALIASES = {
    "add": "create",
    "new": "create",
    "set": "create",
    "创建": "create",
    "新建": "create",
    "添加": "create",
    "查看": "list",
    "列表": "list",
    "取消": "cancel",
    "删除": "cancel",
    "修改": "update",
    "更改": "update",
    "失败": "failures",
    "history": "history",
    "记录": "history",
    "错过": "missed",
    "错过的提醒": "missed",
    "完成": "done",
    "已完成": "done",
    "稍后": "snooze",
    "延后": "snooze",
    "稍后再提醒": "snooze",
    "设置": "settings",
    "配置": "settings",
    "勿扰": "settings",
}

_SETTING_KEYS = (
    "quiet_enabled",
    "quiet_start",
    "quiet_end",
    "quiet_start_minutes",
    "quiet_end_minutes",
    "missed_threshold_minutes",
    "missed_threshold_hours",
    "snooze_minutes",
)


def _dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_reminder_tool(
    principal: str,
    service: Optional[ReminderService] = None,
) -> Tool:
    """构建 ``manage_reminders`` 工具（绑定当前主体）。"""

    def manage_reminders(
        action: str = "",
        text: str = "",
        reminder_id: str = "",
        **kwargs: Any,
    ) -> str:
        raw_action = (
            action
            or kwargs.get("operation")
            or kwargs.get("op")
            or kwargs.get("action")
            or ""
        )
        act = str(raw_action).strip().lower()
        act = _ALIASES.get(act, act)
        reminder_text = str(
            text or kwargs.get("content") or kwargs.get("message") or ""
        ).strip()
        target_id = str(
            reminder_id or kwargs.get("id") or kwargs.get("reminder") or ""
        ).strip()

        try:
            svc = service or get_reminder_service()
            if act == "create":
                if not reminder_text:
                    return _dumps(
                        {
                            "status": "error",
                            "error": "请提供 text，例如「每天十点提醒我喝水」。",
                        }
                    )
                result = svc.create(user_id=principal, text=reminder_text)
                return _dumps(
                    {
                        "status": "ok",
                        "message": f"已设置提醒：{result['human_time']} · {result['title']}",
                        "reminder": result,
                    }
                )

            if act == "list":
                reminders = svc.list(principal, include_cancelled=False)
                return _dumps(
                    {
                        "status": "ok",
                        "count": len(reminders),
                        "message": (
                            f"当前有 {len(reminders)} 条生效中的提醒。"
                            if reminders
                            else "当前没有生效中的提醒。"
                        ),
                        "reminders": reminders,
                    }
                )

            if act == "cancel":
                if not target_id:
                    return _dumps(
                        {"status": "error", "error": "请提供 reminder_id（可先 list）。"}
                    )
                return _dumps(svc.cancel(principal, target_id))

            if act == "update":
                if not target_id or not reminder_text:
                    return _dumps(
                        {
                            "status": "error",
                            "error": "修改需要 reminder_id 与新的 text。",
                        }
                    )
                return _dumps(svc.update(principal, target_id, reminder_text))

            if act == "failures":
                return _dumps({"status": "ok", **svc.failures(principal)})

            if act == "history":
                deliveries = svc.list_deliveries(
                    principal, reminder_id=target_id or None
                )
                return _dumps(
                    {"status": "ok", "count": len(deliveries), "deliveries": deliveries}
                )

            if act == "missed":
                return _dumps(svc.missed(principal))

            if act == "done":
                return _dumps(
                    svc.done(
                        principal,
                        reminder_id=target_id or None,
                        delivery_id=kwargs.get("delivery_id"),
                    )
                )

            if act == "snooze":
                minutes = kwargs.get("minutes")
                if minutes in (None, ""):
                    minutes = None
                return _dumps(
                    svc.snooze(
                        principal,
                        reminder_id=target_id or None,
                        delivery_id=kwargs.get("delivery_id"),
                        minutes=minutes,
                    )
                )

            if act == "settings":
                changes = {
                    key: kwargs[key]
                    for key in _SETTING_KEYS
                    if kwargs.get(key) is not None
                }
                if not changes:
                    return _dumps({"status": "ok", "settings": svc.settings()})
                return _dumps(svc.update_settings(changes))

            return _dumps(
                {
                    "status": "error",
                    "error": f"未知 action={act!r}，可用：{', '.join(_ACTIONS)}。",
                }
            )
        except NeedsClarification as exc:
            return _dumps(
                {
                    "status": "needs_clarification",
                    "question": exc.question,
                    "instruction": "把 question 原样问用户，不要自己猜时间。",
                }
            )
        except Exception as exc:  # noqa: BLE001 - 工具层返回错误，不中断对话
            return _dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})

    return Tool(
        tool_id="liuhao.reminder.manage",
        name="manage_reminders",
        version="1.0.0",
        description=(
            "创建/查看/取消/修改定时提醒。用户说「X点提醒我Y」「每天/每周…提醒我」"
            "时调用。时间含糊（如「下周那个会」「晚点」）会返回 "
            "needs_clarification，必须把 question 原样问用户，不要猜时间。"
            "提醒响过之后用 action=done 标记完成，或 action=snooze 稍后再提醒；"
            "错过的提醒用 action=missed 查明细；勿扰时段/错过阈值用 action=settings 查看或修改。"
            "失败原因用 action=failures 查询。"
        ),
        capability="reminder.manage",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": (
                        "create / list / cancel / update / failures / history /"
                        " missed / done / snooze / settings"
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "自然语言提醒，例如「每天十点提醒我喝水」",
                },
                "reminder_id": {
                    "type": "string",
                    "description": "取消/修改/查历史/完成/稍后时使用；可先用 action=list 获取",
                },
                "delivery_id": {
                    "type": "integer",
                    "description": "完成/稍后指定某一次投递；不填则取最近一条待处理的",
                },
                "minutes": {
                    "type": "integer",
                    "description": "snooze 的延后分钟数，默认取设置的 snooze_minutes（10）",
                },
                "quiet_enabled": {
                    "type": "boolean",
                    "description": "settings：是否启用勿扰时段",
                },
                "quiet_start": {
                    "type": "string",
                    "description": "settings：勿扰开始，HH:MM，例如 23:00",
                },
                "quiet_end": {
                    "type": "string",
                    "description": "settings：勿扰结束，HH:MM，例如 07:00",
                },
                "missed_threshold_hours": {
                    "type": "number",
                    "description": "settings：到点后多久算错过，单位小时，默认 6",
                },
                "snooze_minutes": {
                    "type": "integer",
                    "description": "settings：稍后提醒的默认分钟数",
                },
            },
            "required": ["action"],
        },
        fn=manage_reminders,
        risk="LOW",
    )


__all__ = ["build_reminder_tool"]
