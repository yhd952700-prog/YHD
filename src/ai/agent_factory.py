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

Additionally provides a role-specialization catalogue (``AgentRole`` /
``RoleProfile`` / ``ROLE_PROFILES``) so callers can create role-persona agents
(manager / researcher / analyst / ...) directly through the same factory.

This module EXTENDS the existing agent model in ``employee.py`` — it reuses
``Agent`` / ``AgentStatus`` / ``get_provider`` and does not rewrite them.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from .employee import Agent, AgentStatus
from .providers import BaseProvider, get_provider
from ..kernels.identity import get_identity_manager
from .observability import observe
from .audit import audited

_log = logging.getLogger(__name__)

#: Rough prompt->token heuristic (~4 characters per token). This feeds a
#: *budget guard*, not a tokenizer: it only needs to be the right order of
#: magnitude so a wildly over-budget action is caught before the call.
CHARS_PER_TOKEN = 4

#: Assumed output length when the provider does not declare one. Matches the
#: ``ProviderCapabilities.max_output_tokens`` default in ``providers.py``.
DEFAULT_MAX_OUTPUT_TOKENS = 1024

#: Credibility ceiling for a provider's declared ``max_output_tokens`` when it
#: feeds the budget guard. Real providers deploy 1024-8192 (see
#: ``providers.py``), so this bound is far above any realistic completion and
#: does not weaken enforcement. It exists because the estimate is
#: *worst-case* -- a single implausible declaration (e.g. 12,000,000) would
#: otherwise manufacture a cost no real request can incur and turn the guard
#: into a blanket denial of legitimate traffic. Declarations above the ceiling
#: are clamped *and warned about*, never silently accepted.
MAX_OUTPUT_TOKENS_CEILING = 32768


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


def economy_inputs(
    principal: str,
    model: Optional[str],
    tokens_in: int,
    tokens_out: int,
) -> Dict[str, Any]:
    """Source the REAL inputs the policy quota rule needs -- or omit them.

    The built-in ``quota_enforcement`` rule denies an action when
    ``resource.available`` is less than ``action.estimated_cost``. Both sides
    have to come from real data, so this helper reads:

    * the cost from the billing engine's price table, priced per model by the
      model registry's ``estimated_cost_per_1k``;
    * the budget from the resource kernel's ``COST`` quota (the principal's own
      quota if one exists, otherwise the system-wide one).

    **Nothing here is fabricated.** When an ingredient is genuinely unknown --
    an unpriced model, or no ``COST`` quota at all -- the corresponding key is
    *omitted* rather than passed as 0. A zero would be a lie that silences the
    rule (0 is always within budget), whereas an omission leaves the rule
    inapplicable and the decision then reports it through
    ``PolicyDecision.unapplied_deny_rules`` / ``unresolved_operands``.

    Lookup failures degrade to omission with a warning instead of raising:
    this runs on the authorization path, and a broken price file must not take
    the assistant down. The warning is what keeps that from being silent.

    Returns a kwargs mapping to splat into :meth:`AgentPolicy.authorize`.
    """
    result: Dict[str, Any] = {}

    # ---- cost side: only when the model is actually priced ----------------
    if model:
        try:
            from ..models.registry import get_model_registry
            from .economy import BillingEngine

            metadata = get_model_registry().get_model(model)
            if metadata is not None:
                billing = BillingEngine(
                    {model: float(metadata.estimated_cost_per_1k)}
                )
                # ``has_price`` guards against reading "unpriced" as "free".
                if billing.has_price(model):
                    result["estimated_cost"] = billing.estimate_cost(
                        model, max(0, int(tokens_in)), max(0, int(tokens_out))
                    )
        except Exception:  # pragma: no cover - defensive, never break auth
            _log.warning(
                "economy_inputs: cost lookup failed for model=%r; omitting "
                "estimated_cost (quota will not apply on this call)",
                model,
                exc_info=True,
            )

    # ---- budget side: only when a COST quota actually exists --------------
    try:
        from ..kernels.resource import get_resource_manager, ResourceType

        manager = get_resource_manager()

        def _cost_quotas(owner: str):
            return [
                q
                for q in manager.get_all_quotas(owner)
                if q.resource_type == ResourceType.COST
            ]

        quotas = _cost_quotas(principal) or _cost_quotas("system")
        if quotas:
            # The binding constraint is the tightest quota in force.
            result["resource"] = {"available": min(q.available for q in quotas)}
    except Exception:  # pragma: no cover - defensive, never break auth
        _log.warning(
            "economy_inputs: quota lookup failed for principal=%r; omitting "
            "resource.available (quota will not apply on this call)",
            principal,
            exc_info=True,
        )

    return result


