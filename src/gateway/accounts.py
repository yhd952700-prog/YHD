"""账号树 HTTP 面：注册 / 成员 / 权限 / Key 共享 / 状态 / 恢复 / 备份。

设计要点（与 ``docs/account-hierarchy.md`` 对齐）：

* 身份内核负责"谁"，账号树负责"能对谁操作"；本模块只做 HTTP 编排，不复制判定；
* 所有拒绝都是稳定错误码 + 中性文案，响应里不出现 ``parent/depth/root``；
* 首个注册者成为唯一根；此后只允许在已有账号下创建成员（激活码一次性）；
* 写操作一律从令牌取主体，请求体里的 ``principal`` 只作为**目标**，绝不作为 actor；
* 恢复码/激活码/凭据包都只存哈希，明文只在成功响应里出现一次。
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from .account_context import (
    AccountContext,
    deny,
    from_account_error,
    require_account_principal,
    require_manage,
    require_visible,
)
from .auth import issue_token_for, resolve_secret_store, rotate_credential

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #

class RegisterRootRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: Optional[str] = Field(None, max_length=80)


class CreateMemberRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(None, max_length=80)
    role: Optional[str] = Field(None, description="角色预设：viewer / operator / manager")
    permissions: Optional[List[str]] = None
    allow_key_sharing: bool = Field(False, description="允许团队共享我的 Key（默认关闭）")


class ActivateRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=4, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


class PermissionsRequest(BaseModel):
    grant: List[str] = Field(default_factory=list)
    revoke: List[str] = Field(default_factory=list)


class KeySharingRequest(BaseModel):
    allow: bool


class StatusRequest(BaseModel):
    status: str = Field(..., description="active / suspended / deleted")
    reason: str = Field("", max_length=200)


class PasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=256)


class RecoveryResetRequest(BaseModel):
    recovery_code: str = Field(..., min_length=4, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


class BackupExportRequest(BaseModel):
    scope: str = Field("all", description="all（整棵树，仅主账号）或 self")
    passphrase: str = Field(..., min_length=8, max_length=256)


class BackupImportRequest(BaseModel):
    bundle: Dict[str, Any]
    passphrase: str = Field(..., min_length=8, max_length=256)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def _kernel_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except _accounts.AccountError as exc:
        raise from_account_error(exc)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - 账号面不得 500 裸奔
        logger.exception("account operation failed")
        raise deny(500, "ACCOUNT_INTERNAL_ERROR", "服务暂时不可用")


def _public_member(node: _accounts.AccountNode) -> Dict[str, Any]:
    """**不含** parent_id/depth/root_id —— 成员与根看到同一形状。"""
    return _accounts.public_member(node, show_key_authorization=True)


def _require_scope(context: AccountContext, scope: str, *, code: str) -> None:
    if not context.has_scope(scope):
        raise deny(404, code, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


# --------------------------------------------------------------------------- #
# 静态路由（必须排在 ``/{principal}`` 之前）
# --------------------------------------------------------------------------- #

@router.get("/roles")
def list_roles() -> Dict[str, Any]:
    """角色预设：前端直接渲染，不暴露任何层级信息。"""
    return {
        "roles": [
            {"id": name, "permissions": sorted(_accounts.resolve_role(name, None))}
            for name in sorted(_accounts.ROLE_PRESETS)
        ],
        "scopes": sorted(_accounts.GRANTABLE_SCOPES),
    }


@router.post("/register")
def register_root(payload: RegisterRootRequest) -> Dict[str, Any]:
    """首个注册者成为唯一根；已存在根时 409（并写审计）。"""
    manager = get_identity_manager()
    node, recovery_code = _kernel_call(
        manager.register_root_account,
        payload.principal,
        display_name=payload.display_name,
    )
    if not rotate_credential(payload.principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "账号初始化失败")
    return {
        "account": _public_member(node),
        "recovery_code": recovery_code,
        "notice": "恢复码只显示这一次，请离线保存；它可用于重置密码。",
    }


@router.post("/activate")
def activate(payload: ActivateRequest) -> Dict[str, Any]:
    """用一次性激活码设置密码并登录（一次性 + 限时 + 限次）。"""
    manager = get_identity_manager()
    _kernel_call(manager.verify_activation_code, payload.principal, payload.code)
    if not rotate_credential(payload.principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "账号初始化失败")
    _kernel_call(manager.consume_activation_code, payload.principal)
    token = issue_token_for(payload.principal, client="web")
    return {
        "token": token["access_token"],
        "access_token": token["access_token"],
        "account": _public_member(manager.get_account(payload.principal)),
    }


@router.post("/recovery/reset")
def recovery_reset(payload: RecoveryResetRequest) -> Dict[str, Any]:
    """用根恢复码重置密码；消费即作废，失败限次。"""
    manager = get_identity_manager()
    principal = _kernel_call(manager.consume_recovery_code, payload.recovery_code)
    if not rotate_credential(principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "账号恢复失败")
    token = issue_token_for(principal, client="web")
    return {
        "token": token["access_token"],
        "access_token": token["access_token"],
        "account": _public_member(manager.get_account(principal)),
    }


@router.get("")
def list_accounts(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    """只返回自己的子树；不含 parent/depth/root 字段。"""
    manager = get_identity_manager()
    members = []
    for principal in context.subtree:
        node = manager.get_account(principal)
        if node is None:
            continue
        members.append(_public_member(node))
    return {"members": members, "count": len(members)}


@router.get("/tree")
def account_tree(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    """成员树视图：仅根账号可用（成员的视角永远是"只有自己这一层"）。"""
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    manager = get_identity_manager()
    nodes = manager.account_nodes()
    return {"tree": _accounts.tree_view(nodes, context.principal)}


@router.post("/backup/export")
def backup_export(
    payload: BackupExportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """导出加密凭据包；``scope=all`` 仅根账号可用。"""
    try:
        from .account_backup import export_bundle
    except ImportError:
        raise deny(501, "ACCOUNT_FEATURE_UNAVAILABLE", "该功能尚未启用")

    return _kernel_call(
        export_bundle,
        actor=context.principal,
        scope=payload.scope,
        passphrase=payload.passphrase,
        subtree=context.subtree,
        is_root=context.is_root,
    )


@router.post("/backup/import")
def backup_import(payload: BackupImportRequest) -> Dict[str, Any]:
    """在本机没有账号树时认领凭据包（口令校验 + 防重放 + 限次 + 审计）。"""
    try:
        from .account_backup import import_bundle
    except ImportError:
        raise deny(501, "ACCOUNT_FEATURE_UNAVAILABLE", "该功能尚未启用")

    return _kernel_call(import_bundle, bundle=payload.bundle, passphrase=payload.passphrase)


@router.post("/recovery/rotate")
def recovery_rotate(
    context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """轮换恢复码（仅根账号；旧码立即失效）。"""
    manager = get_identity_manager()
    code, stamp = _kernel_call(manager.rotate_recovery_code, context.principal)
    return {
        "recovery_code": code,
        "expires_at": stamp,
        "notice": "恢复码只显示这一次，请离线保存。",
    }


# --------------------------------------------------------------------------- #
# 成员管理
# --------------------------------------------------------------------------- #

@router.post("")
def create_member(
    payload: CreateMemberRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    manager = get_identity_manager()
    if not context.has_scope(_accounts.SCOPE_MANAGE_CHILDREN):
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    node, activation_code = _kernel_call(
        manager.create_member_account,
        context.principal,
        payload.principal,
        permissions=payload.permissions,
        role=payload.role,
        display_name=payload.display_name,
        allow_key_sharing=payload.allow_key_sharing,
    )
    return {
        "account": _public_member(node),
        "activation_code": activation_code,
        "notice": "把一次性激活码交给该成员，由其在激活页设置密码。",
    }


@router.get("/{principal}")
def get_account(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    require_visible(context, principal)
    node = get_identity_manager().get_account(principal)
    if node is None:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return {"account": _public_member(node)}


@router.post("/{principal}/activation-code")
def reissue_activation(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    require_manage(context, principal)
    code, stamp = _kernel_call(
        get_identity_manager().issue_activation_code, context.principal, principal
    )
    return {"activation_code": code, "expires_at": stamp}


@router.patch("/{principal}/permissions")
def update_permissions(
    principal: str,
    payload: PermissionsRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    require_manage(context, principal)
    node = _kernel_call(
        get_identity_manager().grant_account_permissions,
        context.principal,
        principal,
        payload.grant,
        payload.revoke,
    )
    return {"account": _public_member(node)}


@router.patch("/{principal}/key-sharing")
def update_key_sharing(
    principal: str,
    payload: KeySharingRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """自己可以随时开启/撤回共享；管理者可代为调整（均写审计）。"""
    if principal != context.principal:
        require_manage(context, principal)
    node = _kernel_call(
        get_identity_manager().set_key_sharing, context.principal, principal, payload.allow
    )
    return {"account": _public_member(node)}


@router.patch("/{principal}/status")
def update_status(
    principal: str,
    payload: StatusRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    require_manage(context, principal)
    result = _kernel_call(
        get_identity_manager().set_account_status,
        context.principal,
        principal,
        payload.status,
        payload.reason,
    )
    return result


@router.post("/{principal}/password")
def reset_password(
    principal: str,
    payload: PasswordResetRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """管理者重置成员密码：写凭据 + 立即吊销旧令牌 + 哈希链审计。"""
    require_manage(context, principal)
    if not rotate_credential(principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "密码重置失败")
    manager = get_identity_manager()
    _kernel_call(manager.bump_account_epoch, principal, "password-reset")
    _kernel_call(
        manager.record_event,
        action="account.password.reset",
        actor=context.principal,
        target=principal,
        outcome="allowed",
        details={"credential": "rotated"},
    )
    return {"ok": True}


@router.get("/{principal}/usage")
def account_usage(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """用量按 Key 归属记账、按实际调用者展示；成员只见自己。"""
    require_visible(context, principal)
    try:
        from .account_usage import usage_summary
    except ImportError:
        raise deny(501, "ACCOUNT_FEATURE_UNAVAILABLE", "该功能尚未启用")

    return usage_summary(principal, include_callers=context.is_root)


# --------------------------------------------------------------------------- #
# 迁移（运维）
# --------------------------------------------------------------------------- #

@router.post("/migrate")
def migrate(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return _kernel_call(get_identity_manager().migrate_account_tree)


@router.post("/migrate/rollback")
def migrate_rollback(
    context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return _kernel_call(get_identity_manager().rollback_account_tree)
