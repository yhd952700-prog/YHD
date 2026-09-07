"""Security Kernel — RBAC+ABAC+Vault+Audit enforcement

The Security Kernel provides comprehensive access control with RBAC and ABAC
policies, Vault Transit integration for crypto operations, and full audit logging.

依据 Definition Lock §112: Security Kernel 必须能够
- RBAC role-based access control with role hierarchies
- ABAC attribute-based access control with condition evaluation
- Vault Transit integration for cryptographic operations
- Complete audit trail for all access decisions
- Human sovereignty override capability
- Scope enforcement L0-L7
"""
from __future__ import annotations

from datetime import datetime
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from src.kernels._crosscutting import kernel_action

# Attempt Vault Transit import; graceful fallback if unavailable
try:
    from vault_connect import VaultClient, secret_id
    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False

    class VaultClient:
        def __init__(self, *args, **kwargs):
            pass

        def read(self, *args, **kwargs):
            return {"data": None}

        def write(self, *args, **kwargs):
            pass

        def list(self, *args, **kwargs):
            return []

        def delete(self, *args, **kwargs):
            pass
    secret_id = None


# ---------------------------------------------------------------------------
# Scope governance (S4 design ruling)
#
# The security kernel only honours the eight MemoryScope tiers L0-L7. Any
# other value (None, "", "global", "L9", ...) is rejected as an invalid
# scope and can never produce an ALLOW decision.
# ---------------------------------------------------------------------------
_VALID_SCOPES = frozenset(f"L{i}" for i in range(8))
_SCOPE_LEVEL = {s: i for i, s in enumerate(_VALID_SCOPES)}


def _is_valid_scope(scope: Any) -> bool:
    """True iff *scope* is a recognised MemoryScope tier string (L0-L7)."""
    return isinstance(scope, str) and scope in _VALID_SCOPES


# ---------------------------------------------------------------------------
# Protected security attributes (S3 design ruling)
#
# These attributes drive authorization decisions. Their STORED values are
# authoritative: a request may never override them, even to fill a "missing"
# key. Escalating clearance / trust / role / permission / scope from a
# caller-supplied value is exactly the privilege-escalation gap S3 closes.
# ---------------------------------------------------------------------------
_PROTECTED_SECURITY_ATTRS = frozenset({
    "trust_score", "trust", "clearance",
    "role", "roles", "permission", "permissions",
    "scope", "identity", "identity_id", "principal",
})


class AccessDecision(str, Enum):
    """Access decision outcomes."""
    ALLOW = "allow"
    DENY = "deny"
    CONDITIONAL = "conditional"
    DEFER = "defer"
    ESCALATE = "escalate"


# Normalizes audit result strings to the canonical counter keys used by
# SecurityEngine.stats(): check entries record "allowed"/"denied" while
# decision entries record "allow"/"deny".
_RESULT_KEY_ALIASES = {
    "allowed": "allow",
    "denied": "deny",
}


class RBACRole(str, Enum):
    """Predefined RBAC roles."""
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUDITOR = "auditor"
    SERVICE = "service"
    HUMAN_SOVEREIGNTY = "human_sovereignty"


class ABATCondition(str, Enum):
    """ABAC condition types."""
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    EQUALS = "eq"
    NOT_EQUALS = "ne"
    IN_SET = "in_set"
    NOT_IN_SET = "not_in_set"


@dataclass
class SecurityPrincipal:
    """Principal entity for access control."""
    id: str
    identity_id: str
    roles: Set[RBACRole] = field(default_factory=set)
    attributes: Dict[str, Any] = field(default_factory=dict)
    scope: str = "L1"
    trust_score: float = 0.5
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_validated: Optional[datetime] = None


@dataclass
class RBACRule:
    """RBAC access rule."""
    id: str
    role: RBACRole
    permission: str
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABACRule:
    """ABAC access rule with attribute conditions."""
    id: str
    permission: str
    condition_attribute: str
    condition_operator: ABATCondition
    condition_value: Any
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditLogEntry:
    """Audit log entry for security events."""
    id: str
    principal_id: str
    operation: str
    permission: str
    scope: str
    result: str
    reason: str
    timestamp: datetime
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


