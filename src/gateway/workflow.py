"""工作流编排 HTTP 接口（``/v1/workflow*``）。

设计边界
========
* 这里**只做 HTTP 翻译**：校验 → store → runtime，不写业务状态机。
* 鉴权复用主应用挂在 ``include_router`` 层的人类主权闸门；可见范围复用
  ``ai_management._as_viewer`` 的同一口径（看不见与不存在同为 404）。
* 路由前缀用 ``/v1/workflow``（单数）而不是 ``/v1/workflows``：后者已被
  ``GET /v1/workflows/{goal_id}`` 占用，``/runs`` 会被它先匹配走。
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.workflow.definition import (
    DefinitionError,
    NODE_TYPES,
    NODE_TYPE_LABELS,
    builtin_submit_approve_execute_archive,
    normalise_definition,
)
from src.workflow.engine import get_workflow_runtime
from src.workflow.scheduler import get_workflow_scheduler
from src.workflow.store import StoreError, get_workflow_store

logger = logging.getLogger(__name__)


class EnableRequest(BaseModel):
    enabled: bool = Field(description="true=启用，false=停用（停用后不能再启动新运行）")


class DuplicateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)


class RunRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict, description="本次运行的输入变量")
    trigger_payload: Dict[str, Any] = Field(default_factory=dict)


class FireEventRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


def _no_viewer() -> None:  # pragma: no cover - 没有注入鉴权时的兜底
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


def _viewer_identity(viewer: Any) -> str:
    from .ai_management import _as_viewer

    real = _as_viewer(viewer)
    if real is None:
        return ""
    return str(real.principal or "")


def _is_root(viewer: Any) -> bool:
    from .ai_management import _as_viewer

    real = _as_viewer(viewer)
    return bool(real is not None and real.is_root)


def _http_from(exc: Exception) -> HTTPException:
    if isinstance(exc, DefinitionError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail())
    if isinstance(exc, StoreError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail())
    status_code = int(getattr(exc, "status_code", 500) or 500)
    code = str(getattr(exc, "code", "workflow_failed") or "workflow_failed")
    message = str(getattr(exc, "detail", None) or getattr(exc, "message", "") or exc)
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        from datetime import datetime

        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _stamp_times(item: Dict[str, Any]) -> Dict[str, Any]:
    for source, target in (
        ("created_at", "created_at_text"),
        ("updated_at", "updated_at_text"),
        ("started_at", "started_at_text"),
        ("finished_at", "finished_at_text"),
        ("expires_at", "expires_at_text"),
        ("decided_at", "decided_at_text"),
        ("last_fired_at", "last_fired_at_text"),
        ("next_due_at", "next_due_at_text"),
    ):
        if source in item:
            item[target] = _iso(item[source])
    return item


def _definition_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    definition = record.get("definition") or {}
    nodes = definition.get("nodes") or []
    triggers = definition.get("triggers") or []
    item = {
        "def_id": record["def_id"],
        "name": record.get("name", ""),
        "description": record.get("description", ""),
        "enabled": bool(record.get("enabled", True)),
        "version": int(record.get("version") or 1),
        "owner_account_id": record.get("owner_account_id", ""),
        "created_by": record.get("created_by", ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "node_count": len(nodes),
        "node_types": sorted({str(node.get("type")) for node in nodes if node.get("type")}),
        "edge_count": len(definition.get("edges") or []),
        "triggers": triggers,
        "nodes": nodes,
        "edges": definition.get("edges") or [],
        "variables": definition.get("variables") or {},
    }
    return _stamp_times(item)


def _run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    definition = run.get("definition") or {}
    return _stamp_times(
        {
            "run_id": run["run_id"],
            "def_id": run.get("def_id", ""),
            "def_version": run.get("def_version", 1),
            "name": run.get("name", ""),
            "status": run.get("status", ""),
            "outcome": run.get("outcome", ""),
            "trigger_type": run.get("trigger_type", "manual"),
            "trigger_payload": run.get("trigger_payload") or {},
            "inputs": run.get("inputs") or {},
            "owner_account_id": run.get("owner_account_id", ""),
            "submitted_by": run.get("submitted_by", ""),
            "current_node_id": run.get("current_node_id"),
            "error": run.get("error"),
            "error_code": run.get("error_code"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "node_count": len(definition.get("nodes") or []),
        }
    )


def _await_settled(store: Any, run_id: str, *, timeout: float = 1.2) -> Optional[Dict[str, Any]]:
    """短暂等待运行离开 pending/running，让"立刻卡审批"的流程在响应里可见。"""
    deadline = time.time() + max(0.0, timeout)
    run = store.get_run(run_id)
    while run is not None and run.get("status") in ("pending", "running"):
        if time.time() >= deadline:
            break
        time.sleep(0.02)
        run = store.get_run(run_id)
    return run


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/v1/workflow", tags=["workflow"])

    # 路由装配即启动调度线程：这是应用启动时唯一一定会走到、且属于本线的钩子。
    # 不启动的话，interval/daily 触发器只有在有人手动点 /triggers/tick 时才走，
    # 「定时触发」就成了摆设。`LIUHAO_WORKFLOW_SCHEDULER=0` 时构造器不会起线程，
    # 测试与手动模式仍然只保留 tick()。
    get_workflow_scheduler()

    def _store():
        return get_workflow_store()

    def _runtime():
        return get_workflow_runtime()

    def _require_definition(def_id: str, viewer: Any) -> Dict[str, Any]:
        record = _store().get_definition(def_id)
        owners = _owners_for(viewer)
        if record is None or (owners is not None and record.get("owner_account_id") not in owners):
            raise HTTPException(
                status_code=404,
                detail={"code": "workflow_not_found", "message": f"流程不存在：{def_id}"},
            )
        return record

    def _require_run(run_id: str, viewer: Any) -> Dict[str, Any]:
        run = _store().get_run(run_id)
        owners = _owners_for(viewer)
        if run is None or (owners is not None and run.get("owner_account_id") not in owners):
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": f"运行实例不存在：{run_id}"},
            )
        return run

    # ── 概览 / 模板 ─────────────────────────────────────────

    @router.get("/summary")
    def workflow_summary(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        return _store().summary(owners=_owners_for(viewer))

    @router.get("/node-types")
    def node_types(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        return {
            "types": [
                {"type": node_type, "label": NODE_TYPE_LABELS[node_type]}
                for node_type in NODE_TYPES
            ]
        }

    @router.get("/templates")
    def templates(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        return {"templates": [builtin_submit_approve_execute_archive()]}

    # ── 定义 ────────────────────────────────────────────────

    @router.get("/definitions")
    def list_definitions(
        include_disabled: bool = Query(default=True),
        limit: int = Query(default=100, ge=1, le=300),
        offset: int = Query(default=0, ge=0),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        records = _store().list_definitions(
            owners=_owners_for(viewer),
            include_disabled=include_disabled,
            limit=limit,
            offset=offset,
        )
        return {"definitions": [_definition_summary(record) for record in records]}

    @router.post("/definitions", status_code=201)
    def create_definition(
        payload: Dict[str, Any] = Body(...),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        principal = _viewer_identity(viewer)
        owners = _owners_for(viewer)
        owner = principal or (owners[0] if owners else "")
        try:
            definition = normalise_definition(
                payload, owner_account_id=owner, created_by=principal
            )
            record = _store().create_definition(
                definition, owner_account_id=owner, created_by=principal
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成 HTTP
            raise _http_from(exc)
        return {"definition": _definition_summary(record)}

    @router.get("/definitions/{def_id}")
    def get_definition(def_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        record = _require_definition(def_id, viewer)
        return {"definition": _definition_summary(record)}

    @router.put("/definitions/{def_id}")
    def update_definition(
        def_id: str,
        payload: Dict[str, Any] = Body(...),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        record = _require_definition(def_id, viewer)
        try:
            definition = normalise_definition(
                payload,
                owner_account_id=str(record.get("owner_account_id") or ""),
                created_by=str(record.get("created_by") or ""),
            )
            updated = _store().update_definition(def_id, definition)
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"definition": _definition_summary(updated)}

    @router.post("/definitions/{def_id}/enable")
    def enable_definition(
        def_id: str,
        payload: EnableRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        _require_definition(def_id, viewer)
        try:
            updated = _store().set_definition_enabled(def_id, payload.enabled)
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"definition": _definition_summary(updated)}

    @router.post("/definitions/{def_id}/duplicate", status_code=201)
    def duplicate_definition(
        def_id: str,
        payload: Optional[DuplicateRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        record = _require_definition(def_id, viewer)
        source = copy.deepcopy(record.get("definition") or {})
        source["name"] = (payload.name if payload and payload.name else f"{record.get('name', '')}（副本）")
        principal = _viewer_identity(viewer)
        try:
            definition = normalise_definition(
                source,
                owner_account_id=str(record.get("owner_account_id") or ""),
                created_by=principal or str(record.get("created_by") or ""),
            )
            created = _store().create_definition(
                definition,
                owner_account_id=str(record.get("owner_account_id") or ""),
                created_by=definition.get("created_by") or principal,
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"definition": _definition_summary(created)}

    @router.get("/definitions/{def_id}/export")
    def export_definition(def_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        record = _require_definition(def_id, viewer)
        definition = copy.deepcopy(record.get("definition") or {})
        for key in ("owner_account_id", "created_by"):
            definition.pop(key, None)
        return {
            "kind": "liuhao.workflow.definition",
            "schema_version": definition.get("schema_version", 1),
            "exported_at": _iso(__import__("time").time()),
            "definition": definition,
        }

    @router.post("/import", status_code=201)
    def import_definition(
        payload: Dict[str, Any] = Body(...),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        raw = payload.get("definition") if isinstance(payload.get("definition"), dict) else payload
        principal = _viewer_identity(viewer)
        owners = _owners_for(viewer)
        owner = principal or (owners[0] if owners else "")
        try:
            definition = normalise_definition(
                raw, owner_account_id=owner, created_by=principal
            )
            record = _store().create_definition(
                definition, owner_account_id=owner, created_by=principal
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"definition": _definition_summary(record)}

    # ── 运行实例 ────────────────────────────────────────────

    @router.post("/definitions/{def_id}/run", status_code=201)
    def run_definition(
        def_id: str,
        payload: Optional[RunRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        record = _require_definition(def_id, viewer)
        body = payload or RunRequest()
        try:
            run = _runtime().start_run(
                record,
                trigger_type="manual",
                trigger_payload=body.trigger_payload,
                inputs=body.inputs,
                submitted_by=_viewer_identity(viewer),
                owner_account_id=str(record.get("owner_account_id") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        # 让最常见的"第一步就卡审批"在响应里就体现出来，前端不用先闪一下 pending。
        run = _await_settled(_store(), str(run["run_id"]), timeout=1.2) or run
        return {"run": _run_summary(run)}

    @router.get("/runs")
    def list_runs(
        def_id: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        runs = _store().list_runs(
            owners=_owners_for(viewer),
            def_id=def_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {"runs": [_run_summary(run) for run in runs]}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        run = _require_run(run_id, viewer)
        steps = [_stamp_times(step) for step in _store().list_steps(run_id)]
        approvals = [
            _stamp_times(approval)
            for approval in _store().list_approvals(run_id=run_id, limit=50)
        ]
        return {"run": _run_summary(run), "steps": steps, "approvals": approvals}

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        _require_run(run_id, viewer)
        try:
            run = _runtime().cancel_run(
                run_id, cancelled_by=_viewer_identity(viewer) or "anonymous"
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"run": _run_summary(run)}

    # ── 触发 ────────────────────────────────────────────────

    @router.post("/triggers/tick")
    def trigger_tick(viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        if not _is_root(viewer):
            raise HTTPException(
                status_code=403,
                detail={"code": "root_required", "message": "只有主账号可以手动推进触发器"},
            )
        try:
            return get_workflow_scheduler().tick()
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)

    @router.post("/events/{event_name}/fire")
    def fire_event(
        event_name: str,
        payload: Optional[FireEventRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        body = payload or FireEventRequest()
        try:
            started = get_workflow_scheduler().fire_event(event_name, body.payload)
        except Exception as exc:  # noqa: BLE001
            raise _http_from(exc)
        return {"event": event_name, "started": started}

    return router