# =====================================================================
# Role specialization catalogue
# =====================================================================


class AgentRole(str, Enum):
    """Specialized agent roles with clear responsibilities."""
    MANAGER = "manager"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    CRITIC = "critic"
    CREATOR = "creator"
    EXECUTOR = "executor"
    QA = "qa"
    PLANNER = "planner"
    GENERAL = "general"


@dataclass
class RoleProfile:
    """Configuration profile for a role specialization."""
    role: AgentRole
    description: str
    strengths: List[str]
    system_prompt_template: str
    default_temperature: float = 0.7
    capabilities: List[str] = field(default_factory=list)
    max_concurrent_tasks: int = 3


ROLE_PROFILES: Dict[AgentRole, RoleProfile] = {
    AgentRole.MANAGER: RoleProfile(
        role=AgentRole.MANAGER,
        description="Coordinates work, delegates tasks, and synthesizes results",
        strengths=["delegation", "synthesis", "prioritization", "coordination"],
        system_prompt_template=(
            "You are a MANAGER agent. Your job is to coordinate work, "
            "delegate tasks to specialists, and synthesize results into "
            "coherent deliverables. Think strategically, communicate clearly, "
            "and ensure quality outcomes."
        ),
        default_temperature=0.5,
        capabilities=["delegate", "synthesize", "prioritize", "review"],
        max_concurrent_tasks=5,
    ),
    AgentRole.RESEARCHER: RoleProfile(
        role=AgentRole.RESEARCHER,
        description="Gathers information, finds facts, and explores topics",
        strengths=["information_gathering", "fact_checking", "source_evaluation"],
        system_prompt_template=(
            "You are a RESEARCHER agent. Your job is to gather information, "
            "find relevant facts, evaluate sources, and explore topics deeply. "
            "Be thorough, cite sources, and distinguish between confirmed "
            "facts and speculation."
        ),
        default_temperature=0.3,
        capabilities=["search", "summarize", "fact_check", "cite"],
        max_concurrent_tasks=4,
    ),
    AgentRole.ANALYST: RoleProfile(
        role=AgentRole.ANALYST,
        description="Analyzes data, identifies patterns, and draws insights",
        strengths=["data_analysis", "pattern_recognition", "insight_extraction"],
        system_prompt_template=(
            "You are an ANALYST agent. Your job is to analyze data, identify "
            "patterns, draw insights, and present findings clearly. Be "
            "data-driven, quantify when possible, and support conclusions "
            "with evidence."
        ),
        default_temperature=0.4,
        capabilities=["analyze", "visualize", "predict", "evaluate"],
        max_concurrent_tasks=3,
    ),
    AgentRole.CRITIC: RoleProfile(
        role=AgentRole.CRITIC,
        description="Reviews work, identifies gaps, and provides constructive feedback",
        strengths=["critical_thinking", "gap_identification", "quality_assessment"],
        system_prompt_template=(
            "You are a CRITIC agent. Your job is to review work, identify "
            "gaps, weaknesses, and potential problems, and provide "
            "constructive feedback. Be thorough, fair, and specific — "
            "criticize the work, not the person."
        ),
        default_temperature=0.5,
        capabilities=["review", "audit", "validate", "find_gaps"],
        max_concurrent_tasks=4,
    ),
    AgentRole.CREATOR: RoleProfile(
        role=AgentRole.CREATOR,
        description="Generates ideas, creates content, and builds solutions",
        strengths=["ideation", "content_creation", "solution_design"],
        system_prompt_template=(
            "You are a CREATOR agent. Your job is to generate ideas, create "
            "content, and design innovative solutions. Think creatively, "
            "explore alternatives, and produce original work."
        ),
        default_temperature=0.9,
        capabilities=["generate", "design", "brainstorm", "write"],
        max_concurrent_tasks=3,
    ),
    AgentRole.EXECUTOR: RoleProfile(
        role=AgentRole.EXECUTOR,
        description="Implements solutions, executes tasks, and gets things done",
        strengths=["implementation", "task_execution", "problem_solving"],
        system_prompt_template=(
            "You are an EXECUTOR agent. Your job is to implement solutions, "
            "execute tasks efficiently, and get things done. Be practical, "
            "action-oriented, and deliver working results."
        ),
        default_temperature=0.6,
        capabilities=["implement", "execute", "build", "fix"],
        max_concurrent_tasks=3,
    ),
    AgentRole.QA: RoleProfile(
        role=AgentRole.QA,
        description="Tests, validates, and ensures quality",
        strengths=["testing", "validation", "quality_assurance", "edge_cases"],
        system_prompt_template=(
            "You are a QA agent. Your job is to test, validate, and ensure "
            "quality. Look for bugs, edge cases, and quality issues. "
            "Be systematic, thorough, and relentless in pursuit of quality."
        ),
        default_temperature=0.4,
        capabilities=["test", "validate", "verify", "debug"],
        max_concurrent_tasks=4,
    ),
    AgentRole.PLANNER: RoleProfile(
        role=AgentRole.PLANNER,
        description="Plans strategies, decomposes goals, and creates roadmaps",
        strengths=["strategic_planning", "goal_decomposition", "roadmapping"],
        system_prompt_template=(
            "You are a PLANNER agent. Your job is to plan strategies, "
            "decompose goals into actionable steps, and create clear roadmaps. "
            "Think ahead, anticipate risks, and produce realistic plans."
        ),
        default_temperature=0.5,
        capabilities=["plan", "decompose", "schedule", "risk_assess"],
        max_concurrent_tasks=2,
    ),
    AgentRole.GENERAL: RoleProfile(
        role=AgentRole.GENERAL,
        description="General-purpose agent for miscellaneous tasks",
        strengths=["adaptability", "general_knowledge", "versatility"],
        system_prompt_template=(
            "You are a GENERAL-purpose agent. Handle a variety of tasks "
            "with competence and flexibility. Be helpful, accurate, and clear."
        ),
        default_temperature=0.7,
        capabilities=["answer", "help", "assist"],
        max_concurrent_tasks=5,
    ),
}


