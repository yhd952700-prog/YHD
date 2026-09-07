"""Policy Kernel — Permission Boundaries + Policy Engine

The Policy Kernel defines and enforces permission boundaries across the system.
Implements ABAC (Attribute-Based Access Control) with policy evaluation.

依据 Definition Lock §112: Policy Kernel 必须能够
- Define policy rules with conditions and actions
- Evaluate access decisions (ALLOW/DENY)
- Support policy precedence and conflict resolution
- Enforce scope-based permissions (L0-L7)
- Provide audit trail for all decisions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
import threading
import re
import logging

logger = logging.getLogger("liuhao.kernel.policy")


class PolicyAction(str, Enum):
    """Policy decision actions."""
    ALLOW = "allow"
    DENY = "deny"
    ABSTAIN = "abstain"  # No opinion, defer to other policies


class PolicyEffect(str, Enum):
    """Final policy effect after evaluation."""
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


class PolicyScope(str, Enum):
    """Policy scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


class PolicyOperator(str, Enum):
    """Policy condition operators."""
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    REGEX = "regex"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


@dataclass
class PolicyCondition:
    """Single policy condition."""
    attribute: str  # e.g., "agent.id", "resource.type", "action.name"
    operator: PolicyOperator
    value: Any
    negate: bool = False

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """Evaluate condition against context.

        Condition values may reference other context attributes with a
        leading "$" (e.g. "$action.required_scope"); such values are
        resolved from the same evaluation context before comparison.
        """
        # Get attribute value from context (supports dot notation)
        attr_value = self._get_nested(context, self.attribute)
        # Resolve right-value context references ("$path" -> value)
        value = self._resolve_value(self.value, context)

        if self.operator == PolicyOperator.EXISTS:
            result = attr_value is not None
        elif self.operator == PolicyOperator.NOT_EXISTS:
            result = attr_value is None
        elif attr_value is None:
            result = False
        elif self.operator == PolicyOperator.EQ:
            result = attr_value == value
        elif self.operator == PolicyOperator.NEQ:
            result = attr_value != value
        elif self.operator == PolicyOperator.GT:
            result = self._safe_compare(attr_value, value, lambda a, b: a > b)
        elif self.operator == PolicyOperator.GTE:
            result = self._safe_compare(attr_value, value, lambda a, b: a >= b)
        elif self.operator == PolicyOperator.LT:
            result = self._safe_compare(attr_value, value, lambda a, b: a < b)
        elif self.operator == PolicyOperator.LTE:
            result = self._safe_compare(attr_value, value, lambda a, b: a <= b)
        elif self.operator == PolicyOperator.IN:
            result = attr_value in value if isinstance(value, (list, set, tuple)) else False
        elif self.operator == PolicyOperator.NOT_IN:
            result = attr_value not in value if isinstance(value, (list, set, tuple)) else True
        elif self.operator == PolicyOperator.CONTAINS:
            try:
                result = value in attr_value if hasattr(attr_value, '__contains__') else False
            except TypeError:
                result = False
        elif self.operator == PolicyOperator.REGEX:
            result = bool(re.match(str(value), str(attr_value)))
        else:
            result = False

        return not result if self.negate else result

    def _resolve_value(self, value: Any, context: Dict[str, Any]) -> Any:
        """Resolve "$path" references against the evaluation context.

        A plain string value starting with "$" is treated as a context
        path (without the "$" prefix) and resolved via dot notation.
        Anything else is returned unchanged.
        """
        if isinstance(value, str) and value.startswith("$"):
            return self._get_nested(context, value[1:])
        return value

    @staticmethod
    def _safe_compare(actual: Any, expected: Any, op: Any) -> bool:
        """Type-safe ordered comparison.

        A missing operand or an incomparable pair simply does not
        satisfy the condition; evaluation must never raise on
        mismatched attribute types.
        """
        if actual is None or expected is None:
            return False
        try:
            return bool(op(actual, expected))
        except TypeError:
            return False

    def _get_nested(self, obj: Dict[str, Any], path: str) -> Any:
        """Get nested attribute using dot notation."""
        keys = path.split('.')
        current = obj
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            elif hasattr(current, key):
                current = getattr(current, key)
            else:
                return None
            if current is None:
                return None
        return current


@dataclass
class PolicyRule:
    """Single policy rule with conditions and action."""
    id: str
    name: str
    description: str
    conditions: List[PolicyCondition]
    action: PolicyAction
    scope: PolicyScope = PolicyScope.L0
    precedence: int = 0  # Higher = more important
    effect: PolicyEffect = PolicyEffect.ALLOW
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    enabled: bool = True

    def matches(self, context: Dict[str, Any]) -> bool:
        """Check if all conditions match."""
        if not self.enabled:
            return False
        return all(c.evaluate(context) for c in self.conditions)


