"""事件产品面 —— 把内核事件总线的真实事件摊开给人看（只读）。

LHX-C-008 事件内核长期只有内部 publisher/subscriber 与一条只挂在
``src/api/server.py``（非产品 app）的 WebSocket，没有任何"人类可见"的出口。
本模块给产品 app（``src/gateway/main.py``）补上第一个诚实的只读出口：

- ``GET /v1/events?limit=50`` 返回内核总线里**真实发生**的近期事件
  （id/type/source/timestamp/scope/priority/payload 摘要）；
- ``GET /v1/events/history`` 返回**落盘的**审计历史（读审计库
  ``audit_events``），支持按主体/类型/结果/时间窗过滤。前者是内存环形缓冲
  （最多 10000 条、进程重启即丢），后者才是"上次之后发生了什么"的持久答案；
  两个出口都如实标注数据来源，避免把内存缓冲冒充成历史；
- 数据直接来自事件内核的 ``get_event_history`` —— 内核已经维护了一份内存近期
  事件缓冲（最多 10000 条），这里只是忠实地把它呈现出来，**不**另建一份可能
  与之分叉/造假的环形缓冲（任务明确允许"若内核已暴露，则复用"）；
- 人类主权闸门（``require_human_principal``）：未带令牌 → 401；
- 严格只读：本模块没有任何发布/订阅写入入口，不会伪造一条"心跳"事件来假装系统
  忙碌。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.kernels.event import Event, EventScope, get_event_bus
from .policy import require_human_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["events"])


def _json_safe(value: Any) -> Any:
    """把任意事件载荷变成 JSON 安全的表示；无法序列化时回落到 str/repr。"""
    if value is None or isinstance(value, (str, int, float, bool, datetime)):
        if isinstance(value, datetime):
            return value.isoformat()
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _event_summary_text(event: Event) -> str:
    """人类可读的简短摘要，绝不声称更多信息。"""
    data = event.data
    if isinstance(data, dict) and data:
        parts = [f"{k}={_json_safe(v)}" for k, v in list(data.items())[:3]]
        return "; ".join(parts)
    if isinstance(data, str) and data:
        return data
    if data is None:
        return ""
    return repr(_json_safe(data))


def _summarize(event: Event) -> Dict[str, Any]:
    """把一个真实 Event 收缩成前端友好的只读摘要。"""
    return {
        "id": event.correlation_id,
        "type": event.type,
        "source": event.source,
        "timestamp": event.timestamp.isoformat(),
        "scope": event.scope.value if isinstance(event.scope, EventScope) else str(event.scope),
        "priority": event.priority.value if hasattr(event.priority, "value") else str(event.priority),
        "summary": _event_summary_text(event),
        "data": _json_safe(event.data),
    }


@router.get("")
def list_events(
    limit: int = Query(50, ge=1, le=1000),
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """近期真实事件（只读）。

    直接复用事件内核的内存近期事件缓冲 ``get_event_history`` —— 内核已保真地记录了
    总线上的真实事件；这里只呈现，不复制、不修饰。``limit`` 取最近 N 条。
    """
    bus = get_event_bus()
    events = bus.get_event_history(limit=limit)
    summaries = [_summarize(e) for e in events]
    truncated = len(summaries) == limit
    return {
        "events": summaries,
        "count": len(summaries),
        "limit": limit,
        "truncated": truncated,
        "source": "event_bus_history",
        "durable": False,
        "note": "内存近期缓冲；进程重启即丢。持久历史见 /v1/events/history",
    }


@router.get("/history")
def list_persisted_event_history(
    principal: Optional[str] = Query(None, description="按主体过滤"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    outcome: Optional[str] = Query(None, description="按结果过滤（success/denied/...）"),
    correlation_id: Optional[str] = Query(
        None, description="按关联 ID 过滤 —— 一次目标执行产生的全部事件"),
    since: Optional[float] = Query(None, ge=0, description="起始 Unix 时间戳（秒，含）"),
    until: Optional[float] = Query(None, ge=0, description="结束 Unix 时间戳（秒，含）"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
    reverse: bool = Query(True, description="true = 最新的在前"),
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """持久事件历史（只读，来自审计库）。

    与 ``/v1/events`` 的区别：那个读内存环形缓冲，只覆盖"本进程运行期间"；
    这里读审计库 ``audit_events``，覆盖"这个部署写过的全部事件"。两个都有
    存在的理由，但只有后者能回答"昨天发生了什么"。

    读库失败时返回 503 与真实原因 —— **绝不**返回空列表，否则调用方分不清
    "没有历史"和"历史读不出来"。
    """
    from .audit import _read_events, _unreadable_detail

    try:
        events = _read_events(
            principal=principal,
            event_type=event_type,
            outcome=outcome,
            correlation_id=correlation_id,
            since=since,
            until=until,
            limit=limit,
            reverse=reverse,
        )
    except Exception as exc:  # noqa: BLE001 - 读失败必须如实说出来
        logger.warning("persisted event history query failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=_unreadable_detail(exc))

    return {
        "events": events,
        "count": len(events),
        "limit": limit,
        "truncated": len(events) >= limit,
        "source": "audit_store",
        "durable": True,
        "filters": {
            "principal": principal,
            "event_type": event_type,
            "outcome": outcome,
            "correlation_id": correlation_id,
            "since": since,
            "until": until,
            "limit": limit,
            "reverse": reverse,
        },
    }
