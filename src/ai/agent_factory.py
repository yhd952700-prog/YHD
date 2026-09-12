"""Agent Factory & Runtime Service for LiuHao AI OS (鎏灏).

Implements MASTER-SPEC Phase 3 (Agent Runtime):

- ``AgentSpec``          — the input schema (AgentType / Goal / Capabilities /
                           MemoryPolicy / ModelPolicy / Budget / Resources /
                           Permissions / SandboxPolicy / EvaluationSuite).
- ``AgentRuntimeService`` — the 8 lifecycle methods required by the spec:
                           start / pause / resume / stop / cancel / execute /
                           checkpoint / recover.
- ``AgentMemory``         — agent-scoped binding over the Memory Kernel
                           (entries are tagged with the agent principal).
- ``AgentPolicy``         — agent-scoped binding over the Policy Engine
                           (authorize() consults the real policy rules).
- ``AgentFactory``        — Goal → AgentSpec → Identity → Activate, integrating
                           with the Identity / Memory / Policy kernels so a
                           produced agent has a real identity, a working memory,
                           and a policy-governed action path.

This module EXTENDS the existing agent model in ``employee.py`` — it reuses
``Agent`` / ``AgentStatus`` / ``get_provider`` and does not rewrite them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .employee import Agent, AgentStatus
from .providers import get_provider
from ..kernels.identity import get_identity_manager
from .observability import observe
from .audit import audited


@dataclass
class AgentSpec:
    """Input schema for the Agent Factory (MASTER-SPEC Phase 3 §Agent Factory 输入)."""

    agent_type: str
    goal: str
    capabilities: List[str] = field(default_factory=list)
    memory_policy: Optional[str] = None
    model_policy: Optional[str] = None
    budget: Optional[float] = None
    resources: Dict[str, Any] = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    sandbox_policy: Optional[str] = None
    evaluation_suite: Optional[str] = None
    name: Optional[str] = None  # optional explicit principal; else auto-generated
    # 独立 system prompt（默认 None → 回退到 goal）。领域模板（ZOON）用它把
    # 「领域专家人设」与「具体任务目标」分离，而通用 Agent 仍以 goal 兼作提示。
    system_prompt: Optional[str] = None


@dataclass
class AgentRuntimeService:
    """Lifecycle controller wrapping an ``Agent``.

    Implements the 8 runtime operations required by MASTER-SPEC Phase 3.
    """

    agent: Agent

    @observe("agent.start")
    @audited("p3.agent_runtime.start", module="src.ai.agent_factory")
    def start(self) -> bool:
        """start(): move an idle/paused agent into RUNNING."""
        if self.agent.status in (AgentStatus.IDLE, AgentStatus.PAUSED):
            self.agent.status = AgentStatus.RUNNING
            return True
        return False

    def pause(self) -> bool:
        """pause(): suspend the agent, resumable via resume()."""
        return self.agent.pause()

    def resume(self) -> bool:
        """resume(): continue a paused agent."""
        return self.agent.resume()

    @audited("p3.agent_runtime.stop", module="src.ai.agent_factory")
    def stop(self) -> bool:
        """stop(): terminal stop, not resumable."""
        return self.agent.stop()

    def cancel(self) -> bool:
        """cancel(): abandon the current task and stop the agent."""
        self.agent.current_task = None
        return self.agent.stop()

    @observe("agent.execute")
    def execute(self, task: str, **kwargs) -> Dict[str, Any]:
        """execute(): run a task through the agent's provider."""
        return self.agent.execute(task, **kwargs)

    def checkpoint(self) -> Dict[str, Any]:
        """checkpoint(): snapshot current state for recovery."""
        return self.agent.checkpoint()

    def recover(self) -> bool:
        """recover(): restore state from the last checkpoint."""
        return self.agent.recover()


