"""自动化工具注册：默认三重关闭，任一闸未开即返回空列表。

本轮只注册会话控制面（open / close / status / emergency_stop）；
真实键鼠后端工具**不注册**，下一轮再接入。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from ..ai.tool_registry import Tool
from ..tasks._queue_paths import resolve_sqlite_store
from .audit import AutomationAudit
from .confirm import SqliteConfirmationBroker
from .contracts import AutomationError, BackendKind
from .estop import EmergencyStop, get_emergency_stop
from .gate import AutomationGate, GateDecision
from .guard import Guard
from .sessions import AutomationSessionRegistry, get_session_registry

TICKET_DB_ENV = "LIUHAO_AUTOMATION_TICKET_DB"

_broker_lock = threading.Lock()
_brokers: Dict[str, SqliteConfirmationBroker] = {}


def _ticket_db_path() -> str:
    """确认票据库落点：显式配置 > 审计库同目录 > 统一数据根 > 历史 cwd 位置。

    历史默认是 ``<cwd>/data/automation_tickets.db``：快捷方式把工作目录设成
    用户主目录时，这个"运行时数据库"会悄悄落到 C 盘。现在改走统一数据根，
    旧位置有库时按队列同款流程迁移（复制 + 校验 + 原子落位 + 旧文件保留），
    迁移失败继续用旧库，绝不以空库启动。
    """
    explicit = (os.environ.get(TICKET_DB_ENV) or "").strip()
    if explicit:
        return explicit
    audit_db = (os.environ.get("AUDIT_DB_PATH") or "").strip()
    if audit_db:
        # 审计库位置是显式配置，票据库继续放在它旁边（通常已在数据根内）。
        return os.path.join(
            os.path.dirname(os.path.abspath(audit_db)), "automation_tickets.db"
        )
    return resolve_sqlite_store(
        "automation_tickets.db",
        os.path.join(os.getcwd(), "data", "automation_tickets.db"),
        env_var=TICKET_DB_ENV,
        label="自动化票据库",
    )


def get_confirmation_broker() -> SqliteConfirmationBroker:
    path = _ticket_db_path()
    with _broker_lock:
        broker = _brokers.get(path)
        if broker is None:
            broker = SqliteConfirmationBroker(path)
            _brokers[path] = broker
        return broker


def build_automation_tools(
    principal: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    gate: Optional[AutomationGate] = None,
    registry: Optional[AutomationSessionRegistry] = None,
    broker: Optional[SqliteConfirmationBroker] = None,
    audit: Optional[AutomationAudit] = None,
    estop: Optional[EmergencyStop] = None,
) -> List[Tool]:
    """按闸门判决返回工具列表。关闭时必须是空列表（不是"注册但拒绝"）。"""

    decision: GateDecision = (gate or AutomationGate()).evaluate(
        principal, metadata=metadata
    )
    if not decision.allowed:
        return []

    registry = registry or get_session_registry()
    estop = estop or get_emergency_stop()
    broker = broker or get_confirmation_broker()
    audit = audit or AutomationAudit()

    # 只有闸门放行后才尝试接入真实热键；默认环境变量缺省 → 不注册。
    from .hotkey_windows import configure_from_env

    configure_from_env(estop)

    def _json(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def open_session(backend: str = "", **kwargs: Any) -> str:
        """打开自动化会话（本轮仅 fake 后端）。"""
        requested = str(backend or kwargs.get("backend") or decision.backend_kind.value)
        if requested != decision.backend_kind.value:
            return _json({"status": "error", "error": "backend_not_allowed"})
        from .backends import create_backend

        try:
            backend_impl = create_backend(BackendKind(requested))
        except AutomationError as exc:
            return _json({"status": "error", "error": exc.reason})
        # 允许清单由运维配置，绝不接受模型自带参数；默认空 → 不能执行任何目标。
        session = registry.open(
            principal=principal,
            principal_kind=decision.principal_kind,
            backend=backend_impl,
            guard=Guard(allowlist=()),
            broker=broker,
            audit=audit,
            estop=estop,
        )
        return _json({"status": "ok", "session": session.status_dict()})

    def close_session(session_id: str = "", **kwargs: Any) -> str:
        """关闭当前主体的自动化会话（不传 session_id 时关闭全部）。"""
        sid = str(session_id or kwargs.get("session") or "")
        if sid:
            closed = registry.close(sid, principal=principal)
            return _json({"status": "ok", "closed": 1 if closed else 0})
        return _json({"status": "ok", "closed": registry.close_all(principal=principal)})

    def session_status() -> str:
        """返回会话、急停与闸门的真实状态。"""
        payload = registry.status(principal=principal)
        payload["emergency_stop"] = {
            "active": estop.active,
            "registration": (
                {
                    "registered": estop.registration.registered,
                    "combo": estop.registration.combo,
                    "reason": estop.registration.reason,
                    "attempts": [dict(item) for item in estop.registration.attempts],
                }
                if estop.registration
                else None
            ),
        }
        payload["gate"] = decision.as_dict()
        return _json(payload)

    def emergency_stop(reason: str = "manual", **kwargs: Any) -> str:
        """触发急停：置取消令牌、冻结全部会话、清理修饰键残留。"""
        report = estop.trigger(str(reason or kwargs.get("reason") or "manual"))
        report["frozen_sessions"] = registry.freeze_all("emergency_stop")
        return _json({"status": "ok", "emergency_stop": report})

    return [
        Tool(
            tool_id="liuhao.automation.session.open",
            name="automation.session.open",
            version="1.0.0",
            description="打开自动化会话（本轮仅 fake 后端，默认无允许目标）",
            capability="automation.session.open",
            schema={
                "type": "object",
                "properties": {"backend": {"type": "string", "default": "fake"}},
            },
            fn=open_session,
            risk="MEDIUM",
        ),
        Tool(
            tool_id="liuhao.automation.session.close",
            name="automation.session.close",
            version="1.0.0",
            description="关闭自动化会话（不传 session_id 时关闭全部）",
            capability="automation.session.close",
            schema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
            },
            fn=close_session,
            risk="MEDIUM",
        ),
        Tool(
            tool_id="liuhao.automation.session.status",
            name="automation.session.status",
            version="1.0.0",
            description="查询自动化会话、急停与闸门状态",
            capability="automation.session.status",
            schema={"type": "object", "properties": {}},
            fn=session_status,
            risk="LOW",
        ),
        Tool(
            tool_id="liuhao.automation.emergency_stop",
            name="automation.emergency_stop",
            version="1.0.0",
            description="立即触发急停并冻结全部自动化会话",
            capability="automation.emergency_stop",
            schema={
                "type": "object",
                "properties": {"reason": {"type": "string", "default": "manual"}},
            },
            fn=emergency_stop,
            risk="HIGH",
        ),
    ]
