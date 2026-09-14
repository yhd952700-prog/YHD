"""The colon-permission registry: authority A's space, mapped to authority B.

There are three access-control authorities in this repo (measured, not
assumed -- see ``docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md``):

* **A** -- this kernel (``src/kernels/security``): **colon** permissions such
  as ``context:read``, held in the in-memory ``_rbac_rules`` table.
* **B** -- ``src/kernels/policy``: **point-dot** kernel actions such as
  ``resource.allocate``, gated by ``INTERNAL_SERVICE_ALLOWED_ACTIONS`` and
  the ``@kernel_action`` decorator.
* **C** -- ``src/security``: enum-parameterised (gateway/tenant boundary).

B does not understand A's permission names and A does not consult B, so a
request A allows is invisible to B and vice versa. This module is the **first
half** of closing that gap: an explicit, cross-checked map from A's
permissions to B's actions, plus the discovery helper that stops the map from
drifting silently.

**It deliberately changes no verdict.** ``decide_access`` still decides
exactly as before; what is new is that an unknown or unseeded permission is
now *reported* instead of quietly denied. Switching A over to B is the second
half and must not happen until this map is complete -- a permission with no
entry here would land on B's ``default_deny`` and break the caller.

The honest headline: **most of A's permissions have no counterpart in B.**
B's 44 actions are all *effectful* kernel operations; A is dominated by
*read/query* permissions that simply do not exist over there. That is why
``kernel_actions`` is allowed to be empty -- leaving it empty is a finding,
not an oversight, and ``unmapped_permissions`` reports it.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Dict, List, NamedTuple, Optional, Tuple

KERNELS_DIR = pathlib.Path(__file__).resolve().parent.parent  # src/kernels
REPO_ROOT = KERNELS_DIR.parent.parent  # repo root

#: Roots scanned for permission call sites.
SCAN_ROOTS: Tuple[pathlib.Path, ...] = (
    REPO_ROOT / "src",
    REPO_ROOT / "LiuHao-O" / "packages",
)

#: Calls whose second positional argument is a colon permission.
_PERMISSION_CALLS = frozenset(
    {"decide_access", "check_rbac", "check_abac", "_evaluate_access"}
)

#: Keyword arguments whose value is a colon permission.
_PERMISSION_KEYWORDS = frozenset({"required_permission", "permission"})

#: Calls that take a ``permission=`` argument but do **not** decide anything --
#: they stamp the string into an audit record. Measured: this is how
#: ``plugin:manage`` (``src/kernels/plugin/__init__.py``, 6 occurrences) gets
#: into the tree; it is an audit field, never a permission anyone is checked
#: against. Skipping by callee keeps the guard precise without an allowlist
#: of "strings we promise are not permissions".
_AUDIT_ONLY_CALLS = frozenset({"audit_log", "log_event"})

_COLON_PERMISSION = None  # populated lazily to keep the import cheap


class PermissionMapping(NamedTuple):
    """One colon permission and what is known about it.

    Attributes:
        permission:     the colon-form name (authority A).
        role:           the role A's seed rule grants it to, or ``None`` when
                        no seed rule exists -- an unseeded permission is
                        *always* denied, because ``check_rbac`` is fail-closed.
        kernel_actions: the point-dot action(s) in authority B that this
                        permission corresponds to. Empty when B has no
                        counterpart, which is a genuine finding rather than
                        missing data.
        effect:         ``read`` | ``write`` | ``admin`` -- how dangerous the
                        permission is, independent of which authority holds it.
        note:           why the mapping is what it is.
    """

    permission: str
    role: Optional[str]
    kernel_actions: Tuple[str, ...]
    effect: str
    note: str


#: The authoritative registry. Every entry is backed by a runtime
#: cross-check in ``tests/kernels/security/test_permission_map.py``:
#: the ``role`` must match the real seed table and every name in
#: ``kernel_actions`` must be a real decorated ``@kernel_action``.
PERMISSION_MAP: Dict[str, PermissionMapping] = {
    "context:read": PermissionMapping(
        "context:read", "viewer", (), "read",
        "读上下文。B 只有 context.set_scope（改），没有读动作 ⇒ 无对应。",
    ),
    "context:write": PermissionMapping(
        "context:write", "admin", ("context.set_scope",), "write",
        "改上下文 ⇒ 对应 B 唯一的 context 变更动作。",
    ),
    "capability:lookup": PermissionMapping(
        "capability:lookup", "viewer", (), "read",
        "查能力。B 的 capability.* 全是注册/废弃/下线（改），无读 ⇒ 无对应。",
    ),
    "capability:manage": PermissionMapping(
        "capability:manage", "admin",
        ("capability.register", "capability.deprecate", "capability.retire"),
        "write",
        "管能力 ⇒ 对应 B 的三个 capability 变更动作。",
    ),
    "execution:plan": PermissionMapping(
        "execution:plan", "operator", (), "write",
        "编排计划是能力层概念；B 只有 execution.execute/create_checkpoint，"
        "没有独立的 plan 动作 ⇒ 无对应。",
    ),
    "execution:trigger": PermissionMapping(
        "execution:trigger", "admin", ("execution.execute",), "write",
        "触发执行 ⇒ 对应 B 的 execution.execute。",
    ),
    "resource:allocate": PermissionMapping(
        "resource:allocate", "operator", ("resource.allocate",), "write",
        "同名且同义，A↔B 唯一完全对齐的一条。",
    ),
    "resource:query": PermissionMapping(
        "resource:query", "viewer", (), "read",
        "查配额。B 的 resource.* 全是变更类 ⇒ 无对应。",
    ),
    "policy:manage": PermissionMapping(
        "policy:manage", "admin", (), "admin",
        "改策略。policy 内核**没有任何** @kernel_action ⇒ 无对应。",
    ),
    "evaluation:run": PermissionMapping(
        "evaluation:run", "operator", ("evaluation.evaluate",), "write",
        "跑评估 ⇒ 对应 B 的 evaluation.evaluate。",
    ),
    "audit:query": PermissionMapping(
        "audit:query", "auditor", (), "read",
        "查审计。audit 内核**没有任何** @kernel_action ⇒ 无对应。",
    ),
    "audit:log": PermissionMapping(
        "audit:log", "admin", (), "write",
        "写审计。同上，audit 内核没有 @kernel_action ⇒ 无对应。",
    ),
    "research:read": PermissionMapping(
        "research:read", None, (), "read",
        "读研究成果。**A 侧没有任何种子规则** ⇒ 永远判 deny（fail-closed）；"
        "B 侧 44 个动作里也没有 research.* ⇒ 同样无对应。"
        "这条是被 src/ai/vhl_benchmark.py:182 的生产调用点逼出来的："
        "它登记在这里是为了让这个洞**可见**，而不是让它安静地被拒。",
    ),
    # The entry above is deliberately registered while unseeded: leaving it
    # out would hide the fact that a production call site asks for a
    # permission that can never be granted.
}


def _colon_pattern():
    """``^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$`` -- shape of a colon permission."""
    global _COLON_PERMISSION
    if _COLON_PERMISSION is None:
        import re

        _COLON_PERMISSION = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")
    return _COLON_PERMISSION


def discover_permission_literals() -> List[str]:
    """Every colon-permission **literal** reachable in production code.

    Scans two shapes only, so the guard stays precise instead of flagging
    every ``"vendor:model"`` string in the repo:

    * a keyword argument named ``required_permission`` / ``permission``
      whose value is a string constant;
    * a string constant passed as the second positional argument to
      ``decide_access`` / ``check_rbac`` / ``check_abac``.

    Returns a sorted, de-duplicated list. Values that are not permissions
    (model ids, audit field values) are excluded by construction.
    """
    pattern = _colon_pattern()
    found = set()

    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if callee in _AUDIT_ONLY_CALLS:
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg in _PERMISSION_KEYWORDS
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                        and pattern.match(kw.value.value)
                    ):
                        found.add(kw.value.value)
                if callee in _PERMISSION_CALLS and len(node.args) >= 2:
                    arg = node.args[1]
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and pattern.match(arg.value)
                    ):
                        found.add(arg.value)

    return sorted(found)


def get_mapping(permission: str) -> Optional[PermissionMapping]:
    return PERMISSION_MAP.get(permission)


def is_known(permission: str) -> bool:
    """True when the permission has a registry entry at all."""
    return permission in PERMISSION_MAP


def is_seeded(permission: str) -> bool:
    """True when the permission has a seed rule, i.e. can ever be granted."""
    entry = PERMISSION_MAP.get(permission)
    return bool(entry and entry.role)


def unmapped_permissions() -> List[str]:
    """Permissions with **no** counterpart in authority B (sorted)."""
    return sorted(p for p, m in PERMISSION_MAP.items() if not m.kernel_actions)


def unseeded_permissions() -> List[str]:
    """Permissions with no seed rule -- they can never be granted (sorted)."""
    return sorted(p for p, m in PERMISSION_MAP.items() if not m.role)
