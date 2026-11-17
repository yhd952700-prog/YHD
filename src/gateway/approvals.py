"""审批中心 HTTP 接口（``/v1/approvals*``）。

这是**流程审批**的待办队列，和 ``/v1/policy/approvals``（权限线发放的一次性
授权令牌）是两件事：这里的一条待办对应某个工作流运行卡在 approval/review
节点上，通过或拒绝会直接继续/终止那条运行。

审计回看直接查现有哈希链（``audit_query(correlation_id=run_id)``），本模块
不另建一张"审批日志表"——审批动作已经通过 ``workflow.approval.*`` 写进审计。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.workflow.engine import get_workflow_runtime
from src.workflow.store import TERMINAL_APPROVAL_STATUSES, get_workflow_store

logger = logging.getLogger(__name__)


class DecisionRequest(BaseModel):
    comment: str = Field(default="", max_length=1000)


class CardRequest(BaseModel):
    """危险动作确认卡的创建请求（模块级：FastAPI 要在模块全局解析到它）。"""

    title: str = Field(min_length=1, max_length=200)
    action_text: str = Field(min_length=1, max_length=2000)
    targets: List[str] = Field(min_length=1, max_length=100)
    risk_note: str = Field(default="", max_length=2000)
    impact: str = Field(default="", max_length=2000)
    consequence: str = Field(default="", max_length=2000)
    proposer: str = Field(default="jarvis", max_length=100)
    source: str = Field(default="user_said", max_length=20)


def _no_viewer() -> None:  # pragma: no cover
    return None


def _owners_for(viewer: Any) -> Optional[List[str]]:
    from .ai_management import _as_viewer, default_account_id

    real = _as_viewer(viewer)
    if real is None:
        return None
    owners = {real.principal, *[str(item) for item in (real.subtree or ())]}
    if real.standalone:
        owners.add(default_account_id())
    return sorted(item for item in owners if item)


def _principal(viewer: Any) -> str:
    from .ai_management import _as_viewer

    real = _as_viewer(viewer)
    return str(real.principal or "") if real is not None else ""


def _is_root(viewer: Any) -> bool:
    from .ai_management import _as_viewer

    real = _as_viewer(viewer)
    return bool(real is not None and real.is_root)


def _stamp(item: Dict[str, Any]) -> Dict[str, Any]:
    from .workflow import _stamp_times

    return _stamp_times(item)


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/approvals", tags=["workflow-approvals"])

    def _store():
        return get_workflow_store()

    def _runtime():
        return get_workflow_runtime()

    def _require_approval(approval_id: str, viewer: Any) -> Dict[str, Any]:
        approval = _store().get_approval(approval_id)
        owners = _owners_for(viewer)
        if approval is None or (
            owners is not None and approval.get("owner_account_id") not in owners
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "approval_not_found", "message": f"审批不存在：{approval_id}"},
            )
        return approval

    def _can_decide(approval: Dict[str, Any], viewer: Any) -> bool:
        if _is_root(viewer):
            return True
        principal = _principal(viewer)
        if not principal:
            return False
        approvers = approval.get("approvers") or []
        if approvers and principal in approvers:
            return True
        return principal == str(approval.get("owner_account_id") or "")

    def _enrich(approval: Dict[str, Any]) -> Dict[str, Any]:
        item = _stamp(dict(approval))
        expires_at = approval.get("expires_at")
        remaining = None
        if expires_at is not None:
            remaining = max(0, int(float(expires_at) - time.time()))
        item["remaining_seconds"] = remaining
        run = _store().get_run(str(approval.get("run_id") or ""))
        if run is not None:
            item["run"] = {
                "run_id": run["run_id"],
                "name": run.get("name", ""),
                "status": run.get("status", ""),
                "def_id": run.get("def_id", ""),
                "current_node_id": run.get("current_node_id"),
                "submitted_by": run.get("submitted_by", ""),
            }
        else:
            item["run"] = None
        return item

    # ── 队列 ────────────────────────────────────────────────

    @router.get("/summary")
    def approval_summary(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owners = _owners_for(viewer)
        pending = _store().list_approvals(status="pending", owners=owners, limit=200)
        now = time.time()
        overdue = 0
        soon = 0
        for item in pending:
            expires_at = item.get("expires_at")
            if expires_at is None:
                continue
            remaining = float(expires_at) - now
            if remaining <= 0:
                overdue += 1
            elif remaining <= 300:
                soon += 1
        return {"pending": len(pending), "overdue": overdue, "due_within_5m": soon}

    @router.get("")
    def list_approvals(
        status: Optional[str] = Query(default="pending"),
        run_id: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=300),
        offset: int = Query(default=0, ge=0),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        if status == "pending":
            # 读队列前先把已超时的待办落成 expired，否则用户会看到"点不动的待办"。
            try:
                _runtime().expire_overdue()
            except Exception:  # noqa: BLE001 - 过期扫描失败不阻断查看
                logger.exception("expire approvals before list failed")
        approvals = _store().list_approvals(
            status=status,
            owners=_owners_for(viewer),
            run_id=run_id,
            limit=limit,
            offset=offset,
        )
        return {"approvals": [_enrich(item) for item in approvals]}

    @router.get("/{approval_id}")
    def get_approval(approval_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        approval = _require_approval(approval_id, viewer)
        item = _enrich(approval)
        item["can_decide"] = approval["status"] == "pending" and _can_decide(approval, viewer)
        run = _store().get_run(str(approval["run_id"]))
        if run is not None:
            item["steps"] = [_stamp(step) for step in _store().list_steps(str(run["run_id"]))]
        # 卡片/计划审批不挂运行，审计按 approval_id 关联。
        item["audit"] = _audit_trail(str(approval["run_id"]) or approval_id)
        return {"approval": item}

    # ── 危险动作确认卡（任务 4）─────────────────────────────────

    def _audit_event(
        action: str,
        *,
        principal_id: str,
        outcome: str,
        details: Dict[str, Any],
        correlation_id: str,
    ) -> None:
        try:
            from src.kernels.audit import AuditEventType, AuditScope, log_event

            log_event(
                AuditEventType.STATE_CHANGE,
                principal_id or "system",
                AuditScope.L1,
                outcome,
                {"action": action, **details},
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮建卡
            logger.warning("approval audit failed: %s", action)

    @router.post("/cards", status_code=201)
    def create_action_card(
        payload: CardRequest, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        """建一张危险动作确认卡（30 分钟过期，超时默认拒绝）。

        与工作流审批共用 workflow_approvals 一张表（kind='action_card'），
        决定走同一条 approve/reject，审批中心与聊天卡片看到同一条记录。
        只有 Owner 能建卡；来源不是 Joseph 亲口说的（粘贴/文件/网页）
        一律不建卡——那是数据不是指令，防诱导第 0 关。
        """
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_owner", "message": "只有 Owner 能创建危险动作确认卡"},
            )
        owner = _principal(viewer)
        source = (payload.source or "").strip()
        if source != "user_said":
            _audit_event(
                "workflow.approval.card_refused",
                principal_id=owner,
                outcome="deny",
                details={
                    "source": source,
                    "action_text": payload.action_text,
                    "reason": "动作来源不是 Joseph 亲口说的，不建卡、不执行",
                },
                correlation_id="",
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "induced_source_refused",
                    "message": f"动作来源不是你亲口说的（来源：{source}），我不建卡、也不执行",
                },
            )
        targets = [str(target).strip() for target in payload.targets if str(target).strip()]
        if not targets:
            raise HTTPException(
                status_code=422,
                detail={"code": "empty_targets", "message": "目标清单不能为空，要逐项列出真实目标"},
            )
        for target in targets:
            if any(char in target for char in "*?["):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unexpanded_glob",
                        "message": f"目标清单里不能只给通配符（{target}），要先展开成逐项清单",
                    },
                )
        card = _store().create_action_card(
            title=payload.title.strip(),
            action_text=payload.action_text.strip(),
            targets=targets,
            risk_note=payload.risk_note.strip(),
            impact=payload.impact.strip(),
            consequence=payload.consequence.strip(),
            proposer=(payload.proposer or "jarvis").strip(),
            source=source,
            submitted_by=owner,
            owner_account_id=owner,
        )
        _audit_event(
            "workflow.approval.card_created",
            principal_id=owner,
            outcome="success",
            details={
                "approval_id": card["approval_id"],
                "action_text": payload.action_text,
                "targets": targets,
                "source": source,
            },
            correlation_id=str(card["approval_id"]),
        )
        return {"approval": _enrich(card)}

    def _audit_trail(run_id: str) -> List[Dict[str, Any]]:
        try:
            from src.kernels.audit import audit_query

            events = audit_query(correlation_id=run_id, limit=100)
        except Exception:  # noqa: BLE001 - 审计只读回看，失败不阻断详情
            logger.warning("approval audit trail unavailable: %s", run_id)
            return []
        trail = []
        for event in events or []:
            details = event.get("details") if isinstance(event, dict) else None
            action = ""
            if isinstance(details, dict):
                action = str(details.get("action") or "")
            if action and not action.startswith("workflow."):
                continue
            trail.append(
                {
                    "event_id": event.get("event_id") or event.get("id"),
                    "timestamp": event.get("timestamp"),
                    "timestamp_text": _text_time(event.get("timestamp")),
                    "event_type": event.get("event_type"),
                    "principal_id": event.get("principal_id"),
                    "outcome": event.get("outcome"),
                    "action": action,
                    "details": details if isinstance(details, dict) else {},
                }
            )
        return trail

    def _text_time(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        try:
            from datetime import datetime

            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return None

    # ── 通过 / 拒绝 ─────────────────────────────────────────

    @router.post("/{approval_id}/approve")
    def approve(
        approval_id: str,
        payload: Optional[DecisionRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        return _decide(approval_id, "approved", payload, viewer)

    @router.post("/{approval_id}/reject")
    def reject(
        approval_id: str,
        payload: Optional[DecisionRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        return _decide(approval_id, "rejected", payload, viewer)

    def _decide(
        approval_id: str,
        decision: str,
        payload: Optional[DecisionRequest],
        viewer: Any,
    ) -> Dict[str, Any]:
        approval = _require_approval(approval_id, viewer)
        if approval["status"] in TERMINAL_APPROVAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "approval_already_decided",
                    "message": f"该审批已是 {approval['status']}，不能重复处理",
                },
            )
        if not _can_decide(approval, viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_an_approver", "message": "你不在本审批的审批人范围内"},
            )
        comment = (payload.comment if payload else "") or ""
        try:
            _runtime().apply_approval_decision(
                approval_id,
                decision=decision,
                decided_by=_principal(viewer) or "anonymous",
                comment=comment,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=int(getattr(exc, "status_code", 500) or 500),
                detail={
                    "code": str(getattr(exc, "code", "approval_failed") or "approval_failed"),
                    "message": str(getattr(exc, "message", "") or exc),
                },
            )
        updated = _store().get_approval(approval_id) or approval
        item = _enrich(updated)
        item["can_decide"] = False
        item["run"] = item.get("run") or {}
        item["run"]["status"] = (
            (_store().get_run(str(updated["run_id"])) or {}).get("status", "")
        )
        return {"approval": item}

    return router
