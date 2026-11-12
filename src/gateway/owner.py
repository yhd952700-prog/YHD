"""Owner（超级管理员）面 —— 最高权限账号的显式模型。

为什么要有这个模块
------------------
账号树（``src/kernels/identity/_accounts.py``）早就区分了"根"与"成员"：
首个注册者成为唯一根，能管整棵树；成员只能看/管自己的子树。但"根"一直
是**隐式**概念 —— 没有以 Owner 命名的接口、没有"谁是 Owner、怎么产生
的"的统一答案、没有一处能一次看到"全部成员 + 全部任务 + 用量 + 审计"的
Owner 总览。本模块把这层显式化，**不替换**账号树与 RBAC，只在它们之上
收敛出 Owner 专属面：

* **Owner 的定义**：账号树的根（``parent_id is None``，``depth == 0``），
  即首次启动向导（``POST /v1/accounts/register``）建的第一个账号。
  单人模式（还没有账号树、令牌无账号节点）下，当前账号按既有语义被视作
  独立根，也就是 Owner，功能完整可用 —— 这与 ``account_context`` 的
  standalone 口径一致，不是本模块新开的提权口径。
* **Owner 怎么产生**：只在首次注册时产生（``register_root_account``，
  已存在根时 409）。**本模块不新建任何"指定某人为 Owner"的入口**，
  Owner 身份只来自树位置，不来自请求体、令牌 claim 或配置文件。
* **已有账号库的认定（迁移/兼容）**：复用身份内核启动时的
  ``_bootstrap_account_tree``，不另写迁移：
  - 有合法根 → 它就是 Owner，不动；
  - 历史多根 → 最早注册者当选唯一根，其余收敛为它的直接成员（收敛记录
    进 ``registry_refusals``，不是悄悄提权）；
  - 只有人类身份、没有账号记录的老库 → 最早注册的人类身份成为根，其余
    挂在根下；恢复码缺失时根记录**不落盘**并持续告警，直到 Owner 重新
    生成恢复码；
  - 父不存在 / 成环的行被拒收（fail-closed），不进树。
  本模块只把这次认定的结果与告警原样暴露在 ``GET /v1/owner/me`` 的
  ``origin`` / ``migration_notes`` 里，不做任何静默改写。
* **隔离**：普通用户接口本来就按 ``GoalViewer.sees`` / 账号树收敛
  （任务、记忆、审计、文件、用量均如此，见
  ``tests/gateway/test_account_scope_three_layers.py``）；本模块的 Owner
  接口在此之上再加一道 ``require_owner``：非 Owner 一律 403
  ``OWNER_ACCESS_DENIED``，**不返回 200 空数据**（空数据会让调用方误以为
  "没有成员/没有任务"），也不返回别人的任何数据。
* **Owner 也受限**：Owner 不能通过本模块改自己的权限/状态（403
  ``OWNER_PROTECTED``，防自降级把自己锁死在树外）；删 Owner 同样被内核
  以 ``ACCOUNT_DELETE_ROOT_FORBIDDEN`` 拒绝。改别人权限仍走内核的
  ``grant_decision``（不能授出自己没有的 scope，``account:key_borrow``
  永不可授）。
* **审计**：Owner 的写操作（邀请成员、改权限、改状态）与越权尝试
  （非 Owner 调 Owner 接口、针对 Owner 本人的改动）都经
  ``IdentityManager.record_event`` 写进同一条哈希链；Owner 用
  ``GET /v1/owner/audit`` 查（复用 ``gateway/audit.py`` 的只读查询，
  不另起审计流）。Owner 的读操作（总览 / 成员 / 全部任务）同样写一条
  ``owner.<面>.read`` 事件（"谁看了什么"，actor=Owner、target=Owner
  本人），审计失败只记日志、不影响本次读取。``GET /v1/owner/me``
  （每次鉴权都会调）和 ``GET /v1/owner/audit`` 本身不写审计：前者是
  高频门禁探针，后者写了会自我追加、无限膨胀。

空态
----
成员、任务、审计都没有真实数据时返回空列表/零计数，不放任何演示数据。
种子假数据只在 ``LIUHAO_DEMO_SEED=1`` 时出现 —— 本模块不读该变量，
也不产生任何种子数据。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal
from .accounts import PermissionsRequest, StatusRequest, _kernel_call

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/owner", tags=["owner"])

#: 非 Owner 调 Owner 接口的稳定错误码（403，不是 404：调用方明确知道
#: 自己在请求 Owner 面，404 会伪装成"接口不存在"）。
CODE_OWNER_REQUIRED = "OWNER_ACCESS_DENIED"
#: 针对 Owner 本人的权限/状态改动被拒绝的稳定错误码。
CODE_OWNER_PROTECTED = "OWNER_PROTECTED"


def require_owner(
    context: AccountContext = Depends(require_account_principal),
) -> AccountContext:
    """Owner 门禁：账号树根（含单人模式的独立根）才能继续。

    普通用户（含手握 ``account:manage_children`` 的成员管理员）到此一律
    403 —— 能建自己的下级不等于能看全站。拒绝本身写审计（"谁试过进
    Owner 面"），审计失败不改变拒绝结果。
    """
    if context.is_root:
        return context
    try:
        get_identity_manager().record_event(
            action="owner.access.denied",
            actor=context.principal,
            target=context.principal,
            outcome="denied",
            details={"error_code": CODE_OWNER_REQUIRED.lower()},
        )
    except Exception:  # noqa: BLE001 - 审计失败不能把 403 变成 500
        logger.warning("owner denial audit failed for %s", context.principal)
    raise deny(403, CODE_OWNER_REQUIRED, "无权执行该操作")


def describe_owner_origin(manager) -> Dict[str, Any]:
    """Owner 的来源说明：谁是 Owner、它是怎么来的、有无迁移告警。

    ``origin`` 取值：
    * ``first_registration`` —— 首次启动向导注册的第一个账号；
    * ``legacy_migration`` —— 老库启动时由内核从人类身份重建/收敛出的根；
    * ``standalone_single_user`` —— 还没有账号树，当前账号是单人模式的
      独立根（即 Owner）；
    * ``none`` —— 尚未初始化（无任何账号），本次请求不该走到这里。
    """
    nodes = manager.account_nodes()
    root = manager.account_root()
    warnings: List[str] = []
    try:
        warnings = list(
            manager.describe_identity_namespaces().get("account_warnings") or []
        )
    except Exception:  # noqa: BLE001 - 告警读不到不影响"谁是 Owner"的结论
        warnings = []
    if root is None:
        return {
            "owner_principal": None,
            "origin": "none",
            "migration_notes": warnings,
        }
    if not nodes:
        origin = "standalone_single_user"
    elif getattr(root, "source", "") == "migration":
        origin = "legacy_migration"
    else:
        origin = "first_registration"
    return {
        "owner_principal": root.principal,
        "origin": origin,
        "owner_created_at": root.created_at or None,
        "migration_notes": warnings,
    }


def _member_entry(node: _accounts.AccountNode) -> Dict[str, Any]:
    """Owner 视角的成员条目：不含 parent/depth/root 等结构字段。"""
    entry = _accounts.public_member(node, show_key_authorization=True)
    entry["is_owner"] = bool(node.is_root)
    return entry


def _audit_owner(
    action: str, actor: str, target: str, outcome: str, details: Dict[str, Any]
) -> bool:
    """Owner 写操作 / 拒绝的审计；失败只记日志，调用方如实报告。"""
    try:
        return bool(
            get_identity_manager().record_event(
                action=action,
                actor=actor,
                target=target,
                outcome=outcome,
                details=details,
            )
        )
    except Exception:  # noqa: BLE001 - 审计失败不改变业务结果
        logger.warning("owner audit failed action=%s actor=%s", action, actor)
        return False


def _goals_snapshot(context: AccountContext) -> List[Dict[str, Any]]:
    """Owner 子树内的全部目标（含任务摘要），按 ``GoalViewer.sees`` 过滤。

    只读执行内核当前热集合里的目标（与 ``gateway/org.py`` 的影响检查同
    口径）；读不到时返回空列表并由调用方标注 ``checked=false``，绝不把
    "查不到"说成"没有任务"。
    """
    try:
        from .ai_management import AIStateManager, GoalViewer, owner_of_entry

        manager = AIStateManager()
        viewer = GoalViewer(
            principal=context.principal,
            subtree=tuple(context.subtree),
            is_root=True,
            standalone=context.standalone,
        )
        with manager._goal_lock:
            goals = [dict(goal) for goal in manager._goals.values() if isinstance(goal, dict)]
    except Exception as exc:  # noqa: BLE001 - 读不到任务库时调用方如实标注
        logger.warning("owner tasks snapshot unavailable: %s", exc)
        return []
    visible: List[Dict[str, Any]] = []
    for goal in goals:
        try:
            if not viewer.sees(goal):
                continue
        except Exception:  # noqa: BLE001 - 归属判不了就不把它算进结论
            continue
        tasks: List[Dict[str, Any]] = []
        for task in goal.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            assignees: List[str] = []
            if task.get("assigned_employee"):
                assignees.append(str(task.get("assigned_employee")))
            assignees.extend(str(name) for name in task.get("assigned_employees") or [] if name)
            tasks.append(
                {
                    "task_id": task.get("task_id"),
                    "status": str(task.get("status") or ""),
                    "assignees": sorted(set(assignees)),
                }
            )
        visible.append(
            {
                "goal_id": goal.get("goal_id"),
                "owner_account_id": owner_of_entry(goal),
                "state": str(goal.get("state") or ""),
                "title": str(goal.get("natural_language") or "")[:200],
                "tasks": tasks,
            }
        )
    visible.sort(key=lambda item: str(item.get("goal_id") or ""))
    return visible


def _audit_count(principals: List[str]) -> Optional[int]:
    """Owner 子树内审计事件总数；读不到返回 None（调用方如实标注）。"""
    if not principals:
        return 0
    try:
        from ..kernels.audit import get_audit_store

        store = get_audit_store()
        placeholders = ",".join(["?"] * len(principals))

        def _run(conn):
            row = conn.execute(
                f"SELECT COUNT(*) FROM audit_events WHERE principal_id IN ({placeholders})",
                tuple(principals),
            ).fetchone()
            return int(row[0]) if row else 0

        return store._read_only(_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("owner audit count unavailable: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# 只读：我是谁 / 总览 / 成员 / 全部任务 / 审计
# --------------------------------------------------------------------------- #


@router.get("/me")
def owner_me(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """当前登录者是否为 Owner，以及 Owner 是谁、怎么来的。"""
    manager = get_identity_manager()
    origin = describe_owner_origin(manager)
    return {
        "is_owner": True,
        "principal": context.principal,
        "standalone": bool(context.standalone),
        "member_count": max(0, len(context.subtree) - 1),
        **origin,
    }


@router.get("/overview")
def owner_overview(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """Owner 总览：成员、任务、用量、审计的真实计数（没有数据就是 0/空）。

    用量读不到 / 任务库读不到时对应字段带 ``checked=false`` 与原因，
    不把"查不到"报成 0。
    """
    manager = get_identity_manager()
    nodes = manager.account_nodes()
    members = [_member_entry(node) for node in sorted(nodes.values(), key=lambda n: n.principal)]
    by_status: Dict[str, int] = {}
    for node in nodes.values():
        by_status[node.status] = by_status.get(node.status, 0) + 1
    goals = _goals_snapshot(context)
    tasks_total = sum(len(goal["tasks"]) for goal in goals)
    tasks_by_owner: Dict[str, int] = {}
    for goal in goals:
        owner = str(goal["owner_account_id"])
        tasks_by_owner[owner] = tasks_by_owner.get(owner, 0) + len(goal["tasks"])
    usage: Dict[str, Any] = {"checked": False, "reason": "用量库暂时读不到"}
    try:
        from .account_usage import usage_summary

        summary = usage_summary(
            context.principal,
            include_callers=True,
            owners=tuple(context.subtree),
        )
        usage = {"checked": True, **summary}
    except Exception as exc:  # noqa: BLE001 - 用量失败不影响总览其余部分
        logger.warning("owner overview usage unavailable: %s", exc)
        usage = {"checked": False, "reason": f"用量库暂时读不到（{type(exc).__name__}）"}
    audit_total = _audit_count(list(context.subtree))
    # Owner 读总览本身记一条审计（"谁看了什么"）；读失败不影响本次返回。
    _audit_owner(
        "owner.overview.read", context.principal, context.principal, "allowed", {}
    )
    return {
        "owner": describe_owner_origin(manager),
        "members": {
            "total": len(members),
            "by_status": by_status,
            "count": len(members),
        },
        "tasks": {
            "goals": len(goals),
            "tasks": tasks_total,
            "by_owner": tasks_by_owner,
            "checked": True,
        },
        "usage": usage,
        "audit": {
            "events": audit_total,
            "checked": audit_total is not None,
        },
    }


@router.get("/members")
def owner_members(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """全部成员（整棵子树扁平列表，含 Owner 本人）；没有其他成员时为空列表。"""
    manager = get_identity_manager()
    nodes = manager.account_nodes()
    members = [_member_entry(node) for node in sorted(nodes.values(), key=lambda n: n.principal)]
    if context.standalone and not members:
        members = [
            {
                "principal": context.principal,
                "permissions": [],
                "status": "active",
                "created_at": "",
                "is_owner": True,
            }
        ]
    # Owner 读成员列表记一条审计（"谁看了什么"）；读失败不影响本次返回。
    _audit_owner(
        "owner.members.read", context.principal, context.principal, "allowed", {}
    )
    return {"members": members, "count": len(members)}


@router.get("/tasks")
def owner_tasks(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """全部任务：Owner 子树内所有账号的目标与任务；没有任务时为空列表。"""
    goals = _goals_snapshot(context)
    # Owner 读全部任务记一条审计（"谁看了什么"）；读失败不影响本次返回。
    _audit_owner(
        "owner.tasks.read", context.principal, context.principal, "allowed", {}
    )
    return {
        "goals": goals,
        "goal_count": len(goals),
        "task_count": sum(len(goal["tasks"]) for goal in goals),
        "scope": "subtree",
        "owners": sorted({str(goal["owner_account_id"]) for goal in goals}),
    }


@router.get("/audit")
def owner_audit(
    principal: Optional[str] = Query(None, description="只看某一成员（须在 Owner 子树内）"),
    action: Optional[str] = Query(None, description="按内核动作名过滤"),
    outcome: Optional[str] = Query(None, description="按结果过滤"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
    reverse: bool = Query(True, description="true = 最新的在前"),
    context: AccountContext = Depends(require_owner),
) -> Dict[str, Any]:
    """Owner 查审计：整棵子树的事件（复用 ``gateway/audit.py`` 的只读查询）。

    显式指定子树外的账号一律 404（与"不存在"不可区分，不暴露其存在与否）。
    """
    from .audit import _read_events

    if principal and principal not in context.subtree:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    principals = [principal] if principal else list(context.subtree)
    try:
        events = _read_events(
            principals=principals,
            action=action,
            outcome=outcome,
            limit=limit,
            reverse=reverse,
        )
    except Exception as exc:  # noqa: BLE001 - 读失败必须如实说出来
        logger.warning("owner audit query failed: %s", exc, exc_info=True)
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="审计记录暂时读不到。请确认数据目录完整且对服务可读。",
        )
    for event in events:
        event["is_self"] = str(event.get("principal_id") or "") == context.principal
    return {
        "events": events,
        "count": len(events),
        "read_scope": {"mode": "subtree", "principals": principals, "root_view": True},
        "filters": {
            "principal": principal,
            "action": action,
            "outcome": outcome,
            "limit": limit,
            "reverse": reverse,
        },
        "truncated": len(events) >= limit,
    }


# --------------------------------------------------------------------------- #
# 写：邀请成员 / 改别人权限 / 改别人状态（Owner 本人受保护）
# --------------------------------------------------------------------------- #


class OwnerInviteRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(None, max_length=80)
    role: Optional[str] = Field(None, description="角色预设：viewer / operator / manager")
    permissions: Optional[List[str]] = None
    allow_key_sharing: bool = False


@router.post("/members", status_code=201)
def owner_invite(
    payload: OwnerInviteRequest, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 邀请/创建普通用户：默认只有自己的最小权限，激活码一次性返回。

    复用账号树的成员创建（``create_member_account``）：重名 409、越级授
    权 403、防提权判定都在内核里，不在这里另写一份。
    """
    manager = get_identity_manager()
    node, activation_code = _kernel_call(
        manager.create_member_account,
        context.principal,
        payload.principal,
        permissions=payload.permissions,
        role=payload.role,
        display_name=payload.display_name,
        allow_key_sharing=payload.allow_key_sharing,
    )
    written = _audit_owner(
        "owner.member.create",
        context.principal,
        payload.principal,
        "allowed",
        {"role": payload.role or "", "permissions": sorted(payload.permissions or [])},
    )
    return {
        "account": _member_entry(node),
        "activation_code": activation_code,
        "notice": "把一次性激活码交给该成员，由其在激活页设置密码。",
        "audit_written": written,
    }


