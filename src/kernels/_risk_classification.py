"""Canonical risk classification for the 43 kernel-layer ``@kernel_action`` actions.

D8 deliverable (Policy C-2 prerequisite). **This module is PURE DATA** -- it
imports only the standard library and changing it never alters any policy
decision. It exists so that the (future) C-2 enforcement flip has an
*objective* cut line (``ENFORCED_TIERS``) instead of a hardcoded list, and so
that the ``risk_level`` parameter -- which every ``@kernel_action`` call site
left at its inert ``"LOW"`` default -- finally carries real signal.

Why a single registry instead of per-call annotations?
    The 43 call sites live across 12 kernel modules. Scattering the tier into
    each decorator call is exactly what let the field drift to a dead default
    (everyone forgets to set it). One auditable table, cross-checked by an AST
    completeness guard (``discover_kernel_action_names``), forces a deliberate
    classification the moment a new kernel action is added.

Zero execution risk:
    Nothing here calls the policy engine or the decorator. The decorator in
    ``src/kernels/_crosscutting.py`` *reads* ``get_kernel_action_risk`` to
    populate ``action.risk_level``. For the internal-service actor path that
    input is inert (the only rule that reads ``risk_level`` requires
    ``actor.type == "human"``), so D8 changes what the engine is *told* without
    changing any *verdict*. Once C-2 turns enforcement on, the tiers are
    already correct -- no second pass over 43 call sites.
"""

from __future__ import annotations

import ast
import pathlib
from enum import Enum
from typing import Dict, NamedTuple, Optional

KERNELS_DIR = pathlib.Path(__file__).resolve().parent


