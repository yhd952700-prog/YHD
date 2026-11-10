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

from ..common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

_ENV_DB = "LIUHAO_ACCOUNT_USAGE_DB"
#: 页面/API 共用一句人话，避免"未定价 = 0 元"的误读。
BUDGET_GUARD_NOTE = (
    "未定价模型只统计用量、不参与预算拦截：成本未知，既不会被当成 0 元放行，"
    "也不会因为缺少价格而被拒绝。"
)
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
# 记账写入健康度：调用方（chat / readiness）必须能感知"用量行没写成"，
# 而不是只在日志里留下一条 warning 就让计费数据悄悄缺行。
_health_lock = threading.Lock()
_failures_total = 0
_consecutive_failures = 0
_last_error: Optional[str] = None
_last_error_at: Optional[float] = None


def _note_write_failure(exc: BaseException) -> None:
    import time

    global _failures_total, _consecutive_failures, _last_error, _last_error_at
    with _health_lock:
        _failures_total += 1
        _consecutive_failures += 1
        _last_error = f"{type(exc).__name__}: {exc}"
        _last_error_at = time.time()


def _note_write_success() -> None:
    global _consecutive_failures
    with _health_lock:
        _consecutive_failures = 0


def usage_health() -> Dict[str, Any]:
    """返回账本写入健康度（readiness / 诊断消费）。

    ``consecutive_failures`` 表示**当前仍在失败**；为 0 而
    ``failures_total`` 非 0 只说明历史上出现过失败、现已恢复。
    """
    with _health_lock:
        return {
            "path": db_path(),
            "failures_total": _failures_total,
            "consecutive_failures": _consecutive_failures,
            "last_error": _last_error,
            "last_error_at": _last_error_at,
        }


def db_path() -> str:
    """用量账本落盘路径：``LIUHAO_ACCOUNT_USAGE_DB`` > ``LIUHAO_WORKSPACE_ROOT``
    > ``LIUHAO_DATA_DIR`` > ``data/account_usage.sqlite3``。"""
    return resolve_store_path(
        "account_usage.sqlite3",
        os.path.join("data", "account_usage.sqlite3"),
        env_var=_ENV_DB,
        workspace=True,
    )


def _connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    if not _initialised.get(path):
        connection.execute(_SCHEMA)
        # 按 correlation_id 查重（同一轮对话只允许一行用量）；非唯一索引，
        # 兼容历史库里可能已存在的重复行，不在打开库时让迁移失败。
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_correlation "
            "ON account_usage_events(correlation_id)"
        )
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
    correlation = str(correlation_id or "").strip()
    if not actor_id:
        _note_write_failure(ValueError("usage row rejected: actor is empty"))
        logger.error("account usage row rejected: actor is empty")
        return False
    try:
        from src._time import utc_now

        with _lock, _connect() as connection:
            if correlation:
                # 幂等写入：同一 correlation_id 只允许一行。重复记账会直接
                # 多扣费，因此这里在**写入前**按 correlation 查重（进程内
                # ``_lock`` 保证检查与插入原子），而不是把重复行留给上层去猜。
                exists = connection.execute(
                    "SELECT 1 FROM account_usage_events "
                    "WHERE correlation_id = ? LIMIT 1",
                    (correlation,),
                ).fetchone()
                if exists is not None:
                    logger.warning(
                        "duplicate usage row skipped (correlation_id=%s)", correlation
                    )
                    return True
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
                    correlation,
                ),
            )
        _note_write_success()
        return True
    except Exception as exc:  # noqa: BLE001
        # 计费行缺失是数据完整性问题，不能降级成 warning 后被静默忽略。
        _note_write_failure(exc)
        logger.error("could not record account usage: %s", exc)
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


