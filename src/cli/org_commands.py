"""Organization CLI commands for LiuHao AI OS.

Provides command-line interface for organization management:
- org tree, org teams, org members
- org add-member, org remove-member
- org roles, org capacity
- org performance
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class OrgRole(str, Enum):
    """Organizational role levels."""
    INDIVIDUAL_CONTRIBUTOR = "individual_contributor"
    LEAD = "lead"
    MANAGER = "manager"
    DIRECTOR = "director"
    VP = "vp"
    CTO = "cto"


@dataclass
class Team:
    """A team within the organization."""
    team_id: str
    name: str
    parent_id: Optional[str] = None
    description: str = ""
    manager_id: Optional[str] = None
    member_ids: List[str] = field(default_factory=list)


@dataclass
class Member:
    """An organization member (human or agent)."""
    member_id: str
    name: str
    role: OrgRole
    email: str = ""
    team_id: Optional[str] = None
    is_agent: bool = False
    capacity_pct: float = 100.0
    joined_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class OrgManager:
    """In-memory organization manager for CLI operations.

    Provides organizational hierarchy, team management, member management,
    capacity tracking, and performance metrics.
    """

    def __init__(self) -> None:
        self._teams: Dict[str, Team] = {}
        self._members: Dict[str, Member] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample organization data."""
        # Teams
        teams_data = [
            ("team-exec", "Executive Team", None, "Top-level leadership"),
            ("team-eng", "Engineering", "team-exec", "Engineering department"),
            ("team-research", "Research", "team-eng", "AI research team"),
            ("team-platform", "Platform", "team-eng", "Platform engineering"),
            ("team-product", "Product", "team-exec", "Product department"),
        ]
        for tid, name, parent, desc in teams_data:
            self._teams[tid] = Team(
                team_id=tid, name=name, parent_id=parent, description=desc
            )

        # Members
        members_data = [
            ("m-001", "Alice Chen", OrgRole.CTO, "team-exec", "alice@example.com", False),
            ("m-002", "Bob Wang", OrgRole.DIRECTOR, "team-eng", "bob@example.com", False),
            ("m-003", "Charlie Li", OrgRole.MANAGER, "team-research", "charlie@example.com", False),
            ("m-004", "Diana Zhang", OrgRole.LEAD, "team-research", "diana@example.com", False),
            ("m-005", "Eve Liu", OrgRole.INDIVIDUAL_CONTRIBUTOR, "team-research", "eve@example.com", False),
            ("m-006", "agent-research-1", OrgRole.INDIVIDUAL_CONTRIBUTOR, "team-research", "", True),
            ("m-007", "agent-research-2", OrgRole.INDIVIDUAL_CONTRIBUTOR, "team-research", "", True),
            ("m-008", "Frank Wu", OrgRole.MANAGER, "team-platform", "frank@example.com", False),
            ("m-009", "Grace Sun", OrgRole.LEAD, "team-platform", "grace@example.com", False),
            ("m-010", "Henry Lin", OrgRole.DIRECTOR, "team-product", "henry@example.com", False),
        ]
        for mid, name, role, team_id, email, is_agent in members_data:
            member = Member(
                member_id=mid,
                name=name,
                role=role,
                email=email,
                team_id=team_id,
                is_agent=is_agent,
                capacity_pct=80.0 + hash(mid) % 20,
            )
            self._members[mid] = member
            if team_id:
                self._teams[team_id].member_ids.append(mid)

        # Set managers
        self._teams["team-exec"].manager_id = "m-001"
        self._teams["team-eng"].manager_id = "m-002"
        self._teams["team-research"].manager_id = "m-003"
        self._teams["team-platform"].manager_id = "m-008"
        self._teams["team-product"].manager_id = "m-010"

    def get_org_tree(self, root: Optional[str] = None) -> Dict[str, Any]:
        """Get the organizational tree structure.

        Args:
            root: Optional root team ID (default: top-level teams).

        Returns:
            Dict with org tree structure.
        """
        def build_team_tree(team_id: str) -> Dict[str, Any]:
            team = self._teams[team_id]
            children = [
                build_team_tree(t.team_id)
                for t in self._teams.values()
                if t.parent_id == team_id
            ]
            manager = self._members.get(team.manager_id) if team.manager_id else None
            return {
                "team_id": team.team_id,
                "name": team.name,
                "description": team.description,
                "manager": {
                    "id": manager.member_id,
                    "name": manager.name,
                    "role": manager.role.value,
                } if manager else None,
                "member_count": len(team.member_ids),
                "children": children,
            }

        roots = [t for t in self._teams.values() if t.parent_id == root]
        tree = [build_team_tree(t.team_id) for t in roots]
        return {
            "root": root or "top",
            "teams": tree,
            "total_teams": len(self._teams),
            "total_members": len(self._members),
        }

    def list_teams(self) -> Dict[str, Any]:
        """List all teams.

        Returns:
            Dict with team list.
        """
        return {
            "teams": [
                {
                    "team_id": t.team_id,
                    "name": t.name,
                    "parent_id": t.parent_id,
                    "description": t.description,
                    "manager_id": t.manager_id,
                    "member_count": len(t.member_ids),
                }
                for t in sorted(self._teams.values(), key=lambda t: t.name)
            ],
            "total": len(self._teams),
        }

    def list_members(
        self,
        team_id: Optional[str] = None,
        role: Optional[str] = None,
        is_agent: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """List organization members.

        Args:
            team_id: Filter by team.
            role: Filter by role.
            is_agent: Filter by agent/human.

        Returns:
            Dict with member list.
        """
        members = list(self._members.values())
        if team_id:
            members = [m for m in members if m.team_id == team_id]
        if role:
            members = [m for m in members if m.role.value == role]
        if is_agent is not None:
            members = [m for m in members if m.is_agent == is_agent]
        return {
            "members": [
                {
                    "member_id": m.member_id,
                    "name": m.name,
                    "role": m.role.value,
                    "email": m.email,
                    "team_id": m.team_id,
                    "is_agent": m.is_agent,
                    "capacity_pct": m.capacity_pct,
                }
                for m in sorted(members, key=lambda m: m.name)
            ],
            "total": len(members),
        }

    def add_member(
        self,
        name: str,
        role: str,
        team_id: Optional[str] = None,
        email: str = "",
        is_agent: bool = False,
    ) -> Dict[str, Any]:
        """Add a member to the organization.

        Args:
            name: Member name.
            role: Member role.
            team_id: Optional team assignment.
            email: Optional email.
            is_agent: Whether this is an AI agent.

        Returns:
            Dict with new member details.

        Raises:
            ValueError: If team not found.
        """
        if team_id and team_id not in self._teams:
            raise ValueError(f"Team not found: {team_id}")
        member_id = f"m-{uuid.uuid4().hex[:6]}"
        member = Member(
            member_id=member_id,
            name=name,
            role=OrgRole(role),
            email=email,
            team_id=team_id,
            is_agent=is_agent,
        )
        self._members[member_id] = member
        if team_id:
            self._teams[team_id].member_ids.append(member_id)
        return {
            "member_id": member.member_id,
            "name": member.name,
            "role": member.role.value,
            "email": member.email,
            "team_id": member.team_id,
            "is_agent": member.is_agent,
            "joined_at": member.joined_at,
        }

    def remove_member(self, member_id: str) -> Dict[str, Any]:
        """Remove a member from the organization.

        Args:
            member_id: Member identifier.

        Returns:
            Dict confirming removal.

        Raises:
            ValueError: If member not found.
        """
        member = self._members.get(member_id)
        if not member:
            raise ValueError(f"Member not found: {member_id}")
        if member.team_id and member_id in self._teams[member.team_id].member_ids:
            self._teams[member.team_id].member_ids.remove(member_id)
        del self._members[member_id]
        return {"status": "removed", "member_id": member_id}

    def list_roles(self) -> Dict[str, Any]:
        """List all organizational roles.

        Returns:
            Dict with role hierarchy.
        """
        return {
            "roles": [
                {
                    "name": r.value,
                    "level": i,
                    "member_count": sum(1 for m in self._members.values() if m.role == r),
                }
                for i, r in enumerate(OrgRole)
            ],
        }

    def get_capacity(self, team_id: Optional[str] = None) -> Dict[str, Any]:
        """Get team/member capacity information.

        Args:
            team_id: Optional team filter.

        Returns:
            Dict with capacity data.
        """
        members = list(self._members.values())
        if team_id:
            members = [m for m in members if m.team_id == team_id]
        total_capacity = sum(m.capacity_pct for m in members)
        return {
            "scope": team_id or "organization",
            "total_members": len(members),
            "total_capacity_pct": round(total_capacity, 2),
            "avg_capacity_pct": round(total_capacity / len(members), 2) if members else 0,
            "agent_count": sum(1 for m in members if m.is_agent),
            "human_count": sum(1 for m in members if not m.is_agent),
            "by_team": {
                tid: {
                    "members": len(t.member_ids),
                    "avg_capacity": round(
                        sum(self._members[mid].capacity_pct for mid in t.member_ids) / len(t.member_ids), 2
                    ) if t.member_ids else 0,
                }
                for tid, t in self._teams.items()
                if not team_id or tid == team_id
            },
        }

    def get_performance(self, team_id: Optional[str] = None) -> Dict[str, Any]:
        """Get team/member performance metrics.

        Args:
            team_id: Optional team filter.

        Returns:
            Dict with performance data.
        """
        return {
            "scope": team_id or "organization",
            "period": "last_30_days",
            "tasks_completed": 142,
            "avg_turnaround_hours": 4.5,
            "quality_score": 0.91,
            "collaboration_index": 0.85,
            "top_performers": [
                {"member_id": mid, "name": self._members[mid].name, "score": 0.95 + i * 0.01}
                for i, mid in enumerate(list(self._members.keys())[:3])
                if mid in self._members
            ],
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_org_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register org subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_org = subparsers.add_parser("org", help="Organization management")
    org_sub = p_org.add_subparsers(dest="org_command", required=True)

    # org tree
    p_tree = org_sub.add_parser("tree", help="Show organization tree")
    p_tree.add_argument("--root", help="Root team ID (default: top level)")

    # org teams
    org_sub.add_parser("teams", help="List all teams")

    # org members
    p_members = org_sub.add_parser("members", help="List organization members")
    p_members.add_argument("--team", help="Filter by team ID")
    p_members.add_argument("--role", help="Filter by role")
    p_members.add_argument("--agent", action="store_true", help="Show only agents")
    p_members.add_argument("--human", action="store_true", help="Show only humans")

    # org add-member
    p_add = org_sub.add_parser("add-member", help="Add a member")
    p_add.add_argument("--name", required=True, help="Member name")
    p_add.add_argument(
        "--role",
        required=True,
        choices=[r.value for r in OrgRole],
        help="Member role",
    )
    p_add.add_argument("--team", help="Team ID")
    p_add.add_argument("--email", default="", help="Email address")
    p_add.add_argument("--agent", action="store_true", help="This is an AI agent")

    # org remove-member
    p_remove = org_sub.add_parser("remove-member", help="Remove a member")
    p_remove.add_argument("member_id", help="Member identifier")
    p_remove.add_argument("--force", action="store_true", help="Force removal")

    # org roles
    org_sub.add_parser("roles", help="List organizational roles")

    # org capacity
    p_capacity = org_sub.add_parser("capacity", help="Show capacity information")
    p_capacity.add_argument("--team", help="Filter by team ID")

    # org performance
    p_perf = org_sub.add_parser("performance", help="Show performance metrics")
    p_perf.add_argument("--team", help="Filter by team ID")


# =====================================================================
# Command Handler
# =====================================================================


def handle_org_command(
    args: argparse.Namespace, manager: OrgManager
) -> int:
    """Dispatch an org CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: OrgManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.org_command == "tree":
            result = manager.get_org_tree(root=args.root)
        elif args.org_command == "teams":
            result = manager.list_teams()
        elif args.org_command == "members":
            is_agent = None
            if args.agent:
                is_agent = True
            elif args.human:
                is_agent = False
            result = manager.list_members(
                team_id=args.team, role=args.role, is_agent=is_agent
            )
        elif args.org_command == "add-member":
            result = manager.add_member(
                name=args.name,
                role=args.role,
                team_id=args.team,
                email=args.email,
                is_agent=args.agent,
            )
        elif args.org_command == "remove-member":
            result = manager.remove_member(args.member_id)
        elif args.org_command == "roles":
            result = manager.list_roles()
        elif args.org_command == "capacity":
            result = manager.get_capacity(team_id=args.team)
        elif args.org_command == "performance":
            result = manager.get_performance(team_id=args.team)
        else:
            print(f"Unknown org command: {args.org_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
