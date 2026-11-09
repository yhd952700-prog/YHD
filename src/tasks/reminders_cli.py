"""提醒的命令行只读入口（``python -m src.reminders ...``）。

用户和排查的人不必打开界面、也不必先问鎏灏，就能看到提醒的真实状态：
``list`` / ``failures`` / ``history``。三条命令全部只读：不创建、不修改、
不取消提醒，也不写投递记录；需要改提醒仍然走对话。

数据库路径与运行时完全一致：提醒库走 ``reminders.default_reminders_db()``
（数据根 → 工作区根 → 旧位置，必要时先迁移），排程器库走
``local_scheduler.default_scheduler_db()``（由排程器进程定义，CLI 只跟随，
不另搬一份，避免两套并存），所以看到的就是排程器进程正在用的那份数据。
``--json`` 输出稳定结构，方便以后做界面或监控。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.tasks.local_scheduler import LocalScheduler, default_scheduler_db
from src.tasks.reminder_time import WEEKDAY_NAMES, local_timezone
from src.tasks.reminders import (
    DIGEST_REMINDER_ID,
    REMINDER_DIGEST_JOB,
    REMINDER_FIRE_JOB,
    ReminderStore,
    default_reminders_db,
)

DEFAULT_LIST_LIMIT = 10000
DEFAULT_FAILURES_LIMIT = 50
DEFAULT_HISTORY_LIMIT = 20

STATUS_LABELS = {
    "正常": "normal",
    "已错过": "missed",
    "已完成": "completed",
    "已取消": "cancelled",
}

ACK_LABELS = {
    "done": "已完成",
    "snoozed": "稍后提醒",
}


def _reminders_db(args: argparse.Namespace) -> str:
    """与提醒运行时同一个入口（含旧位置一次性迁移），不另拼路径。"""
    return getattr(args, "db", None) or default_reminders_db()


def _scheduler_db(args: argparse.Namespace) -> str:
    """排程器库落点由 ``local_scheduler`` 定义，CLI 只跟随同一函数。"""
    return getattr(args, "scheduler_db", None) or default_scheduler_db()


def _local(
    value: Any, tz, *, with_weekday: bool = False
) -> Optional[str]:
    """把库里存的 naive UTC ISO 转成本地可读时间。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(tz)
    text = local.strftime("%Y-%m-%d %H:%M")
    if with_weekday:
        text += f"（周{WEEKDAY_NAMES[local.weekday()]}）"
    return text


def _repeat_label(record: Dict[str, Any]) -> str:
    kind = record.get("repeat_kind")
    if kind == "daily":
        return f"每天 {int(record['hour']):02d}:{int(record['minute']):02d}"
    if kind == "weekly":
        weekday = int(record["weekday"])
        return (
            f"每周{WEEKDAY_NAMES[weekday]} "
            f"{int(record['hour']):02d}:{int(record['minute']):02d}"
        )
    return "一次"


def _status_label(record: Dict[str, Any], counts: Dict[str, int]) -> str:
    raw = record.get("status")
    if raw == "cancelled":
        return "已取消"
    if raw == "completed":
        # 一次性提醒错过之后会被标成 completed；投递记录才能区分"错过"和"做完"。
        if counts.get("missed") and not counts.get("delivered"):
            return "已错过"
        return "已完成"
    return "正常"


def _collect_reminders(
    store: ReminderStore, users: List[str]
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for user_id in users:
        records.extend(store.list(user_id, limit=DEFAULT_LIST_LIMIT))
    records.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    records.reverse()
    return records


def _reminder_items(
    store: ReminderStore, users: List[str], tz
) -> List[Dict[str, Any]]:
    summary = store.delivery_status_counts()
    items = []
    for record in _collect_reminders(store, users):
        counts = summary.get(str(record["id"]), {})
        status_label = _status_label(record, counts)
        active = record.get("status") == "active"
        items.append(
            {
                "id": record["id"],
                "user_id": record["user_id"],
                "title": record["title"],
                "status": record["status"],
                "status_label": status_label,
                "repeat": record.get("repeat_kind"),
                "repeat_label": _repeat_label(record),
                "next_run_at": record.get("run_at") if active else None,
                "next_run_at_local": (
                    _local(record.get("run_at"), tz, with_weekday=True)
                    if active
                    else None
                ),
                "last_fired_at": record.get("last_fired_at"),
                "last_fired_at_local": _local(record.get("last_fired_at"), tz),
                "missed_count": int(counts.get("missed", 0)),
                "delivered_count": int(counts.get("delivered", 0)),
                "last_error": record.get("last_error"),
            }
        )
    return items


def _counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"total": len(items), "normal": 0, "missed": 0, "completed": 0, "cancelled": 0}
    for item in items:
        key = STATUS_LABELS.get(item["status_label"])
        if key:
            counts[key] += 1
    return counts


def _title_map(
    store: Optional[ReminderStore], users: List[str]
) -> Dict[str, str]:
    if store is None:
        return {}
    titles: Dict[str, str] = {}
    for user_id in users:
        for record in store.list(user_id, limit=DEFAULT_LIST_LIMIT):
            titles[str(record["id"])] = str(record["title"])
    return titles


