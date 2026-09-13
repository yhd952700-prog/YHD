"""鎏灏 Policy Controlled 审批端点（内核层真拦截的**真实入口**）。

内核层 43 个 ``@kernel_action`` 的 HIGH/CRITICAL 动作在开启拦截后，必须由
**经核验的人类**授权才能执行（OD-010）。C-3 提供了 ``human_sovereign`` 上下文，
C-4 把授权本身变成可审计凭据。本模块提供那个凭据的**真实签发/查询/撤销入口**。

端点：

- ``POST   /v1/policy/approvals``         签发审批凭据（人工授权 HIGH/CRITICAL 动作）
- ``GET    /v1/policy/approvals``         列出当前有效的审批凭据
- ``DELETE /v1/policy/approvals/{id}``    撤销审批凭据
- ``GET    /v1/policy/enforcement``       查看内核层拦截的当前配置（只读）

安全设计（**关键**）
--------------------
1. **主体只来自身份令牌**：``principal`` 一律取自 ``Authorization: Bearer <JWT>``
   的 ``sub``，**绝不从请求体读取**。请求体里就算带 ``principal`` 也不会被采信
   —— 否则任何人都能替别人"授权"，那正是 C-3 通道此前唯一的真实缺口。
2. **签发前先验人**：``principal`` 必须是身份内核中**存在且 ACTIVE 的非 service
   身份**，否则 400 拒绝（``issue_grant`` 内校验）。service 身份永远不能持有
   人类主权。
3. **凭据有限**：动作集必须全部是**被拦截门管辖的 HIGH/CRITICAL 动作**
   （白名单由 ``_risk_classification`` 唯一裁决），TTL 有上限，可撤销。
4. **有据可查**：签发/撤销各写一条 ``HUMAN_SOVEREIGNTY_OVERRIDE`` 审计事件；
   内核动作执行时的审计事件带 ``sovereignty_grant`` 字段，构成
   「动作 ← 凭据 ← 授权人」的完整因果链。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..kernels._enforcement import describe as describe_enforcement
from ..kernels._sovereignty import (
    DEFAULT_GRANT_TTL_SECONDS,
    MAX_GRANT_TTL_SECONDS,
    get_grant,
    issue_grant,
    list_grants,
    revoke_grant,
)

router = APIRouter(prefix="/v1", tags=["policy"])


def require_bearer_payload(authorization: Optional[str] = Header(None)):
    """Validate ``Authorization: Bearer <JWT>`` and return the verified payload.

    This is the **single** implementation of "bearer token -> verified claims"
    in the gateway. ``/v1/auth/*`` depends on it too, so there is exactly one
    place where a token is parsed and checked -- two implementations would be
    two chances to disagree.

    The JWT signature is verified by ``src.security``; a caller-supplied
    identity is never trusted (see the module docstring, point 1).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty bearer token")
    try:
        from ..security import get_jwt_handler

        return get_jwt_handler().validate_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"invalid bearer token: {exc}")


def require_human_principal(payload=Depends(require_bearer_payload)) -> str:
    """Resolve the authenticated principal from the bearer token, or 401.

    Deliberately *only* the token can name the principal -- see the module
    docstring, point 1. This dependency never trusts a caller-supplied
    identity.
    """
    subject = getattr(payload, "sub", None)
    if not subject:
        raise HTTPException(status_code=401, detail="token has no subject claim")
    return str(subject)


class ApprovalRequest(BaseModel):
    """审批凭据签发请求。

    注意：**没有** ``principal`` 字段 —— 主体只能来自身份令牌。
    """

    actions: List[str] = Field(..., min_length=1, description="被授权的内核动作名（HIGH/CRITICAL）")
    reason: str = Field("", max_length=500, description="授权理由（记入审计）")
    ttl_seconds: float = Field(
        DEFAULT_GRANT_TTL_SECONDS,
        gt=0,
        le=MAX_GRANT_TTL_SECONDS,
        description=f"有效期秒数，上限 {MAX_GRANT_TTL_SECONDS:.0f}",
    )


@router.post("/policy/approvals", response_model=Dict[str, Any])
def create_approval(
    req: ApprovalRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """签发一条审批凭据（审计留痕）。主体取自令牌，请求体无法指定。"""
    try:
        grant = issue_grant(
            principal,
            req.actions,
            reason=req.reason,
            ttl_seconds=req.ttl_seconds,
            issued_by=principal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"approved": True, "grant": grant.to_dict()}


@router.get("/policy/approvals", response_model=Dict[str, Any])
def list_approvals(
    include_expired: bool = False,
    include_revoked: bool = False,
    _: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """列出审批凭据（默认只看有效的）。"""
    grants = list_grants(
        include_expired=include_expired, include_revoked=include_revoked
    )
    return {
        "count": len(grants),
        "grants": [g.to_dict() for g in grants],
        "enforcement": describe_enforcement(),
    }


@router.get("/policy/approvals/{grant_id}", response_model=Dict[str, Any])
def get_approval(grant_id: str, _: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """按 id 读取一条审批凭据（含已过期/已撤销）。"""
    grant = get_grant(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail=f"unknown grant: {grant_id}")
    return grant.to_dict()


@router.delete("/policy/approvals/{grant_id}", response_model=Dict[str, Any])
def revoke_approval(
    grant_id: str,
    reason: str = "",
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """撤销一条审批凭据（审计留痕）。重复撤销幂等。"""
    try:
        grant = revoke_grant(grant_id, revoked_by=principal, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"revoked": True, "grant": grant.to_dict()}


@router.get("/policy/enforcement", response_model=Dict[str, Any])
def get_enforcement(_: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """内核层拦截配置快照（只读）。默认 ``enabled: false`` = 记录型（L1）。"""
    return describe_enforcement()
