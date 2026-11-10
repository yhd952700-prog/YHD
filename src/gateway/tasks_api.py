"""任务台账 HTTP 接口（``/v1/tasks*``）。

为什么放在 ``src/gateway/`` 而不是直接用 ``src/api/tasks_api.py``
==============================================================
``src/api/tasks_api.py`` 是 LEGACY 内存 scaffold（未被网关挂载，且
``tests/test_api_extended.py`` 仍在导入它）。本模块是**真正挂在网关上的**
任务台账接口：由 ``ai_management.router.include_router`` 编进既有的 ``/v1``
路由，因此：

* 不新增 ``main.py`` 挂载点（避免和正在做组织架构数据层的线冲突）；
* 复用同一道人类操作员鉴权（主应用的 ``require_human_principal``）；
* 复用同一套 ``GoalViewer`` 可见范围：普通账号只能看见自己子树内的任务，
  别人的任务一律 404（与 ``/v1/goals`` 完全同口径，不泄露存在性）。

端点
====
* ``GET  /v1/tasks``                     按状态/负责人/目标查询 + 分页
* ``GET  /v1/tasks/{task_id}``           任务详情（含子步骤与执行记录）
* ``GET  /v1/tasks/{task_id}/steps``     子步骤
* ``GET  /v1/tasks/{task_id}/runs``      执行记录（最新在前）
* ``POST /v1/tasks/{task_id}/retry``     失败重试（委托现有 ``retry_task``）
* ``POST /v1/tasks/{task_id}/takeover``  人工接管（执行中 409）
* ``GET  /v1/tasks/ledger/status``       台账概览 + 落后自检（仅根账号）
* ``POST /v1/tasks/ledger/migrate``      幂等迁移（仅根账号）
* ``POST /v1/tasks/ledger/repair``       写穿透失败后的补偿（仅根账号）
* ``POST /v1/tasks/ledger/rollback``     回滚一次迁移（仅根账号）
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .task_ledger import ALL_STATUSES, LedgerError, get_task_ledger

logger = logging.getLogger(__name__)


class RetryRequest(BaseModel):
    step_key: Optional[str] = Field(
        default=None,
        description="要重试的子步骤 key（内核 task id）；缺省取第一个失败步骤",
    )
    reason: Optional[str] = Field(default=None, max_length=500)


class TakeoverRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class MigrateRequest(BaseModel):
    force: bool = Field(default=False, description="源指纹未变也重跑一次")


class RollbackRequest(BaseModel):
    migration_id: str = Field(min_length=1)


def _no_viewer() -> None:  # pragma: no cover - 仅在没有鉴权依赖时兜底
    return None


def _http_from_ledger(exc: LedgerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


def _http_from_goal(exc: BaseException) -> HTTPException:
    status_code = int(getattr(exc, "status_code", 500) or 500)
    code = str(getattr(exc, "code", "goal_action_failed") or "goal_action_failed")
    detail = str(getattr(exc, "detail", None) or getattr(exc, "message", "") or exc)
    return HTTPException(status_code=status_code, detail={"code": code, "message": detail})


def _owners_for(viewer: Any) -> Optional[List[str]]:
    """把请求视角翻译成 SQL 可见 owner 集合（与 ``GoalViewer.sees`` 同口径）。"""
    from .ai_management import _as_viewer, default_account_id

    real = _as_viewer(viewer)
    if real is None:
        return None  # 内部调用/测试：不做成员收敛（与 goals 旧行为一致）
    owners = {real.principal, *[str(item) for item in (real.subtree or ())]}
    if real.standalone:
        owners.add(default_account_id())
    return sorted(item for item in owners if item)


def _scope_label(viewer: Any) -> str:
    from .ai_management import _visible_scope

    return _visible_scope(viewer)


def _require_visible(ledger, viewer: Any, task_id: str) -> Dict[str, Any]:
    task = ledger.get_task(task_id)
    real_owner_ok = task is not None
    if real_owner_ok:
        from .ai_management import _as_viewer

        real = _as_viewer(viewer)
        if real is not None and not real.sees(task):
            real_owner_ok = False
    if not real_owner_ok:
        # 不可见与不存在同一响应：不泄露 id 是否存在。
        raise HTTPException(
            status_code=404,
            detail={"code": "task_not_found", "message": f"任务不存在：{task_id}"},
        )
    return task


def _require_root(viewer: Any) -> None:
    from .ai_management import _as_viewer

    real = _as_viewer(viewer)
    if real is not None and not real.is_root:
        raise HTTPException(
            status_code=403,
            detail={"code": "root_required", "message": "台账运维操作仅限主账号"},
        )


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    """构造台账路由。

    ``viewer_dependency`` 由 ``ai_management`` 注入（同一个 ``_goal_viewer``），
    测试可注入自己的 viewer；不传则不做可见性收敛。
    """
    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/tasks", tags=["task-ledger"])

    def _ledger():
        return get_task_ledger()

    # ── 运维/自检（静态路径，先于 /{task_id} 注册）────────────

    @router.get("/ledger/status")
    def ledger_status(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """台账概览 + 落后自检：缺多少条、偏差多少条、写穿透失败几次。"""
        _require_root(viewer)
        from .ai_management import AIStateManager

        try:
            return AIStateManager().task_ledger_status()
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc

    @router.post("/ledger/migrate")
    def ledger_migrate(
        req: Optional[MigrateRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """幂等迁移（同一源指纹只导入一次；force=True 重跑补新目标）。"""
        _require_root(viewer)
        from .ai_management import AIStateManager

        body = req or MigrateRequest()
        try:
            return AIStateManager().task_ledger_migrate(force=bool(body.force))
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc

    @router.post("/ledger/repair")
    def ledger_repair(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """写穿透失败后的补偿：按热文件重放投影（幂等）。"""
        _require_root(viewer)
        from .ai_management import AIStateManager

        try:
            return AIStateManager().task_ledger_repair()
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc

    @router.post("/ledger/rollback")
    def ledger_rollback(
        req: RollbackRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """回滚一次迁移：删掉该次导入的台账行，源文件不受影响。"""
        _require_root(viewer)
        try:
            return _ledger().rollback_migration(req.migration_id)
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc

    # ── 查询 ────────────────────────────────────────────────

    @router.get("")
    def list_tasks(
        status: Optional[str] = Query(default=None),
        assignee_type: Optional[str] = Query(default=None),
        assignee_id: Optional[str] = Query(default=None),
        assignee_name: Optional[str] = Query(default=None),
        goal_id: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        sort_by: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        if status is not None and status not in ALL_STATUSES:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_status",
                    "message": f"未知状态：{status}；可选 {', '.join(ALL_STATUSES)}",
                },
            )
        try:
            result = _ledger().list_tasks(
                status=status,
                assignee_type=assignee_type,
                assignee_id=assignee_id,
                assignee_name=assignee_name,
                goal_id=goal_id,
                owners=_owners_for(viewer),
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc
        result["visible_scope"] = _scope_label(viewer)
        return result

    @router.get("/{task_id}")
    def get_task(
        task_id: str,
        run_limit: int = Query(default=50, ge=1, le=200),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        ledger = _ledger()
        _require_visible(ledger, viewer, task_id)
        detail = ledger.task_detail(task_id, run_limit=run_limit)
        if detail is None:  # pragma: no cover - 竞态下被删
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": f"任务不存在：{task_id}"},
            )
        return detail

    @router.get("/{task_id}/steps")
    def list_steps(
        task_id: str, viewer: Any = Depends(dependency)
    ) -> Dict[str, Any]:
        ledger = _ledger()
        _require_visible(ledger, viewer, task_id)
        steps = ledger.list_steps(task_id)
        return {"task_id": task_id, "count": len(steps), "steps": steps}

    @router.get("/{task_id}/runs")
    def list_runs(
        task_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        ledger = _ledger()
        _require_visible(ledger, viewer, task_id)
        runs = ledger.list_runs(task_id, limit=limit)
        return {"task_id": task_id, "count": len(runs), "runs": runs}

    # ── 动作 ────────────────────────────────────────────────

    @router.post("/{task_id}/retry")
    def retry_task_endpoint(
        task_id: str,
        req: Optional[RetryRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """失败重试：委托 ``AIStateManager.retry_task``，不另造执行路径。"""
        from .ai_management import AIStateManager, GoalWorkError, _as_viewer

        body = req or RetryRequest()
        ledger = _ledger()
        task = _require_visible(ledger, viewer, task_id)
        real = _as_viewer(viewer)
        if task.get("human_hold"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "human_takeover_active",
                    "message": "任务已被人工接管，自动重试不可用",
                },
            )
        steps = ledger.list_steps(task_id)
        step = None
        if body.step_key:
            step = next(
                (item for item in steps if item["step_key"] == body.step_key), None
            )
            if step is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "step_not_found",
                        "message": f"任务 {task_id} 下不存在子步骤 {body.step_key}",
                    },
                )
            if step.get("status") != "failed":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "step_not_retryable",
                        "message": f"子步骤当前状态为 {step.get('status')}，只有失败步骤可重试",
                    },
                )
        else:
            step = next(
                (item for item in steps if item.get("status") == "failed"), None
            )
            if step is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "no_retryable_step",
                        "message": (
                            "该任务没有失败的子步骤；若失败发生在拆解阶段，"
                            "请重新提交目标或使用 replan"
                        ),
                    },
                )
        principal = real.principal if real is not None else ""
        try:
            ledger.record_retry_request(
                task_id, step_key=step["step_key"],
                principal=principal, reason=body.reason or "",
            )
            entry = AIStateManager().retry_task(
                goal_id=str(task["goal_id"]),
                task_id=str(step["step_key"]),
                principal=principal,
                viewer=real,
            )
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc
        except GoalWorkError as exc:
            raise _http_from_goal(exc) from exc
        return {
            "task_id": task_id,
            "step_key": step["step_key"],
            "goal_state": entry.get("state"),
            "task": ledger.get_task(task_id),
        }

    @router.post("/{task_id}/takeover")
    def takeover_task(
        task_id: str,
        req: Optional[TakeoverRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """人工接管：转给人类操作员，状态落"待执行"，不伪造执行。"""
        from .ai_management import _as_viewer

        body = req or TakeoverRequest()
        real = _as_viewer(viewer)
        if real is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "principal_required", "message": "人工接管需要操作员身份"},
            )
        ledger = _ledger()
        _require_visible(ledger, viewer, task_id)
        try:
            task = ledger.takeover(
                task_id, principal=real.principal, reason=body.reason or ""
            )
        except LedgerError as exc:
            raise _http_from_ledger(exc) from exc
        return {"task": task}

    return router


#: 无鉴权依赖时自测用（部署态由 ai_management 注入真实 ``_goal_viewer``）
router = build_router()
