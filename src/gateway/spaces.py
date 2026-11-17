"""协作空间 HTTP API（0.4.6-big A1 组）。

空间 = 名字 + 一句话介绍 + 成员 + 任务墙。此前只存在于预览里，
全仓库搜不到"协作空间"；本模块是它第一次进真代码：数据落 SQLite
（``spaces.db``，随现有数据目录走），重启/刷新还在。

可见范围按现有 Owner/成员机制过滤（与文件/任务台账同口径）：
根账号（Joseph）见子树内所有空间，普通用户只见自己创建的、
或自己是成员的空间；看不见与不存在一律 404，不泄露存在性。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..common.input_guard import normalize_required_text
from ..spaces.store import SPACE_TASK_STATUSES, get_space_store
from .account_context import AccountContext, deny, require_account_principal
from ..kernels.identity import _accounts

router = APIRouter(prefix="/v1/spaces", tags=["spaces"])


class CreateSpaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="空间名字")
    intro: str = Field(default="", max_length=200, description="一句话介绍")


class SpaceTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="任务墙条目标题")
    assignee: Optional[str] = Field(default=None, max_length=128, description="负责人（账号名，可空）")


class SpaceTaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    status: Optional[str] = Field(default=None, description="todo/doing/done")
    assignee: Optional[str] = Field(default=None, max_length=128)


class SpaceMemberRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=128, description="要拉进空间的账号")
    role: str = Field(default="member", description="member 或 admin")


def _neutral_not_found() -> HTTPException:
    return deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def _principals(context: AccountContext) -> List[str]:
    """当前请求能看到的账号集合：根账号是整棵子树，成员只有自己。"""
    if context.is_root:
        return sorted({context.principal, *[str(item) for item in context.subtree]})
    return [context.principal]


def _visible_space(context: AccountContext, space_id: str) -> Dict[str, Any]:
    space = get_space_store().get_space(space_id, principals=_principals(context))
    if space is None:
        raise _neutral_not_found()
    return space


def _can_act(context: AccountContext, space: Dict[str, Any]) -> bool:
    """能在空间里加任务/加人：创建者、成员，或能读该空间的根账号。"""
    if str(space.get("owner") or "") == context.principal:
        return True
    members = {str(item.get("principal") or "") for item in space.get("members") or []}
    if context.principal in members:
        return True
    return bool(
        context.is_root and context.can_read(str(space.get("owner") or ""))
    )


def _require_act(context: AccountContext, space: Dict[str, Any]) -> None:
    if not _can_act(context, space):
        raise HTTPException(
            status_code=403,
            detail={"code": "space_not_member", "message": "你不是这个空间的成员，不能改它。"},
        )


def _require_owner_or_root(context: AccountContext, space: Dict[str, Any]) -> None:
    """删空间/管成员：只限创建者，或能读该空间的根账号。"""
    if str(space.get("owner") or "") == context.principal:
        return
    if context.is_root and context.can_read(str(space.get("owner") or "")):
        return
    raise HTTPException(
        status_code=403,
        detail={"code": "space_owner_required", "message": "只有空间创建者能做这件事。"},
    )


@router.post("", status_code=201)
def create_space(
    body: CreateSpaceRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """创建空间（名字 + 一句话介绍）；创建者自动成为成员（owner）。"""
    try:
        name = normalize_required_text(body.name, "空间名字", max_chars=64)
        intro = normalize_required_text(body.intro, "空间介绍", max_chars=200) if body.intro.strip() else ""
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    space = get_space_store().create_space(context.principal, name, intro)
    return {"space": space}


@router.get("")
def list_spaces(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """空间列表（根账号见子树全部，普通用户只见自己的/自己加入的）。"""
    spaces = get_space_store().list_spaces(_principals(context))
    return {"spaces": spaces, "count": len(spaces)}


@router.get("/{space_id}")
def get_space(
    space_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """点开一个空间：详情 + 任务墙 + 成员。"""
    return {"space": _visible_space(context, space_id)}


@router.delete("/{space_id}")
def delete_space(
    space_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """删除空间（连同任务墙与成员一起删；只有创建者或根账号能删）。"""
    space = _visible_space(context, space_id)
    _require_owner_or_root(context, space)
    get_space_store().delete_space(space_id)
    return {"space_id": space_id, "deleted": True}


@router.post("/{space_id}/tasks", status_code=201)
def add_space_task(
    space_id: str,
    body: SpaceTaskRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """往任务墙上加一条（todo 起步）。"""
    space = _visible_space(context, space_id)
    _require_act(context, space)
    try:
        item = get_space_store().add_task(
            space_id, body.title, context.principal, assignee=body.assignee or ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:  # pragma: no cover - 竞态下空间被删
        raise _neutral_not_found()
    return {"task": item}


@router.patch("/{space_id}/tasks/{item_id}")
def update_space_task(
    space_id: str,
    item_id: str,
    body: SpaceTaskUpdate,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """改任务墙条目：改标题 / 改状态（todo/doing/done）/ 改负责人。"""
    space = _visible_space(context, space_id)
    _require_act(context, space)
    changes: Dict[str, Any] = {}
    if body.title is not None:
        changes["title"] = body.title
    if body.status is not None:
        changes["status"] = body.status
    if body.assignee is not None:
        changes["assignee"] = body.assignee
    if not changes:
        item = get_space_store().get_task(item_id, space_id)
        if item is None:
            raise HTTPException(status_code=404, detail="任务墙里没有这一条")
        return {"task": item}
    try:
        item = get_space_store().update_task(item_id, space_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="任务墙里没有这一条")
    return {"task": item}


@router.delete("/{space_id}/tasks/{item_id}")
def delete_space_task(
    space_id: str,
    item_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """从任务墙上删一条。"""
    space = _visible_space(context, space_id)
    _require_act(context, space)
    if not get_space_store().delete_task(item_id, space_id):
        raise HTTPException(status_code=404, detail="任务墙里没有这一条")
    return {"item_id": item_id, "deleted": True}


@router.post("/{space_id}/members", status_code=201)
def add_space_member(
    space_id: str,
    body: SpaceMemberRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """拉人进空间（只限创建者或根账号；只能拉自己子树里的人）。"""
    space = _visible_space(context, space_id)
    _require_owner_or_root(context, space)
    target = str(body.principal or "").strip()
    if target != context.principal and not context.can_read(target):
        # 成员拉"自己树外的人"与"不存在的人"同一响应，不泄露账号存在性。
        raise _neutral_not_found()
    try:
        member = get_space_store().add_member(space_id, target, role=body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if member is None:  # pragma: no cover - 竞态下空间被删
        raise _neutral_not_found()
    return {"member": member}


@router.delete("/{space_id}/members/{principal}")
def remove_space_member(
    space_id: str,
    principal: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """把人移出空间（空间创建者不能被移出）。"""
    space = _visible_space(context, space_id)
    _require_owner_or_root(context, space)
    if not get_space_store().remove_member(space_id, principal):
        raise HTTPException(
            status_code=409,
            detail={"code": "space_member_remove_failed", "message": "移不出去：空间创建者不能被移出，或这个人本来就不在空间里。"},
        )
    return {"space_id": space_id, "principal": principal, "removed": True}


__all__ = ["router", "SPACE_TASK_STATUSES"]