@dataclass
class AgentMemory:
    """Agent-scoped memory binding over the Memory Kernel.

    Every entry is tagged with ``agent:{principal}`` so an agent's memories are
    attributable and queryable, while remaining backed by the single canonical
    Memory Kernel (not a private store).
    """

    principal: str
    default_tier: Any = None  # str or MemoryTier; resolved in __post_init__
    _kernel: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from ..kernels.memory import get_memory_kernel, MemoryTier

        self._kernel = get_memory_kernel()
        if isinstance(self.default_tier, str):
            mapping = {t.value: t for t in MemoryTier}
            self.default_tier = mapping.get(self.default_tier, MemoryTier.MID_TERM)
        elif self.default_tier is None:
            self.default_tier = MemoryTier.MID_TERM

    def store(
        self,
        key: str,
        value: Any,
        tier: Any = None,
        scope: Any = None,
        tags: Optional[set] = None,
        ttl: Any = None,
    ):
        from ..kernels.memory import MemoryScope

        merged = set(tags or ()) | {f"agent:{self.principal}"}
        return self._kernel.store(
            key,
            value,
            tier=tier or self.default_tier,
            scope=scope or MemoryScope.L1,
            tags=merged,
            ttl=ttl,
        )

    def recall(self, key: str, scope: Any = None, tier_filter: Any = None, tags: Any = None):
        from ..kernels.memory import MemoryScope

        # Agent-scoped recall: by default only return THIS agent's entries.
        merged = set(tags or ()) | {f"agent:{self.principal}"}
        return self._kernel.recall(
            key,
            scope=scope or MemoryScope.L1,
            tier_filter=tier_filter,
            tags=merged,
        )

    def stats(self) -> Dict[str, Any]:
        return self._kernel.stats()


@dataclass
class AgentPolicy:
    """Agent-scoped policy binding over the Policy Engine.

    ``authorize()`` builds the canonical actor/action context (agent type,
    principal, scope, capabilities) and consults the real policy rules — the
    same default-deny engine the Policy Kernel owns.
    """

    principal: str
    capabilities: List[str] = field(default_factory=list)
    scope: str = "L1"
    _engine: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from ..kernels.policy import get_policy_engine

        self._engine = get_policy_engine()

    def authorize(
        self,
        action: str,
        resource: Optional[Dict[str, Any]] = None,
        *,
        risk_level: str = "LOW",
        required_scope: Optional[str] = None,
        required_capability: Optional[str] = None,
        estimated_cost: Optional[float] = None,
    ):
        from ..kernels.policy import PolicyScope

        action_dict: Dict[str, Any] = {
            "name": action,
            "type": action,
            "risk_level": risk_level,
        }
        if required_scope is not None:
            action_dict["required_scope"] = required_scope
        if required_capability is not None:
            action_dict["required_capability"] = required_capability
        if estimated_cost is not None:
            action_dict["estimated_cost"] = estimated_cost

        actor = {
            "type": "agent",
            "principal": self.principal,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
        }

        return self._engine.evaluate_simple(
            actor=actor,
            action=action_dict,
            resource=resource,
            scope=PolicyScope.L1,
        )

    def is_allowed(self, action: str, **kwargs) -> bool:
        return self.authorize(action, **kwargs).is_allowed


class AgentFactory:
    """Goal → AgentSpec → Identity → Activate.

    Integrates with the Identity / Memory / Policy kernels so the produced
    agent carries a real ``AgentIdentity``, a working ``AgentMemory`` and a
    policy-governed ``AgentPolicy`` — no self-declared identity, no detached
    memory, no policy-less action path.
    """

    @observe("agent.create")
    def create(self, spec: AgentSpec) -> Dict[str, Any]:
        """Instantiate an agent, its identity, memory, policy and runtime."""
        manager = get_identity_manager()

        principal = spec.name or f"agent-{uuid.uuid4().hex[:12]}"

        # 1. Identity — reuse existing identity or create a new, verified one.
        identity = manager.get_identity_by_principal(principal)
        if identity is None:
            identity = manager.create_identity(
                principal=principal,
                permissions=set(spec.permissions) if spec.permissions else None,
                trust_score=0.5,
            )

        # 2. Agent — reuse the existing model (extend, don't rewrite).
        provider = get_provider()
        agent = Agent(
            id=principal,
            agent_type=spec.agent_type,
            name=principal,
            provider=provider,
            system_prompt=spec.system_prompt if spec.system_prompt is not None else spec.goal,
            metadata={
                "capabilities": list(spec.capabilities),
                "memory_policy": spec.memory_policy,
                "model_policy": spec.model_policy,
                "budget": spec.budget,
                "resources": dict(spec.resources),
                "sandbox_policy": spec.sandbox_policy,
                "evaluation_suite": spec.evaluation_suite,
            },
        )

        # 3. Runtime — wrap the agent with the 8-method lifecycle controller.
        runtime = AgentRuntimeService(agent=agent)

        # 4. Memory & Policy — real bindings over the canonical kernels.
        memory = AgentMemory(principal=principal, default_tier=spec.memory_policy)
        policy = AgentPolicy(
            principal=principal,
            capabilities=list(spec.capabilities),
            scope="L1",
        )

        # 5. Activate — the agent is created in IDLE state, ready to start.
        return {
            "agent": agent,
            "identity": identity,
            "runtime": runtime,
            "memory": memory,
            "capabilities": list(spec.capabilities),
            "policy": policy,
        }