def _display_title(reminder_id: str, titles: Dict[str, str]) -> str:
    if reminder_id == DIGEST_REMINDER_ID:
        return "错过提醒汇总"
    return titles.get(reminder_id, "（提醒记录已不存在）")


def _failed_delivery_items(
    store: Optional[ReminderStore],
    users: List[str],
    titles: Dict[str, str],
    tz,
    limit: int,
) -> List[Dict[str, Any]]:
    if store is None:
        return []
    rows: List[Dict[str, Any]] = []
    for user_id in users:
        rows.extend(store.list_deliveries(user_id, status="failed", limit=limit))
        # unknown：进程死在投递中途，无法确认是否响过。按"最多一次"不再重响，
        # 用户必须能在这里看到（否则就是静默漏响）。
        rows.extend(store.list_deliveries(user_id, status="unknown", limit=limit))
    rows.sort(key=lambda item: int(item["id"]), reverse=True)
    rows = rows[:limit]
    return [
        {
            "delivery_id": row["id"],
            "reminder_id": row["reminder_id"],
            "user_id": row["user_id"],
            "title": _display_title(str(row["reminder_id"]), titles),
            "occurrence": row["occurrence"],
            "fired_at": row["created_at"],
            "fired_at_local": _local(row["created_at"], tz),
            "status": row.get("status"),
            "status_label": (
                "结果不明" if row.get("status") == "unknown" else "投递失败"
            ),
            "reason": row.get("error") or "",
            "channels": row.get("channels") or [],
        }
        for row in rows
    ]


def _dead_letter_items(
    scheduler_db: str, user_id: Optional[str], tz, limit: int
) -> List[Dict[str, Any]]:
    if not os.path.exists(scheduler_db):
        return []
    scheduler = LocalScheduler(
        scheduler_db, audit_sink=lambda action, outcome, details: None
    )
    try:
        jobs = scheduler.list_dead_letters(limit=max(limit, 100))
    finally:
        scheduler.close()
    items = []
    for job in jobs:
        if job.get("job_name") not in (REMINDER_FIRE_JOB, REMINDER_DIGEST_JOB):
            continue
        payload = job.get("payload") or {}
        if user_id and payload.get("user_id") != user_id:
            continue
        reminder_id = str(payload.get("reminder_id") or "")
        items.append(
            {
                "job_id": job.get("id"),
                "job_name": job.get("job_name"),
                "user_id": payload.get("user_id"),
                "reminder_id": reminder_id,
                "title": str(payload.get("title") or "错过提醒汇总"),
                "attempts": job.get("attempts"),
                "max_attempts": job.get("max_attempts"),
                "reason": job.get("last_error") or "",
                "dead_lettered_at": job.get("dead_lettered_at"),
                "dead_lettered_at_local": _local(job.get("dead_lettered_at"), tz),
            }
        )
    items.sort(key=lambda item: item.get("dead_lettered_at") or "", reverse=True)
    return items[:limit]


def _history_items(
    store: Optional[ReminderStore],
    users: List[str],
    titles: Dict[str, str],
    tz,
    limit: int,
) -> List[Dict[str, Any]]:
    if store is None:
        return []
    rows: List[Dict[str, Any]] = []
    for user_id in users:
        rows.extend(store.list_history(user_id, limit=limit))
    rows.sort(key=lambda item: int(item["id"]), reverse=True)
    rows = rows[:limit]
    items = []
    for row in rows:
        ack_status = row.get("ack_status")
        items.append(
            {
                "delivery_id": row["id"],
                "reminder_id": row["reminder_id"],
                "user_id": row["user_id"],
                "title": _display_title(str(row["reminder_id"]), titles),
                "status": row["status"],
                "status_label": "已错过" if row["status"] == "missed" else "已触发",
                "occurrence": row["occurrence"],
                "occurrence_local": _local(row["occurrence"], tz),
                "fired_at": row["created_at"],
                "fired_at_local": _local(row["created_at"], tz),
                "lateness_seconds": row.get("lateness_seconds"),
                "ack_status": ack_status,
                "ack_label": ACK_LABELS.get(ack_status, "未处理"),
                "acked_at": row.get("acked_at"),
                "acked_at_local": _local(row.get("acked_at"), tz),
            }
        )
    return items


def _print_list(items: List[Dict[str, Any]], counts: Dict[str, int]) -> None:
    print(
        f"提醒共 {counts['total']} 条：正常 {counts['normal']} / "
        f"已错过 {counts['missed']} / 已完成 {counts['completed']} / "
        f"已取消 {counts['cancelled']}"
    )
    if not items:
        print("还没有提醒记录。")
        return
    for item in items:
        next_text = item["next_run_at_local"] or "—"
        extra = ""
        if item["missed_count"]:
            extra = f"（错过 {item['missed_count']} 次）"
        print(
            f"[{item['status_label']}] {next_text}  {item['repeat_label']}  "
            f"{item['title']}{extra}  id={item['id']}"
        )
        if item["last_error"]:
            print(f"    最近错误：{item['last_error']}")


