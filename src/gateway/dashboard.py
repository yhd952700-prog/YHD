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
from typing import Any, Dict, List

from fastapi import APIRouter, Query

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
    """当前生效的 provider（经 .env / os.environ 解析后的真实值）。"""
    from ..ai.providers import _provider_env, get_provider

    info: Dict[str, Any] = {
        "type": _provider_env("AI_PROVIDER_TYPE", "mock"),
        "model": None,
        "name": None,
    }
    try:
        provider = get_provider()
        info["model"] = getattr(provider, "model", None)
        info["name"] = getattr(provider, "name", None)
    except Exception as exc:  # provider 不可用时不编造，记录真实原因
        info["error"] = str(exc)
    return info


def recent_activity(limit: int = 8) -> List[Dict[str, Any]]:
    """返回最近的有业务含义的审计事件（真实数据，噪声已过滤）。

    取样窗口必须足够大：审计里 ``state_change`` 噪声占 99%+（3w 条量级），
    窗口太小时最新一批可能全是噪声，过滤后得到空列表（曾导致驾驶舱动态长期空白）。
    """
    from ..kernels.audit import audit_query

    # 先取一个大窗口，过滤噪声后再截断
    window = max(limit * 50, _ACTIVITY_WINDOW)
    try:
        events = audit_query(reverse=True, limit=window)
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


def _session_details(session_ids: List[str]) -> List[Dict[str, Any]]:
    """会话明细：每个会话的真实对话轮次（来自持久化的 ConversationStore）。"""
    try:
        from ..ai.conversation_store import get_conversation_store

        store = get_conversation_store()
    except Exception:
        return []

    details: List[Dict[str, Any]] = []
    for sid in session_ids:
        try:
            turns = store.get_last_turn(f"liuhao-{sid}")
        except Exception:
            turns = 0
        details.append({"id": sid, "turns": int(turns or 0)})
    return details


@router.get("/dashboard/summary")
def dashboard_summary() -> Dict[str, Any]:
    """系统摘要：状态、运行时长、provider、会话、审计统计。"""
    summary: Dict[str, Any] = {
        "status": "ready",
        "uptime_seconds": _uptime_seconds(),
        "provider": _provider_info(),
        "timestamp": time.time(),
    }

    # 会话（真实：进程内活跃会话表 + ConversationStore 的轮次）
    try:
        from .chat import list_sessions

        sessions = list_sessions()
        summary["sessions"] = {
            "count": len(sessions),
            "ids": sessions,
            "detail": _session_details(sessions),
        }
    except Exception as exc:
        summary["sessions"] = {"count": 0, "ids": [], "detail": [], "error": str(exc)}

    # 审计（真实：审计存储统计）
    try:
        from ..kernels.audit import audit_stats

        summary["audit"] = audit_stats()
    except Exception as exc:
        logger.warning("dashboard_summary audit_stats failed: %s", exc, exc_info=True)
        summary["audit"] = {"total_events": 0, "error": str(exc)}

    return summary


@router.get("/dashboard/activity")
def dashboard_activity(limit: int = Query(8, ge=1, le=100)) -> Dict[str, Any]:
    """最近动态：真实审计事件（已过滤 kernel state_change 噪声）。"""
    items = recent_activity(limit=limit)
    return {"activity": items, "count": len(items), "limit": limit}


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

#: 单次查询的事件上限。审计量级可达数万条；超过这个数只统计最近的部分，
#: 并由 ``truncated`` 字段如实告知，避免悄悄给出偏低的数字。
_ANALYTICS_MAX_EVENTS = 200_000


def _day_key(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def event_timeseries(days: int = 14) -> Dict[str, Any]:
    """按天统计真实审计事件（total / allowed / denied）。"""
    from ..kernels.audit import audit_query

    days = max(1, min(90, int(days)))
    now = time.time()
    # 以"今天"为最后一天，向前取 days 天。
    start = now - (days - 1) * 86400
    start = time.mktime(
        time.strptime(_day_key(start), "%Y-%m-%d")
    )

    try:
        events = audit_query(start_time=start, limit=_ANALYTICS_MAX_EVENTS)
    except Exception as exc:
        logger.warning("event_timeseries query failed: %s", exc, exc_info=True)
        return {"available": False, "error": str(exc), "days": []}

    # 预置全部日期：没有事件的日子显示 0 而不是从轴上消失（那会歪曲趋势）。
    buckets: Dict[str, Dict[str, Any]] = {}
    for offset in range(days):
        key = _day_key(start + offset * 86400)
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

    return {
        "available": True,
        "days": [buckets[key] for key in sorted(buckets)],
        "sampled_events": len(events),
        "truncated": len(events) >= _ANALYTICS_MAX_EVENTS,
    }


def event_breakdown() -> Dict[str, Any]:
    """真实事件分布（event_type × outcome），供环形图使用。"""
    from ..kernels.audit import audit_stats

    try:
        stats = audit_stats()
    except Exception as exc:
        logger.warning("event_breakdown failed: %s", exc, exc_info=True)
        return {"available": False, "error": str(exc), "items": []}

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
def dashboard_analytics(days: int = Query(14, ge=1, le=90)) -> Dict[str, Any]:
    """驾驶舱图表数据源：真实审计时序 + 真实事件分布。"""
    return {
        "generated_at": time.time(),
        "timeseries": event_timeseries(days=days),
        "breakdown": event_breakdown(),
    }
