"""账号用量账本：按 ``key_owner`` 记账单，按实际 ``actor`` 记用量。

设计约束（``docs/account-hierarchy.md`` §7）：

* 一次模型调用写**一行**，同时带 ``actor``（真实调用者）与 ``key_owner``
  （实际消耗额度的账号）；两个维度都从同一行聚合，不产生两套口径。
* 根账号看整棵子树（含调用者身份）；
* 普通账号只看到"自己的 Key 被消耗了多少"，**不返回调用者身份**——即使
  这次调用是主账号借用的，也只体现在额度消耗上。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_ENV_DB = "LIUHAO_ACCOUNT_USAGE_DB"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_usage_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    key_owner     TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    estimated     INTEGER NOT NULL DEFAULT 1,
    session_id    TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT ''
)
"""
_lock = threading.Lock()
_initialised: Dict[str, bool] = {}


def db_path() -> str:
    explicit = (os.environ.get(_ENV_DB) or "").strip()
    if explicit:
        return explicit
    root = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if root:
        return os.path.join(root, "account_usage.sqlite3")
    return os.path.join("data", "account_usage.sqlite3")


def _connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    if not _initialised.get(path):
        connection.execute(_SCHEMA)
        connection.commit()
        _initialised[path] = True
    return connection


def record_usage(
    *,
    actor: str,
    key_owner: Optional[str],
    model: str,
    tokens_in: int,
    tokens_out: int,
    session_id: str = "",
    correlation_id: str = "",
    estimated: bool = True,
) -> bool:
    """写一条用量事件。失败只记日志，**不阻断**对话主链路。"""
    actor_id = str(actor or "").strip()
    owner_id = str(key_owner or actor_id).strip()
    if not actor_id:
        return False
    try:
        from src._time import utc_now

        with _lock, _connect() as connection:
            connection.execute(
                "INSERT INTO account_usage_events "
                "(created_at, actor, key_owner, model, tokens_in, tokens_out, "
                " estimated, session_id, correlation_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    utc_now().isoformat(),
                    actor_id,
                    owner_id,
                    str(model or ""),
                    max(0, int(tokens_in)),
                    max(0, int(tokens_out)),
                    1 if estimated else 0,
                    str(session_id or ""),
                    str(correlation_id or ""),
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record account usage: %s", exc)
        return False


def _aggregate(rows: List[sqlite3.Row], field: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row[field] or "")
        bucket = buckets.setdefault(
            key,
            {
                field: key,
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "total_tokens": 0,
            },
        )
        bucket["calls"] += 1
        bucket["tokens_in"] += int(row["tokens_in"] or 0)
        bucket["tokens_out"] += int(row["tokens_out"] or 0)
        bucket["total_tokens"] = bucket["tokens_in"] + bucket["tokens_out"]
    return sorted(buckets.values(), key=lambda item: item[field])


def usage_summary(
    principal: str,
    *,
    include_callers: bool = False,
    owners: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """返回某账号可见的用量。

    ``include_callers=True`` 仅用于根账号：按 actor 与 key_owner 两个维度都
    返回；成员只看到**由本人发起的调用**，且响应里没有 ``by_actor``。

    成员视角刻意不按 ``key_owner`` 全量计数：主账号借用成员 Key 时，计费与
    额度仍记在 Key 主人名下（根视角能看到真实总量与代用记录），但成员侧
    的 ``calls`` / token 只统计 ``actor = 本人`` 的事件，避免"我的 Key 被谁
    用过"从计数里被察觉。

    ``owners`` 把账单限定在给定账号集合内（根=目标子树，成员=本人）；不传
    表示不做归属过滤（仅供进程内诊断，不暴露给 HTTP）。
    """
    pid = str(principal or "").strip()
    scope_owners = None
    if owners is not None:
        scope_owners = tuple(dict.fromkeys(str(item) for item in owners if item))
    with _lock, _connect() as connection:
        if scope_owners is None:
            sql = "SELECT actor, key_owner, tokens_in, tokens_out FROM account_usage_events"
            params: tuple = ()
        elif not scope_owners:
            sql = "SELECT actor, key_owner, tokens_in, tokens_out FROM account_usage_events WHERE 0"
            params = ()
        else:
            placeholders = ",".join(["?"] * len(scope_owners))
            sql = (
                "SELECT actor, key_owner, tokens_in, tokens_out "
                f"FROM account_usage_events WHERE key_owner IN ({placeholders})"
            )
            params = scope_owners
        rows = connection.execute(sql, params).fetchall()

    if not include_callers:
        # 成员侧只统计本人发起的调用；借用记录仍留在账本里供根账号核对。
        rows = [row for row in rows if str(row["actor"] or "") == pid]

    summary: Dict[str, Any] = {
        "principal": pid,
        "scope": "subtree" if include_callers else "self",
        "callers_visible": bool(include_callers),
        "calls": len(rows),
        "tokens_in": sum(int(row["tokens_in"] or 0) for row in rows),
        "tokens_out": sum(int(row["tokens_out"] or 0) for row in rows),
    }
    summary["total_tokens"] = summary["tokens_in"] + summary["tokens_out"]
    summary["by_key_owner"] = _aggregate(rows, "key_owner")
    if include_callers:
        # 调用者身份只对根账号可见；成员响应里连这个键都不出现。
        summary["by_actor"] = _aggregate(rows, "actor")
    return summary
