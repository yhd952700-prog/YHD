"""小队网关（0.4.6，Muse+Grok 融合后端）。

Joseph 只跟一个 Jarvis 说话：``POST /v1/team/tasks`` 交一句话任务，
Jarvis 拆成子任务派给内部 worker；``GET /v1/team/tasks/{id}`` 看每
个子任务的进度（读的时候先把审批中心的结论同步回来）；需要拍板的子
任务在审批中心（``/v1/approvals``）里等 Joseph 点，点完再读本接口就
能看到结论。

没有「Bot 列表」接口：worker 不对外设入口，不能被一个一个指派。

小队成员（``/v1/team/members``）是另一回事：派活按 ``kind`` 对应的成员
执行，成员可新增、停用/启用、换模型（存在 ``team_members`` 表里）。
空白开局：首次打开不再播种 4 人，成员只在派活时按需建记录或手动新增后
出现；停用的成员对应的子任务转拍板问人。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..team import advance_task, create_team_task, get_team_store, sync_decisions
from .policy import require_human_principal

router = APIRouter(prefix="/v1/team", tags=["team"])


class TeamTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="一句话任务")
    auto_advance: bool = Field(
        True, description="建完立即推进：能做的子任务马上做，需要拍板的马上进审批中心"
    )


class TeamMemberRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=64, description="成员类型（如 research/files/reminder/code，或自定）")
    name: str = Field(..., min_length=1, max_length=64, description="成员名字")
    model_id: Optional[str] = Field(
        default=None, max_length=128, description="该成员用的模型配置 id；空=未指定，用默认模型"
    )


class TeamMemberUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    kind: Optional[str] = Field(default=None, min_length=1, max_length=64)
    model_id: Optional[str] = Field(default=None, max_length=128)
    active: Optional[bool] = Field(default=None, description="停用传 false，启用传 true")


def _public_member(member: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "member_id": str(member.get("member_id") or ""),
        "kind": str(member.get("kind") or ""),
        "name": str(member.get("name") or ""),
        "model_id": member.get("model_id"),
        "active": bool(member.get("active")),
        "created_at": member.get("created_at"),
        "updated_at": member.get("updated_at"),
    }


def _push_refresh(principal: str) -> None:
    """A2b 第 16 项：小队状态变了，把真实总览/小队快照推上 WS。

    推送失败不影响本次操作（只记日志），没变化不推、下次变化再推。
    """
    try:
        from .a2b import publish_push_refresh

        publish_push_refresh(principal)
    except Exception:  # noqa: BLE001 - 推送失败不影响成员/任务操作
        import logging

        logging.getLogger(__name__).warning("team push refresh failed", exc_info=True)


@router.get("/members", response_model=Dict[str, Any])
def list_members(principal: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """我的小队成员（空白开局：全新状态下是空列表；成员只在派活时按需建记录，或手动新增）。"""
    store = get_team_store()
    return {"members": [_public_member(item) for item in store.list_members(principal)]}


@router.post("/members", status_code=201, response_model=Dict[str, Any])
def create_member(
    req: TeamMemberRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """新增一个小队成员（类型 + 名字 + 可选模型）。"""
    store = get_team_store()
    try:
        member = store.create_member(
            principal, req.kind, req.name, model_id=req.model_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _push_refresh(principal)
    return {"member": _public_member(member)}


@router.patch("/members/{member_id}", response_model=Dict[str, Any])
def update_member(
    member_id: str,
    req: TeamMemberUpdate,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """改成员：改名、换模型、停用/启用。只能改自己的成员。"""
    store = get_team_store()
    changes: Dict[str, Any] = {}
    if req.name is not None:
        changes["name"] = req.name
    if req.kind is not None:
        changes["kind"] = req.kind
    if req.model_id is not None:
        changes["model_id"] = req.model_id
    if req.active is not None:
        changes["active"] = req.active
    if not changes:
        member = store.get_member(member_id, principal)
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        return {"member": _public_member(member)}
    try:
        member = store.update_member(member_id, principal, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    _push_refresh(principal)
    return {"member": _public_member(member)}


@router.post("/tasks", response_model=Dict[str, Any])
def create_task(
    req: TeamTaskRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """交任务给 Jarvis：拆分子任务，派给内部 worker。"""
    store = get_team_store()
    try:
        task = create_team_task(store, req.title, principal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.auto_advance:
        task = advance_task(store, str(task["task_id"]), principal)
    _push_refresh(principal)
    return {"task": task}


@router.get("/tasks", response_model=Dict[str, Any])
def list_tasks(
    limit: int = 20, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """我的小队任务列表（只看自己的）。"""
    store = get_team_store()
    return {"tasks": store.list_tasks(created_by=principal, limit=max(1, min(100, limit)))}


@router.get("/tasks/{task_id}", response_model=Dict[str, Any])
def get_task(
    task_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """一单的详情 + 子任务进度（先同步审批中心的结论）。"""
    store = get_team_store()
    task = store.get_task(task_id)
    if task is None or str(task.get("created_by")) != principal:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = sync_decisions(store, task_id)
    return {"task": task}


@router.get("/tasks/{task_id}/events", response_model=Dict[str, Any])
def get_task_events(
    task_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """一单的进度事件流（聊天页轮询它来显示子任务进度）。"""
    store = get_team_store()
    task = store.get_task(task_id)
    if task is None or str(task.get("created_by")) != principal:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"events": store.list_events(task_id)}


@router.post("/tasks/{task_id}/advance", response_model=Dict[str, Any])
def advance(
    task_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """再推进一次（只跑还排队的子任务；已拍板 / 已完成的不会重跑）。"""
    store = get_team_store()
    task = store.get_task(task_id)
    if task is None or str(task.get("created_by")) != principal:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = advance_task(store, task_id, principal)
    _push_refresh(principal)
    return {"task": result}


__all__ = ["router"]
