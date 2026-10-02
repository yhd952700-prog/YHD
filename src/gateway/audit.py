"""审计可解释性面（**严格只读**）—— P8 Explainability / Audit UX。

为什么要有这个模块
------------------
审计内核（``src/kernels/audit``）本来就是真的：append → ``verify_integrity()``
→ 篡改会被检测出来 → 损坏走 quarantine（永不删除）。但**没有任何人类能碰到它**：
既没有 HTTP 端点也没有 CLI，所以"我能证明链没被改过"这句话在运行时无从验证。

这里补齐的正是那一层：把已经存在的机制**暴露**出来，不新增机制、不重新实现哈希。

硬约束：只读，永远只读
----------------------
这个模块里的每一个端点都只能 SELECT。任何"顺手修一下链"的能力都会摧毁
tamper-evidence —— 一个能自愈的审计链等于一个可以被悄悄改写的审计链
（改完自动重算哈希，验证永远返回 True）。所以：

* 没有 POST/PUT/PATCH/DELETE 的写入端点（唯一的 POST 是 ``/verify``，它只跑
  校验，不写任何东西）；
* 读失败（库损坏 / 不可读）时返回**诚实的错误**，绝不返回 ``{ok: true}``。

``/verify`` 返回什么
--------------------
``ok`` 来自内核真正的 ``AuditStore.verify_integrity()`` —— 不是这里自己算的。
``details.failures`` 来自 ``verification.verify_segment()``，只在 ``ok=false``
时才跑（链完好时不必再扫一遍），用来指出**具体哪一条 seq 坏了**。

为什么 ``ok=false`` 也可能 ``failures`` 为空：``verify_integrity()`` 还会检查
尾部锚点（``chain_state``）与 seq 唯一性（RCA-1 分叉特征），这两类失败是
"整链级"的，不是某一条事件的内容不匹配。此时 ``first_failure`` 为 None 而
``ok`` 仍为 false —— 这是事实，不是缺陷。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .policy import require_human_principal

router = APIRouter(prefix="/v1/audit", tags=["audit"])

logger = logging.getLogger(__name__)

# 单次返回上限：驾驶舱/审计员都需要翻页，不能把百万行的链拖进内存。
_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 100

# ``seq`` 定位与事件列表都读同一批列，保证 /events 与 /events/{seq} 返回同构。
_EVENT_COLUMNS = (
    "seq, event_id, event_type, principal_id, scope, timestamp, "
    "correlation_id, outcome, details, event_hash, prev_event_hash, "
    "hash_alg, link_hash"
)


# ---------------------------------------------------------------------------
# 读取原语 —— 全部走 AuditStore 自己的只读快照连接
# ---------------------------------------------------------------------------
#
# 用 ``store._read_only`` 而不是自己 sqlite3.connect：那是 store 唯一批准的
# 读路径（钉住一个一致性快照，绝不会读到未提交的 append，也不会阻塞写者）。
# 这里没有重新实现任何哈希/链逻辑，只是把行取出来。


def _store():
    """当前进程生效的审计 store（路径由 AUDIT_DB_PATH 决定）。"""
    from ..kernels.audit import get_audit_store

    return get_audit_store()


def _row_to_event(row) -> Dict[str, Any]:
    (
        seq, event_id, event_type, principal_id, scope, timestamp,
        correlation_id, outcome, details_json, event_hash, prev_event_hash,
        hash_alg, link_hash,
    ) = row
    try:
        details = json.loads(details_json) if details_json else {}
    except (ValueError, TypeError):
        # details 不是合法 JSON —— 如实暴露，不要静默吞成 {}
        details = {"_unparseable_details": True}
    return {
        "seq": seq,
        "event_id": event_id,
        "event_type": event_type,
        "principal_id": principal_id,
        "scope": scope,
        "timestamp": timestamp,
        "correlation_id": correlation_id,
        "outcome": outcome,
        "details": details,
        # 内核动作名（execution.execute 等）在 details 里，提到顶层方便人读/筛选
        "action": details.get("action") if isinstance(details, dict) else None,
        "event_hash": event_hash,
        "prev_event_hash": prev_event_hash,
        "hash_alg": hash_alg,
        "link_hash": link_hash,
    }


def _read_events(
    *,
    principal: Optional[str] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    correlation_id: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    event_type: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
    reverse: bool = False,
) -> List[Dict[str, Any]]:
    """只读查询。``action`` 用 json_extract 下推到 SQL，不是事后过滤。

    事后过滤会在 limit 截断之后才生效，于是"筛 action 只回来 3 条"可能是
    "窗口里恰好只有 3 条"也可能是"窗口外还有 300 条" —— 那是假答案。下推到
    SQL 才能让 limit 真的是"给我前 N 条匹配的"。
    """
    clauses = ["1=1"]
    params: List[Any] = []

    if principal:
        clauses.append("principal_id = ?")
        params.append(principal)
    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome)
    if correlation_id:
        clauses.append("correlation_id = ?")
        params.append(correlation_id)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until is not None:
        clauses.append("timestamp <= ?")
        params.append(until)
    if action:
        # details 是 canonical JSON 文本；SQLite JSON1 直接取键。
        # json_valid 守卫：一条 details 已损坏的行无法匹配任何 action，但也不该
        # 让整个查询报错 —— 损坏本身由 /verify 的内容哈希校验负责报出来。
        clauses.append("json_valid(details)")
        clauses.append("json_extract(details, '$.action') = ?")
        params.append(action)

    order = "ORDER BY seq DESC" if reverse else "ORDER BY seq ASC"
    params.append(limit)
    sql = (
        f"SELECT {_EVENT_COLUMNS} FROM audit_events "
        f"WHERE {' AND '.join(clauses)} {order} LIMIT ?"
    )

    def _run(conn):
        return [_row_to_event(r) for r in conn.execute(sql, params).fetchall()]

    return _store()._read_only(_run)


def _read_event_by_seq(seq: int) -> Optional[Dict[str, Any]]:
    sql = f"SELECT {_EVENT_COLUMNS} FROM audit_events WHERE seq = ? ORDER BY rowid ASC LIMIT 1"

    def _run(conn):
        row = conn.execute(sql, (seq,)).fetchone()
        return _row_to_event(row) if row else None

    return _store()._read_only(_run)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/events")
def list_events(
    principal: Optional[str] = Query(None, description="按主体过滤"),
    action: Optional[str] = Query(
        None, description="按内核动作名过滤（details.action，如 execution.execute）"),
    outcome: Optional[str] = Query(None, description="按结果过滤（success/denied/...）"),
    correlation_id: Optional[str] = Query(
        None, description="按关联 ID 过滤 —— 一次目标执行产生的全部事件"),
    since: Optional[float] = Query(None, description="起始 Unix 时间戳（秒，含）"),
    until: Optional[float] = Query(None, description="结束 Unix 时间戳（秒，含）"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="返回条数上限"),
    reverse: bool = Query(False, description="true = 最新的在前"),
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """列出审计事件（只读）。

    返回值带 ``filters`` 回声，因为"我看到的列表是哪个过滤条件下看到的"
    本身就是要展示给人的信息 —— 空结果到底是没有事件还是筛错了，必须可分。
    """
    try:
        events = _read_events(
            principal=principal,
            action=action,
            outcome=outcome,
            correlation_id=correlation_id,
            since=since,
            until=until,
            event_type=event_type,
            limit=limit,
            reverse=reverse,
        )
    except Exception as exc:  # noqa: BLE001 - 读失败必须如实说出来
        logger.warning("audit events query failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"audit store unreadable: {exc}",
        )
    return {
        "events": events,
        "count": len(events),
        "filters": {
            "principal": principal,
            "action": action,
            "outcome": outcome,
            "correlation_id": correlation_id,
            "since": since,
            "until": until,
            "event_type": event_type,
            "limit": limit,
            "reverse": reverse,
        },
        # 命中上限时如实说明：调用方必须能区分"只有这些"和"还有更多"。
        "truncated": len(events) >= limit,
    }


@router.get("/events/{seq}")
def get_event(
    seq: int,
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """按序号取单条事件（只读）。

    同一 seq 若存在多行（RCA-1 分叉特征），只返回 rowid 最小的一行并如实
    标注 ``duplicate_seq`` —— 隐藏第二行等于替攻击者保守秘密。
    """
    try:
        event = _read_event_by_seq(seq)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit event lookup failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"audit store unreadable: {exc}")

    if event is None:
        raise HTTPException(status_code=404, detail=f"no audit event at seq={seq}")

    duplicate = False
    try:
        def _count(conn):
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE seq = ?", (seq,)).fetchone()
            return int(row[0]) if row else 0
        duplicate = _store()._read_only(_count) > 1
    except Exception:  # noqa: BLE001 - 计数失败不影响已经取到的事件
        duplicate = None  # type: ignore[assignment]

    return {"event": event, "duplicate_seq": duplicate}


def _run_verification() -> Dict[str, Any]:
    """跑**真实**的链校验。这里不实现哈希，只调用内核。"""
    from ..kernels.audit import get_audit_store
    from ..kernels.audit import verification as _verification

    store = get_audit_store()
    started = time.time()

    # 权威结论：内核自己的 verify_integrity()（链连接 + seq 连续/唯一 +
    # 内容哈希重算 + 尾部锚点）。
    ok, entries_checked = store.verify_integrity()

    failures: List[str] = []
    pinpoint_ran = False
    if not ok:
        # 只在失败时才逐条定位：链完好时不要再扫一遍全链。
        try:
            def _scan(conn):
                tail = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM audit_events").fetchone()[0]
                if not tail:
                    return []
                result = _verification.verify_segment(
                    conn, 1, int(tail), None, require_link_hash=False)
                return list(result.failures)

            failures = store._read_only(_scan)
            pinpoint_ran = True
        except Exception as exc:  # noqa: BLE001
            # 定位失败不能把 ok 翻成 true；如实记下"定位没跑成"。
            logger.warning("audit failure pinpointing failed: %s", exc, exc_info=True)
            failures = [f"pinpoint scan failed: {exc}"]

    return {
        "ok": bool(ok),
        "entries_checked": entries_checked,
        "first_failure": failures[0] if failures else None,
        "details": {
            "failures": failures,
            "failure_count": len(failures),
            "pinpoint_ran": pinpoint_ran,
            "db_path": store._db_path,
            "duration_seconds": round(time.time() - started, 4),
            # 明确声明本端点没有做任何修复动作 —— 能自愈的链不是证据。
            "mutation_performed": False,
            "read_only": True,
        },
    }


@router.get("/verify")
def verify_chain(
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """校验哈希链完整性（只读，不修复）。

    ``ok=false`` 就是被改过/被截断/被分叉。本端点**从不**修复链：写回一个
    重算过的哈希正是 tamper-evidence 的反面。
    """
    try:
        return _run_verification()
    except Exception as exc:  # noqa: BLE001
        # 库损坏/不可读：诚实报错。绝不返回 ok=true。
        logger.error("audit chain verification failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "entries_checked": 0,
            "first_failure": None,
            "details": {
                "failures": [],
                "failure_count": 0,
                "pinpoint_ran": False,
                "error": f"verification could not run: {exc}",
                "mutation_performed": False,
                "read_only": True,
            },
        }


# 同一个只读校验同时挂 POST：少数客户端/代理会缓存 GET，而"链现在是否完好"
# 是每次都想知道的即时结论。它与 GET 走完全相同的只读实现，不写任何东西。
@router.post("/verify")
def verify_chain_post(
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """与 ``GET /v1/audit/verify`` 完全相同的只读校验。"""
    return verify_chain(_principal=_principal)


@router.get("/summary")
def summary(
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """审计概览（只读）：按 outcome / action 计数，供驾驶舱看。

    这里**故意不**附带 verify 结果 —— 那会为每个轮询请求扫一遍全链（生产库
    30 万行量级）。要结论就显式打 ``/v1/audit/verify``。
    """
    try:
        def _run(conn):
            total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            tail = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM audit_events").fetchone()[0]
            by_outcome = {
                r[0]: r[1] for r in conn.execute(
                    "SELECT outcome, COUNT(*) FROM audit_events GROUP BY outcome"
                ).fetchall()
            }
            by_action_raw = conn.execute(
                "SELECT json_extract(details, '$.action') AS a, COUNT(*) "
                "FROM audit_events GROUP BY a"
            ).fetchall()
            by_action = {(r[0] if r[0] is not None else "(none)"): r[1]
                         for r in by_action_raw}
            correlations = conn.execute(
                "SELECT COUNT(DISTINCT correlation_id) FROM audit_events"
            ).fetchone()[0]
            return {
                "total_events": int(total),
                "tail_seq": int(tail),
                "by_outcome": by_outcome,
                "by_action": by_action,
                "distinct_correlation_ids": int(correlations),
            }

        data = _store()._read_only(_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit summary failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"audit store unreadable: {exc}")

    data["generated_at"] = time.time()
    return data