def _price_table(models: Iterable[str]) -> Dict[str, float]:
    """按模型注册表/价格配置给出每 1K token 价格；没有价格的不进表。

    与成本护栏使用同一个数据源（``ModelRegistry.estimated_cost_per_1k``），
    因此页面显示的"已计入/未计入"与拦截行为不会互相矛盾。
    """
    table: Dict[str, float] = {}
    wanted = [str(model or "") for model in models]
    wanted = [model for model in wanted if model]
    if not wanted:
        return table
    try:
        from src.models.registry import get_model_registry

        registry = get_model_registry()
    except Exception:  # noqa: BLE001 - 注册表不可用时按"全部未定价"如实展示
        logger.warning("model registry unavailable; usage costs will be unknown", exc_info=True)
        return table
    for model in dict.fromkeys(wanted):
        metadata = registry.get_model(model)
        if metadata is None:
            continue
        try:
            table[model] = float(metadata.estimated_cost_per_1k)
        except (TypeError, ValueError):
            continue
    return table


def _billing_report(
    models: Iterable[str], usages: Iterable["tuple[str, int, int]"]
) -> Dict[str, Any]:
    """复用 ``BillingEngine`` 的定价口径：未知模型 ``cost=None``，绝不记 0 元。

    ``models`` 是参与定价的模型集合；``usages`` 是 (model, tokens_in, tokens_out)
    三元组。返回 ``usage_report`` 结构（``models`` / ``total_cost`` /
    ``total_cost_complete`` / ``unknown_models`` / ``message``）。
    """
    from ..ai.economy import BillingEngine

    billing = BillingEngine(_price_table(models))
    for model, tokens_in, tokens_out in usages:
        billing.record_usage(model, tokens_in, tokens_out)
    return billing.usage_report()


