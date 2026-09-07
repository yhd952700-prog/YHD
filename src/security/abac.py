"""
Attribute-Based Access Control (ABAC) for LiuHao AI OS.

Provides:
- Attribute-based permission evaluation
- Policy-as-code with condition expressions
- Integration with RBAC for hybrid ABAC+RBAC decisions
- Subject/Resource/Environment attribute matching
"""

import time
from typing import Optional, Dict, Any, List, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .rbac import Permission, PermissionAction, ResourceType, RBACManager


class ABACDecision(Enum):
    PERMIT = "permit"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class ABACSubject:
    """Subject attributes for ABAC evaluation."""
    user_id: str
    roles: List[str] = field(default_factory=list)
    departments: List[str] = field(default_factory=list)
    clearance: str = "basic"  # basic, confidential, secret, top_secret
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABACResource:
    """Resource attributes for ABAC evaluation."""
    resource_id: str
    resource_type: ResourceType
    owner_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABACEnvironment:
    """Environment attributes for ABAC evaluation."""
    timestamp: float = field(default_factory=lambda: time.time())
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABACPolicy:
    """
    ABAC policy definition.

    Policies evaluate a condition expression against subject, resource,
    and environment attributes. When the condition matches, the policy
    applies its effect (PERMIT or DENY).
    """
    policy_id: str
    description: str = ""
    effect: ABACDecision = ABACDecision.PERMIT  # PERMIT or DENY
    target: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Dict[str, Any]] = None
    priority: int = 100  # Lower number = higher priority

    def evaluate(
        self,
        subject: ABACSubject,
        resource: ABACResource,
        environment: ABACEnvironment,
    ) -> bool:
        """Evaluate whether this policy applies to the request."""
        # Check target match
        if not self._target_matches(resource):
            return False

        # Check condition (if specified)
        if self.condition:
            return self._condition_matches(subject, resource, environment)

        return True

    def _target_matches(self, resource: ABACResource) -> bool:
        """Check if resource matches policy target."""
        for key, expected in self.target.items():
            if key == "resource_type":
                if resource.resource_type.value != expected:
                    return False
            elif key == "tags":
                if not any(t in resource.tags for t in expected):
                    return False
            else:
                actual = resource.attributes.get(key)
                if actual != expected:
                    return False
        return True

    def _condition_matches(
        self,
        subject: ABACSubject,
        resource: ABACResource,
        environment: ABACEnvironment,
    ) -> bool:
        """Evaluate condition expression against attributes."""
        if not self.condition:
            return True

        op = self.condition.get("op")
        attr = self.condition.get("attribute")
        value = self.condition.get("value")

        if op == "equals":
            actual = self._resolve_attr(subject, resource, environment, attr)
            return actual == value

        elif op == "in":
            actual = self._resolve_attr(subject, resource, environment, attr)
            return actual in value if isinstance(value, list) else actual == value

        elif op == "not_in":
            actual = self._resolve_attr(subject, resource, environment, attr)
            return actual not in value if isinstance(value, list) else actual != value

        elif op == "and":
            return all(
                ABACPolicy("sub", "", ABACDecision.PERMIT, condition=c).evaluate(
                    subject, resource, environment
                )
                for c in self.condition.get("conditions", [])
            )

        elif op == "or":
            return any(
                ABACPolicy("sub", "", ABACDecision.PERMIT, condition=c).evaluate(
                    subject, resource, environment
                )
                for c in self.condition.get("conditions", [])
            )

        elif op == "greater_than":
            actual = self._resolve_attr(subject, resource, environment, attr)
            try:
                return float(actual) > float(value)
            except (TypeError, ValueError):
                return False

        elif op == "less_than":
            actual = self._resolve_attr(subject, resource, environment, attr)
            try:
                return float(actual) < float(value)
            except (TypeError, ValueError):
                return False

        return False

    def _resolve_attr(
        self,
        subject: ABACSubject,
        resource: ABACResource,
        environment: ABACEnvironment,
        attr_path: str,
    ) -> Any:
        """Resolve a dotted attribute path (e.g., 'subject.clearance', 'subject.attributes.department')."""
        parts = attr_path.split(".", 1)
        namespace = parts[0]
        key = parts[1] if len(parts) > 1 else None

        if namespace == "subject":
            obj = subject
        elif namespace == "resource":
            obj = resource
        elif namespace == "environment":
            obj = environment
        else:
            return None

        if key is None:
            return None

        # Handle nested attributes dict (e.g., "attributes.department")
        if key.startswith("attributes."):
            sub_key = key.split(".", 1)[1]
            return obj.attributes.get(sub_key)

        # Check named fields first, then attributes dict
        if hasattr(obj, key):
            return getattr(obj, key)
        return obj.attributes.get(key)


class ABACEngine:
    """
    ABAC policy engine.

    Evaluates a set of policies and returns a combined decision
    using deny-overrides or permit-overrides strategies.
    """

    def __init__(self, strategy: str = "deny_overrides"):
        self.strategy = strategy
        self._policies: List[ABACPolicy] = []
        self._rbac_manager: Optional[RBACManager] = None

    def set_rbac_manager(self, rbac: RBACManager) -> None:
        """Link to an RBACManager for hybrid evaluation."""
        self._rbac_manager = rbac

    def add_policy(self, policy: ABACPolicy) -> None:
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.priority)

    def evaluate(
        self,
        subject: ABACSubject,
        resource: ABACResource,
        action: PermissionAction,
        environment: Optional[ABACEnvironment] = None,
    ) -> bool:
        """Evaluate all policies and return a combined decision."""
        if environment is None:
            environment = ABACEnvironment()

        applicable: List[Tuple[ABACPolicy, ABACDecision]] = []

        for policy in self._policies:
            if policy.evaluate(subject, resource, environment):
                applicable.append((policy, policy.effect))

        if not applicable:
            # Fall back to RBAC if available
            if self._rbac_manager:
                for role_id in subject.roles:
                    if self._rbac_manager.has_role_permission(
                        role_id,
                        resource.resource_type,
                        action,
                        resource.resource_id,
                    ):
                        return True
            return False

        if self.strategy == "deny_overrides":
            return all(effect == ABACDecision.PERMIT for _, effect in applicable)

        if self.strategy == "permit_overrides":
            return any(effect == ABACDecision.PERMIT for _, effect in applicable)

        return all(effect == ABACDecision.PERMIT for _, effect in applicable)

    def get_matching_policies(
        self,
        subject: ABACSubject,
        resource: ABACResource,
        environment: Optional[ABACEnvironment] = None,
    ) -> List[ABACPolicy]:
        """Return all policies that match the given context."""
        if environment is None:
            environment = ABACEnvironment()

        return [p for p in self._policies if p.evaluate(subject, resource, environment)]


def get_default_abac_engine() -> ABACEngine:
    """Get a default ABAC engine instance with common policies."""
    engine = ABACEngine()
    # Add default policies here
    return engine
