"""Network Gateway — MASTER-SPEC Phase 14 (§68-69) tests.

Covers:
- A2A / MCP adapter roundtrip (real serialize -> deserialize -> deliver)
- Honest failure when no transport / handler is configured (NO FAKE)
- The mandatory delegation chain: unauthenticated / policy-denied /
  missing-capability are all rejected and audited
- Full chain success produces an audit trail
- Capability discovery
- Privilege escalation is blocked (denied step never reaches execute)
"""
import uuid

import pytest

from src.kernels.network import Message, MessageStatus, ProtocolType
from src.kernels.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyOperator,
    PolicyRule,
    PolicyScope,
    get_policy_engine,
)
from src.ai.network_gateway import (
    A2AAdapter,
    MCPAdapter,
    AgentNetworkGateway,
    AgentRegistry,
    ExternalAgent,
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _allow_rule(action_name: str):
    """Register a real ALLOW policy rule for an action (default-deny bypass)."""
    engine = get_policy_engine()
    rule_id = f"allow-{action_name}"
    engine.unregister_rule(rule_id)
    rule = PolicyRule(
        id=rule_id,
        name=f"Allow {action_name}",
        description=f"Explicit allow for test action {action_name}",
        conditions=[PolicyCondition("action.name", PolicyOperator.EQ, action_name)],
        action=PolicyAction.ALLOW,
        scope=PolicyScope.L7,
        precedence=500,
    )
    engine.register_rule(rule)
    return rule_id


# ---------------------------------------------------------------------------
# Adapter roundtrips
# ---------------------------------------------------------------------------
class TestA2AAdapter:
    def test_roundtrip_delivers_serialized_message(self):
        adapter = A2AAdapter()
        received: list = []
        adapter.register_handler("dest-a", lambda m: received.append(m))

        msg = Message(
            type="ping",
            content={"k": "v"},
            source="src",
            destination="dest-a",
            protocol=ProtocolType.A2A,
        )
        ok = adapter.send(msg)

        assert ok is True
        assert len(received) == 1
        delivered = received[0]
        # The delivered message is a reconstructed copy (serialized -> deserialized).
        assert delivered.content == {"k": "v"}
        assert delivered.type == "ping"
        assert delivered is not msg
        assert delivered.status == MessageStatus.DELIVERED

    def test_send_without_handler_is_honest_failure(self):
        adapter = A2AAdapter()
        msg = Message(destination="nowhere", content="x", protocol=ProtocolType.A2A)
        ok = adapter.send(msg)
        assert ok is False
        assert msg.metadata.get("error") == "transport not configured"
        assert msg.status == MessageStatus.FAILED


class TestMCPAdapter:
    def test_roundtrip_delivers_serialized_message(self):
        adapter = MCPAdapter()
        received: list = []
        adapter.register_handler("dest-m", lambda m: received.append(m))

        msg = Message(
            type="tool",
            content="do-it",
            source="src",
            destination="dest-m",
            protocol=ProtocolType.MCP,
        )
        ok = adapter.send(msg)

        assert ok is True
        assert len(received) == 1
        assert received[0].content == "do-it"
        assert received[0] is not msg

    def test_send_without_handler_is_honest_failure(self):
        adapter = MCPAdapter()
        msg = Message(destination="nowhere", content="x", protocol=ProtocolType.MCP)
        ok = adapter.send(msg)
        assert ok is False
        assert msg.metadata.get("error") == "transport not configured"


# ---------------------------------------------------------------------------
# Delegation chain — rejection paths
# ---------------------------------------------------------------------------
class TestDelegationRejections:
    def test_unauthenticated_target_rejected(self):
        gw = AgentNetworkGateway()
        principal = _uid("ghost")
        # Registered as an external agent but with a non-existent identity
        # (i.e. it was never authenticated through the Identity Kernel).
        gw.registry._agents[principal] = ExternalAgent(
            principal=principal,
            identity_id="does-not-exist",
            capabilities=["read"],
            registered=True,
        )
        result = gw.delegate("requester", principal, "read", {})
        assert result["status"] == "denied"
        assert result["denied_step"] == "authenticate"
        steps = {e["step"]: e["outcome"] for e in result["audit"]}
        assert steps["authenticate"] == "denied"

    def test_unregistered_target_rejected(self):
        gw = AgentNetworkGateway()
        principal = _uid("unknown")
        result = gw.delegate("requester", principal, "read", {})
        assert result["status"] == "denied"
        assert result["denied_step"] == "authenticate"

    def test_policy_denied_rejected(self):
        gw = AgentNetworkGateway()
        principal = _uid("agent-p")
        gw.registry.register(principal, capabilities=["read"], trust_score=0.8)
        action = _uid("action-p")
        # No ALLOW rule registered => default-deny policy rejects it.
        result = gw.delegate("requester", principal, action, {})
        assert result["status"] == "denied"
        assert result["denied_step"] == "authorize"
        steps = {e["step"]: e["outcome"] for e in result["audit"]}
        assert steps["authenticate"] == "allowed"
        assert steps["authorize"] == "denied"

    def test_missing_capability_rejected(self):
        gw = AgentNetworkGateway()
        principal = _uid("agent-c")
        # Agent has "read" but NOT the required "write" capability.
        gw.registry.register(principal, capabilities=["read"], trust_score=0.8)
        action = _uid("action-c")
        _allow_rule(action)
        result = gw.delegate(
            "requester", principal, action, {}, required_capability="write"
        )
        assert result["status"] == "denied"
        assert result["denied_step"] == "capability"
        steps = {e["step"]: e["outcome"] for e in result["audit"]}
        assert steps["authenticate"] == "allowed"
        assert steps["authorize"] == "allowed"
        assert steps["capability"] == "denied"


# ---------------------------------------------------------------------------
# Delegation chain — success + audit + discovery + escalation
# ---------------------------------------------------------------------------
class TestDelegationSuccess:
    def test_full_chain_produces_audit_trail(self):
        gw = AgentNetworkGateway()
        principal = _uid("agent-ok")
        gw.registry.register(principal, capabilities=["compute"], trust_score=0.9)

        delivered: list = []
        gw.register_handler(principal, lambda m: delivered.append(m))

        action = _uid("action-ok")
        _allow_rule(action)
        # Delegate a capability the agent actually holds.
        result = gw.delegate("requester", principal, action, {"x": 1}, required_capability="compute")

        assert result["status"] == "allowed"
        assert result["denied_step"] is None
        assert result["message_id"] is not None

        steps = {e["step"]: e["outcome"] for e in result["audit"]}
        assert steps == {
            "authenticate": "allowed",
            "authorize": "allowed",
            "capability": "allowed",
            "execute": "allowed",
            "audit": "allowed",
        }
        # The target's handler was actually invoked with the routed message.
        assert len(delivered) == 1
        assert delivered[0].content == {"x": 1}

    def test_privilege_escalation_blocked(self):
        gw = AgentNetworkGateway()
        principal = _uid("agent-esc")
        gw.registry.register(principal, capabilities=["read"], trust_score=0.8)

        invoked = {"count": 0}

        def handler(msg):
            invoked["count"] += 1

        gw.register_handler(principal, handler)

        action = _uid("action-esc")
        _allow_rule(action)
        # The agent is allowed by policy for `action` but lacks the required
        # "admin" capability => capability step must deny and execute must
        # NEVER run.
        result = gw.delegate(
            "requester", principal, action, {}, required_capability="admin"
        )
        assert result["status"] == "denied"
        assert result["denied_step"] == "capability"
        # Critical: the denied delegation must not have executed anything.
        assert invoked["count"] == 0
        executed = [e for e in result["audit"] if e["step"] == "execute"]
        assert executed == []


class TestCapabilityDiscovery:
    def test_discover_by_capability(self):
        registry = AgentRegistry()
        a = registry.register(_uid("a1"), capabilities=["search", "read"], trust_score=0.6)
        b = registry.register(_uid("a2"), capabilities=["read", "write"], trust_score=0.6)
        c = registry.register(_uid("a3"), capabilities=["search"], trust_score=0.6)

        readers = registry.discover_by_capability("read")
        assert set(x.principal for x in readers) == {a.principal, b.principal}

        searchers = registry.discover_by_capability("search")
        assert set(x.principal for x in searchers) == {a.principal, c.principal}

        none = registry.discover_by_capability("admin")
        assert none == []

    def test_unregister_removes_from_discovery(self):
        registry = AgentRegistry()
        p = _uid("u1")
        registry.register(p, capabilities=["read"], trust_score=0.6)
        assert len(registry.discover_by_capability("read")) == 1
        assert registry.unregister(p) is True
        assert registry.discover_by_capability("read") == []