def _cost_fields(report: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``BillingEngine`` 报表映射成用量响应里统一的成本字段。"""
    return {
        "cost": report["total_cost"],
        "cost_complete": report["total_cost_complete"],
        "unknown_models": report["unknown_models"],
        "cost_message": report["message"],
        "budget_note": BUDGET_GUARD_NOTE,
        "by_model": [
            {"model": model, **detail}
            for model, detail in report["models"].items()
        ],
    }


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
            sql = "SELECT actor, key_owner, model, tokens_in, tokens_out FROM account_usage_events"
            params: tuple = ()
        elif not scope_owners:
            sql = "SELECT actor, key_owner, model, tokens_in, tokens_out FROM account_usage_events WHERE 0"
            params = ()
        else:
            placeholders = ",".join(["?"] * len(scope_owners))
            sql = (
                "SELECT actor, key_owner, model, tokens_in, tokens_out "
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
    # 成本：与预算护栏同一口径；未定价模型 ``cost=None`` 且明确提示，绝不按 0 元。
    summary.update(
        _cost_fields(
            _billing_report(
                (str(row["model"] or "") for row in rows),
                (
                    (str(row["model"] or "unknown"), int(row["tokens_in"] or 0), int(row["tokens_out"] or 0))
                    for row in rows
                ),
            )
        )
    )
    return summary


def _normalise_time(value: Optional[str]) -> Optional[str]:
    """把 ``since`` / ``until`` 归一成与账本相同的 ISO 形态（无时区按 UTC）。

    账本写的是 ``utc_now().isoformat()``；直接字符串比较时，缺时区的输入会
    少掉 ``+00:00`` 后缀而把边界行漏掉，所以先解析再统一输出。
    """
    if not value:
        return None
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def usage_events(
    principal: str,
    *,
    include_callers: bool = False,
    owners: Optional[Iterable[str]] = None,
    actor: Optional[str] = None,
    key_owner: Optional[str] = None,
    correlation_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """按行返回用量明细（逐笔对账用），权限与 ``usage_summary`` 同一套规则。

    * 根账号：``owners`` 限定在目标子树，可按 ``actor`` / ``key_owner`` 过滤；
    * 成员：**强制** ``actor = 本人``（即使传了别人的 actor 也查不到），
      与用量页"只算自己"的口径一致；
    * 支持 ``correlation_id``、``since`` / ``until``、分页（``limit``/``offset``）。

    每笔都带成本（未定价模型 ``cost=None`` + ``pricing_status="unknown"``），
    因此"这笔钱是谁花的"可以直接在接口里回答，不用再去翻 SQLite。
    """
    pid = str(principal or "").strip()
    scope_owners = None
    if owners is not None:
        scope_owners = tuple(dict.fromkeys(str(item) for item in owners if item))

    try:
        limit_value = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit_value = 50
    try:
        offset_value = max(0, int(offset))
    except (TypeError, ValueError):
        offset_value = 0

    since_value = _normalise_time(since)
    until_value = _normalise_time(until)

    clauses: List[str] = []
    params: List[Any] = []
    if scope_owners is None:
        pass
    elif not scope_owners:
        clauses.append("0")
    else:
        placeholders = ",".join(["?"] * len(scope_owners))
        clauses.append(f"key_owner IN ({placeholders})")
        params.extend(scope_owners)
    if not include_callers:
        clauses.append("actor = ?")
        params.append(pid)
    else:
        if actor:
            clauses.append("actor = ?")
            params.append(str(actor))
        if key_owner:
            clauses.append("key_owner = ?")
            params.append(str(key_owner))
    if correlation_id:
        clauses.append("correlation_id = ?")
        params.append(str(correlation_id))
    if since_value:
        clauses.append("created_at >= ?")
        params.append(since_value)
    if until_value:
        clauses.append("created_at <= ?")
        params.append(until_value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with _lock, _connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM account_usage_events{where}", tuple(params)
            ).fetchone()[0]
        )
        aggregate_rows = connection.execute(
            "SELECT model, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out "
            f"FROM account_usage_events{where} GROUP BY model",
            tuple(params),
        ).fetchall()
        rows = connection.execute(
            "SELECT * FROM account_usage_events"
            f"{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit_value, offset_value),
        ).fetchall()

    report = _billing_report(
        (str(row["model"] or "") for row in aggregate_rows),
        (
            (
                str(row["model"] or "unknown"),
                int(row["tokens_in"] or 0),
                int(row["tokens_out"] or 0),
            )
            for row in aggregate_rows
        ),
    )

    # 逐笔成本：与上方的合计使用同一价格表，避免"合计有价、明细没价"。
    from ..ai.economy import BillingEngine

    page_billing = BillingEngine(
        _price_table(str(row["model"] or "") for row in rows)
    )
    events: List[Dict[str, Any]] = []
    for row in rows:
        model = str(row["model"] or "")
        tokens_in = int(row["tokens_in"] or 0)
        tokens_out = int(row["tokens_out"] or 0)
        cost = page_billing.estimate_cost(model or "unknown", tokens_in, tokens_out)
        events.append(
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"] or ""),
                "actor": str(row["actor"] or ""),
                "key_owner": str(row["key_owner"] or ""),
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "total_tokens": tokens_in + tokens_out,
                "estimated": bool(row["estimated"]),
                "session_id": str(row["session_id"] or ""),
                "correlation_id": str(row["correlation_id"] or ""),
                "cost": None if cost is None else round(float(cost), 6),
                "pricing_status": "unknown" if cost is None else "priced",
                "message": BillingEngine.UNKNOWN_COST_MESSAGE if cost is None else "",
            }
        )

    result: Dict[str, Any] = {
        "principal": pid,
        "scope": "subtree" if include_callers else "self",
        "callers_visible": bool(include_callers),
        "filters": {
            "actor": str(actor) if actor else None,
            "key_owner": str(key_owner) if key_owner else None,
            "correlation_id": str(correlation_id) if correlation_id else None,
            "since": since_value,
            "until": until_value,
        },
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "count": len(events),
        "has_more": offset_value + len(events) < total,
        "events": events,
    }
    result.update(_cost_fields(report))
    return result