@dataclass
class PolicyDecision:
    """Result of policy evaluation."""
    decision: PolicyEffect
    matched_rules: List[PolicyRule]
    denied_rules: List[PolicyRule]
    abstained_rules: List[PolicyRule]
    traceability: List[str]
    context: Dict[str, Any]
    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_allowed(self) -> bool:
        return self.decision == PolicyEffect.ALLOW

    @property
    def is_denied(self) -> bool:
        return self.decision == PolicyEffect.DENY


@dataclass
class PolicySet:
    """Named set of policies."""
    id: str
    name: str
    description: str
    rules: List[PolicyRule] = field(default_factory=list)
    scope: PolicyScope = PolicyScope.L0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class PolicyEngine:
    """Policy evaluation engine with ABAC support."""

    def __init__(self):
        self._rules: Dict[str, PolicyRule] = {}
        self._policy_sets: Dict[str, PolicySet] = {}
        self._lock = threading.RLock()

        # Register built-in policies
        self._register_builtin_policies()

    def _register_builtin_policies(self) -> None:
        """Register built-in system policies."""
        builtin_policies = [
            # Human sovereignty - always allow verified human operators
            PolicyRule(
                id="human_sovereignty",
                name="Human Sovereignty Override",
                description="Verified human operators always allowed for high-risk actions",
                conditions=[
                    PolicyCondition("actor.type", PolicyOperator.EQ, "human"),
                    PolicyCondition("action.risk_level", PolicyOperator.IN, ["HIGH", "CRITICAL"]),
                    # P10: the human claim must be backed by a registered,
                    # ACTIVE identity. A self-declared (unverified)
                    # actor.type == "human" does NOT satisfy this and thus
                    # cannot trigger the override.
                    PolicyCondition("actor.verified", PolicyOperator.EQ, True),
                ],
                action=PolicyAction.ALLOW,
                scope=PolicyScope.L0,
                precedence=1000,
            ),
            # Default deny for unknown
            PolicyRule(
                id="default_deny",
                name="Default Deny",
                description="Deny by default if no explicit allow",
                conditions=[
                    # Match whenever an action is present to decide on.
                    # A missing action.name must NOT let an action slip
                    # through to NOT_APPLICABLE (fail-open) -- any attempted
                    # action without an explicit allow is denied.
                    PolicyCondition("action", PolicyOperator.EXISTS, True),
                ],
                action=PolicyAction.DENY,
                scope=PolicyScope.L7,
                precedence=-1000,
            ),
            # Scope enforcement
            # Right values reference other context attributes with "$";
            # they are resolved at evaluation time, not compared as
            # literal strings.
            PolicyRule(
                id="scope_enforcement",
                name="Scope Enforcement",
                description="Agents cannot exceed their autonomy scope",
                conditions=[
                    PolicyCondition("agent.scope", PolicyOperator.LT, "$action.required_scope"),
                ],
                action=PolicyAction.DENY,
                scope=PolicyScope.L0,
                precedence=100,
            ),
            # Capability requirement
            # Deny when the action requires a capability the agent does
            # not hold: NOT (required capability contained in the
            # agent's capability list).
            PolicyRule(
                id="capability_required",
                name="Capability Required",
                description="Actions require matching capability",
                conditions=[
                    PolicyCondition("action.required_capability", PolicyOperator.EXISTS, True),
                    PolicyCondition(
                        "agent.capabilities",
                        PolicyOperator.CONTAINS,
                        "$action.required_capability",
                        negate=True,
                    ),
                ],
                action=PolicyAction.DENY,
                scope=PolicyScope.L1,
                precedence=200,
            ),
            # Resource quota enforcement
            PolicyRule(
                id="quota_enforcement",
                name="Resource Quota Enforcement",
                description="Actions cannot exceed resource quotas",
                conditions=[
                    PolicyCondition("resource.available", PolicyOperator.LT, "$action.estimated_cost"),
                ],
                action=PolicyAction.DENY,
                scope=PolicyScope.L2,
                precedence=150,
            ),
        ]

        for rule in builtin_policies:
            self.register_rule(rule)

    def register_rule(self, rule: PolicyRule) -> bool:
        """Register a policy rule."""
        with self._lock:
            if rule.id in self._rules:
                return False
            self._rules[rule.id] = rule
            return True

    def unregister_rule(self, rule_id: str) -> bool:
        """Unregister a policy rule."""
        with self._lock:
            if rule_id in self._rules:
                del self._rules[rule_id]
                return True
            return False

    def get_rule(self, rule_id: str) -> Optional[PolicyRule]:
        """Get rule by ID."""
        with self._lock:
            return self._rules.get(rule_id)

    def list_rules(self, enabled_only: bool = True) -> List[PolicyRule]:
        """List all rules."""
        with self._lock:
            rules = list(self._rules.values())
            if enabled_only:
                rules = [r for r in rules if r.enabled]
            # Sort by precedence (highest first)
            return sorted(rules, key=lambda r: r.precedence, reverse=True)

    def create_policy_set(
        self,
        id: str,
        name: str,
        description: str,
        rule_ids: List[str],
        scope: PolicyScope = PolicyScope.L0
    ) -> PolicySet:
        """Create a named policy set from rule IDs."""
        with self._lock:
            rules = [self._rules[rid] for rid in rule_ids if rid in self._rules]
            policy_set = PolicySet(
                id=id,
                name=name,
                description=description,
                rules=rules,
                scope=scope,
            )
            self._policy_sets[id] = policy_set
            return policy_set

    def get_policy_set(self, set_id: str) -> Optional[PolicySet]:
        """Get policy set by ID."""
        with self._lock:
            return self._policy_sets.get(set_id)

    def evaluate(
        self,
        context: Dict[str, Any],
        scope: Optional[PolicyScope] = None,
        policy_set_id: Optional[str] = None
    ) -> PolicyDecision:
        """Evaluate policies against context."""
        with self._lock:
            # Ensure the human-verification signal is present on the
            # actor/agent context so the human_sovereignty rule can gate
            # on it (P10). We never trust a caller-supplied "verified"
            # flag: it is recomputed from the identity kernel here.
            context = dict(context)
            actor = context.get("actor")
            if isinstance(actor, dict):
                actor = dict(actor)
                verified = self._is_verified_human(actor)
                actor["verified"] = verified
                context["actor"] = actor
                agent = context.get("agent")
                if isinstance(agent, dict):
                    agent = dict(agent)
                    agent["verified"] = verified
                    context["agent"] = agent

            # Determine which rules to evaluate
            if policy_set_id and policy_set_id in self._policy_sets:
                rules = self._policy_sets[policy_set_id].rules
            else:
                rules = list(self._rules.values())

            # Filter by scope
            if scope:
                scope_order = {s: i for i, s in enumerate(PolicyScope)}
                rules = [r for r in rules if scope_order[r.scope] <= scope_order[scope]]

            # Filter enabled
            rules = [r for r in rules if r.enabled]

            # Sort by precedence
            rules = sorted(rules, key=lambda r: r.precedence, reverse=True)

            matched = []
            denied = []
            abstained = []
            traceability = []

            # Evaluate rules in precedence order
            for rule in rules:
                if rule.matches(context):
                    traceability.append(f"RULE:{rule.id}:{rule.action.value}")
                    if rule.action == PolicyAction.ALLOW:
                        matched.append(rule)
                        # First ALLOW with highest precedence wins (unless DENY with higher precedence)
                        # Check if there's a higher precedence DENY
                        higher_deny = any(
                            r.action == PolicyAction.DENY and r.precedence > rule.precedence
                            for r in rules
                            if r.matches(context)
                        )
                        if not higher_deny:
                            return PolicyDecision(
                                decision=PolicyEffect.ALLOW,
                                matched_rules=matched,
                                denied_rules=denied,
                                abstained_rules=abstained,
                                traceability=traceability,
                                context=context,
                            )
                    elif rule.action == PolicyAction.DENY:
                        denied.append(rule)
                        # DENY with highest precedence wins immediately
                        return PolicyDecision(
                            decision=PolicyEffect.DENY,
                            matched_rules=matched,
                            denied_rules=denied,
                            abstained_rules=abstained,
                            traceability=traceability,
                            context=context,
                        )
                    elif rule.action == PolicyAction.ABSTAIN:
                        abstained.append(rule)

            # No explicit ALLOW - check if any DENY matched
            if denied:
                return PolicyDecision(
                    decision=PolicyEffect.DENY,
                    matched_rules=matched,
                    denied_rules=denied,
                    abstained_rules=abstained,
                    traceability=traceability,
                    context=context,
                )

            # No applicable rules
            return PolicyDecision(
                decision=PolicyEffect.NOT_APPLICABLE,
                matched_rules=matched,
                denied_rules=denied,
                abstained_rules=abstained,
                traceability=traceability,
                context=context,
            )

    def evaluate_simple(
        self,
        actor: Dict[str, Any],
        action: Dict[str, Any],
        resource: Optional[Dict[str, Any]] = None,
        scope: Optional[PolicyScope] = None
    ) -> PolicyDecision:
        """Simple evaluation with actor/action/resource.

        The actor dict is mirrored under the "agent" key so the builtin
        scope/capability/quota rules (which read "agent.*") also apply
        on this path. "agent.*" is the canonical key for agent
        attributes; "actor.type" remains the canonical key for the
        human/agent distinction.
        """
        actor = dict(actor)
        actor["verified"] = self._is_verified_human(actor)
        context = {
            "actor": actor,
            "agent": actor,
            "action": action,
            "resource": resource or {},
        }
        decision = self.evaluate(context, scope)
        logger.debug(
            "policy.evaluate_simple action=%s risk=%s is_allowed=%s is_denied=%s",
            action.get("name"),
            action.get("risk_level"),
            decision.is_allowed,
            decision.is_denied,
        )
        return decision

    def _is_verified_human(self, actor: Dict[str, Any]) -> bool:
        """Return True iff the actor is a registered, ACTIVE human (P10).

        A human sovereignty override is only legitimate for an actor whose
        identity has been verified by the Identity Kernel. The actor must
        reference a known identity (by ``id`` / ``identity_id`` /
        ``principal``); that identity must exist and be ACTIVE. A bare
        ``actor.type == "human"`` with no verifiable identity is treated
        as unverified and cannot trigger the override.
        """
        ref = actor.get("id") or actor.get("identity_id") or actor.get("principal")
        if not ref:
            return False
        try:
            from src.kernels.identity import get_identity_manager, IdentityStatus
        except Exception:
            return False
        try:
            mgr = get_identity_manager()
            ident = mgr.get_identity(ref) or mgr.get_identity_by_principal(ref)
        except Exception:
            return False
        if ident is None:
            return False
        return ident.status == IdentityStatus.ACTIVE

    def stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        with self._lock:
            enabled = sum(1 for r in self._rules.values() if r.enabled)
            return {
                "total_rules": len(self._rules),
                "enabled_rules": enabled,
                "policy_sets": len(self._policy_sets),
                "by_action": {
                    "allow": sum(1 for r in self._rules.values() if r.action == PolicyAction.ALLOW),
                    "deny": sum(1 for r in self._rules.values() if r.action == PolicyAction.DENY),
                    "abstain": sum(1 for r in self._rules.values() if r.action == PolicyAction.ABSTAIN),
                },
                "by_scope": {s.value: sum(1 for r in self._rules.values() if r.scope == s) for s in PolicyScope},
            }


