"""``python -m src.artifacts`` —— 产物索引的只读查询入口。

五条子命令：

* ``execution <execution_id>``：这次执行产出了哪些文件；
* ``file <path>``：这个文件是哪几次执行写的；
* ``goal <goal_id>``：这个目标的所有产物；
* ``usage``：当前工作区占用（按目录 + 最大的 N 个）；
* ``cleanup``：清理建议（**只建议，绝不删除**）。

全局参数写在子命令之前：``python -m src.artifacts --db ... usage``。
默认值与执行链保持一致（``default_index_path()`` / ``workspace_root()`` /
``LIUHAO_WORKSPACE_ROOT`` 下的 journal），因此日常使用不需要显式传。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import unicodedata
from collections.abc import Sequence
from typing import Any, Dict, List, Optional

from src.ai.workspace import resolve_in_workspace, workspace_root

from .index import ArtifactIndex, default_index_path
from .report import (
    FileState,
    cleanup_suggestions,
    states_for_records,
    status_label,
    usage,
)


# --------------------------------------------------------------------------- #
# 排版
# --------------------------------------------------------------------------- #
def _display_width(text: str) -> int:
    """按终端显示宽度计字符数（中文等宽字符记 2）。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _display_width(text), 0)


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = []
    for column, header in enumerate(headers):
        width = _display_width(str(header))
        for row in rows:
            width = max(width, _display_width(str(row[column])))
        widths.append(width)
    lines = [
        "  ".join(_pad(str(header), widths[i]) for i, header in enumerate(headers)),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        lines.append(
            "  ".join(_pad(str(cell), widths[i]) for i, cell in enumerate(row))
        )
    return "\n".join(lines)


def _fmt_bytes(value: Optional[int]) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _emit_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.artifacts",
        description="产物索引只读查询：查执行、查文件、查目标、看占用、给清理建议。",
        epilog="全局参数（--db / --workspace / --json）请写在子命令之前。",
    )
    parser.add_argument(
        "--db", default=None, help="索引库路径（默认 default_index_path()）"
    )
    parser.add_argument(
        "--workspace", default=None, help="工作区根（默认 workspace_root()）"
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="输出 JSON（便于脚本消费）"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_execution = subparsers.add_parser("execution", help="按执行查产物")
    p_execution.add_argument("execution_id", help="执行 ID（即 goal id）")
    p_execution.add_argument(
        "--all-versions",
        action="store_true",
        help="列出同一路径的历史版本（默认只看每条路径最新一次写入）",
    )

    p_file = subparsers.add_parser("file", help="按文件查执行")
    p_file.add_argument("path", help="工作区内相对路径或工作区内绝对路径")
    p_file.add_argument(
        "--history", action="store_true", help="额外列出该文件的全部写入记录"
    )

    p_goal = subparsers.add_parser("goal", help="按目标查全部产物")
    p_goal.add_argument("goal_id", help="目标 ID")
    p_goal.add_argument(
        "--all-versions",
        action="store_true",
        help="列出同一路径的历史版本（默认只看每条路径最新一次写入）",
    )

    p_usage = subparsers.add_parser("usage", help="工作区占用统计")
    p_usage.add_argument(
        "--top", type=int, default=10, help="列出最大的 N 个产物（默认 10）"
    )

    p_cleanup = subparsers.add_parser(
        "cleanup", help="清理建议（只建议，不删除任何文件）"
    )
    p_cleanup.add_argument(
        "--journal", default=None, help="执行 journal 路径（默认自动探测）"
    )
    p_cleanup.add_argument(
        "--min-versions",
        type=int,
        default=3,
        help="同一路径达到多少次写入才提示版本堆积（默认 3）",
    )
    return parser


# --------------------------------------------------------------------------- #
# 默认路径
# --------------------------------------------------------------------------- #
def _default_journal_path() -> str:
    """与 worker / 网关共用同一个解析入口（含旧位置迁移）。"""
    from src.tasks.journal import execution_journal_path

    return execution_journal_path()


def _resolve_workspace(args: argparse.Namespace) -> str:
    """返回真实存在的绝对工作区根；不存在就抛 ``ValueError`` 交给上层提示。"""
    raw = args.workspace or workspace_root()
    real = os.path.realpath(os.path.abspath(raw))
    if not os.path.isdir(real):
        raise ValueError(
            f"工作区不存在：{real}（用 --workspace 指定一个已存在的目录，"
            "或设置 LIUHAO_WORKSPACE_ROOT）"
        )
    return real


# --------------------------------------------------------------------------- #
# 各子命令
# --------------------------------------------------------------------------- #
def _state_row(state: FileState) -> List[str]:
    return [
        state.path,
        str(state.record.version),
        _fmt_bytes(state.size),
        state.record.recorded_at,
        status_label(state.status),
        state.record.task_name or "-",
    ]


def _history_row(state: FileState) -> List[str]:
    return [
        state.path,
        str(state.record.version),
        _fmt_bytes(state.size),
        state.record.recorded_at,
        status_label(state.status),
        state.record.execution_id or "-",
        state.record.task_name or "-",
    ]


def _cmd_execution(index: ArtifactIndex, args: argparse.Namespace) -> int:
    records = index.artifacts_for_execution(
        args.execution_id, latest_only=not args.all_versions
    )
    states = states_for_records(index, records)

    if args.as_json:
        _emit_json(
            {
                "command": "execution",
                "execution_id": args.execution_id,
                "all_versions": bool(args.all_versions),
                "artifacts": [state.as_dict() for state in states],
            }
        )
        return 0

    if not states:
        print(f"执行 {args.execution_id} 没有产物记录。")
        return 0
    print(f"执行 {args.execution_id}：{len(states)} 个产物")
    print(
        _render_table(
            ["路径", "版本", "大小", "写入时间", "状态", "任务"],
            [_state_row(state) for state in states],
        )
    )
    return 0


def _cmd_goal(index: ArtifactIndex, args: argparse.Namespace) -> int:
    records = index.artifacts_for_goal(
        args.goal_id, latest_only=not args.all_versions
    )
    states = states_for_records(index, records)

    if args.as_json:
        _emit_json(
            {
                "command": "goal",
                "goal_id": args.goal_id,
                "all_versions": bool(args.all_versions),
                "artifacts": [state.as_dict() for state in states],
            }
        )
        return 0

    if not states:
        print(f"目标 {args.goal_id} 没有产物记录。")
        return 0
    print(f"目标 {args.goal_id}：{len(states)} 个产物")
    print(
        _render_table(
            ["路径", "版本", "大小", "写入时间", "状态", "任务"],
            [_state_row(state) for state in states],
        )
    )
    return 0


def _cmd_file(index: ArtifactIndex, args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args)
    history = index.history_for_file(args.path, workspace=ws)
    executions = index.executions_for_file(args.path, workspace=ws)
    states = states_for_records(index, history)

    abs_path = resolve_in_workspace(args.path, ws)
    rel = os.path.relpath(abs_path, ws).replace(os.sep, "/")
    exists = os.path.exists(abs_path)
    latest_state = states[0] if states else None

    if args.as_json:
        _emit_json(
            {
                "command": "file",
                "path": rel,
                "abs_path": abs_path,
                "exists": exists,
                "tracked": bool(states),
                "status": latest_state.status if latest_state else "untracked",
                "executions": executions,
                "history": [state.as_dict() for state in states],
            }
        )
        return 0

    if not states:
        print(
            f"文件 {rel}：索引中没有写入记录；磁盘上"
            f"{'存在' if exists else '不存在'}。"
        )
        return 0

    latest = latest_state
    print(
        f"文件 {rel}：当前{status_label(latest.status)}，"
        f"共 {len(states)} 条写入记录。"
    )
    print(
        _render_table(
            ["路径", "版本", "大小", "写入时间", "状态", "执行", "任务"],
            [_history_row(state) for state in states[:1]] if not args.history else
            [_history_row(state) for state in states],
        )
    )

    if executions:
        print()
        print("写过这个文件的执行：")
        print(
            _render_table(
                ["执行", "目标", "首次写入", "最近写入", "最高版本", "任务"],
                [
                    [
                        item["execution_id"] or "-",
                        item["goal_id"] or "-",
                        item["first_seen"] or "-",
                        item["last_seen"] or "-",
                        str(item["max_version"]),
                        "、".join(item["task_names"]) or "-",
                    ]
                    for item in executions
                ],
            )
        )
    else:
        print()
        print("没有可归因的执行（这些记录来自目标历史对账，没有 execution_id）。")
    return 0


def _cmd_usage(index: ArtifactIndex, args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args)
    summary = usage(index, ws, top=args.top)

    if args.as_json:
        payload = {"command": "usage", "top": args.top}
        payload.update(summary.as_dict())
        _emit_json(payload)
        return 0

    print(f"工作区：{summary.workspace}")
    print(
        f"磁盘占用：{summary.total_files} 个文件，{_fmt_bytes(summary.total_bytes)}"
    )
    print(
        f"索引观测：{summary.index_records} 条；其中 "
        f"{summary.missing_records} 个路径已不在盘上"
    )

    if not summary.directories:
        print("索引里还没有落在盘上的产物。")
        return 0

    print()
    print("按目录：")
    print(
        _render_table(
            ["目录", "文件数", "大小"],
            [
                [entry.directory, str(entry.files), _fmt_bytes(entry.bytes)]
                for entry in summary.directories
            ],
        )
    )

    if summary.largest:
        print()
        print(f"最大的 {len(summary.largest)} 个产物：")
        print(
            _render_table(
                ["路径", "大小", "版本", "写入时间", "状态"],
                [
                    [
                        state.path,
                        _fmt_bytes(state.size),
                        str(state.record.version),
                        state.record.recorded_at,
                        status_label(state.status),
                    ]
                    for state in summary.largest
                ],
            )
        )
    return 0


def _cmd_cleanup(index: ArtifactIndex, args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args)
    journal_path = args.journal or _default_journal_path()
    journal = None
    # 只在 journal 已存在时打开：只读查询不应顺手创建一个空 journal。
    if os.path.exists(journal_path):
        from src.kernels.execution._journal import ExecutionJournal

        journal = ExecutionJournal(journal_path)

    try:
        suggestions = cleanup_suggestions(
            index, ws, journal=journal, min_versions=args.min_versions
        )
    finally:
        if journal is not None:
            journal.close()

    if args.as_json:
        _emit_json(
            {
                "command": "cleanup",
                "workspace": ws,
                "journal": journal_path if journal is not None else None,
                "min_versions": args.min_versions,
                "suggestions": [item.as_dict() for item in suggestions],
                "note": "仅建议，CLI 未删除任何文件；删除前请人工确认。",
            }
        )
        return 0

    if not suggestions:
        if journal is None:
            print("（未找到执行 journal，因此跳过「失败执行留下的产物」判定）")
        print("没有发现可清理项。")
        return 0

    print(f"清理建议（工作区：{ws}）")
    if journal is None:
        print("（未找到执行 journal，因此跳过「失败执行留下的产物」判定）")
    print()
    for item in suggestions:
        print(f"[{item.kind}] {item.title}")
        print(f"  可回收：{_fmt_bytes(item.reclaimable_bytes)}")
        for path in item.paths:
            print(f"    - {path}")
        print(f"  建议：{item.action}")
        print()
    print("注意：以上仅为建议，CLI 未删除任何文件；删除前请人工确认。")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    db_path = args.db or default_index_path()
    if not os.path.exists(db_path):
        if args.as_json:
            _emit_json(
                {
                    "command": args.command,
                    "db": db_path,
                    "exists": False,
                    "note": "索引库不存在；启动贾维斯并执行一次目标后会自动生成。",
                }
            )
        else:
            print(f"索引库不存在：{db_path}")
            print(
                "（启动贾维斯并执行一次目标后会自动生成；也可用 --db 指定其他索引库）"
            )
        return 0

    try:
        index = ArtifactIndex(db_path)
    except sqlite3.DatabaseError as exc:
        print(f"索引库无法打开：{db_path}（{exc}）", file=sys.stderr)
        return 1

    try:
        if args.command == "execution":
            return _cmd_execution(index, args)
        if args.command == "file":
            return _cmd_file(index, args)
        if args.command == "goal":
            return _cmd_goal(index, args)
        if args.command == "usage":
            return _cmd_usage(index, args)
        if args.command == "cleanup":
            return _cmd_cleanup(index, args)
    except (ValueError, sqlite3.DatabaseError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        index.close()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
