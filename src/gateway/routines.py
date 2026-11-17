"""例程管理接口（任务 5）。

定时 / webhook 是第一批触发器；新邮件 / 新消息 / 日历事件还没接，
创建时直接 422，不画假选项。

webhook 调用不走人类登录——只认密钥（``X-Routine-Secret``），密钥错了
401 并记审计；其余端点都要人类 Owner。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.tasks.routines import (
    ACTION_CHAT_MESSAGE,
    ACTION_CREATE_PLAN,
    ACTION_WEB_RESEARCH,
    ALLOWED_ACTIONS,
    ALLOWED_TRIGGERS,
    TRIGGER_SCHEDULE,
    TRIGGER_WEBHOOK,
    CronSchedule,
    RoutineError,
    RoutineRunner,
    generate_webhook_secret,
    get_routine_store,
    resolve_timezone,
    validate_plan_steps,
)
from .approvals import _is_root, _owners_for, _principal

logger = logging.getLogger(__name__)


def _http_error(exc: RoutineError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _audit(action: str, *, principal_id: str, outcome: str,
           details: Dict[str, Any], correlation_id: str) -> None:
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
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮例程接口
        logger.warning("routine audit failed: %s", action)


# 请求模型放模块级：FastAPI 要在模块全局解析到它们。
class RoutineCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger_type: str = Field(min_length=1, max_length=20)
    schedule: str = Field(default="", max_length=100)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    action_kind: str = Field(min_length=1, max_length=20)
    action_text: str = Field(default="", max_length=2000)
    plan_title: str = Field(default="", max_length=200)
    plan_goal: str = Field(default="", max_length=2000)
    plan_steps: List[Dict[str, Any]] = Field(default_factory=list)


class PauseAllRequest(BaseModel):
    paused: bool


class RoutineUpdateRequest(BaseModel):
    """改名 / 改时间：两个字段都可选，只改传了的；一个都不传 422。"""

    name: Optional[str] = None
    schedule: Optional[str] = None
    timezone: Optional[str] = None


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    from .approvals import _no_viewer

    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/routines", tags=["routines"])

    def _store():
        return get_routine_store()

    def _require_routine(routine_id: str, viewer: Any) -> Dict[str, Any]:
        routine = _store().get_routine(routine_id)
        owners = _owners_for(viewer)
        if routine is None or (
            owners is not None and routine.get("owner_account_id") not in owners
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "routine_not_found", "message": f"例程不存在：{routine_id}"},
            )
        return routine

    def _require_root(viewer: Any) -> str:
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "not_owner", "message": "只有 Owner 能管理例程"},
            )
        return _principal(viewer)

    @router.post("", status_code=201)
    def create_routine(payload: RoutineCreateRequest, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        trigger_type = (payload.trigger_type or "").strip()
        if trigger_type not in ALLOWED_TRIGGERS:
            if trigger_type in ("new_email", "new_message", "calendar_event"):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "trigger_not_ready",
                        "message": f"触发器「{trigger_type}」还没接，现在只有定时和 webhook 两种",
                    },
                )
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_trigger", "message": f"没有这种触发器：{trigger_type}"},
            )
        action_kind = (payload.action_kind or "").strip()
        if action_kind not in ALLOWED_ACTIONS:
            if action_kind == "agent_goal":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "action_not_ready",
                        "message": "让贾维斯替你干活的例程动作还没接，现在只有“发消息”、“建草稿计划”和“定时查询”三种",
                    },
                )
            raise HTTPException(
                status_code=422,
                detail={"code": "bad_action", "message": f"没有这种例程动作：{action_kind}"},
            )
        schedule = (payload.schedule or "").strip()
        timezone_name = (payload.timezone or "Asia/Shanghai").strip()
        plan_steps: List[Dict[str, Any]] = []
        try:
            if trigger_type == TRIGGER_SCHEDULE:
                CronSchedule(schedule)
                resolve_timezone(timezone_name)
            if action_kind == ACTION_CHAT_MESSAGE and not (payload.action_text or "").strip():
                raise RoutineError(422, "empty_action", "发消息的例程要先写一句话")
            if action_kind == ACTION_WEB_RESEARCH and not (payload.action_text or "").strip():
                raise RoutineError(422, "empty_action", "定时查询的例程要先写查询词")
            if action_kind == ACTION_CREATE_PLAN:
                plan_steps = validate_plan_steps(payload.plan_steps)
                if not (payload.plan_title or "").strip():
                    raise RoutineError(422, "empty_plan_title", "建计划的例程要先写计划标题")
        except RoutineError as exc:
            raise _http_error(exc)
        webhook_secret = generate_webhook_secret() if trigger_type == TRIGGER_WEBHOOK else ""
        try:
            routine = _store().create_routine(
                name=payload.name.strip(),
                owner_account_id=owner,
                created_by=owner,
                trigger_type=trigger_type,
                schedule=schedule,
                timezone_name=timezone_name,
                action_kind=action_kind,
                action_text=(payload.action_text or "").strip(),
                plan_title=(payload.plan_title or "").strip(),
                plan_goal=(payload.plan_goal or "").strip(),
                plan_steps=plan_steps,
                webhook_secret=webhook_secret,
            )
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.created",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine["routine_id"], "trigger_type": trigger_type},
            correlation_id=str(routine["routine_id"]),
        )
        # webhook 密钥只在创建时返回一次：之后再也查不到，丢了就重建例程。
        body: Dict[str, Any] = {"routine": routine}
        if webhook_secret:
            body["webhook_secret"] = webhook_secret
            body["webhook_url"] = f"/v1/routines/{routine['routine_id']}/webhook"
        return body

    @router.get("")
    def list_routines(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        routines = _store().list_routines(owners=_owners_for(viewer), limit=200)
        return {"routines": routines, "paused_all": _store().get_paused_all()}

    @router.get("/summary")
    def routines_summary(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owners = _owners_for(viewer)
        routines = _store().list_routines(owners=owners, limit=500)
        failures = _store().recent_failures(
            owners=owners, since=time.time() - 24 * 3600, limit=200
        )
        return {
            "total": len(routines),
            "enabled": sum(1 for item in routines if item.get("enabled")),
            "auto_paused": sum(1 for item in routines if item.get("auto_paused")),
            "failed_24h": len(failures),
            "paused_all": _store().get_paused_all(),
        }

    @router.get("/settings")
    def get_settings(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        _require_root(viewer)
        return {"paused_all": _store().get_paused_all()}

    @router.post("/settings/pause-all")
    def set_pause_all(payload: PauseAllRequest, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        paused = _store().set_paused_all(bool(payload.paused))
        _audit(
            "routine.pause_all",
            principal_id=owner,
            outcome="success",
            details={"paused": paused},
            correlation_id="routines",
        )
        return {"paused_all": paused}

    @router.get("/{routine_id}")
    def get_routine(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        routine = _require_routine(routine_id, viewer)
        runs = _store().list_runs(routine_id, limit=20)
        return {"routine": routine, "runs": runs}

    @router.get("/{routine_id}/runs")
    def list_runs(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        _require_routine(routine_id, viewer)
        return {"runs": _store().list_runs(routine_id, limit=100)}

    @router.patch("/{routine_id}")
    def update_routine(routine_id: str, payload: RoutineUpdateRequest,
                       viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """改名 / 改时间：改名真的存库；改时间只对定时例程有效，新时间
        过 cron 校验，下次到点按新时间从现在重算；开关、失败计数、运行
        记录、错过规则都不动。"""
        owner = _require_root(viewer)
        _require_routine(routine_id, viewer)
        if payload.name is None and payload.schedule is None and payload.timezone is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "nothing_to_update", "message": "没传要改的东西：名字或时间至少传一个"},
            )
        try:
            updated = _store().update_routine_details(
                routine_id,
                name=payload.name,
                schedule=payload.schedule,
                timezone_name=payload.timezone,
            )
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.updated",
            principal_id=owner,
            outcome="success",
            details={
                "routine_id": routine_id,
                "changed": [
                    field
                    for field, value in (
                        ("name", payload.name),
                        ("schedule", payload.schedule),
                        ("timezone", payload.timezone),
                    )
                    if value is not None
                ],
            },
            correlation_id=routine_id,
        )
        return {"routine": updated}

    @router.post("/{routine_id}/enable")
    def enable_routine(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        routine = _require_routine(routine_id, viewer)
        try:
            # 重新打开 = Joseph 看过了：失败计数与自动暂停一起清零，运行记录还在。
            updated = _store().update_routine(
                routine_id, enabled=1, auto_paused=0, consecutive_failures=0
            )
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.enabled",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine_id},
            correlation_id=routine_id,
        )
        return {"routine": updated}

    @router.post("/{routine_id}/disable")
    def disable_routine(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        _require_routine(routine_id, viewer)
        try:
            updated = _store().update_routine(routine_id, enabled=0)
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.disabled",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine_id},
            correlation_id=routine_id,
        )
        return {"routine": updated}

    @router.post("/{routine_id}/run")
    def run_routine_now(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        routine = _require_routine(routine_id, viewer)
        if not routine.get("enabled"):
            raise HTTPException(
                status_code=409,
                detail={"code": "routine_disabled", "message": "例程已停用，先打开再手动运行"},
            )
        if _store().get_paused_all():
            raise HTTPException(
                status_code=409,
                detail={"code": "routines_paused_all", "message": "全部例程已暂停，先恢复再手动运行"},
            )
        record = RoutineRunner(_store()).run_once(routine, trigger="manual")
        return {"run": record, "routine": _store().get_routine(routine_id)}

    @router.delete("/{routine_id}")
    def delete_routine(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        owner = _require_root(viewer)
        _require_routine(routine_id, viewer)
        try:
            _store().delete_routine(routine_id)
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.deleted",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine_id},
            correlation_id=routine_id,
        )
        return {"deleted": True, "routine_id": routine_id}

    @router.post("/{routine_id}/webhook/revoke")
    def revoke_webhook(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """吊销 webhook 密钥（P3）：旧密钥立即失效，此后调用都 401。"""
        owner = _require_root(viewer)
        _require_routine(routine_id, viewer)
        try:
            routine = _store().revoke_webhook_secret(routine_id)
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.webhook_revoked",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine_id},
            correlation_id=routine_id,
        )
        return {"revoked": True, "routine": routine}

    @router.post("/{routine_id}/webhook/rotate")
    def rotate_webhook(routine_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """轮换 webhook 密钥（P3）：新密钥只返回一次明文，旧密钥立即失效。"""
        owner = _require_root(viewer)
        _require_routine(routine_id, viewer)
        try:
            result = _store().rotate_webhook_secret(routine_id)
        except RoutineError as exc:
            raise _http_error(exc)
        _audit(
            "routine.webhook_rotated",
            principal_id=owner,
            outcome="success",
            details={"routine_id": routine_id},
            correlation_id=routine_id,
        )
        return {
            "rotated": True,
            "routine": result["routine"],
            "webhook_secret": result["webhook_secret"],
        }

    @router.post("/{routine_id}/webhook")
    def webhook_trigger(
        routine_id: str,
        x_routine_secret: Optional[str] = Header(default=None),
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """webhook 触发：不走人类登录，只认密钥。密钥错了 401 并记审计。"""
        routine = _store().get_routine(routine_id)
        if routine is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "routine_not_found", "message": f"例程不存在：{routine_id}"},
            )
        secret = (x_routine_secret or "").strip()
        if not secret or not _store().verify_webhook_secret(routine_id, secret):
            _audit(
                "routine.webhook_denied",
                principal_id="webhook",
                outcome="deny",
                details={"routine_id": routine_id},
                correlation_id=routine_id,
            )
            raise HTTPException(
                status_code=401,
                detail={"code": "bad_webhook_secret", "message": "webhook 密钥不对"},
            )
        if not routine.get("enabled"):
            raise HTTPException(
                status_code=409,
                detail={"code": "routine_disabled", "message": "例程已停用，webhook 不触发"},
            )
        if _store().get_paused_all():
            record = _store().record_run(
                routine_id=routine_id,
                trigger="webhook",
                reason="webhook 调用（已跳过：全局暂停）",
                status="skipped",
                result_text="到点但全局暂停，没执行，不补跑。",
                started_at=time.time(),
                ended_at=time.time(),
            )
            return {"run": record}
        record = RoutineRunner(_store()).run_once(
            routine, trigger="webhook", reason="webhook 调用"
        )
        return {"run": record}

    return router