# Global policy engine instance
_global_engine: Optional[PolicyEngine] = None
_global_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    """Get or create the global policy engine."""
    global _global_engine
    if _global_engine is None:
        with _global_lock:
            if _global_engine is None:
                _global_engine = PolicyEngine()
    return _global_engine


# Convenience functions
def define_policy_rule(
    id: str,
    name: str,
    description: str,
    conditions: List[Dict[str, Any]],
    action: PolicyAction,
    scope: PolicyScope = PolicyScope.L0,
    precedence: int = 0,
) -> PolicyRule:
    """Define and register a policy rule."""
    rule = PolicyRule(
        id=id,
        name=name,
        description=description,
        conditions=[PolicyCondition(**c) for c in conditions],
        action=action,
        scope=scope,
        precedence=precedence,
    )
    get_policy_engine().register_rule(rule)
    return rule


def evaluate_policy(
    context: Dict[str, Any],
    scope: Optional[PolicyScope] = None,
    policy_set_id: Optional[str] = None
) -> PolicyDecision:
    """Evaluate policies."""
    return get_policy_engine().evaluate(context, scope, policy_set_id)


def evaluate_policy_simple(
    actor: Dict[str, Any],
    action: Dict[str, Any],
    resource: Optional[Dict[str, Any]] = None,
    scope: Optional[PolicyScope] = None
) -> PolicyDecision:
    """Simple policy evaluation."""
    return get_policy_engine().evaluate_simple(actor, action, resource, scope)


def create_policy_set(
    id: str,
    name: str,
    description: str,
    rule_ids: List[str],
    scope: PolicyScope = PolicyScope.L0
) -> PolicySet:
    """Create a policy set."""
    return get_policy_engine().create_policy_set(id, name, description, rule_ids, scope)