class SecurityEngine:
    """Core security engine with RBAC, ABAC, and audit capabilities."""

    def __init__(self):
        self._rbac_rules: Dict[str, RBACRule] = {}
        self._abac_rules: Dict[str, ABACRule] = {}
        self._principal_roles: Dict[str, Set[RBACRole]] = {}
        self._principal_attrs: Dict[str, Dict[str, Any]] = {}
        # Per-principal authorized scope ceiling (S4). Absent => no ceiling.
        self._principal_scopes: Dict[str, str] = {}
        self._audit_log: List[AuditLogEntry] = []
        self._lock = threading.RLock()

        self._seed_default_rules()

    def _seed_default_rules(self) -> None:
        """Seed default RBAC rules for core kernel operations.

        Single source of truth: the colon-form permission string is used
        both as the rule id, the rule's permission field, and the dict
        key. Checkers and callers therefore agree on one form.
        """
        default_rules = [
            ("context:read", RBACRole.VIEWER),
            ("context:write", RBACRole.ADMIN),
            ("capability:lookup", RBACRole.VIEWER),
            ("capability:manage", RBACRole.ADMIN),
            ("execution:plan", RBACRole.OPERATOR),
            ("execution:trigger", RBACRole.ADMIN),
            ("resource:allocate", RBACRole.OPERATOR),
            ("resource:query", RBACRole.VIEWER),
            ("policy:manage", RBACRole.ADMIN),
            ("evaluation:run", RBACRole.OPERATOR),
            ("audit:query", RBACRole.AUDITOR),
            ("audit:log", RBACRole.ADMIN),
        ]

        for permission, role in default_rules:
            self._rbac_rules[permission] = RBACRule(
                id=permission, role=role, permission=permission
            )

    def set_principal_roles(self, principal_id: str, roles: Set[RBACRole]) -> None:
        """Set RBAC roles for a principal."""
        with self._lock:
            self._principal_roles[principal_id] = roles

    def set_principal_attributes(self, principal_id: str, attributes: Dict[str, Any]) -> None:
        """Set ABAC attributes for a principal."""
        with self._lock:
            self._principal_attrs[principal_id] = attributes

    def set_principal_scope(self, principal_id: str, scope: str) -> None:
        """Record the maximum scope a principal is authorized to act at (S4).

        *scope* must be a valid tier (L0-L7); otherwise it is rejected so a
        malformed ceiling can never widen authority. The stored scope forms
        the ceiling used by ``_enforce_principal_scope``.
        """
        with self._lock:
            if not _is_valid_scope(scope):
                return
            self._principal_scopes[principal_id] = scope

    def _enforce_principal_scope(self, principal_id: str, scope: str) -> Optional[AccessDecision]:
        """Enforce the principal's authorized scope ceiling (S4).

        ``SecurityEngine.set_principal_scope`` records the maximum scope a
        principal may act at. A request is permitted only when the request
        scope level is <= the principal's authorized level -- equivalently,
        the principal's authorized scope is an *ancestor* (superset) of the
        requested scope in the authorization lattice. When no ceiling is
        recorded the check is a no-op (backward compatible).
        """
        p_scope = self._principal_scopes.get(principal_id)
        if p_scope is None:
            return None
        if _SCOPE_LEVEL[scope] > _SCOPE_LEVEL[p_scope]:
            return AccessDecision.DENY
        return None

    def _audit_rbac_check(
        self,
        principal_id: str,
        permission: str,
        scope: str,
        result: str,
        reason: str,
    ) -> None:
        """Append an RBAC audit entry (shared by the reject paths)."""
        audit = AuditLogEntry(
            id=str(uuid.uuid4())[:8],
            principal_id=principal_id,
            operation="rbac_check",
            permission=permission,
            scope=scope,
            result=result,
            reason=reason,
            timestamp=datetime.utcnow(),
        )
        self._audit_log.append(audit)

    @kernel_action("security.grant_rbac_role")
    def grant_rbac_role(self, principal_id: str, role: RBACRole, reason: str = "") -> bool:
        """Grant an RBAC role to a principal."""
        with self._lock:
            if principal_id not in self._principal_roles:
                self._principal_roles[principal_id] = set()
            self._principal_roles[principal_id].add(role)

            audit = AuditLogEntry(
                id=str(uuid.uuid4())[:8],
                principal_id=principal_id,
                operation="grant_role",
                permission=f"role:{role.value}",
                scope=principal_id,
                result="allowed",
                reason=reason or f"RBAC role {role.value} granted to principal {principal_id}",
                timestamp=datetime.utcnow(),
            )
            self._audit_log.append(audit)
            return True

    @kernel_action("security.revoke_rbac_role")
    def revoke_rbac_role(self, principal_id: str, role: RBACRole, reason: str = "") -> bool:
        """Revoke an RBAC role from a principal."""
        with self._lock:
            if principal_id in self._principal_roles and role in self._principal_roles[principal_id]:
                self._principal_roles[principal_id].discard(role)

                audit = AuditLogEntry(
                    id=str(uuid.uuid4())[:8],
                    principal_id=principal_id,
                    operation="revoke_role",
                    permission=f"role:{role.value}",
                    scope=principal_id,
                    result="allowed",
                    reason=reason or f"RBAC role {role.value} revoked from principal {principal_id}",
                    timestamp=datetime.utcnow(),
                )
                self._audit_log.append(audit)
                return True
            return False

    def check_rbac(self, principal_id: str, permission: str, scope: str = "L1") -> AccessDecision:
        """Check RBAC access for principal + permission.

        Scope participates in the decision (S4): an invalid scope value is
        rejected with DENY, and a request whose scope exceeds the
        principal's authorized ceiling is denied.
        """
        with self._lock:
            if not _is_valid_scope(scope):
                self._audit_rbac_check(principal_id, permission, scope, "denied",
                                       f"RBAC check denied: invalid scope {scope!r}")
                return AccessDecision.DENY

            ceiling_denied = self._enforce_principal_scope(principal_id, scope)
            if ceiling_denied is not None:
                self._audit_rbac_check(
                    principal_id, permission, scope, "denied",
                    f"RBAC check denied: request scope {scope} exceeds principal "
                    f"authorized scope {self._principal_scopes.get(principal_id)!r}",
                )
                return ceiling_denied

            principal_roles = self._principal_roles.get(principal_id, set())
            rule = self._rbac_rules.get(permission)
            if rule and rule.enabled:
                if rule.role in principal_roles:
                    audit = AuditLogEntry(
                        id=str(uuid.uuid4())[:8],
                        principal_id=principal_id,
                        operation="rbac_check",
                        permission=permission,
                        scope=scope,
                        result="allowed",
                        reason=f"RBAC check passed: principal {principal_id} has role {rule.role.value} for {permission}",
                        timestamp=datetime.utcnow(),
                    )
                    self._audit_log.append(audit)
                    return AccessDecision.ALLOW

            audit = AuditLogEntry(
                id=str(uuid.uuid4())[:8],
                principal_id=principal_id,
                operation="rbac_check",
                permission=permission,
                scope=scope,
                result="denied",
                reason=f"RBAC check failed: principal {principal_id} lacks role {rule.role.value if rule else 'N/A'} for {permission}",
                timestamp=datetime.utcnow(),
            )
            self._audit_log.append(audit)
            return AccessDecision.DENY

    def check_abac(self, principal_id: str, permission: str, scope: str = "L1", attributes: Optional[Dict[str, Any]] = None) -> AccessDecision:
        """Check ABAC access for principal + permission + attributes.

        Scope participates in the decision (S4): an invalid scope value is
        rejected with DENY.

        Attribute merge policy (S3): the principal's STORED attributes are
        the authoritative baseline. Request-supplied ``attributes`` may only
        *fill missing keys*; they can never override an existing stored
        value, and any protected security attribute (see
        ``_PROTECTED_SECURITY_ATTRS``) is always taken from storage even if
        a request attempts to supply it. This closes the privilege-
        escalation gap where a caller fabricated ``clearance`` /
        ``trust_score`` / ``scope`` values.
        """
        with self._lock:
            if not _is_valid_scope(scope):
                audit = AuditLogEntry(
                    id=str(uuid.uuid4())[:8],
                    principal_id=principal_id,
                    operation="abac_check",
                    permission=permission,
                    scope=scope,
                    result="denied",
                    reason=f"ABAC check denied: invalid scope {scope!r}",
                    timestamp=datetime.utcnow(),
                )
                self._audit_log.append(audit)
                return AccessDecision.DENY

            stored_attrs = self._principal_attrs.get(principal_id, {})
            principal_attrs = self._merge_attributes(stored_attrs, attributes)

            rule = self._abac_rules.get(permission)
            if rule and rule.enabled:
                attr_value = principal_attrs.get(rule.condition_attribute)

                result = self._eval_condition(attr_value, rule.condition_operator, rule.condition_value)

                if result:
                    audit = AuditLogEntry(
                        id=str(uuid.uuid4())[:8],
                        principal_id=principal_id,
                        operation="abac_check",
                        permission=permission,
                        scope=scope,
                        result="allowed",
                        reason=f"ABAC check passed: {rule.condition_attribute} {rule.condition_operator} {rule.condition_value}",
                        timestamp=datetime.utcnow(),
                    )
                    self._audit_log.append(audit)
                    return AccessDecision.ALLOW

            audit = AuditLogEntry(
                id=str(uuid.uuid4())[:8],
                principal_id=principal_id,
                operation="abac_check",
                permission=permission,
                scope=scope,
                result="denied",
                reason=f"ABAC check failed: condition not met for {permission}",
                timestamp=datetime.utcnow(),
            )
            self._audit_log.append(audit)
            return AccessDecision.DENY

    def _merge_attributes(
        self,
        stored: Dict[str, Any],
        request: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Merge stored and request attributes under the S3 baseline rule.

        The stored attributes are authoritative. The request may contribute
        values only for keys the principal has not stored; a request value
        for a key that already exists in storage is ignored (stored wins).
        Protected security attributes (``_PROTECTED_SECURITY_ATTRS``) are
        additionally never taken from the request, even for a missing key,
        so a caller cannot inject a fabricated security claim.
        """
        merged: Dict[str, Any] = dict(stored)
        for key, value in (request or {}).items():
            if key in _PROTECTED_SECURITY_ATTRS:
                # Security attribute: storage is authoritative; ignore request.
                continue
            if key not in merged:
                # Request may supply a value only for an absent key.
                merged[key] = value
            # Existing (non-protected) stored value wins -> do nothing.
        return merged

    def _eval_condition(self, actual: Any, operator: ABATCondition, expected: Any) -> bool:
        """Evaluate an ABAC condition.

        A missing attribute (None) never satisfies a comparison
        condition: absent evidence must fail closed, not fall back to
        string comparison of "None".
        """
        if operator == ABATCondition.EQUALS:
            return actual == expected
        elif operator == ABATCondition.NOT_EQUALS:
            return actual != expected
        elif operator == ABATCondition.GREATER_THAN:
            if actual is None or expected is None:
                return False
            try:
                return float(actual) > float(expected)
            except (ValueError, TypeError):
                return str(actual) > str(expected)
        elif operator == ABATCondition.LESS_THAN:
            if actual is None or expected is None:
                return False
            try:
                return float(actual) < float(expected)
            except (ValueError, TypeError):
                return str(actual) < str(expected)
        elif operator == ABATCondition.IN_SET:
            return actual in expected if isinstance(expected, (list, set, tuple)) else actual == expected
        elif operator == ABATCondition.NOT_IN_SET:
            return actual not in expected if isinstance(expected, (list, set, tuple)) else actual != expected
        return False

    @kernel_action("security.set_abac_rule")
    def set_abac_rule(self, permission: str, condition_attribute: str, condition_operator: ABATCondition, condition_value: Any, enabled: bool = True) -> ABACRule:
        """Set an ABAC rule for a permission."""
        with self._lock:
            rule = ABACRule(
                id=str(uuid.uuid4())[:8],
                permission=permission,
                condition_attribute=condition_attribute,
                condition_operator=condition_operator,
                condition_value=condition_value,
                enabled=enabled,
            )
            self._abac_rules[permission] = rule
            return rule

    def check_access(self, principal_id: str, permission: str, scope: str = "L1", attributes: Optional[Dict[str, Any]] = None) -> AccessDecision:
        """Full access check: RBAC + ABAC combination."""
        decision, _, _ = self._evaluate_access(principal_id, permission, scope, attributes)
        return decision

    def _evaluate_access(
        self,
        principal_id: str,
        permission: str,
        scope: str = "L1",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Tuple[AccessDecision, AccessDecision, Optional[AccessDecision]]:
        """Combined RBAC + ABAC evaluation.

        Returns (final_decision, rbac_result, abac_result_or_None).
        An absent or disabled ABAC rule means "no constraint", not a
        failed constraint: a passing RBAC check then yields a full ALLOW.
        """
        with self._lock:
            rbac_result = self.check_rbac(principal_id, permission, scope)
            abac_rule = self._abac_rules.get(permission)
            abac_applicable = bool(abac_rule and abac_rule.enabled)

            if rbac_result == AccessDecision.ALLOW:
                if not abac_applicable:
                    return AccessDecision.ALLOW, rbac_result, None
                abac_result = self.check_abac(principal_id, permission, scope, attributes)
                if abac_result == AccessDecision.ALLOW:
                    return AccessDecision.ALLOW, rbac_result, abac_result
                # RBAC passed but ABAC failed - conditional
                return AccessDecision.CONDITIONAL, rbac_result, abac_result

            if abac_applicable:
                # RBAC failed, try ABAC alone
                abac_result = self.check_abac(principal_id, permission, scope, attributes)
                if abac_result == AccessDecision.ALLOW:
                    # ABAC passed but RBAC failed - deferred to human
                    return AccessDecision.DEFER, rbac_result, abac_result
                return AccessDecision.DENY, rbac_result, abac_result

            # Both failed
            return AccessDecision.DENY, rbac_result, None

    @kernel_action("security.decide_access")
    def decide_access(self, principal_id: str, permission: str, scope: str = "L1", attributes: Optional[Dict[str, Any]] = None, human_override: bool = False) -> Dict[str, Any]:
        """Full access decision with reasoning.

        Definition Lock section 112 human sovereignty override: when
        human_override is True and the combined decision is not ALLOW,
        the decision is overridden to ALLOW and the override itself is
        recorded in the audit trail.
        """
        with self._lock:
            decision, rbac_result, abac_result = self._evaluate_access(
                principal_id, permission, scope, attributes
            )

            abac_value = abac_result.value if abac_result is not None else "not_applicable"
            parts = [f"rbac={rbac_result.value}", f"abac={abac_value}"]

            if human_override and decision != AccessDecision.ALLOW:
                decision = AccessDecision.ALLOW
                parts.append("human sovereignty override applied")
                override_audit = AuditLogEntry(
                    id=str(uuid.uuid4())[:8],
                    principal_id=principal_id,
                    operation="human_sovereignty_override",
                    permission=permission,
                    scope=scope,
                    result="allowed",
                    reason="Human sovereignty override converted a non-allow decision to allow",
                    timestamp=datetime.utcnow(),
                )
                self._audit_log.append(override_audit)

            result = {
                "decision": decision.value,
                "principal_id": principal_id,
                "permission": permission,
                "scope": scope,
                "rbac_check": rbac_result.value,
                "abac_check": abac_value,
                "reason": "; ".join(parts),
                "human_override": human_override,
            }

            # Record decision audit
            audit = AuditLogEntry(
                id=str(uuid.uuid4())[:8],
                principal_id=principal_id,
                operation="access_decision",
                permission=permission,
                scope=scope,
                result=decision.value,
                reason=result["reason"],
                timestamp=datetime.utcnow(),
            )
            self._audit_log.append(audit)

            return result

    def audit_trail(self, principal_id: Optional[str] = None, since: Optional[datetime] = None, operation: Optional[str] = None) -> List[AuditLogEntry]:
        """Get security audit trail."""
        with self._lock:
            entries = self._audit_log

            if principal_id:
                entries = [e for e in entries if e.principal_id == principal_id]

            if operation:
                entries = [e for e in entries if e.operation == operation]

            if since:
                entries = [e for e in entries if e.timestamp >= since]

            return sorted(entries, key=lambda e: e.timestamp, reverse=True)

    def stats(self) -> Dict[str, Any]:
        """Get security engine statistics."""
        with self._lock:
            by_operation = {}
            by_result = {"allow": 0, "deny": 0, "conditional": 0, "defer": 0}

            for entry in self._audit_log:
                op = entry.operation
                by_operation[op] = by_operation.get(op, 0) + 1

                # Audit entries record "allowed"/"denied" for checks and
                # decision values ("allow"/"deny"/...) for decisions;
                # normalize so both forms land on the same counter keys.
                normalized = _RESULT_KEY_ALIASES.get(entry.result, entry.result)
                by_result[normalized] = by_result.get(normalized, 0) + 1

            unique_principals = len(set(e.principal_id for e in self._audit_log))

            rbac_count = sum(1 for r in self._rbac_rules.values() if r.enabled)
            abac_count = sum(1 for r in self._abac_rules.values() if r.enabled)

            return {
                "total_audit_entries": len(self._audit_log),
                "by_operation": by_operation,
                "by_result": by_result,
                "unique_principals": unique_principals,
                "rbac_rules_total": len(self._rbac_rules),
                "rbac_rules_enabled": rbac_count,
                "abac_rules_total": len(self._abac_rules),
                "abac_rules_enabled": abac_count,
                "unique_permissions": len(self._rbac_rules) + len(self._abac_rules),
            }


# Global security engine instance
_global_security: Optional[SecurityEngine] = None


def get_security_engine() -> SecurityEngine:
    """Get or create the global security engine instance."""
    global _global_security
    if _global_security is None:
        _global_security = SecurityEngine()
    return _global_security


def check_rbac(principal_id: str, permission: str, scope: str = "L1") -> AccessDecision:
    """Check RBAC access."""
    return get_security_engine().check_rbac(principal_id, permission, scope)


def check_abac(principal_id: str, permission: str, scope: str = "L1", attributes: Optional[Dict[str, Any]] = None) -> AccessDecision:
    """Check ABAC access."""
    return get_security_engine().check_abac(principal_id, permission, scope, attributes)


def decide_access(principal_id: str, permission: str, scope: str = "L1", attributes: Optional[Dict[str, Any]] = None, human_override: bool = False) -> Dict[str, Any]:
    """Full access decision."""
    return get_security_engine().decide_access(principal_id, permission, scope, attributes, human_override)


def audit_trail(principal_id: Optional[str] = None, since: Optional[datetime] = None, operation: Optional[str] = None) -> List[AuditLogEntry]:
    """Get security audit trail."""
    return get_security_engine().audit_trail(principal_id, since, operation)


def security_stats() -> Dict[str, Any]:
    """Get security engine statistics."""
    return get_security_engine().stats()