def _refuse_owner_target(context: AccountContext, target: str, action: str) -> None:
    """针对 Owner 本人的改动：403 + 审计，不走到内核。"""
    _audit_owner(
        action,
        context.principal,
        target,
        "denied",
        {"error_code": CODE_OWNER_PROTECTED.lower()},
    )
    raise deny(403, CODE_OWNER_PROTECTED, "无权执行该操作")


@router.patch("/members/{principal}/permissions")
def owner_update_permissions(
    principal: str,
    payload: PermissionsRequest,
    context: AccountContext = Depends(require_owner),
) -> Dict[str, Any]:
    """Owner 改别人权限。目标是 Owner 本人时 403（防自降级/被改）。"""
    if principal == context.principal:
        _refuse_owner_target(context, principal, "owner.permission.update.denied")
    node = _kernel_call(
        get_identity_manager().grant_account_permissions,
        context.principal,
        principal,
        payload.grant,
        payload.revoke,
    )
    written = _audit_owner(
        "owner.permission.update",
        context.principal,
        principal,
        "allowed",
        {"grant": sorted(payload.grant or []), "revoke": sorted(payload.revoke or [])},
    )
    return {"account": _member_entry(node), "audit_written": written}


@router.patch("/members/{principal}/status")
def owner_update_status(
    principal: str,
    payload: StatusRequest,
    context: AccountContext = Depends(require_owner),
) -> Dict[str, Any]:
    """Owner 停用/恢复/删除别人。目标是 Owner 本人时 403（删 Owner 内核也拒）。"""
    if principal == context.principal:
        _refuse_owner_target(context, principal, "owner.status.update.denied")
    result = _kernel_call(
        get_identity_manager().set_account_status,
        context.principal,
        principal,
        payload.status,
        payload.reason,
    )
    written = _audit_owner(
        "owner.status.update",
        context.principal,
        principal,
        "allowed",
        {"status": payload.status},
    )
    if isinstance(result, dict):
        result["audit_written"] = written
        return result
    return {"ok": True, "audit_written": written}
