"""The colon-permission registry: authority A's space, mapped to authority B.

There are three access-control authorities in this repo (measured, not
assumed -- see ``docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md``):

* **A** -- this kernel (``src/kernels/security``): **colon** permissions such
  as ``research:read``, held in the in-memory ``_rbac_rules`` table.
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

**2026-09-15 full cut: authority A now seeds no colon permissions at all, and
this registry carries only ``research:read``.** Measured with
``discover_permission_literals`` across the production roots: of the 13 entries
that used to live here, only ``research:read`` had any call site -- and that
one was never seeded, so it is fail-closed by design. The other 12 had zero
production consumers, so shipping them as seeded grants was dead
configuration; they were removed from both the seed table
(``src/kernels/security/__init__.py::_seed_default_rules``) and this registry.
``research:read`` stays registered-but-unseeded so its real call site
(``src/ai/vhl_benchmark.py``) remains visible rather than silently denied.

Historical context for the cut: most of A's permissions had no counterpart in
B. B's 44 actions are all *effectful* kernel operations, while A was dominated
by *read/query* permissions that do not exist over there -- so
``kernel_actions`` was allowed to be empty, a finding rather than an oversight.
Having a counterpart was still not enough: measured 2026-09-14, of the twelve
seeded permissions only **two** mapped onto a B action B actually allow-lists
for the internal service, so delegating A to B would have flipped the other
ten from ``allow`` to ``deny`` (all the reads, plus three admin permissions).
Worse, ``_adjudicate`` takes no ``principal_id``, so delegation would also
erase the subject dimension. Delegation was therefore **rejected**, not
deferred; see ``docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`` section 5. The
``b_delegatable_permissions`` / ``b_denied_under_delegation`` helpers remain
queryable (now trivially empty) so the measurement stays executable.
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
    # 2026-09-15 (boss-approved full cut): the 12 previously-seeded colon
    # permissions (context:read/write, capability:lookup/manage,
    # execution:plan/trigger, resource:allocate/query, policy:manage,
    # evaluation:run, audit:query/log) were removed from both the seed table
    # (src/kernels/security/__init__.py::_seed_default_rules) and this
    # registry. Measured with ``discover_permission_literals``: none had a
    # production call site, so they were dead grants that only widened the
    # unused authority surface. Only ``research:read`` remains, kept
    # registered-but-unseeded so its real call site stays visible.
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


def _internal_service_allowed_actions() -> frozenset:
    """Authority B's internal-service allow-list.

    Imported lazily: ``src.kernels.policy`` is a sibling kernel and importing
    it at module scope would make this audit module a load-order dependency of
    the Security Kernel.
    """
    from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS

    return frozenset(INTERNAL_SERVICE_ALLOWED_ACTIONS)


def b_delegatable_permissions() -> List[str]:
    """Seeded permissions B would still allow, if A delegated its verdict to B.

    A permission survives delegation only when it is seeded (so A could ever
    grant it) **and** at least one of its mapped ``kernel_actions`` is on B's
    internal-service allow-list. Having a counterpart action is *not* enough:
    ``_adjudicate`` asks "may the internal service do this action?", whereas
    ``decide_access`` asks "does *this principal* hold this permission?".

    Measured 2026-09-14 (before the full cut): exactly two of the twelve seeded
    permissions survived -- ``execution:trigger`` and ``evaluation:run``. The
    ten that did not were the reason delegation was rejected. After the
    2026-09-15 full cut nothing is seeded, so this now returns ``[]``; the
    function is kept so reading ``kernel_actions`` can never again be mistaken
    for "B will allow it".
    """
    allowed = _internal_service_allowed_actions()
    return sorted(
        permission
        for permission, mapping in PERMISSION_MAP.items()
        if mapping.role and any(action in allowed for action in mapping.kernel_actions)
    )


def b_denied_under_delegation() -> List[str]:
    """Seeded permissions B would flip to ``deny`` if A delegated to it.

    The complement of :func:`b_delegatable_permissions` within the seeded set.
    This is the blast radius of the rejected plan, kept queryable so the claim
    stays testable rather than becoming prose.
    """
    delegatable = set(b_delegatable_permissions())
    return sorted(
        permission
        for permission, mapping in PERMISSION_MAP.items()
        if mapping.role and permission not in delegatable
    )
