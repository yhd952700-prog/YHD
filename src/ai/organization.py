"""
AI Organization Engine — MASTER-SPEC Phase 12.

Implements §61-64 (Organization Engine / Functions / AI Organization Model /
KPI) and §161 NO FAKE ORGANIZATION: an Organization is a *first-class object*
that really owns Members, Roles, Departments, Teams, Goals, Policies, Budget,
Memory, KPIs, and an Audit trail — none of them empty placeholders.

Members are real Agent runtime entities produced by ``AgentFactory`` (so each
carries a real identity, memory and policy), reusing the ``Role`` enum and
``Agent.execute()`` from the collaboration/employee layer.

Layering:

    collaboration.MultiAgentTeam   (peer message protocol, Phase 10)
      L-- Organization             (org structure: depts/teams/budget/KPI, Phase 12)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .employee import Agent
from .agent_factory import AgentFactory, AgentSpec
from .collaboration import Role
from .observability import observe
from .audit import audited


class MemberStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class GoalStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Goal:
    """A first-class organizational goal (MASTER-SPEC §61)."""
    id: str
    description: str
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@dataclass
class Budget:
    """Real budget with overspend protection (MASTER-SPEC §61)."""
    total: float
    spent: float = 0.0

    def remaining(self) -> float:
        return self.total - self.spent

    def spend(self, amount: float) -> bool:
        if amount < 0 or amount > self.remaining():
            return False
        self.spent += amount
        return True


@dataclass
class Member:
    """A member of the organization — wraps a real Agent entity."""
    id: str
    name: str
    role: Role
    department: str
    agent: Optional[Agent] = None
    status: MemberStatus = MemberStatus.ACTIVE
    joined_at: float = field(default_factory=time.time)
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0


@dataclass
class Department:
    """A department grouping members and teams (MASTER-SPEC §63)."""
    id: str
    name: str
    member_ids: List[str] = field(default_factory=list)
    team_ids: List[str] = field(default_factory=list)


@dataclass
class Team:
    """A cross-cutting team with a shared goal (MASTER-SPEC §61)."""
    id: str
    name: str
    goal: Optional[str] = None
    member_ids: List[str] = field(default_factory=list)


class Organization:
    """AI Organization — a first-class object (MASTER-SPEC §61, §161)."""

    def __init__(self, name: str, budget: float = 0.0,
                 factory: Optional[AgentFactory] = None) -> None:
        self.name = name
        self.factory = factory or AgentFactory()
        self.budget = Budget(total=budget)
        self.goals: Dict[str, Goal] = {}
        self.members: Dict[str, Member] = {}
        self.departments: Dict[str, Department] = {}
        self.teams: Dict[str, Team] = {}
        self.policies: List[str] = []
        self.memory: Dict[str, Any] = {}
        self.audit_log: List[Dict[str, Any]] = []
        self._audit("create_organization", name=name, budget=budget)

    # ------------------------------------------------------------------ audit
    def _audit(self, action: str, **details: Any) -> None:
        self.audit_log.append({"action": action, "timestamp": time.time(), **details})

    # ------------------------------------------------------------ §62 functions
    @observe("organization.create_goal")
    @audited("p12.organization.create_goal", module="src.ai.organization")
    def create_goal(self, description: str) -> str:
        goal_id = f"goal_{uuid.uuid4().hex[:8]}"
        self.goals[goal_id] = Goal(id=goal_id, description=description)
        self._audit("create_goal", goal_id=goal_id, description=description)
        return goal_id

    @audited("p12.organization.create_department", module="src.ai.organization")
    def create_department(self, name: str) -> str:
        dep_id = f"dept_{uuid.uuid4().hex[:8]}"
        self.departments[dep_id] = Department(id=dep_id, name=name)
        self._audit("create_department", department_id=dep_id, name=name)
        return dep_id

    def create_team(self, name: str, goal: Optional[str] = None) -> str:
        team_id = f"team_{uuid.uuid4().hex[:8]}"
        self.teams[team_id] = Team(id=team_id, name=name, goal=goal)
        self._audit("create_team", team_id=team_id, name=name)
        return team_id

    @observe("organization.hire")
    def hire(self, role: Role, department_id: str, name: Optional[str] = None,
             capabilities: Optional[List[str]] = None) -> str:
        """Hire a real agent (via AgentFactory) into a department."""
        principal = name or f"{self.name}-{role.value}-{uuid.uuid4().hex[:6]}"
        out = self.factory.create(AgentSpec(
            agent_type=role.value,
            goal=f"serve {self.name} as {role.value}",
            capabilities=capabilities or [],
            name=principal,
        ))
        self.members[principal] = Member(
            id=principal,
            name=principal,
            role=role,
            department=department_id,
            agent=out["agent"],
        )
        if department_id in self.departments:
            self.departments[department_id].member_ids.append(principal)
        self._audit("hire", member_id=principal, role=role.value,
                    department=department_id)
        return principal

    def assign(self, member_id: str, department_id: str) -> bool:
        """Move a member to another department."""
        if member_id not in self.members or department_id not in self.departments:
            return False
        old = self.members[member_id].department
        if old in self.departments and member_id in self.departments[old].member_ids:
            self.departments[old].member_ids.remove(member_id)
        self.members[member_id].department = department_id
        self.departments[department_id].member_ids.append(member_id)
        self._audit("assign", member_id=member_id, department=department_id)
        return True

    @observe("organization.delegate")
    def delegate(self, manager_id: str, role: Role, task: str) -> Dict[str, Any]:
        """Delegate a task to the first active member of ``role``."""
        member = self._first_active_by_role(role)
        if member is None:
            return {"status": "failed", "error": f"no active {role.value} member"}
        member.tasks_submitted += 1
        result = member.agent.execute(task)  # real provider call
        if result.get("status") == "completed":
            member.tasks_completed += 1
        else:
            member.tasks_failed += 1
        self._audit("delegate", manager=manager_id, member=member.id, task=task,
                    status=result.get("status"))
        return result

    def evaluate(self, member_id: str) -> Dict[str, Any]:
        """Real KPI snapshot for a member (MASTER-SPEC §64)."""
        member = self.members[member_id]
        total = member.tasks_completed + member.tasks_failed
        return {
            "member_id": member_id,
            "role": member.role.value,
            "tasks_submitted": member.tasks_submitted,
            "tasks_completed": member.tasks_completed,
            "tasks_failed": member.tasks_failed,
            "success_rate": (member.tasks_completed / total) if total else 0.0,
            "avg_latency_ms": (
                member.agent.total_latency_ms / total if total and member.agent else 0.0
            ),
        }

    def promote(self, member_id: str, new_role: Role) -> bool:
        if member_id not in self.members:
            return False
        self.members[member_id].role = new_role
        self._audit("promote", member_id=member_id, role=new_role.value)
        return True

    def suspend(self, member_id: str) -> bool:
        if member_id not in self.members:
            return False
        self.members[member_id].status = MemberStatus.SUSPENDED
        self._audit("suspend", member_id=member_id)
        return True

    def terminate(self, member_id: str) -> bool:
        if member_id not in self.members:
            return False
        self.members[member_id].status = MemberStatus.TERMINATED
        self._audit("terminate", member_id=member_id)
        return True

    def spend(self, amount: float) -> bool:
        ok = self.budget.spend(amount)
        if ok:
            self._audit("spend", amount=amount)
        return ok

    def add_policy(self, policy_name: str) -> None:
        self.policies.append(policy_name)
        self._audit("add_policy", policy=policy_name)

    def remember(self, key: str, value: Any) -> None:
        """Organization-scoped memory (real store/recall)."""
        self.memory[key] = value
        self._audit("remember", key=key)

    def recall(self, key: str) -> Any:
        return self.memory.get(key)

    def report(self) -> Dict[str, Any]:
        """Organization-level report with real aggregates (§64)."""
        submitted = sum(m.tasks_submitted for m in self.members.values())
        completed = sum(m.tasks_completed for m in self.members.values())
        failed = sum(m.tasks_failed for m in self.members.values())
        return {
            "name": self.name,
            "member_count": len(self.members),
            "department_count": len(self.departments),
            "team_count": len(self.teams),
            "goal_count": len(self.goals),
            "policy_count": len(self.policies),
            "budget_total": self.budget.total,
            "budget_spent": self.budget.spent,
            "budget_remaining": self.budget.remaining(),
            "tasks_submitted": submitted,
            "tasks_completed": completed,
            "tasks_failed": failed,
            "success_rate": (completed / submitted) if submitted else 0.0,
            "audit_entries": len(self.audit_log),
        }

    # ---------------------------------------------------------------- helpers
    def _first_active_by_role(self, role: Role) -> Optional[Member]:
        for member in self.members.values():
            if member.role == role and member.status == MemberStatus.ACTIVE:
                return member
        return None