def _print_failures(
    failed: List[Dict[str, Any]], dead: List[Dict[str, Any]]
) -> None:
    if not failed and not dead:
        print("没有失败的投递或死信，提醒都正常。")
        return
    print(f"失败/不明投递 {len(failed)} 条，死信 {len(dead)} 个。")
    for item in failed:
        label = item.get("status_label") or "投递失败"
        print(
            f"[{label}] {item['fired_at_local'] or item['fired_at']}  "
            f"{item['title']}  原因：{item['reason']}"
        )
    for item in dead:
        print(
            f"[死信] {item['dead_lettered_at_local'] or item['dead_lettered_at']}  "
            f"{item['title']}  重试 {item['attempts']}/{item['max_attempts']} 后放弃  "
            f"原因：{item['reason']}"
        )


def _print_history(items: List[Dict[str, Any]], limit: int) -> None:
    if not items:
        print("还没有触发记录。")
        return
    print(f"最近 {len(items)} 条触发记录（最多 {limit} 条）：")
    for item in items:
        missed = "（已错过）" if item["status"] == "missed" else ""
        print(
            f"[{item['ack_label']}] {item['fired_at_local'] or item['fired_at']}  "
            f"{item['title']}{missed}"
        )


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS 让写在子命令前后的选项都不会被默认值覆盖。
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出 JSON（便于界面/监控）")
    common.add_argument("--user", default=argparse.SUPPRESS,
                        help="只看某个用户；默认全部")
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help="提醒数据库路径（默认与运行时一致）")
    common.add_argument("--scheduler-db", dest="scheduler_db",
                        default=argparse.SUPPRESS,
                        help="排程器数据库路径（读死信用）")

    parser = argparse.ArgumentParser(
        prog="python -m src.reminders",
        description="只读查看提醒状态；修改提醒仍走对话。",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser(
        "list", parents=[common], help="列出全部提醒（内容/下次触发/重复规则/状态）"
    )
    list_parser.set_defaults(command="list")

    fail_parser = sub.add_parser(
        "failures", parents=[common], help="只列失败的投递与死信（含原因与时间）"
    )
    fail_parser.add_argument("--limit", type=_positive_int,
                             default=DEFAULT_FAILURES_LIMIT,
                             help=f"最多显示多少条（默认 {DEFAULT_FAILURES_LIMIT}）")
    fail_parser.set_defaults(command="failures")

    history_parser = sub.add_parser(
        "history", parents=[common], help="最近触发的提醒与处理结果"
    )
    history_parser.add_argument("--limit", type=_positive_int,
                                default=DEFAULT_HISTORY_LIMIT,
                                help=f"最多显示多少条（默认 {DEFAULT_HISTORY_LIMIT}）")
    history_parser.set_defaults(command="history")

    return parser


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"需要整数，收到 {raw!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("--limit 必须 >= 1")
    return value


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    json_output = bool(getattr(args, "json", False))
    try:
        return _dispatch(args, json_output)
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        print(
            f"读取提醒数据失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


def _dispatch(args: argparse.Namespace, json_output: bool) -> int:
    db_path = _reminders_db(args)
    scheduler_db = _scheduler_db(args)
    tz = local_timezone()
    user = getattr(args, "user", None)

    store: Optional[ReminderStore] = None
    if os.path.exists(db_path):
        store = ReminderStore(db_path)
    try:
        if store is not None:
            users = [user] if user else store.distinct_users()
        else:
            users = [user] if user else []

        if args.command == "list":
            items = _reminder_items(store, users, tz) if store else []
            counts = _counts(items)
            if json_output:
                print(json.dumps(
                    {
                        "command": "list",
                        "db": db_path,
                        "count": len(items),
                        "counts": counts,
                        "reminders": items,
                    },
                    ensure_ascii=False,
                    indent=2,
                ))
            else:
                if store is None:
                    print(f"还没有提醒数据（数据库不存在）：{db_path}")
                else:
                    _print_list(items, counts)
            return 0

        titles = _title_map(store, users)
        limit = int(getattr(args, "limit", 0) or 0)

        if args.command == "failures":
            failed = _failed_delivery_items(store, users, titles, tz, limit)
            dead = _dead_letter_items(scheduler_db, user, tz, limit)
            if json_output:
                print(json.dumps(
                    {
                        "command": "failures",
                        "db": db_path,
                        "scheduler_db": scheduler_db,
                        "count": len(failed) + len(dead),
                        "failed_deliveries": failed,
                        "dead_letter_jobs": dead,
                    },
                    ensure_ascii=False,
                    indent=2,
                ))
            else:
                _print_failures(failed, dead)
            return 0

        # history
        items = _history_items(store, users, titles, tz, limit)
        if json_output:
            print(json.dumps(
                {
                    "command": "history",
                    "db": db_path,
                    "count": len(items),
                    "limit": limit,
                    "records": items,
                },
                ensure_ascii=False,
                indent=2,
            ))
        else:
            _print_history(items, limit)
        return 0
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
