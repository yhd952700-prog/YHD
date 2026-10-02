"""身份 / 权限可解释性面（**严格只读**）—— P9 Permissions / Identity UX。

为什么要有这个模块
------------------
身份内核（``src/kernels/identity``）之前已经是可用的：生命周期、作用域上限
（L0-L7）、主体唯一性、human/agent 命名空间不相交、注册表完整性 fail-closed。
但**它从未出现在用户面前**：没有任何 HTTP 端点能回答"现在有哪些主体、每个主体
被授予了什么"，所以"用户能看见并控制每个 AI 员工/主体的权限"这句话在运行时
无从验证。

这里补齐的正是那一层：把**已经存在**的身份内核**暴露**出来，不新增一层自己的
主体表、不重新实现判定、不用别的数据源拼凑计数。

硬约束：只读，永远只读
----------------------
``AgentIdentity.grant_permission`` / ``revoke_permission`` 就在内核里，加一层
 HTTP 写接口只需要十分钟 —— 但那是**赋权面（authority surface）**：一次
``grant_permission`` 就能给某个主体加上一条它此前没有的权限，而这条路径此刻没有
人类复核、也没有审批凭据兜底。所以先把账本摊开给人看，写操作留到它有真实闸门之后
再谈。本轮：

* 只有 GET。没有 POST/PUT/PATCH/DELETE，也没有 GraphQL 式的 mutation；
* 每个 handler 内部显式 ``Depends(require_human_principal)``（与 audit.py 同
  一个闸门），所以权限账本不是匿名的；
* 读不到/判不出来时返回**诚实的错误**（503 / 404），绝不返回读起来像"没有主体"
  的空列表。

数据从哪来（以及不来）
----------------------
所有字段都直接来自 :class:`src.kernels.identity.AgentIdentity`：

* ``kind`` 取 :attr:`AgentIdentity.namespace`（= ``metadata["kind"]``，
  缺失时内核归类为 ``agent``）。因为"缺失标记"和"真的声明了 agent"在内核里
  无法区分，额外给出 ``kind_marked`` 说明这个 kind 是显式标注的还是推断的；
* ``permission_count`` 是该身份真实 ``permissions`` 集合的长度；
* ``scope`` 是身份的**作用域上限**（L0-L7），不是单条授权的 scope —— 身份内核
  把权限存成扁平 ``Set[str]``，**不保留**每条 grant 的 scope，所以这里不编造它；
* ``resource`` / ``action`` 是**派生**字段：由权限字符串按 ``:`` 拆分得到
  （仓库里真实形态见 ``src/ai/domain_templates.py:220`` 的 ``read:security``）。
  ``permission`` 原串永远在场，派生字段永远带着 ``derived: true`` 标签。

注册表状态也是真实答案的一部分：缺 ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY``
时注册表**拒绝每一行**（fail-closed, HC-11/U6），此时"零个人类"是拒绝的结果，
不是人类不存在 —— 那种情况下响应里一定有 ``warnings`` 说明原因。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from .policy import require_human_principal

router = APIRouter(prefix="/v1/identity", tags=["identity"])

#: 单次返回上限（grants 是全主体扁平展开，天然比主体列表大）。
_MAX_LIMIT = 2000
_DEFAULT_LIMIT = 200

#: 权限字符串里分隔 resource / action 的分隔符（见模块 docstring，派生用）。
_PERMISSION_SEPARATOR = ":"

#: 常量说明：**为什么 per-grant scope 不在响应里**。放在函数外是因为它是一条关于
#: 数据模型的陈述，不是某次请求的状态。
_SCOPE_NOTE = (
    "identity kernel stores grants as a flat Set[str]; the per-grant scope "
    "passed to IdentityManager.grant_permission() is NOT retained, so each "
    "row reports the identity's scope CEILING instead."
)
_DERIVATION_NOTE = (
    "derived_action/derived_resource are split out of the raw permission "
    "string on %r; the kernel has no separate resource column. The raw "
    "`permission` field is always present and is authoritative."
    % _PERMISSION_SEPARATOR
)

_SOURCE = {
    "module": "src.kernels.identity",
    "api": "IdentityManager.list_identities() / get_identity() / get_identity_by_principal()",
}


# ---------------------------------------------------------------------------
# 读取原语 —— 只调身份内核，自己不维护任何副本
# ---------------------------------------------------------------------------


def _manager():
    """当前进程的身份管理器。不可用时**报错**，绝不退化成空答案。

    两层防御：

    * 构造/取用失败 -> 503 ``identity kernel unavailable``；
    * 生命周期不是 READY（例如有人真的调了 ``shutdown()``）-> 503。

    第二层是"空 == 不存在"这道缺陷的具体入口：内核停摆时 ``list_identities()``
    并不抛异常，它就是一个空的 dict。返回一个空列表会被读成"系统里没有主体"，
    而真实答案是"我无法判定"。
    """
    from ..kernels._base import KernelLifecycle
    from ..kernels.identity import get_identity_manager

    try:
        manager = get_identity_manager()
    except Exception as exc:  # noqa: BLE001 - 读不到必须如实说出来
        raise HTTPException(status_code=503, detail=f"identity kernel unavailable: {exc}")

    lifecycle = getattr(manager, "lifecycle", None)
    if lifecycle is not None and lifecycle is not KernelLifecycle.READY:
        raise HTTPException(
            status_code=503,
            detail=(
                f"identity kernel is not READY (lifecycle={lifecycle}); "
                "the principal list would be indistinguishable from 'no "
                "principals exist', so it is not returned"
            ),
        )
    return manager


def _registry_report(manager) -> Tuple[Dict[str, Any], List[str]]:
    """注册表自身的真相 + 需要说给人听的话。

    返回 ``(registry, warnings)``。``warnings`` 非空意味着"你即将看到的主体列表
    不完整是有原因的"，列举/COUNT 端点都把它原样带上。
    """
    try:
        described = manager.describe_identity_namespaces()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"identity registry unreadable: {exc}")

    store = described.get("store") or {}
    integrity = described.get("registry_integrity") or {}
    last_load = integrity.get("last_load") or {}
    rows_refused = [str(row) for row in (last_load.get("rejected") or [])]
    rows_admitted = last_load.get("admitted")
    key_env = integrity.get("integrity_key_env") or "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"

    registry: Dict[str, Any] = {
        "backend": store.get("backend"),
        "location": store.get("location"),
        "configured": bool(store.get("configured")),
        "integrity_enforced": bool(integrity.get("integrity_enforced")),
        "integrity_state": integrity.get("integrity_state"),
        "integrity_key_env": key_env,
        "rows_admitted": rows_admitted if isinstance(rows_admitted, int) else None,
        "rows_refused": rows_refused,
        "rows_offered": (
            rows_admitted + len(rows_refused) if isinstance(rows_admitted, int) else None
        ),
    }

    warnings: List[str] = []
    if registry["configured"] and not registry["integrity_enforced"]:
        warnings.append(
            f"human registry {registry['location']!r} is configured but "
            f"{key_env} is unset: per fail-closed (HC-11 / U6) the store "
            f"REFUSES every row, so no registered human is admitted. That is "
            f"not the same answer as 'no human exists'."
        )
    if rows_refused:
        warnings.append(
            f"{len(rows_refused)} registry row(s) were refused at load and are "
            f"therefore absent from this listing: {rows_refused}. Set "
            f"{key_env} and re-register those humans "
            f"(scripts/register_human_identity.py) to admit them."
        )
    for reason in described.get("registry_refusals") or []:
        warnings.append(f"kernel refused a registration at boot: {reason}")

    return registry, warnings


def _split_permission(permission: str) -> Tuple[Optional[str], Optional[str]]:
    """``"read:security"`` -> ``("read", "security")``；无分隔符 -> ``(None, None)``.

    仓库里真实的权限串是 ``<动作>:<域>``（``src/ai/domain_templates.py:220`` 的
    ``read:security`` / ``audit:security``），但身份内核并没有把这层结构落成列，
    所以这是**展示层派生**，不是内核事实 —— 响应里同时给出 ``derived: true``
    与原始 ``permission``。
    """
    if not isinstance(permission, str) or _PERMISSION_SEPARATOR not in permission:
        return None, None
    head, _, rest = permission.partition(_PERMISSION_SEPARATOR)
    action = head.strip() or None
    resource = rest.strip() or None
    if resource is not None and _PERMISSION_SEPARATOR in resource:
        # 多段时保留剩余部分，不猜测更深的结构。
        resource = resource.strip()
    return action, resource


def _identity_view(identity, *, include_permissions: bool = False) -> Dict[str, Any]:
    """一个主体的真相视图（字段全部来自 AgentIdentity 自己）。"""
    metadata = identity.metadata if isinstance(identity.metadata, dict) else {}
    permissions = sorted(identity.permissions or ())
    view: Dict[str, Any] = {
        "id": identity.id,
        "principal": identity.principal,
        # kind == 内核自己的 namespace 判定，避免这里重造一套 "是不是人类"。
        "kind": identity.namespace,
        "namespace": identity.namespace,
        # 缺 metadata["kind"] 的身份被内核归为 agent；这个字段说明是推断的。
        "kind_marked": "kind" in metadata,
        "status": identity.status.value,
        "active": identity.status.value == "active",
        "scope": identity.scope.value,
        "trust_score": identity.trust_score,
        "permission_count": len(permissions),
        "fingerprint": identity.fingerprint,
        "created_at": identity.created_at.isoformat()
        if getattr(identity, "created_at", None) else None,
        "last_modified": identity.last_modified.isoformat()
        if getattr(identity, "last_modified", None) else None,
        "display_name": metadata.get("display_name"),
    }
    if include_permissions:
        view["permissions"] = permissions
    return view


def _grant_view(identity) -> List[Dict[str, Any]]:
    """把一个身份的权限展开成扁平 grant 行（一行 = 谁 -> 被授予了什么）。"""
    grants: List[Dict[str, Any]] = []
    for permission in sorted(identity.permissions or ()):
        action, resource = _split_permission(permission)
        grants.append(
            {
                "principal_id": identity.id,
                "principal": identity.principal,
                "kind": identity.namespace,
                "permission": permission,
                "derived_action": action,
                "derived_resource": resource,
                "derived": action is not None or resource is not None,
                # 身份的作用域上限。不是这条 grant 的 scope —— 内核不存它。
                "scope": identity.scope.value,
                "identity_active": identity.status.value == "active",
            }
        )
    return grants


def _collect(**filters) -> List:
    """从真实注册表取主体。任何异常都向上冒泡 -> 503，不在此处吃掉。"""
    manager = _manager()
    try:
        return list(manager.list_identities())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"identity registry could not be enumerated: {exc}"
        )


# ---------------------------------------------------------------------------
# 端点 —— 全部 GET，全部人类主权闸门
# ---------------------------------------------------------------------------


@router.get("/principals")
def list_principals(
    kind: Optional[str] = Query(None, description="按类型过滤：human / agent / service"),
    principal: Optional[str] = Query(None, description="按主体名精确过滤"),
    status: Optional[str] = Query(None, description="按状态过滤：active / suspended / deactivated"),
    scope: Optional[str] = Query(None, description="按作用域上限过滤：L0..L7"),
    include_permissions: bool = Query(False, description="是否把权限明细一并带回"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="返回主体数上限"),
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """列出**真实**身份内核里的全部主体（只读）。

    返回值带 ``registry`` 与 ``warnings``：缺项/拒收时必须能被看见，否则
    "只有 2 个内置身份"会被读成"系统里就这 2 个主体"。
    """
    identities = _collect()
    manager = _manager()
    registry, warnings = _registry_report(manager)

    selected = identities
    if kind is not None:
        selected = [i for i in selected if i.namespace == kind]
    if principal is not None:
        selected = [i for i in selected if i.principal == principal]
    if status is not None:
        selected = [i for i in selected if i.status.value == status]
    if scope is not None:
        wanted = scope.upper()
        selected = [i for i in selected if i.scope.value == wanted]

    by_kind: Dict[str, int] = {}
    for identity in selected:
        by_kind[identity.namespace] = by_kind.get(identity.namespace, 0) + 1

    rows = sorted(
        (_identity_view(i, include_permissions=include_permissions) for i in selected),
        key=lambda row: (row["kind"], row["principal"]),
    )
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    return {
        "principals": rows,
        "count": len(rows),
        "total": len(selected),
        "truncated": truncated,
        "by_kind": by_kind,
        "filters": {
            "kind": kind,
            "principal": principal,
            "status": status,
            "scope": scope,
            "include_permissions": include_permissions,
            "limit": limit,
        },
        "registry": registry,
        "warnings": warnings,
        "source": _SOURCE,
        "read_only": True,
    }


@router.get("/principals/{principal_id}")
def get_principal(
    principal_id: str,
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """读单个主体（只读）。``principal_id`` 既接受**身份 id**也接受**主体名**。

    ``system`` 的 id 与 principal 同名，而人类是 ``human:<name>``、agent 是 8/16 位
    hex，两条查找路径必须与 :func:`~src.kernels.identity.resolve_principal_fingerprint`
    一致地依次尝试，否则同一个名字会查出两个不同的主体。

    不存在 -> **404 诚实报错**；返回 ``{}`` 会被读成"这个主体存在但没有权限"。
    """
    manager = _manager()
    try:
        identity = manager.get_identity(principal_id)
        if identity is None:
            identity = manager.get_identity_by_principal(principal_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"identity lookup failed: {exc}")

    registry, warnings = _registry_report(manager)
    if identity is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no identity with id or principal {principal_id!r}; "
                "this is a real 'not found', not an empty permission set"
            ),
        )

    grants = _grant_view(identity)
    return {
        "principal": _identity_view(identity, include_permissions=True),
        "permissions": sorted(identity.permissions or ()),
        "grants": grants,
        "permission_count": len(grants),
        "scope_note": _SCOPE_NOTE,
        "derivation_note": _DERIVATION_NOTE,
        "registry": registry,
        "warnings": warnings,
        "source": _SOURCE,
        "read_only": True,
    }


@router.get("/permissions")
def list_permissions(
    principal: Optional[str] = Query(None, description="按主体名或身份 id 过滤"),
    resource: Optional[str] = Query(
        None, description="按派生 resource 过滤（permission 串里 ':' 之后的部分）"),
    permission: Optional[str] = Query(None, description="按权限原串精确过滤"),
    kind: Optional[str] = Query(None, description="按主体类型过滤：human / agent / service"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="返回 grant 行数上限"),
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """扁平列出全部 grant（只读）：谁 -> 被授予了什么 -> 受哪个作用域上限约束。

    过滤是**真的收窄**（先把集合缩小再计数），不是"返回全部让前端自己筛"：
    假过滤会让"筛完只有 3 条"既可能是真的只有 3 条，也可能是被 limit 截断。
    """
    identities = _collect()
    manager = _manager()
    registry, warnings = _registry_report(manager)

    if principal is not None:
        identities = [i for i in identities
                      if i.principal == principal or i.id == principal]
    if kind is not None:
        identities = [i for i in identities if i.namespace == kind]

    grants: List[Dict[str, Any]] = []
    for identity in sorted(identities, key=lambda i: (i.namespace, i.principal)):
        for grant in _grant_view(identity):
            if resource is not None and grant["derived_resource"] != resource:
                continue
            if permission is not None and grant["permission"] != permission:
                continue
            grants.append(grant)

    truncated = len(grants) > limit
    if truncated:
        grants = grants[:limit]

    return {
        "grants": grants,
        "count": len(grants),
        "truncated": truncated,
        "filters": {
            "principal": principal,
            "resource": resource,
            "permission": permission,
            "kind": kind,
            "limit": limit,
        },
        # 明示两件**有意不说**的事，免得使用者把展示层派生当成内核事实。
        "notes": [_SCOPE_NOTE, _DERIVATION_NOTE],
        "registry": registry,
        "warnings": warnings,
        "source": _SOURCE,
        "read_only": True,
    }


@router.get("/summary")
def summary(
    _principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """身份总览（只读）：按类型 / 状态 / 作用域上限计数，外加注册表诚实度。

    计数全部来自这次遍历的真实身份对象，没有来自别处（没有 roster、没有 AI
    员工注册表）—— 那些是别的概念，混进来会让"这个主体持有几条权限"变成假数字。
    """
    identities = _collect()
    manager = _manager()
    registry, warnings = _registry_report(manager)

    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_scope: Dict[str, int] = {}
    total_grants = 0
    for identity in identities:
        by_kind[identity.namespace] = by_kind.get(identity.namespace, 0) + 1
        by_status[identity.status.value] = by_status.get(identity.status.value, 0) + 1
        by_scope[identity.scope.value] = by_scope.get(identity.scope.value, 0) + 1
        total_grants += len(identity.permissions or ())

    return {
        "total_principals": len(identities),
        "by_kind": by_kind,
        "by_status": by_status,
        "by_scope": by_scope,
        "total_grants": total_grants,
        "individual_principals": {
            "kind_states_are_inferred_when_unmarked": True,
            "note": (
                "kind comes from metadata['kind']; an identity without that "
                "marker is classified 'agent' by the kernel and carries "
                "kind_marked=false in GET /v1/identity/principals."
            ),
        },
        "scope_note": _SCOPE_NOTE,
        "registry": registry,
        "warnings": warnings,
        "source": _SOURCE,
        "read_only": True,
    }
