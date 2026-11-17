"""分步计划审批视图（任务 3）。

派活前贾维斯先出分步计划，Joseph 能改、能批、能叫停。状态机：

    draft → pending_approval → approved → running → completed / failed
                 │                 │           │
                 │                 └─ cancelled（叫停）
                 ├─ rejected（驳回，可改后重送）
                 └─ amended → pending_approval（改完必须重批，不许直接执行）

计划本身一张表（workflow_plans）；计划的“审批”复用 workflow_approvals
（kind='plan'、ref_id=plan_id），不另起第二套审批。审批中心点通过/拒绝
与计划页点通过/拒绝走同一个 store.decide_approval，状态双向同步。
执行中某步失败两次，计划停下（failed），问 Joseph。
叫停 = 不再开始新步骤，正在跑的跑到安全点再停；叫停报告与审计记录
用同一个 plan_id 对上。已对外发出的消息、已花掉的钱回滚不了，
如实写进叫停报告；已创建文件的回滚要再走危险动作卡片确认，不擅自删。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.workflow.engine import get_workflow_runtime
from src.workflow.store import StoreError, get_workflow_store
from .approvals import _is_root, _owners_for, _principal

logger = logging.getLogger(__name__)

PLAN_ROLES = ("captain", "researcher", "organizer", "reminder", "coder")
PLAN_STATUSES = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "amended",
    "running",
    "completed",
    "failed",
    "cancelled",
)
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped", "cancelled")
STEP_RISKS = ("low", "medium", "high")


# 请求模型放模块级：FastAPI 要在模块全局解析到它们（放 build_router 里会
# 被当成 query 参数）。
class PlanCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    steps: List[Dict[str, Any]] = Field(min_length=1, max_length=50)


class PlanAmendRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    steps: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=50)


class StepStatusRequest(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=2000)


def _audit(
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
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮计划操作
        logger.warning("plan audit failed: %s", action)


def _validate_steps(raw: Any) -> List[Dict[str, Any]]:
    """校验计划步骤形状。队长只能派给 4 个固定角色 + 自己。"""
    if not isinstance(raw, list) or not raw:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_steps", "message": "计划至少要有一个步骤"},
        )
    if len(raw) > 50:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_steps", "message": "计划最多 50 步，先拆小再派"},
        )
    steps: List[Dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_step", "message": f"第 {index + 1} 步不是一个步骤对象"},
            )
        title = str(item.get("title") or "").strip()
        what = str(item.get("what") or "").strip()
        assignee = str(item.get("assignee") or "").strip()
        risk = str(item.get("risk") or "low").strip()
        if not title:
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_step", "message": f"第 {index + 1} 步没有标题"},
            )
        if not what:
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_step", "message": f"第 {index + 1} 步没有写要干什么"},
            )
        if assignee not in PLAN_ROLES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "bad_assignee",
                    "message": f"第 {index + 1} 步的执行人只能是队长或 4 个固定角色（{assignee} 不在其中）",
                },
            )
        if risk not in STEP_RISKS:
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_risk", "message": f"第 {index + 1} 步的风险只能是 low/medium/high"},
            )
        targets = item.get("targets") or []
        if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_targets", "message": f"第 {index + 1} 步的目标清单必须是字符串列表"},
            )
        steps.append(
            {
                "title": title,
                "what": what,
                "assignee": assignee,
                "tool": str(item.get("tool") or "").strip(),
                "risk": risk,
                "expected_impact": str(item.get("expected_impact") or "").strip(),
                "targets": [str(t) for t in targets],
                "reversible": bool(item.get("reversible", True)),
                "rollback_note": str(item.get("rollback_note") or "").strip(),
                "approval_required": bool(item.get("approval_required", False)),
            }
        )
    return steps


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    from .approvals import _no_viewer

    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/plans", tags=["plans"])

    def _store():
        return get_workflow_store()

    def _runtime():
        return get_workflow_runtime()

    def _require_plan(plan_id: str, viewer: Any) -> Dict[str, Any]:
        plan = _store().get_plan(plan_id)
        owners = _owners_for(viewer)
        if plan is None or (
            owners is not None and plan.get("owner_account_id") not in owners
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "plan_not_found", "message": f"计划不存在：{plan_id}"},
            )
        return plan

    def _require_root(viewer: Any) -> str:
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_owner", "message": "只有 Owner 能操作计划"},
            )
        return _principal(viewer)

    def _audit_trail(plan_id: str) -> List[Dict[str, Any]]:
        try:
            from src.kernels.audit import audit_query

            events = audit_query(correlation_id=plan_id, limit=100)
        except Exception:  # noqa: BLE001 - 审计只读回看，失败不阻断详情
            logger.warning("plan audit trail unavailable: %s", plan_id)
            return []
        trail = []
        for event in events or []:
            details = event.get("details") if isinstance(event, dict) else None
            action = ""
            if isinstance(details, dict):
                action = str(details.get("action") or "")
            if action and not action.startswith("workflow.plan"):
                continue
            trail.append(
                {
                    "event_id": event.get("event_id") or event.get("id"),
                    "timestamp": event.get("timestamp"),
                    "principal_id": event.get("principal_id"),
                    "outcome": event.get("outcome"),
                    "action": action,
                    "details": details if isinstance(details, dict) else {},
                }
            )
        return trail

    def _view(plan: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(plan)
        approval_id = str(plan.get("approval_id") or "")
        if approval_id:
            approval = _store().get_approval(approval_id)
            item["approval"] = (
                {
                    "approval_id": approval["approval_id"],
                    "status": approval["status"],
                    "decided_by": approval.get("decided_by", ""),
                    "comment": approval.get("comment", ""),
                    "remaining_seconds": (
                        max(0, int(float(approval["expires_at"]) - time.time()))
                        if approval.get("expires_at") is not None
                        else None
                    ),
                }
                if approval is not None
                else None
            )
        else:
            item["approval"] = None
        return item

    def _stop_report(plan: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        """叫停报告：做到第几步、每步结果、哪些已回滚、哪些回滚不了、下一步建议。

        回滚本身不擅自执行：已创建文件的留/删要 Joseph 在报告里选，删文件
        另走危险动作卡片确认；已对外发出的消息、已花掉的钱回滚不了，如实写明。
        """
        steps = plan.get("steps") or []
        done_steps = [s for s in steps if s.get("status") == "done"]
        failed_steps = [s for s in steps if s.get("status") == "failed"]
        # 叫停报告在步骤已置 cancelled 后才生成：被叫停的步骤就是状态为 cancelled 的。
        pending_steps = [s for s in steps if s.get("status") == "cancelled"]
        rollbackable = [
            {
                "n": s["n"],
                "title": s["title"],
                "targets": s.get("targets") or [],
                "rollback_note": s.get("rollback_note") or "按步骤记录回滚",
            }
            for s in done_steps
            if s.get("reversible")
        ]
        not_rollbackable = [
            {
                "n": s["n"],
                "title": s["title"],
                "reason": "该步不可逆（已对外发出或已花掉的钱，回不去）",
            }
            for s in done_steps
            if not s.get("reversible")
        ]
        return {
            "plan_id": plan["plan_id"],
            "title": plan.get("title", ""),
            "reason": reason,
            "stopped_at": time.time(),
            "done_count": len(done_steps),
            "failed_count": len(failed_steps),
            "cancelled_count": len(pending_steps),
            "steps": [
                {
                    "n": s["n"],
                    "title": s["title"],
                    "status": s.get("status"),
                    "note": s.get("note") or "",
                }
                for s in steps
            ],
            "rollbackable": rollbackable,
            "not_rollbackable": not_rollbackable,
            "rollback_executed": False,
            "rollback_note": (
                "回滚没有擅自执行：可回滚的步骤列在上面，删文件要你确认后另走确认卡；"
                "回滚不了的步骤已如实列出。"
                if rollbackable or not_rollbackable
                else "没有已完成的步骤，无需回滚。"
            ),
            "next_steps": (
                "看看上面哪些已做的要留、哪些要删，再决定要不要改完重新批。"
                if done_steps
                else "什么都没开始做，可以直接改完重新批，或不要这个计划了。"
            ),
        }

    # ── 创建 / 查看 ───────────────────────────────────────────

    @router.post("", status_code=201)
    def create_plan(payload: PlanCreateRequest, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        steps = _validate_steps(payload.steps)
        plan = _store().create_plan(
            title=payload.title.strip(),
            goal=payload.goal.strip(),
            created_by=owner,
            owner_account_id=owner,
            steps=steps,
        )
        _audit(
            "workflow.plan.created",
            principal_id=owner,
            outcome="success",
            details={"title": plan["title"], "step_count": len(steps)},
            correlation_id=str(plan["plan_id"]),
        )
        return {"plan": _view(plan)}

    @router.get("")
    def list_plans(
        status: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=300),
        offset: int = Query(default=0, ge=0),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        if status is not None and status not in PLAN_STATUSES:
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_status", "message": f"没有这个计划状态：{status}"},
            )
        plans = _store().list_plans(
            status=status, owners=_owners_for(viewer), limit=limit, offset=offset
        )
        return {"plans": [_view(plan) for plan in plans]}

    @router.get("/{plan_id}")
    def get_plan(plan_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        plan = _require_plan(plan_id, viewer)
        item = _view(plan)
        item["audit"] = _audit_trail(plan_id)
        return {"plan": item}

    # ── 送审 / 批 / 改 ────────────────────────────────────────

    @router.post("/{plan_id}/submit")
    def submit_plan(plan_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "draft":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有草稿能送审，现在是 {plan['status']}",
                },
            )
        approval = _store().create_approval(
            run_id="",
            step_id="",
            node_id="",
            def_id="plan",
            title=f"执行计划：{plan['title']}",
            operation=f"按计划执行：{plan['goal']}",
            reason=plan["goal"],
            approvers=[owner],
            submitted_by=owner,
            owner_account_id=owner,
            timeout_seconds=0,
            kind="plan",
            proposer="jarvis",
            ref_id=plan_id,
        )
        updated = _store().update_plan(
            plan_id, status="pending_approval", approval_id=str(approval["approval_id"])
        )
        _audit(
            "workflow.plan.submitted",
            principal_id=owner,
            outcome="success",
            details={"approval_id": approval["approval_id"]},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    def _decide_linked(plan: Dict[str, Any], decision: str, owner: str, comment: str) -> Dict[str, Any]:
        approval_id = str(plan.get("approval_id") or "")
        if not approval_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "no_linked_approval", "message": "这个计划没有关联的审批记录"},
            )
        try:
            _runtime().apply_approval_decision(
                approval_id,
                decision=decision,
                decided_by=owner,
                comment=comment,
            )
        except StoreError as exc:
            raise HTTPException(
                status_code=int(exc.status_code or 500),
                detail={"code": str(exc.code or "approval_failed"), "message": str(exc.message or exc)},
            )
        return _store().get_plan(str(plan["plan_id"])) or plan

    @router.post("/{plan_id}/approve")
    def approve_plan(
        plan_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有待批的计划能批，现在是 {plan['status']}",
                },
            )
        comment = str((payload or {}).get("comment") or "")
        updated = _decide_linked(plan, "approved", owner, comment)
        _audit(
            "workflow.plan.approved",
            principal_id=owner,
            outcome="success",
            details={"comment": comment},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    @router.post("/{plan_id}/reject")
    def reject_plan(
        plan_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有待批的计划能驳回，现在是 {plan['status']}",
                },
            )
        comment = str((payload or {}).get("comment") or "")
        updated = _decide_linked(plan, "rejected", owner, comment)
        _audit(
            "workflow.plan.rejected",
            principal_id=owner,
            outcome="deny",
            details={"comment": comment},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    @router.post("/{plan_id}/amend")
    def amend_plan(
        plan_id: str, payload: PlanAmendRequest, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] not in ("pending_approval", "approved", "rejected"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"现在是 {plan['status']}，改计划要先叫停正在跑的计划",
                },
            )
        steps = _validate_steps(payload.steps) if payload.steps is not None else None
        # 旧审批作废（已决定就不再动它，只作废还在待批的那张）。
        old_approval_id = str(plan.get("approval_id") or "")
        if old_approval_id and plan["status"] == "pending_approval":
            old = _store().get_approval(old_approval_id)
            if old is not None and old["status"] == "pending":
                try:
                    _runtime().apply_approval_decision(
                        old_approval_id,
                        decision="rejected",
                        decided_by=owner,
                        comment="已修改，重新审批",
                    )
                except StoreError:
                    pass
        version = int(plan.get("version") or 1) + 1
        updated = _store().update_plan(
            plan_id,
            status="pending_approval",
            title=payload.title.strip() if payload.title is not None else None,
            goal=payload.goal.strip() if payload.goal is not None else None,
            steps=steps,
            version=version,
        )
        approval = _store().create_approval(
            run_id="",
            step_id="",
            node_id="",
            def_id="plan",
            title=f"执行计划（第 {version} 版）：{updated['title']}",
            operation=f"按计划执行：{updated['goal']}",
            reason=updated["goal"],
            approvers=[owner],
            submitted_by=owner,
            owner_account_id=owner,
            timeout_seconds=0,
            kind="plan",
            proposer="jarvis",
            ref_id=plan_id,
        )
        updated = _store().update_plan(
            plan_id, approval_id=str(approval["approval_id"])
        )
        _audit(
            "workflow.plan.amended",
            principal_id=owner,
            outcome="success",
            details={"version": version, "approval_id": approval["approval_id"]},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    # ── 执行 / 叫停 ───────────────────────────────────────────

    @router.post("/{plan_id}/start")
    def start_plan(plan_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有已批的计划能开始执行，现在是 {plan['status']}",
                },
            )
        updated = _store().update_plan(
            plan_id, status="running", started_at=time.time()
        )
        _audit(
            "workflow.plan.started",
            principal_id=owner,
            outcome="success",
            details={},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    @router.post("/{plan_id}/steps/{step_n}/status")
    def update_step_status(
        plan_id: str,
        step_n: int,
        payload: StepStatusRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "running":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有执行中的计划能更新步骤，现在是 {plan['status']}",
                },
            )
        steps: List[Dict[str, Any]] = list(plan.get("steps") or [])
        target = next((s for s in steps if int(s.get("n") or 0) == step_n), None)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "step_not_found", "message": f"没有第 {step_n} 步"},
            )
        new_status = payload.status.strip()
        if new_status not in ("running", "done", "failed", "skipped", "blocked"):
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_step_status", "message": f"步骤状态只能是 running/done/failed/skipped/blocked：{new_status}"},
            )
        current = str(target.get("status") or "pending")
        allowed = {
            "pending": {"running", "done", "failed", "skipped", "blocked"},
            "running": {"done", "failed", "skipped", "blocked"},
            # 失败过的步骤可以重试（再跑一次）或跳过；再失败一次就按“失败两次”停下。
            "failed": {"running", "failed", "skipped"},
            # 卡住的步骤不是失败：等 Joseph 回话后继续（running）或跳过；
            # 不能直接标 done——没干完的事不能算干完了。
            "blocked": {"running", "skipped"},
        }
        if new_status not in allowed.get(current, set()):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_step_transition",
                    "message": f"第 {step_n} 步现在是 {current}，不能变成 {new_status}",
                },
            )
        target["status"] = new_status
        target["note"] = payload.note.strip()
        plan_status = "running"
        finished_at = None
        if new_status == "failed":
            target["fail_count"] = int(target.get("fail_count") or 0) + 1
            if target["fail_count"] >= 2:
                # 某步失败两次，停下问 Joseph：不再开始新步骤。
                plan_status = "failed"
                finished_at = time.time()
                for other in steps:
                    if other.get("status") in ("pending", "running"):
                        other["status"] = "cancelled"
        if plan_status == "running" and all(
            s.get("status") in ("done", "skipped", "cancelled") for s in steps
        ):
            plan_status = "completed"
            finished_at = time.time()
        updated = _store().update_plan(
            plan_id,
            status=plan_status,
            steps=steps,
            finished_at=finished_at,
        )
        _audit(
            "workflow.plan.step",
            principal_id=owner,
            outcome="success" if new_status != "failed" else "deny",
            details={"step": step_n, "status": new_status, "plan_status": plan_status},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    @router.post("/{plan_id}/complete")
    def complete_plan(plan_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "running":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有执行中的计划能标记完成，现在是 {plan['status']}",
                },
            )
        steps = plan.get("steps") or []
        unfinished = [s for s in steps if s.get("status") in ("pending", "running")]
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "steps_unfinished",
                    "message": f"还有 {len(unfinished)} 步没做完，不能直接标记完成",
                },
            )
        updated = _store().update_plan(
            plan_id, status="completed", finished_at=time.time()
        )
        _audit(
            "workflow.plan.completed",
            principal_id=owner,
            outcome="success",
            details={},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated)}

    @router.post("/{plan_id}/cancel")
    def cancel_plan(
        plan_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        owner = _require_root(viewer)
        plan = _require_plan(plan_id, viewer)
        if plan["status"] != "running":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "bad_plan_status",
                    "message": f"只有执行中的计划能叫停，现在是 {plan['status']}",
                },
            )
        reason = str((payload or {}).get("reason") or "Joseph 叫停").strip()
        steps: List[Dict[str, Any]] = list(plan.get("steps") or [])
        for step in steps:
            if step.get("status") in ("pending", "running"):
                step["status"] = "cancelled"
        updated = _store().update_plan(
            plan_id,
            status="cancelled",
            steps=steps,
            finished_at=time.time(),
        )
        report = _stop_report(updated, reason=reason)
        _audit(
            "workflow.plan.cancelled",
            principal_id=owner,
            outcome="deny",
            details={"reason": reason, "report": report},
            correlation_id=plan_id,
        )
        return {"plan": _view(updated), "stop_report": report}

    return router
