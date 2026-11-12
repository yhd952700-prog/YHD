"""AI Organization Engine — MASTER-SPEC Phase 12.

Implements §61-64 (Organization Engine / Functions / AI Organization Model /
KPI) and §161 NO FAKE ORGANIZATION: an Organization is a *first-class object*
that really owns Members, Roles, Departments, Teams, Goals, Policies, Budget,
Memory, KPIs, and an Audit trail — none of them empty placeholders.

Members are real Agent runtime entities produced by ``AgentFactory`` (so each
carries a real identity, memory and policy), reusing the ``Role`` enum and
``Agent.execute()`` from the collaboration/employee layer.

The module also provides an organizational-structure layer — hierarchical
``OrgRole`` levels, ``OrgMember`` chart nodes, capacity-tracking ``Team``s,
``Department`` groupings, and a role-based access-control matrix
(``Permission`` / ``RBACMatrix``) — integrated with the same ``Organization``
container so org-chart traversal (reporting lines, subordinates, capacity)
works alongside the Phase-12 engine.

Layering:

    collaboration.MultiAgentTeam   (peer message protocol, Phase 10)
      L-- Organization             (org structure: depts/teams/budget/KPI, Phase 12)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

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


# =====================================================================
# Role Hierarchy (organizational chart layer)
# =====================================================================


class OrgRole(str, Enum):
    """Hierarchical role levels within the organization.

    Ordered from lowest to highest seniority.  Integrates conceptually
    with ``AgentRole`` — each org role typically maps to one or more
    agent specializations (e.g. a MANAGER org-role supervises agents
    with the ``AgentRole.MANAGER`` factory profile).
    """

    INDIVIDUAL_CONTRIBUTOR = "individual_contributor"
    LEAD = "lead"
    MANAGER = "manager"
    DIRECTOR = "director"

    @classmethod
    def hierarchy(cls) -> List["OrgRole"]:
        """Return roles ordered from most junior to most senior."""
        return [
            cls.INDIVIDUAL_CONTRIBUTOR,
            cls.LEAD,
            cls.MANAGER,
            cls.DIRECTOR,
        ]

    def is_senior_to(self, other: "OrgRole") -> bool:
        """Return True if this role is strictly more senior than *other*."""
        order = {r: i for i, r in enumerate(self.hierarchy())}
        return order[self] > order[other]

    def is_junior_to(self, other: "OrgRole") -> bool:
        """Return True if this role is strictly more junior than *other*."""
        order = {r: i for i, r in enumerate(self.hierarchy())}
        return order[self] < order[other]


@dataclass
class OrgMember:
    """A member of the organization chart (human or AI agent).

    Attributes:
        member_id: Unique identifier.
        name: Human-readable display name.
        role: Hierarchical role within the organization.
        agent_role: Optional link to an ``AgentRole`` specialization
            produced by the agent factory.
        manager_id: ID of the member this person reports to (None for top).
        team_id: ID of the team this member belongs to.
        is_active: Whether the member is currently active.
        skills: List of skill tags for capacity planning.
        metadata: Arbitrary key-value data.
    """

    member_id: str
    name: str
    role: OrgRole
    agent_role: Optional[str] = None  # maps to AgentRole.value
    manager_id: Optional[str] = None
    team_id: Optional[str] = None
    is_active: bool = True
    skills: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.member_id:
            self.member_id = f"mem_{uuid.uuid4().hex[:8]}"


@dataclass
class Department:
    """A department grouping members and teams (MASTER-SPEC §63).

    Attributes:
        dept_id: Unique identifier (``id`` alias provided for the engine API).
        name: Department display name.
        description: Mandate / description.
        head_id: Member ID of the department head (typically a director).
        team_ids: List of team IDs belonging to this department.
        member_ids: List of member IDs belonging to this department.
    """

    dept_id: str
    name: str
    description: str = ""
    head_id: Optional[str] = None
    team_ids: List[str] = field(default_factory=list)
    member_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.dept_id:
            self.dept_id = f"dept_{uuid.uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        """Alias for ``dept_id`` (engine-layer compatibility)."""
        return self.dept_id

    def add_team(self, team_id: str) -> bool:
        """Add a team to the department.  Returns False if already present."""
        if team_id in self.team_ids:
            return False
        self.team_ids.append(team_id)
        return True

    def remove_team(self, team_id: str) -> bool:
        """Remove a team.  Returns False if not found."""
        if team_id not in self.team_ids:
            return False
        self.team_ids.remove(team_id)
        return True


@dataclass
class Team:
    """A cross-cutting team with a shared goal (MASTER-SPEC §61).

    Attributes:
        team_id: Unique identifier (``id`` alias provided for the engine API).
        name: Team display name.
        goal: Optional shared goal driving the team.
        description: Short description of the team's mandate.
        department_id: Parent department.
        manager_id: Member ID of the team's manager / lead.
        member_ids: Set of member IDs belonging to this team.
        capacity: Maximum number of members the team is sized for.
        utilization_target: Target utilization ratio (0.0 - 1.0).
        performance_metrics: Named performance counters.
    """

    team_id: str
    name: str
    goal: Optional[str] = None
    description: str = ""
    department_id: Optional[str] = None
    manager_id: Optional[str] = None
    member_ids: List[str] = field(default_factory=list)
    capacity: int = 10
    utilization_target: float = 0.8
    performance_metrics: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.team_id:
            self.team_id = f"team_{uuid.uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        """Alias for ``team_id`` (engine-layer compatibility)."""
        return self.team_id

    @property
    def size(self) -> int:
        """Current number of members on the team."""
        return len(self.member_ids)

    @property
    def utilization(self) -> float:
        """Current utilization ratio (size / capacity)."""
        if self.capacity <= 0:
            return 0.0
        return self.size / self.capacity

    def is_over_capacity(self) -> bool:
        """Return True if the team is above its utilization target."""
        return self.utilization > self.utilization_target

    def add_member(self, member_id: str) -> bool:
        """Add a member to the team.  Returns False if already present."""
        if member_id in self.member_ids:
            return False
        self.member_ids.append(member_id)
        return True

    def remove_member(self, member_id: str) -> bool:
        """Remove a member from the team.  Returns False if not found."""
        if member_id not in self.member_ids:
            return False
        self.member_ids.remove(member_id)
        return True

    def record_metric(self, metric: str, value: float) -> None:
        """Record or update a performance metric value."""
        self.performance_metrics[metric] = value

    def increment_metric(self, metric: str, delta: float = 1.0) -> None:
        """Increment a performance metric by *delta*."""
        self.performance_metrics[metric] = (
            self.performance_metrics.get(metric, 0.0) + delta
        )


# =====================================================================
# RBAC — Role-based access control matrix
# =====================================================================


class Permission(str, Enum):
    """Standard permission identifiers used in the RBAC matrix."""

    VIEW_DASHBOARD = "view_dashboard"
    EDIT_TEAM = "edit_team"
    MANAGE_BUDGET = "manage_budget"
    ASSIGN_TASKS = "assign_tasks"
    REVIEW_WORK = "review_work"
    APPROVE_SPEND = "approve_spend"
    HIRE_FIRE = "hire_fire"
    ACCESS_SENSITIVE = "access_sensitive"
    ADMINISTER_ORG = "administer_org"


# Default RBAC matrix: which roles have which permissions.
DEFAULT_RBAC_MATRIX: Dict[OrgRole, Set[Permission]] = {
    OrgRole.INDIVIDUAL_CONTRIBUTOR: {
        Permission.VIEW_DASHBOARD,
        Permission.ASSIGN_TASKS,
    },
    OrgRole.LEAD: {
        Permission.VIEW_DASHBOARD,
        Permission.ASSIGN_TASKS,
        Permission.REVIEW_WORK,
        Permission.EDIT_TEAM,
    },
    OrgRole.MANAGER: {
        Permission.VIEW_DASHBOARD,
        Permission.EDIT_TEAM,
        Permission.ASSIGN_TASKS,
        Permission.REVIEW_WORK,
        Permission.APPROVE_SPEND,
        Permission.MANAGE_BUDGET,
        Permission.HIRE_FIRE,
    },
    OrgRole.DIRECTOR: {
        Permission.VIEW_DASHBOARD,
        Permission.EDIT_TEAM,
        Permission.MANAGE_BUDGET,
        Permission.ASSIGN_TASKS,
        Permission.REVIEW_WORK,
        Permission.APPROVE_SPEND,
        Permission.HIRE_FIRE,
        Permission.ACCESS_SENSITIVE,
        Permission.ADMINISTER_ORG,
    },
}


@dataclass
class RBACMatrix:
    """Role-based access control matrix.

    Maps each ``OrgRole`` to the set of ``Permission`` values it holds.
    Supports role inheritance (senior roles get all junior permissions
    plus their own) when configured.
    """

    _matrix: Dict[OrgRole, Set[Permission]] = field(default_factory=dict)
    inherit_junior: bool = True

    def __post_init__(self) -> None:
        if not self._matrix:
            # Start from defaults but allow override
            for role, perms in DEFAULT_RBAC_MATRIX.items():
                self._matrix[role] = set(perms)

    def has_permission(self, role: OrgRole, permission: Permission) -> bool:
        """Check whether *role* holds *permission*.

        When ``inherit_junior`` is True (default), a senior role
        automatically inherits all permissions of every more-junior role.
        """
        effective = self.effective_permissions(role)
        return permission in effective

    def effective_permissions(self, role: OrgRole) -> Set[Permission]:
        """Return the full set of permissions for *role*.

        Includes inherited permissions when inheritance is enabled.
        """
        perms = set(self._matrix.get(role, set()))
        if self.inherit_junior:
            for junior in OrgRole.hierarchy():
                if junior.is_junior_to(role):
                    perms.update(self._matrix.get(junior, set()))
        return perms

    def grant(self, role: OrgRole, permission: Permission) -> None:
        """Grant a permission to a role."""
        if role not in self._matrix:
            self._matrix[role] = set()
        self._matrix[role].add(permission)

    def revoke(self, role: OrgRole, permission: Permission) -> None:
        """Revoke a permission from a role."""
        if role in self._matrix:
            self._matrix[role].discard(permission)

    def can_member_do(
        self,
        member: Any,
        permission: Permission,
    ) -> bool:
        """Convenience: check whether a member has a permission."""
        return self.has_permission(member.role, permission)


# =====================================================================
# Organization — top-level container / engine
# =====================================================================


class Organization:
    """AI Organization — a first-class object (MASTER-SPEC §61, §161).

    Combines the Phase-12 engine (goals / budget / hire / delegate / KPI /
    audit) with org-chart structure (OrgMember graph, teams, departments,
    RBAC).
    """

    def __init__(self, name: str, budget: float = 0.0,
                 factory: Optional[AgentFactory] = None) -> None:
        self.name = name
        self.factory = factory or AgentFactory()
        self.budget = Budget(total=budget)
        self.goals: Dict[str, Goal] = {}
        self.members: Dict[str, Any] = {}
        self.departments: Dict[str, Department] = {}
        self.teams: Dict[str, Team] = {}
        self.policies: List[str] = []
        self.memory: Dict[str, Any] = {}
        self.audit_log: List[Dict[str, Any]] = []
        self.rbac: RBACMatrix = RBACMatrix()
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
        self.departments[dep_id] = Department(dept_id=dep_id, name=name)
        self._audit("create_department", department_id=dep_id, name=name)
        return dep_id

    def create_team(self, name: str, goal: Optional[str] = None) -> str:
        team_id = f"team_{uuid.uuid4().hex[:8]}"
        self.teams[team_id] = Team(team_id=team_id, name=name, goal=goal)
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
        submitted = sum(getattr(m, "tasks_submitted", 0) for m in self.members.values())
        completed = sum(getattr(m, "tasks_completed", 0) for m in self.members.values())
        failed = sum(getattr(m, "tasks_failed", 0) for m in self.members.values())
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
            if (
                isinstance(member, Member)
                and member.role == role
                and member.status == MemberStatus.ACTIVE
            ):
                return member
        return None

    # ------------------------------------------------------------------
    # Org-chart: member management
    # ------------------------------------------------------------------

    def add_member(self, member: OrgMember) -> None:
        """Add or update an org-chart member."""
        self.members[member.member_id] = member

        # Sync team membership
        if member.team_id and member.team_id in self.teams:
            team = self.teams[member.team_id]
            if member.member_id not in team.member_ids:
                team.add_member(member.member_id)

    def remove_member(self, member_id: str) -> bool:
        """Remove a member.  Returns False if not found."""
        if member_id not in self.members:
            return False
        member = self.members[member_id]
        # Remove from team
        team_id = getattr(member, "team_id", None)
        if team_id and team_id in self.teams:
            self.teams[team_id].remove_member(member_id)
        # Reassign direct reports' manager to None (orphan them)
        for m in self.members.values():
            if getattr(m, "manager_id", None) == member_id:
                m.manager_id = None
        del self.members[member_id]
        return True

    def get_member(self, member_id: str) -> Optional[Any]:
        """Look up a member by ID."""
        return self.members.get(member_id)

    # ------------------------------------------------------------------
    # Org-chart: team management
    # ------------------------------------------------------------------

    def add_team(self, team: Team) -> None:
        """Add or update a team."""
        self.teams[team.team_id] = team
        if team.department_id and team.department_id in self.departments:
            dept = self.departments[team.department_id]
            if team.team_id not in dept.team_ids:
                dept.add_team(team.team_id)

    def get_team(self, team_id: str) -> Optional[Team]:
        """Look up a team by ID."""
        return self.teams.get(team_id)

    def team_members(self, team_id: str) -> List[Any]:
        """Return all members of a team."""
        team = self.teams.get(team_id)
        if not team:
            return []
        return [
            self.members[mid]
            for mid in team.member_ids
            if mid in self.members
        ]

    # ------------------------------------------------------------------
    # Org-chart: department management
    # ------------------------------------------------------------------

    def add_department(self, dept: Department) -> None:
        """Add or update a department."""
        self.departments[dept.dept_id] = dept

    def get_department(self, dept_id: str) -> Optional[Department]:
        """Look up a department by ID."""
        return self.departments.get(dept_id)

    def department_teams(self, dept_id: str) -> List[Team]:
        """Return all teams in a department."""
        dept = self.departments.get(dept_id)
        if not dept:
            return []
        return [self.teams[tid] for tid in dept.team_ids if tid in self.teams]

    def department_members(self, dept_id: str) -> List[Any]:
        """Return all members across all teams in a department."""
        result: List[Any] = []
        for team in self.department_teams(dept_id):
            result.extend(self.team_members(team.team_id))
        return result

    # ------------------------------------------------------------------
    # Org-chart: reporting-line traversal
    # ------------------------------------------------------------------

    def find_manager(self, member_id: str) -> Optional[Any]:
        """Return the direct manager of a member, or None."""
        member = self.members.get(member_id)
        if not member:
            return None
        manager_id = getattr(member, "manager_id", None)
        if not manager_id:
            return None
        return self.members.get(manager_id)

    def find_reports(self, manager_id: str) -> List[Any]:
        """Return all direct reports of a manager."""
        return [
            m for m in self.members.values()
            if getattr(m, "manager_id", None) == manager_id
            and getattr(m, "is_active", True)
        ]

    def find_all_subordinates(self, member_id: str) -> List[Any]:
        """Return every member who reports (directly or indirectly)
        to the given member.

        Uses BFS to traverse the full org chart downward.
        """
        if member_id not in self.members:
            return []

        result: List[Any] = []
        visited: Set[str] = set()
        queue: List[str] = [member_id]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for report in self.find_reports(current):
                report_id = getattr(report, "member_id", None) or report.id
                if report_id not in visited:
                    result.append(report)
                    queue.append(report_id)

        return result

    def find_management_chain(self, member_id: str) -> List[Any]:
        """Return the chain of managers from the member upward.

        First element is the direct manager; last is the top of the org.
        """
        chain: List[Any] = []
        current_id = member_id
        seen: Set[str] = set()

        while True:
            manager = self.find_manager(current_id)
            if manager is None:
                break
            manager_key = getattr(manager, "member_id", None) or manager.id
            if manager_key in seen:
                break
            chain.append(manager)
            seen.add(manager_key)
            current_id = manager_key

        return chain

    # ------------------------------------------------------------------
    # Org-chart: capacity & performance
    # ------------------------------------------------------------------

    def total_members(self) -> int:
        """Total number of active members in the organization."""
        return sum(
            1 for m in self.members.values() if getattr(m, "is_active", True)
        )

    def total_teams(self) -> int:
        """Total number of teams."""
        return len(self.teams)

    def total_departments(self) -> int:
        """Total number of departments."""
        return len(self.departments)

    def over_capacity_teams(self) -> List[Team]:
        """Return all teams above their utilization target."""
        return [t for t in self.teams.values() if t.is_over_capacity()]

    def team_performance_summary(self) -> Dict[str, Dict[str, float]]:
        """Return a performance summary across all teams."""
        summary: Dict[str, Dict[str, float]] = {}
        for team_id, team in self.teams.items():
            summary[team_id] = {
                "size": float(team.size),
                "capacity": float(team.capacity),
                "utilization": team.utilization,
                **team.performance_metrics,
            }
        return summary

    # ------------------------------------------------------------------
    # RBAC convenience
    # ------------------------------------------------------------------

    def can_member_do(self, member_id: str, permission: Permission) -> bool:
        """Check whether a member has a given permission."""
        member = self.members.get(member_id)
        if not member:
            return False
        return self.rbac.can_member_do(member, permission)
