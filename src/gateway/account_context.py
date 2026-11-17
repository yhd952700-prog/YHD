"""账号上下文：把"令牌主体"扩展成"主体 + 在树里的位置"。

Why this module exists
----------------------
``policy.require_human_principal`` 只回答"你是谁"；账号体系还需要"你能作用于
谁"。本模块在**不替换**既有 RBAC/ABAC 依赖的前提下，组合出
:class:`AccountContext`：

* 主体仍然只来自经核验的令牌（不读请求体）；
* 账号节点来自身份内核的账号树；
* 没有账号节点的主体（老部署、单机工具令牌）被当作**独立根**，可见范围只有
  自己 —— 这是"单账号老用户不受影响"的兼容路径，不是提权。

所有拒绝都走 :func:`deny`：稳定错误码 + 中性文案（不含上级/子账号/层级等词），
且不会返回 500。越权尝试由调用方写审计（本模块只负责判定）。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException

from ..kernels.identity import _accounts
from ..kernels.identity import get_identity_manager
from .policy import require_bearer_payload, _human_claims

#: 前端 ViewAs 视角请求头（见 console ``lib/auth.ts`` 的 ``tokenHeaders``）。
VIEW_AS_HEADER = "X-Liuhao-View-As"

#: 当前请求的 ViewAs 目标（由 :class:`gateway.view_as.ViewAsReadOnlyMiddleware`
#: 在路由前校验后写入；纯 ASGI 中间件与端点在同一任务内，上下文变量直达）。
#: ``require_account_principal`` 据此把读视角换成目标账号；未开视角时为 None。
#: 中间件在请求结束后复位，不会漏到下一个请求。
view_as_target_var: ContextVar[Optional[str]] = ContextVar(
    "liuhao_view_as_target", default=None
)

#: 中性文案常量（响应里只允许出现这些，不拼层级信息）。
NEUTRAL_DENIED = _accounts.NEUTRAL_FORBIDDEN_MESSAGE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyScope:
    """某账号的 Key 使用视角（模型路由与存储过滤的唯一输入）。

    * ``visible_to``：列表/默认模型可见的归属账号集合；
    * ``allow_unowned``：账号体系上线前的历史模型归根账号所有，仅根可见；
    * ``allow_owners``：本次允许**实际使用其 Key** 的集合。Key 不共用
      （Joseph 2026-11-14 新规矩）：恒为调用者自己（+ 无归属历史模型
      的空串占位），不再含任何其他账号，哪怕对方开过共享授权。
    """

    principal: str
    is_root: bool
    visible_to: Tuple[str, ...]
    allow_unowned: bool
    allow_owners: Tuple[str, ...]

    def can_use(self, owner: Optional[str]) -> bool:
        if not owner:
            return self.allow_unowned
        return owner in self.allow_owners


@dataclass(frozen=True)
class AccountContext:
    """一次请求的账号视角（不可变）。"""

    principal: str
    node: _accounts.AccountNode
    subtree: Tuple[str, ...]
    standalone: bool = False
    #: ViewAs 只读视角下为查看者本人（谁在以该成员视角查看）；正常请求为 None。
    viewed_as_by: Optional[str] = None

    @property
    def is_root(self) -> bool:
        return self.node.is_root

    @property
    def is_owner(self) -> bool:
        """Owner 即账号树根（含单人模式的独立根）；与 ``is_root`` 同义，
        只是用产品语言命名，供 Owner 面（``gateway/owner.py``）调用。"""
        return self.node.is_root

    @property
    def is_member(self) -> bool:
        return not self.node.is_root

    def can_read(self, target: str) -> bool:
        return target in self.subtree

    def can_manage(self, target: str) -> bool:
        nodes = get_identity_manager().account_nodes()
        return _accounts.can_manage(nodes, self.principal, target)

    def has_scope(self, scope: str) -> bool:
        return self.node.has(scope)

    def public_self(self) -> dict:
        return _accounts.public_member(self.node)


def _standalone_node(principal: str) -> _accounts.AccountNode:
    return _accounts.AccountNode(
        principal=principal,
        parent_id=None,
        depth=0,
        root_id=principal,
        permissions=frozenset(),
        source="standalone",
    )


def validate_view_as(viewer: str, target: str) -> None:
    """校验一次 ViewAs 请求是否合法；非法时 raise 403（中性文案）。

    规则（与前端 ``accountCaps.canViewAs`` 同口径：根或能管下级）：

    * 目标必须存在、状态为 active、不能是查看者本人；
    * 目标必须在查看者的子树内；
    * 查看者须为根，或能管理该目标（直接下级）。
    """
    viewer = (viewer or "").strip()
    target = (target or "").strip()
    if not viewer or not target or viewer == target:
        raise deny(403, "VIEW_AS_DENIED", "无权以该视角查看")
    manager = get_identity_manager()
    target_node = manager.get_account(target)
    if target_node is None or target_node.status != _accounts.ACCOUNT_STATUS_ACTIVE:
        raise deny(403, "VIEW_AS_DENIED", "无权以该视角查看")
    viewer_node = manager.get_account(viewer)
    if viewer_node is None:
        # 独立根 / 老部署令牌：没有树，就没有“别人的视角”。
        raise deny(403, "VIEW_AS_DENIED", "无权以该视角查看")
    if target not in manager.subtree_principals(viewer):
        raise deny(403, "VIEW_AS_DENIED", "无权以该视角查看")
    if not viewer_node.is_root and not _accounts.can_manage(
        manager.account_nodes(), viewer, target
    ):
        raise deny(403, "VIEW_AS_DENIED", "无权以该视角查看")


def require_account_principal(
    payload=Depends(require_bearer_payload),
) -> AccountContext:
    """Authenticated human + tree position. Never trusts a body-supplied name.

    ViewAs：:class:`ViewAsReadOnlyMiddleware` 在路由前校验过目标并把
    ``X-Liuhao-View-As`` 头的值放进请求上下文变量（见 ``view_as`` 模块）；
    这里读到目标时返回**目标账号**的视角（读范围随目标走；写操作已由
    中间件在路由前拦掉）。目标非法时这里同样 403，绝不静默回落到查看者本人。

    签名刻意不加 ``Request`` 参数：``ai_management`` / ``audit`` 等模块会
    直接位置调用 ``require_account_principal(payload)``，加参会把 payload
    错位成 Request（已踩过一次，见提交说明）。
    """
    subject, _metadata = _human_claims(payload)
    manager = get_identity_manager()
    node = manager.get_account(subject)
    standalone = node is None
    if node is None:
        # 老部署 / 单机工具令牌：视作"只有自己"的独立根，绝不因此看到别人。
        node = _standalone_node(subject)
    if node.status != _accounts.ACCOUNT_STATUS_ACTIVE:
        raise deny(403, "ACCOUNT_UNAVAILABLE", "该账号当前不可用")
    subtree = tuple(manager.subtree_principals(subject)) if not standalone else (subject,)
    if not subtree:
        subtree = (subject,)
    target = (view_as_target_var.get() or "").strip()
    if target:
        validate_view_as(subject, target)
        target_node = manager.get_account(target)
        assert target_node is not None  # validate_view_as 已保证存在
        target_subtree = tuple(manager.subtree_principals(target)) or (target,)
        return AccountContext(
            principal=target,
            node=target_node,
            subtree=target_subtree,
            standalone=False,
            viewed_as_by=subject,
        )
    return AccountContext(
        principal=subject,
        node=node,
        subtree=subtree,
        standalone=standalone,
    )


def structured_error_body(detail: Any) -> Optional[Dict[str, Any]]:
    """把 ``{"code","message"}`` 形态的 detail 摊平成驾驶舱读取的顶层错误体。

    非结构化 detail（普通字符串）返回 ``None``，调用方沿用框架默认错误体，
    避免改变既有 API 的响应形状。
    """
    if not isinstance(detail, dict) or "code" not in detail:
        return None
    body = dict(detail)
    body.setdefault("detail", body.get("message"))
    return body


def deny(status_code: int, code: str, message: str, **extra) -> HTTPException:
    """Build an explicit, neutral HTTP error (no hierarchy words)."""
    detail = {"code": code, "message": message}
    detail.update({k: v for k, v in extra.items() if v is not None})
    return HTTPException(status_code=status_code, detail=detail)


def from_account_error(exc: Exception) -> HTTPException:
    """Translate a kernel :class:`AccountError` into the wire format."""
    if isinstance(exc, _accounts.AccountError):
        return deny(exc.status_code, exc.code, exc.message, **exc.extra)
    return deny(500, "ACCOUNT_INTERNAL_ERROR", "服务暂时不可用")


def require_visible(context: AccountContext, target: str) -> None:
    """读类操作的树判定；不在自己子树内一律 404（防枚举，不暴露存在与否）。"""
    if not context.can_read(target):
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def _audit_manage_denied(context: AccountContext, target: str, code: str) -> None:
    """越级/越界写尝试必须留痕：安全团队要能回答"谁试过动谁"。

    审计失败不能把响应从 404 变成 500 —— 判定已经在调用前完成。
    """
    try:
        get_identity_manager().record_event(
            action="account.manage.denied",
            actor=context.principal,
            target=target,
            outcome="denied",
            details={"error_code": code},
        )
    except Exception:  # noqa: BLE001
        logger.warning("manage denial audit failed for %s -> %s", context.principal, target,
                       exc_info=True)


def require_manage(context: AccountContext, target: str) -> None:
    """写类操作的树判定。

    * 不在自己子树内 → 404（与"资源不存在"不可区分）；
    * 在子树内但非直接下级 → 403 ``ACCOUNT_LEAPFROG_DENIED``。
    """
    if not context.can_read(target):
        _audit_manage_denied(context, target, _accounts.CODE_SUBTREE_DENIED)
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    if not context.can_manage(target):
        _audit_manage_denied(context, target, _accounts.CODE_LEAPFROG_DENIED)
        raise deny(403, _accounts.CODE_LEAPFROG_DENIED, NEUTRAL_DENIED)


# --------------------------------------------------------------------------- #
# Key 归属（模型路由）
# --------------------------------------------------------------------------- #

def key_scope_for(
    context: Optional[AccountContext] = None, *, principal: Optional[str] = None
) -> KeyScope:
    """构造 Key 使用视角。

    新规矩（Joseph 2026-11-14，Key 不共用）：**任何账号只用自己的
    Key**。根账号也不再借用成员的 Key（哪怕对方开过共享授权）——
    ``allow_owners`` 恒为 ``(自己, "")``，``""`` 只代表上线前无归属的
    历史模型（单机老部署，归根账号自己所有），不代表别人的 Key。
    成员：只可见/只可使用自己的 Key；无归属历史模型也不可用。
    无账号节点的老部署：等同“只有自己”的根（历史模型归属自己）。

    注意：``visible_to`` 同样只含自己——根账号的模型列表/详情里不再出现
    成员的模型配置（哪怕只是名字与掩码）；成员“配没配 Key”这个管理信息
    走 ``/v1/a3m`` 的 Owner 汇总（只给 bool，不给 Key、不给掩码）。
    """

    if context is None:
        if not principal:
            raise ValueError("key scope needs a context or principal")
        manager = get_identity_manager()
        node = manager.get_account(principal)
        if node is None:
            return KeyScope(
                principal=principal,
                is_root=True,
                visible_to=(principal,),
                allow_unowned=True,
                allow_owners=(principal, ""),
            )
        context = AccountContext(
            principal=principal,
            node=node,
            subtree=tuple(manager.subtree_principals(principal)) or (principal,),
            standalone=False,
        )

    if context.is_member:
        return KeyScope(
            principal=context.principal,
            is_root=False,
            visible_to=(context.principal,),
            allow_unowned=False,
            allow_owners=(context.principal,),
        )

    # 根账号：Key 不共用，不再把已授权成员的 Key 放进 allow_owners；
    # 可见范围也只含自己（列表/详情里不再出现成员的模型配置）。
    return KeyScope(
        principal=context.principal,
        is_root=True,
        visible_to=(context.principal,),
        allow_unowned=True,
        allow_owners=(context.principal, ""),
    )


def enforce_key_use(scope: KeyScope, owner: Optional[str]) -> None:
    """显式模型选择时的 Key 归属判定（定稿码见 §5.2）。

    Key 不共用（Joseph 2026-11-14 亲口定，2026-11-15 彻底移除开关）：
    别人的模型配置一律不能用，没有任何授权能把它打开。
    """
    if scope.can_use(owner):
        return
    owner = owner or ""
    if owner and owner not in scope.visible_to:
        raise deny(403, _accounts.CODE_KEY_NOT_OWNED, NEUTRAL_DENIED)
    raise deny(
        403,
        _accounts.CODE_KEY_NOT_OWNED,
        "该模型配置属于其他账号，不能使用",
    )


def audit_key_use(
    scope: KeyScope,
    owner: Optional[str],
    *,
    outcome: str = "allowed",
    source: str = "",
    extra: Optional[dict] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Key 使用/冒用事件写哈希链：actor 恒为当前账号，另记 ``key_owner``。

    ``correlation_id`` 与触发它的 HTTP 请求绑定：403 拒绝时调用方把该 id
    回给客户端，审计页就能按同一个 id 找到这条拒绝记录。
    """
    try:
        details = {
            "key_owner": owner or "",
            "authorization_source": source or "self",
        }
        if correlation_id:
            details["correlation_id"] = correlation_id
        if extra:
            details.update(extra)
        get_identity_manager().record_event(
            action="key.use",
            actor=scope.principal,
            target=owner or scope.principal,
            outcome=outcome,
            details=details,
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 - 审计失败不得阻断已通过的判定
        pass
