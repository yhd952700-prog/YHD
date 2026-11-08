"""换机恢复后，把 SQLite JSON 字段里写死的源机绝对路径改到新位置。

工件索引有明确的 ``workspace`` / ``abs_path`` 列，由
:func:`src.artifacts.index.rewrite_workspace_paths` 结构化改写。执行日志
（``journal_events.payload``）和账号记忆迁移标记
（``account_memory_migrations.details``）则把路径放在 JSON 文本里，无法按列
改写，只能解析 JSON、逐字符串替换。

替换规则是**前缀替换**，且要求前缀后面紧跟路径分隔符（或整串相等），避免
把 ``D:\\LiuHaoData2`` 误当成 ``D:\\LiuHaoData``。只处理调用方点名的
``(表, 列)``，不碰正文/记忆内容。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Tuple

__all__ = ["rewrite_json_text", "rewrite_sqlite_json_column"]


def _same_prefix(value: str, old: str) -> bool:
    if len(value) < len(old):
        return False
    if value[: len(old)] == old:
        return True
    # JSON 里可能写的是正斜杠；Windows 路径大小写不敏感。
    left = value[: len(old)].replace("\\", "/").lower()
    right = old.replace("\\", "/").lower()
    return left == right


def _is_boundary(value: str, old: str) -> bool:
    if len(value) == len(old):
        return True
    return value[len(old)] in ("\\", "/")


def _rewrite_value(value: Any, old: str, new: str) -> Tuple[Any, int]:
    if isinstance(value, str):
        if _same_prefix(value, old) and _is_boundary(value, old):
            return new + value[len(old):], 1
        return value, 0
    if isinstance(value, list):
        changed = 0
        out = []
        for item in value:
            replaced, count = _rewrite_value(item, old, new)
            out.append(replaced)
            changed += count
        return out, changed
    if isinstance(value, dict):
        changed = 0
        out_dict = {}
        for key, item in value.items():
            replaced, count = _rewrite_value(item, old, new)
            out_dict[key] = replaced
            changed += count
        return out_dict, changed
    return value, 0


def rewrite_json_text(raw: str, old_root: str, new_root: str) -> Tuple[str, int]:
    """把 JSON 文本里落在 ``old_root`` 下的字符串改到 ``new_root``。"""
    old = str(old_root or "").rstrip("\\/")
    new = str(new_root or "").rstrip("\\/")
    if not old or not new or not raw:
        return raw, 0
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw, 0
    rewritten, count = _rewrite_value(parsed, old, new)
    if not count:
        return raw, 0
    return json.dumps(rewritten, ensure_ascii=False), count


def rewrite_sqlite_json_column(
    db_path: str,
    table: str,
    column: str,
    old_root: str,
    new_root: str,
) -> int:
    """改写指定表/列里的 JSON 文本；返回被替换的字符串个数。"""
    if not os.path.isfile(db_path):
        return 0
    old = str(old_root or "").rstrip("\\/")
    new = str(new_root or "").rstrip("\\/")
    if not old or not new:
        return 0
    connection = sqlite3.connect(db_path, timeout=10)
    changed = 0
    try:
        try:
            rows = connection.execute(
                f'SELECT rowid, "{column}" FROM "{table}"'
            ).fetchall()
        except sqlite3.DatabaseError:
            return 0
        for rowid, raw in rows:
            if not isinstance(raw, str):
                continue
            # JSON 文本里的反斜杠是转义的（``D:\\cache``）；快速过滤要把
            # 单反斜杠、双反斜杠、正斜杠三种写法都认出来。
            probes = (old, old.replace("\\", "\\\\"), old.replace("\\", "/"))
            if not any(probe in raw for probe in probes):
                continue
            rewritten, count = rewrite_json_text(raw, old, new)
            if not count:
                continue
            connection.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                (rewritten, rowid),
            )
            changed += count
        connection.commit()
        # 与工件索引同一取舍：更新落进主库，别留在 -wal 里被新机漏掉。
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    finally:
        connection.close()
    return changed