class AgentFactory:
    """Goal → AgentSpec → Identity → Activate.

    Integrates with the Identity / Memory / Policy kernels so the produced
    agent carries a real ``AgentIdentity``, a working ``AgentMemory`` and a
    policy-governed ``AgentPolicy`` — no self-declared identity, no detached
    memory, no policy-less action path.

    Also supports role-persona creation: ``create(AgentRole.RESEARCHER, ...)``
    builds an agent from the role catalogue (system prompt + capabilities).
    """

    def __init__(self, provider: Optional[BaseProvider] = None):
        self._provider = provider
        self._created: Dict[str, Agent] = {}

    @observe("agent.create")
    def create(self, spec: Union[AgentSpec, "AgentRole"], **kwargs) -> Dict[str, Any]:
        """Instantiate an agent, its identity, memory, policy and runtime.

        Accepts either an :class:`AgentSpec` (full Phase-3 pipeline) or an
        :class:`AgentRole` (role-persona agent from the catalogue).
        """
        if isinstance(spec, AgentRole):
            return self._create_role_agent(spec, **kwargs)
        return self._create_from_spec(spec)

    # ------------------------------------------------------------------
    # Spec-based creation (MASTER-SPEC Phase 3)
    # ------------------------------------------------------------------

    def _create_from_spec(self, spec: AgentSpec) -> Dict[str, Any]:
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
        provider = self._provider or get_provider()
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

    # ------------------------------------------------------------------
    # Role-based creation (role specialization catalogue)
    # ------------------------------------------------------------------

    def _create_role_agent(
        self,
        role: AgentRole,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        provider: Optional[BaseProvider] = None,
        custom_capabilities: Optional[List[str]] = None,
    ) -> Agent:
        """Create a role-specialized agent from the role catalogue."""
        profile = ROLE_PROFILES.get(role, ROLE_PROFILES[AgentRole.GENERAL])

        agent_id = agent_id or f"{role.value}_{uuid.uuid4().hex[:8]}"
        name = name or f"{role.value.capitalize()} Agent"

        prompt = system_prompt or profile.system_prompt_template

        capabilities = list(profile.capabilities)
        if custom_capabilities:
            capabilities.extend(custom_capabilities)

        agent = Agent(
            id=agent_id,
            agent_type=role.value,
            name=name,
            provider=provider or self._provider or get_provider(),
            system_prompt=prompt,
        )

        # Store role profile info in agent metadata
        agent._role_profile = profile
        agent._capabilities = capabilities

        self._created[agent_id] = agent
        return agent

    def create_team(
        self,
        roles: List[AgentRole],
        team_name: str = "Team",
        provider: Optional[BaseProvider] = None,
    ) -> Dict[AgentRole, List[Agent]]:
        """Create a team with one agent per role."""
        team: Dict[AgentRole, List[Agent]] = {}
        for role in roles:
            agent = self._create_role_agent(
                role,
                name=f"{team_name}-{role.value.capitalize()}",
                provider=provider,
            )
            team[role] = [agent]
        return team

    def create_pool(
        self,
        role: AgentRole,
        count: int,
        pool_name: str = "Pool",
        provider: Optional[BaseProvider] = None,
    ) -> List[Agent]:
        """Create a pool of agents with the same role.

        Useful for parallel work or committee-style voting.
        """
        agents = []
        for i in range(count):
            agent = self._create_role_agent(
                role,
                name=f"{pool_name}-{role.value}-{i+1}",
                provider=provider,
            )
            agents.append(agent)
        return agents

    def get_profile(self, role: AgentRole) -> RoleProfile:
        """Get the profile for a role."""
        return ROLE_PROFILES.get(role, ROLE_PROFILES[AgentRole.GENERAL])

    def available_roles(self) -> List[AgentRole]:
        """List all available roles."""
        return list(ROLE_PROFILES.keys())

    @property
    def created_count(self) -> int:
        return len(self._created)

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        return self._created.get(agent_id)

    # ------------------------------------------------------------------
    # Role capability queries
    # ------------------------------------------------------------------

    def can_do(self, role: AgentRole, capability: str) -> bool:
        """Check if a role has a given capability."""
        profile = ROLE_PROFILES.get(role)
        if profile is None:
            return False
        return capability.lower() in [c.lower() for c in profile.capabilities]

    def best_role_for(self, task_description: str) -> AgentRole:
        """Heuristic: pick the best role for a task description.

        Simple keyword-based matching. For production, use an LLM-based
        router instead.
        """
        task_lower = task_description.lower()

        # Keyword → role mapping
        role_keywords = {
            AgentRole.RESEARCHER: ["research", "search", "find", "investigate",
                                   "gather", "fact", "information", "explore"],
            AgentRole.ANALYST: ["analyze", "data", "pattern", "insight",
                                "evaluate", "metrics", "statistics"],
            AgentRole.CREATOR: ["create", "design", "write", "generate",
                                "idea", "brainstorm", "build", "develop"],
            AgentRole.EXECUTOR: ["execute", "implement", "do", "perform",
                                 "run", "complete", "action"],
            AgentRole.QA: ["test", "qa", "verify", "validate", "check",
                           "debug", "quality"],
            AgentRole.CRITIC: ["review", "critique", "audit", "assess",
                               "feedback", "gap"],
            AgentRole.PLANNER: ["plan", "strategy", "roadmap", "decompose",
                                "schedule", "organize"],
            AgentRole.MANAGER: ["manage", "coordinate", "delegate", "supervise",
                                "lead", "organize"],
        }

        best_role = AgentRole.GENERAL
        best_score = 0

        for role, keywords in role_keywords.items():
            score = sum(1 for kw in keywords if kw in task_lower)
            if score > best_score:
                best_score = score
                best_role = role

        return best_role
