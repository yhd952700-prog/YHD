"""驾驶舱遥测端点 —— console CEO Command Center 的真实数据源。

只暴露**真实**运行数据，不编造任何业务指标：

- ``GET /v1/dashboard/summary``   系统摘要（状态 / 运行时长 / provider / 会话 / 审计统计）
- ``GET /v1/dashboard/activity``  最近动态（真实审计事件）
- ``GET /v1/dashboard/analytics`` 图表数据（真实审计时序 + 真实事件分布）

设计要点（NO-FAKE）：

- 审计事件里 99% 是 kernel 内部 ``state_change``（3w+ 条），对驾驶舱是噪声，
  过滤后才是有业务含义的动态；事件确实为空时如实返回空列表，不伪造条目。
- 任何数据源异常时返回 ``*_error`` 字段并降级为空值，不用假数据填充。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from .policy import require_bearer_payload

router = APIRouter(prefix="/v1", tags=["dashboard"])

logger = logging.getLogger(__name__)

# 进程启动时刻（运行时长基准）
_START_TIME = time.time()

# kernel 内部状态变更事件，量极大且无业务语义，驾驶舱动态里过滤掉
_NOISE_EVENT_TYPES = {"state_change"}

# 动态取样窗口：审计噪声占比 >99%，窗口必须够大才能取到有意义的事件
_ACTIVITY_WINDOW = 5000


def _uptime_seconds() -> float:
    return round(time.time() - _START_TIME, 1)


def _provider_info() -> Dict[str, Any]:
    """当前生效的 provider（必须与 ``/v1/health`` 使用同一个权威判据）。

    这里**不能**直接读 ``AI_PROVIDER_TYPE`` 环境变量：模型配置中心里的默认
    模型优先于环境变量，真实 Ollama/OpenAI 部署会因此被错报成 ``mock``。
    ``ai_provider_status()`` 是唯一裁决处（环境变量与配置库都已合并进去）。
    """
    from ..ai.providers import ai_provider_status, get_provider

    try:
        info: Dict[str, Any] = dict(ai_provider_status())
    except Exception as exc:  # provider 不可用时不编造，记录真实原因
        logger.warning("dashboard provider status failed: %s", exc, exc_info=True)
        info = {
            "type": "unknown",
            "mode": "unknown",
            "simulated": False,
            "error": "模型服务状态暂时读不到，请稍后重试。",
        }
    info.setdefault("type", "unknown")
    info.setdefault("mode", "unknown")
    info["real"] = info.get("mode") == "real"
    info.setdefault("model", None)
    info.setdefault("name", None)
    try:
        provider = get_provider()
        info["model"] = getattr(provider, "model", None)
        info["name"] = getattr(provider, "name", None)
    except Exception as exc:  # provider 不可用时不编造，记录真实原因
        logger.warning("dashboard provider instance failed: %s", exc, exc_info=True)
        info.setdefault("error", "模型服务状态暂时读不到，请稍后重试。")
    return info


def _caller_scope(payload) -> tuple[Any, Optional[List[str]], str]:
    """请求者可读的主体集合（与 audit.py 的账号树口径一致）。

    * 成员：只看自己（``[自己]``）；
    * 根：整棵子树（``list(subtree)``），并把归属标在响应里；
    * 无账号节点的老部署：沿用既有口径 —— L0 不设限（``None``），其余只看自己。

    ``payload`` 不是经核验令牌（进程内直接调用 / 单元测试）时同样返回不设限：
    这条分支在 HTTP 上不可达，路由永远先经过 ``require_bearer_payload``。
    """
    if payload is None or not hasattr(payload, "sub"):
        return None, None, "all"
    from .audit import _account_scope, _scope_filter

    context, sovereign = _account_scope(payload)
    principals, mode = _scope_filter(context, sovereign, None)
    return context, principals, mode


def _read_scope(context, principals, mode) -> Dict[str, Any]:
    """读取范围必须显式标记：根的整树视图不能与"只看自己"长得一样。"""
    return {
        "mode": mode,
        "principals": principals if principals is not None else "all",
        "root_view": bool(
            context is not None and context.is_root and mode == "subtree"
        ),
    }


def recent_activity(
    limit: int = 8, principals: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """返回最近的有业务含义的审计事件（真实数据，噪声已过滤）。

    取样窗口必须足够大：审计里 ``state_change`` 噪声占 99%+（3w 条量级），
    窗口太小时最新一批可能全是噪声，过滤后得到空列表（曾导致驾驶舱动态长期空白）。

    ``principals`` 非空时只取这些主体的事件（成员=自己，根=子树），过滤在 SQL
    层完成，避免"窗口里恰好只有别人的事件"把成员的动态挤成空白。
    """
    # 先取一个大窗口，过滤噪声后再截断
    window = max(limit * 50, _ACTIVITY_WINDOW)
    try:
        if principals is None:
            from ..kernels.audit import audit_query

            events = audit_query(reverse=True, limit=window)
        else:
            from .audit import _read_events

            events = _read_events(
                principals=list(principals), limit=window, reverse=True
            )
    except Exception as exc:
        logger.warning("recent_activity query failed: %s", exc, exc_info=True)
        return []

    activity: List[Dict[str, Any]] = []
    for e in events:
        if e.get("event_type") in _NOISE_EVENT_TYPES:
            continue
        activity.append(
            {
                "type": e.get("event_type"),
                "outcome": e.get("outcome"),
                "principal": e.get("principal_id"),
                "timestamp": e.get("timestamp"),
                "correlation_id": e.get("correlation_id"),
            }
        )
        if len(activity) >= limit:
            break
    return activity


def _session_details(
    session_ids: List[str], owners: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """会话明细：每个会话的真实对话轮次（来自持久化的 ConversationStore）。

    键必须是 ``liuhao-{principal}-{session}``（新）或 ``liuhao-{session}``（0.1.0
    旧键）。旧实现拼 ``liuhao-{sid}`` 却把「主体」漏掉，真实会话一行都命中
    不到，驾驶舱的 ``turns`` 因此恒为 0。这里按 ``chat`` 的同一套候选键取最大轮次。
    """
    try:
        from ..ai.conversation_store import get_conversation_store

        store = get_conversation_store()
    except Exception:
        return []

    try:
        from .chat import _history_principal_candidates
    except Exception:  # pragma: no cover - chat 模块异常时退回单键口径
        _history_principal_candidates = lambda principal, sid: [f"liuhao-{sid}"]  # noqa: E731

    owners = owners or {}
    details: List[Dict[str, Any]] = []
    for sid in session_ids:
        owner = owners.get(sid)
        turns = 0
        try:
            for key in _history_principal_candidates(owner, sid):
                turns = max(turns, int(store.get_last_turn(key) or 0))
        except Exception:
            turns = 0
        details.append({"id": sid, "turns": int(turns or 0)})
    return details


def _sessions_payload(context, principals) -> Dict[str, Any]:
    """会话列表（账号作用域内）。

    * 不限（老部署 L0 / 直接调用）：沿用进程内全局视图；
    * 有作用域：按子树逐个主体取会话，并把 ``owner`` 标在明细上 —— 根的整树
      视图必须能看出"这条会话是谁的"，成员的视图则只有自己。
    """
    from .chat import list_sessions

    if principals is None:
        ids = list_sessions()
        return {"count": len(ids), "ids": ids, "detail": _session_details(ids)}

    owners: Dict[str, str] = {}
    for principal in principals:
        # known_principals 必须传全子树：否则根会把"lh1-s-member"这类新键
        # 误当成旧键（旧键无主体段），同一条会话在列表里出现第二次。
        for sid in list_sessions(principal=principal, known_principals=principals):
            owners.setdefault(sid, principal)
    ids = sorted(owners)
    detail = _session_details(ids, owners)
    for item in detail:
        owner = owners.get(item["id"])
        if owner is not None:
            item["owner"] = owner
            item["is_self"] = bool(
                context is not None and owner == context.principal
            )
    return {"count": len(ids), "ids": ids, "detail": detail}


def _scoped_audit_stats(principals: Optional[List[str]]) -> Dict[str, Any]:
    """审计统计（账号作用域内），并显式给出每个数字的口径。

    此前三处数字互相矛盾：驾驶舱 ``total_events=24``（按主体子树过滤）、
    ``/v1/ready`` 审计检查 ``total_events=372``（全库）、事件页只显示
    业务事件（过滤 ``state_change`` 后另一套数字）。三处都是真实的，但
    同一个字段名装不同的东西，用户看到的就是"两套话"。

    现在统一为：

    * ``total_events``   全库存储事件（与 ``/v1/ready`` 的 audit_store 同口径）；
    * ``noise_events``   其中内核内部 ``state_change`` 噪声；
    * ``business_events`` 全库业务事件 = total - noise（事件页展示的就是这类）；
    * ``visible_events`` / ``visible_business_events`` 当前账号子树能看到的部分；
    * ``scope`` / ``scope_principals`` / ``definition`` 供界面逐字展示口径。

    绝对 ``db_path`` 永不出现在 HTTP 响应里。
    """
    from ..kernels.audit import audit_stats, get_audit_store

    stats = dict(audit_stats())
    stats.pop("db_path", None)
    total = int(stats.get("total_events") or 0)
    global_breakdown = dict(stats.get("breakdown") or {})
    noise = sum(
        int(count)
        for key, count in global_breakdown.items()
        if key.split(":", 1)[0] == "state_change"
    )
    stats["total_events"] = total
    stats["noise_events"] = noise
    stats["business_events"] = max(total - noise, 0)
    stats["definition"] = {
        "total_events": "全库存储的审计事件（与 /v1/ready 的 audit_store.total_events 同源）",
        "noise_events": "内核内部 state_change 事件（不进事件页、不进驾驶舱动态）",
        "business_events": "total_events - noise_events，即事件页可展示的业务事件",
        "visible_events": "当前账号子树可读的审计事件",
    }
    if principals is None:
        stats["visible_events"] = total
        stats["visible_business_events"] = stats["business_events"]
        stats["scope"] = "all"
        return stats

    def _read(conn):
        if not principals:
            return 0, {}
        placeholders = ",".join(["?"] * len(principals))
        where = f" WHERE principal_id IN ({placeholders})"
        args = tuple(principals)
        breakdown = {
            f"{row[0]}:{row[1]}": int(row[2])
            for row in conn.execute(
                "SELECT event_type, outcome, COUNT(*) FROM audit_events"
                + where
                + " GROUP BY event_type, outcome",
                args,
            ).fetchall()
        }
        total = conn.execute(
            "SELECT COUNT(*) FROM audit_events" + where, args
        ).fetchone()[0]
        return int(total), breakdown

    visible, breakdown = get_audit_store()._read_only(_read)
    visible_noise = sum(
        int(count) for key, count in breakdown.items() if key.split(":", 1)[0] == "state_change"
    )
    stats["visible_events"] = visible
    stats["visible_business_events"] = max(visible - visible_noise, 0)
    stats["scope"] = "principal_subtree"
    stats["scope_principals"] = list(principals)
    stats["breakdown"] = breakdown
    return stats


@router.get("/dashboard/summary")
def dashboard_summary(payload=Depends(require_bearer_payload)) -> Dict[str, Any]:
    """系统摘要：状态、运行时长、provider、会话、审计统计（账号作用域内）。"""
    context, principals, mode = _caller_scope(payload)
    summary: Dict[str, Any] = {
        "status": "ready",
        "uptime_seconds": _uptime_seconds(),
        "provider": _provider_info(),
        "timestamp": time.time(),
        "read_scope": _read_scope(context, principals, mode),
    }

    # 会话（真实：进程内活跃会话表 + ConversationStore 的轮次）
    try:
        summary["sessions"] = _sessions_payload(context, principals)
    except Exception as exc:
        logger.warning("dashboard sessions failed: %s", exc, exc_info=True)
        summary["sessions"] = {
            "count": 0,
            "ids": [],
            "detail": [],
            "error": "活跃会话暂时读不到，请稍后重试。",
        }

    # 审计（真实：审计存储统计）— CRIT-1C：审计不可用必须如实暴露，不得伪装成 "0 条事件"
    try:
        from ..kernels.audit import audit_failure_count

        summary["audit"] = _scoped_audit_stats(principals)
    except Exception as exc:
        logger.warning("dashboard_summary audit_stats failed: %s", exc, exc_info=True)
        summary["audit"] = {
            "available": False,
            "error": "审计记录暂时读不到，请稍后重试。",
            "failures": audit_failure_count(),
        }

    return summary


@router.get("/dashboard/activity")
def dashboard_activity(
    limit: int = Query(8, ge=1, le=100),
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """最近动态：真实审计事件（已过滤 kernel state_change 噪声，账号作用域内）。"""
    context, principals, mode = _caller_scope(payload)
    items = recent_activity(limit=limit, principals=principals)
    if context is not None:
        for item in items:
            item["is_self"] = str(item.get("principal") or "") == context.principal
    return {
        "activity": items,
        "count": len(items),
        "limit": limit,
        "read_scope": _read_scope(context, principals, mode),
    }


# ---------------------------------------------------------------------------
# 分析面（时序 + 分布）—— 仅供驾驶舱图表使用
# ---------------------------------------------------------------------------
#
# 与 summary/activity 同一 NO-FAKE 约定：图表的每一点都来自审计存储的真实
# 事件。**不做**趋势外推、不做平滑填充 —— 某天没有事件就是 0，那是真实的 0，
# 不是缺失数据。审计不可用时返回 available:false + error，前端必须显示
# "数据不可用"而不是画一条假曲线。

#: 时序与分布都排除的噪声事件类型（kernel 内部 state_change 占比 >99%）。
_ANALYTICS_NOISE = frozenset({"state_change"})

#: 单次查询的事件上限来自内核（``audit_query`` 会把任何 limit 截到
#: ``_QUERY_EVENTS_MAX_ROWS`` = 10000）。这里曾经写死 200_000，于是：
#:   * ``truncated = len(events) >= 200_000`` 永远为 false；
#:   * 事件超过 1 万条时图表静默少计，且没有任何提示。
#: 现在上限直接引用内核常量，并用 ``audit_count`` 取真实条数来判定截断。


def _day_key(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def event_timeseries(
    days: int = 14, principals: Optional[List[str]] = None
) -> Dict[str, Any]:
    """按天统计真实审计事件（total / allowed / denied，账号作用域内）。"""
    from ..kernels.audit import _QUERY_EVENTS_MAX_ROWS, audit_count, audit_query

    days = max(1, min(90, int(days)))
    # 以本地"今天"为最后一天，向前取 days 个**日历日**。
    #
    # 不能用 ``now - (days-1)*86400`` 再加 86400 秒推日期：一天不总是 86400 秒。
    # 在实行夏令时的时区（例如 US Pacific，2026-11-01 回拨一小时），跨过回拨点
    # 后 "+86400s" 会落在前一天的 23:00，于是轴上少一天、且末点变成"昨天"——
    # CI 跑在 UTC 永远看不到，用户机器在本地时区却每天都会看到。按日历日构造
    # 才是唯一定义正确的做法。
    today = date.today()
    first = today - timedelta(days=days - 1)
    axis = [first + timedelta(days=offset) for offset in range(days)]
    start = time.mktime(datetime.combine(first, datetime.min.time()).timetuple())

    try:
        if principals is None:
            events = audit_query(start_time=start, limit=_QUERY_EVENTS_MAX_ROWS)
        else:
            from .audit import _read_events

            events = _read_events(
                principals=list(principals),
                since=start,
                limit=_QUERY_EVENTS_MAX_ROWS,
            )
    except Exception as exc:
        logger.warning("event_timeseries query failed: %s", exc, exc_info=True)
        return {
            "available": False,
            "error": "这段时间的审计趋势暂时读不到，请稍后重试。",
            "days": [],
        }

    # 预置全部日期：没有事件的日子显示 0 而不是从轴上消失（那会歪曲趋势）。
    buckets: Dict[str, Dict[str, Any]] = {}
    for day in axis:
        key = day.isoformat()
        buckets[key] = {
            "date": key,
            "total": 0,
            "allowed": 0,
            "denied": 0,
            "noise": 0,
        }

    for event in events:
        key = _day_key(float(event.get("timestamp") or 0))
        bucket = buckets.get(key)
        if bucket is None:
            continue
        if event.get("event_type") in _ANALYTICS_NOISE:
            bucket["noise"] += 1
            continue
        bucket["total"] += 1
        outcome = str(event.get("outcome") or "")
        if outcome in ("allow", "allowed"):
            bucket["allowed"] += 1
        elif outcome in ("deny", "denied"):
            bucket["denied"] += 1

    sampled = len(events)
    # 真实条数用 COUNT(*) 取，而不是拿"返回条数"当总数：查询被内核截断在
    # 1 万条，超出的部分必须能被 ``truncated`` 如实标出，否则图表会静默偏低。
    try:
        if principals is None:
            actual = audit_count(start_time=start)
        else:
            actual = sum(
                audit_count(principal_id=principal, start_time=start)
                for principal in principals
            )
    except Exception as exc:
        logger.warning("event_timeseries count failed: %s", exc, exc_info=True)
        actual = sampled

    return {
        "available": True,
        "days": [buckets[key] for key in sorted(buckets)],
        "sampled_events": sampled,
        "actual_events": actual,
        "truncated": sampled < actual,
    }


def event_breakdown(principals: Optional[List[str]] = None) -> Dict[str, Any]:
    """真实事件分布（event_type × outcome），供环形图使用（账号作用域内）。"""
    try:
        if principals is None:
            from ..kernels.audit import audit_stats

            stats = dict(audit_stats())
            stats.pop("db_path", None)
        else:
            stats = _scoped_audit_stats(principals)
    except Exception as exc:
        logger.warning("event_breakdown failed: %s", exc, exc_info=True)
        return {
            "available": False,
            "error": "事件分布暂时读不到，请稍后重试。",
            "items": [],
        }

    items: List[Dict[str, Any]] = []
    excluded = 0
    for key, count in (stats.get("breakdown") or {}).items():
        event_type, _, outcome = str(key).partition(":")
        if event_type in _ANALYTICS_NOISE:
            excluded += int(count or 0)
            continue
        items.append(
            {"type": event_type, "outcome": outcome, "count": int(count or 0)}
        )
    items.sort(key=lambda item: item["count"], reverse=True)

    return {
        "available": True,
        "items": items,
        # 如实说明有多少噪声被排除，而不是让总数对不上却不说原因。
        "excluded_noise_events": excluded,
        "total_events": int(stats.get("total_events") or 0),
    }


@router.get("/dashboard/analytics")
def dashboard_analytics(
    days: int = Query(14, ge=1, le=90),
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """驾驶舱图表数据源：真实审计时序 + 真实事件分布（账号作用域内）。"""
    context, principals, mode = _caller_scope(payload)
    return {
        "generated_at": time.time(),
        "read_scope": _read_scope(context, principals, mode),
        "timeseries": event_timeseries(days=days, principals=principals),
        "breakdown": event_breakdown(principals=principals),
    }