class RiskTier(str, Enum):
    """Security risk tier for a kernel action.

    Ordered LOW < MEDIUM < HIGH < CRITICAL by blast radius / authority impact.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def __ge__(self, other: "RiskTier") -> bool:  # type: ignore[override]
        if not isinstance(other, RiskTier):
            return NotImplemented
        return self.rank >= other.rank

    def __lt__(self, other: "RiskTier") -> bool:  # type: ignore[override]
        if not isinstance(other, RiskTier):
            return NotImplemented
        return self.rank < other.rank


_RANK: Dict[RiskTier, int] = {
    RiskTier.LOW: 0,
    RiskTier.MEDIUM: 1,
    RiskTier.HIGH: 2,
    RiskTier.CRITICAL: 3,
}


# C-2 cut line: enforcement (deny execution unless a verified human actor)
# applies only to these tiers. LOW/MEDIUM stay additive (record only).
ENFORCED_TIERS: frozenset = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})


def is_enforced_tier(tier: RiskTier) -> bool:
    """Return True iff ``tier`` would be enforcement-gated under C-2."""
    return tier in ENFORCED_TIERS


class ActionRisk(NamedTuple):
    """Classification record for one kernel action."""

    tier: RiskTier
    authority_changing: bool
    destructive: bool
    rationale: str


# --------------------------------------------------------------------------- #
# The 43 actions, classified by a single mechanical rubric (so the table is
# auditable, not a matter of taste):
#
#   LOW      = query / compute / bookkeeping: no authority change, no
#              destruction. (Equals INTERNAL_SERVICE_ALLOWED_ACTIONS -- the
#              pre-approved internal-service set; see test cross-check.)
#   MEDIUM   = reversible operational authority/state change, bounded blast
#              radius (scope, planning state, topology add/remove, quota
#              lifecycle, trust-score write, identity creation).
#   HIGH     = authority change OR locally destructive OR code/adapter
#              lifecycle (permissions, capability register/deprecate, plugin
#              lifecycle, RBAC roles, trust establish/revoke, adapter register,
#              irreversible state/history loss).
#   CRITICAL = system-wide authority change or irreversible system-wide
#              breakage (capability.retire cascades to every dependent;
#              security.set_abac_rule mutates the access-control boundary
#              itself -- the keys to the kingdom).
# --------------------------------------------------------------------------- #

KERNEL_ACTION_RISK: Dict[str, ActionRisk] = {
    # ---- LOW (14): query / compute / bookkeeping ------------------------- #
    "context.compress": ActionRisk(
        RiskTier.LOW, False, False,
        "上下文压缩：计算/簿记，不改变权限、不销毁记录。",
    ),
    "context.process": ActionRisk(
        RiskTier.LOW, False, False,
        "上下文处理：摄入与计算，纯簿记。",
    ),
    "evaluation.evaluate": ActionRisk(
        RiskTier.LOW, False, False,
        "只读评分：不改变任何状态。",
    ),
    "event.publish": ActionRisk(
        RiskTier.LOW, False, False,
        "事件发布：消息总线机制，非权限变更。",
    ),
    "event.subscribe": ActionRisk(
        RiskTier.LOW, False, False,
        "订阅事件：注册监听器，可退订。",
    ),
    "event.unsubscribe": ActionRisk(
        RiskTier.LOW, False, False,
        "退订事件：移除监听器，可逆。",
    ),
    "event.retry_dead_letter": ActionRisk(
        RiskTier.LOW, False, False,
        "死信重试：重新投递，不改变权限。",
    ),
    "execution.execute": ActionRisk(
        RiskTier.LOW, False, False,
        "执行已批准计划：运行环操作，作用域有界，非权限变更。",
    ),
    "execution.create_checkpoint": ActionRisk(
        RiskTier.LOW, False, False,
        "创建检查点：快照，簿记。",
    ),
    "memory.store": ActionRisk(
        RiskTier.LOW, False, False,
        "记忆持久化：写入，非销毁。",
    ),
    "memory.compress": ActionRisk(
        RiskTier.LOW, False, False,
        "记忆压缩：计算/簿记。",
    ),
    "network.route": ActionRisk(
        RiskTier.LOW, False, False,
        "路由已存在路线：机制，非拓扑变更。",
    ),
    "resource.release": ActionRisk(
        RiskTier.LOW, False, False,
        "释放配额：归还容量，簿记。",
    ),
    "security.decide_access": ActionRisk(
        RiskTier.LOW, False, False,
        "读取访问决策：查询，非权限变更。",
    ),

    # ---- MEDIUM (12): reversible operational authority/state ------------- #
    "context.set_scope": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "切换活动作用域：权限维度变化，但操作性、可逆。",
    ),
    "evaluation.apply_feedback": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "施加反馈：改变规划状态，可逆。",
    ),
    "evaluation.approve_replan": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "批准重规划：操作性授权，可逆。",
    ),
    "evaluation.execute_replan": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "执行重规划：操作性，有界。",
    ),
    "identity.create_identity": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "创建身份：建立主体（尚未赋权），可逆。",
    ),
    "network.add_route": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "新增路由：拓扑变更，可逆（可移除）。",
    ),
    "network.remove_route": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "移除路由：拓扑变更，可逆（可重建）。",
    ),
    "resource.create_quota": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "创建配额：容量授权，可逆。",
    ),
    "resource.allocate": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "分配配额：可逆（可释放）。",
    ),
    "resource.commit": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "提交分配：可逆（可释放）。",
    ),
    "trust.assign_score": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "设定信任分：写安全信号，可逆（可更新）。",
    ),
    "trust.update_score": ActionRisk(
        RiskTier.MEDIUM, True, False,
        "更新信任分：写安全信号，可逆。",
    ),

    # ---- HIGH (15): authority change / destructive / code lifecycle ------ #
    "identity.grant_permission": ActionRisk(
        RiskTier.HIGH, True, False,
        "授予权限：权限变更。",
    ),
    "identity.revoke_permission": ActionRisk(
        RiskTier.HIGH, True, False,
        "撤销权限：权限变更。",
    ),
    "capability.register": ActionRisk(
        RiskTier.HIGH, True, False,
        "注册能力：部署/登记代码（能力面扩展）。",
    ),
    "capability.deprecate": ActionRisk(
        RiskTier.HIGH, True, False,
        "弃用能力：可能影响依赖方。",
    ),
    "trust.establish_trust": ActionRisk(
        RiskTier.HIGH, True, False,
        "建立信任：信任基设变更。",
    ),
    "trust.revoke": ActionRisk(
        RiskTier.HIGH, True, False,
        "撤销信任：信任基设变更。",
    ),
    "network.register_adapter": ActionRisk(
        RiskTier.HIGH, True, False,
        "注册外部适配器：新增攻击面/代码。",
    ),
    "plugin.register_plugin": ActionRisk(
        RiskTier.HIGH, True, False,
        "注册插件：安装代码。",
    ),
    "plugin.unregister_plugin": ActionRisk(
        RiskTier.HIGH, True, False,
        "注销插件：移除代码（可能影响依赖方）。",
    ),
    "plugin.activate_plugin": ActionRisk(
        RiskTier.HIGH, True, False,
        "激活插件：启用代码。",
    ),
    "plugin.deactivate_plugin": ActionRisk(
        RiskTier.HIGH, True, False,
        "停用插件：禁用代码。",
    ),
    "security.grant_rbac_role": ActionRisk(
        RiskTier.HIGH, True, False,
        "授予 RBAC 角色：权限变更。",
    ),
    "security.revoke_rbac_role": ActionRisk(
        RiskTier.HIGH, True, False,
        "撤销 RBAC 角色：权限变更。",
    ),
    "memory.auto_cleanup": ActionRisk(
        RiskTier.HIGH, False, True,
        "自动清理记忆：不可逆地销毁已存状态。",
    ),
    "event.clear_history": ActionRisk(
        RiskTier.HIGH, False, True,
        "清空事件历史：不可逆的数据丢失。",
    ),

    # ---- CRITICAL (2): system-wide authority / irreversible breakage --- #
    "capability.retire": ActionRisk(
        RiskTier.CRITICAL, True, False,
        "退役能力：系统性移除，级联影响所有依赖方。",
    ),
    "security.set_abac_rule": ActionRisk(
        RiskTier.CRITICAL, True, False,
        "设置 ABAC 规则：直接改写访问控制边界本身（守门钥匙）。",
    ),
}

ALL_CLASSIFIED_ACTIONS: frozenset = frozenset(KERNEL_ACTION_RISK)


def get_kernel_action_risk(action: str) -> str:
    """Return the canonical ``risk_level`` string for ``action``.

    Falls back to ``"LOW"`` for unknown actions (safe default: the future C-2
    gate only escalates HIGH/CRITICAL, so an unclassified action is treated as
    the least dangerous -- and the completeness guard test refuses to let an
    action stay unclassified anyway).
    """
    rec = KERNEL_ACTION_RISK.get(action)
    return rec.tier.value if rec is not None else RiskTier.LOW.value


def get_action_risk(action: str) -> Optional[ActionRisk]:
    """Return the full :class:`ActionRisk` for ``action`` or ``None``."""
    return KERNEL_ACTION_RISK.get(action)


def discover_kernel_action_names() -> set:
    """Every kernel action name passed to ``@kernel_action("...")`` in src/.

    AST scan (not a keyword scan) so it tracks the real decorator usages and
    can be cross-checked against :data:`KERNEL_ACTION_RISK` -- the completeness
    guard that makes "add a kernel action without classifying it" a test
    failure instead of silent drift.
    """
    names: set = set()
    for path in KERNELS_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                func = dec.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname != "kernel_action":
                    continue
                arg0 = dec.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    names.add(arg0.value)
    return names
