"""Comprehensive organization tests for LiuHao AI OS.

Covers:
- OrgRole hierarchy and comparison
- Team management (add/remove members, capacity, utilization)
- Department management
- RBAC matrix (permissions, role inheritance, checks)
- Organization structure (find manager, find reports, org chart)
- Performance metrics
"""

import pytest
import uuid
from typing import Any

from src.ai.organization import (
    OrgRole,
    OrgMember,
    Team,
    Department,
    Permission,
    RBACMatrix,
    Organization,
    DEFAULT_RBAC_MATRIX,
)


# ======================================================================
# OrgRole Tests (7 tests)
# ======================================================================

class TestOrgRole:
    """Tests for OrgRole hierarchy and comparison."""

    def test_all_roles_exist(self):
        """Test all expected org roles are defined."""
        roles = list(OrgRole)
        assert OrgRole.INDIVIDUAL_CONTRIBUTOR in roles
        assert OrgRole.LEAD in roles
        assert OrgRole.MANAGER in roles
        assert OrgRole.DIRECTOR in roles
        assert len(roles) == 4

    def test_hierarchy_order(self):
        """Test hierarchy returns roles from junior to senior."""
        hierarchy = OrgRole.hierarchy()
        assert len(hierarchy) == 4
        assert hierarchy[0] == OrgRole.INDIVIDUAL_CONTRIBUTOR
        assert hierarchy[1] == OrgRole.LEAD
        assert hierarchy[2] == OrgRole.MANAGER
        assert hierarchy[3] == OrgRole.DIRECTOR

    def test_is_senior_to(self):
        """Test is_senior_to comparison."""
        assert OrgRole.DIRECTOR.is_senior_to(OrgRole.MANAGER)
        assert OrgRole.MANAGER.is_senior_to(OrgRole.LEAD)
        assert OrgRole.LEAD.is_senior_to(OrgRole.INDIVIDUAL_CONTRIBUTOR)
        assert OrgRole.DIRECTOR.is_senior_to(OrgRole.INDIVIDUAL_CONTRIBUTOR)

    def test_is_senior_to_same_role(self):
        """Test that a role is not senior to itself."""
        assert not OrgRole.MANAGER.is_senior_to(OrgRole.MANAGER)

    def test_is_junior_to(self):
        """Test is_junior_to comparison."""
        assert OrgRole.INDIVIDUAL_CONTRIBUTOR.is_junior_to(OrgRole.LEAD)
        assert OrgRole.LEAD.is_junior_to(OrgRole.MANAGER)
        assert OrgRole.MANAGER.is_junior_to(OrgRole.DIRECTOR)
        assert OrgRole.INDIVIDUAL_CONTRIBUTOR.is_junior_to(OrgRole.DIRECTOR)

    def test_is_junior_to_same_role(self):
        """Test that a role is not junior to itself."""
        assert not OrgRole.LEAD.is_junior_to(OrgRole.LEAD)

    def test_role_values(self):
        """Test role string values."""
        assert OrgRole.INDIVIDUAL_CONTRIBUTOR.value == "individual_contributor"
        assert OrgRole.LEAD.value == "lead"
        assert OrgRole.MANAGER.value == "manager"
        assert OrgRole.DIRECTOR.value == "director"


# ======================================================================
# OrgMember Tests (5 tests)
# ======================================================================

class TestOrgMember:
    """Tests for OrgMember."""

    def test_member_creation(self):
        """Test creating an org member."""
        member = OrgMember(
            member_id="m1",
            name="Alice",
            role=OrgRole.ENGINEER if hasattr(OrgRole, 'ENGINEER') else OrgRole.INDIVIDUAL_CONTRIBUTOR,
        )
        assert member.member_id == "m1"
        assert member.name == "Alice"
        assert member.is_active is True

    def test_member_auto_generates_id(self):
        """Test member auto-generates ID if not provided."""
        member = OrgMember(
            member_id="",
            name="Bob",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
        )
        assert member.member_id != ""
        assert member.member_id.startswith("mem_")

    def test_member_with_skills(self):
        """Test member with skills."""
        member = OrgMember(
            member_id="m2",
            name="Charlie",
            role=OrgRole.LEAD,
            skills=["python", "ml", "leadership"],
        )
        assert len(member.skills) == 3
        assert "python" in member.skills

    def test_member_with_metadata(self):
        """Test member with metadata."""
        member = OrgMember(
            member_id="m3",
            name="Diana",
            role=OrgRole.MANAGER,
            metadata={"location": "NYC", "hire_date": "2024-01-01"},
        )
        assert member.metadata["location"] == "NYC"
        assert member.metadata["hire_date"] == "2024-01-01"

    def test_member_manager_and_team(self):
        """Test member with manager and team assignments."""
        member = OrgMember(
            member_id="m4",
            name="Eve",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
            team_id="team_alpha",
        )
        assert member.manager_id == "mgr1"
        assert member.team_id == "team_alpha"


# ======================================================================
# Team Tests (10 tests)
# ======================================================================

class TestTeam:
    """Tests for Team management."""

    def test_team_creation(self):
        """Test creating a team."""
        team = Team(
            team_id="t1",
            name="Platform Team",
            description="Builds the platform",
            capacity=12,
        )
        assert team.team_id == "t1"
        assert team.name == "Platform Team"
        assert team.capacity == 12
        assert team.size == 0

    def test_team_auto_generates_id(self):
        """Test team auto-generates ID if not provided."""
        team = Team(team_id="", name="New Team")
        assert team.team_id.startswith("team_")

    def test_add_member(self):
        """Test adding a member to a team."""
        team = Team(team_id="t1", name="Test")
        result = team.add_member("m1")
        assert result is True
        assert team.size == 1
        assert "m1" in team.member_ids

    def test_add_duplicate_member(self):
        """Test adding a duplicate member returns False."""
        team = Team(team_id="t1", name="Test")
        team.add_member("m1")
        result = team.add_member("m1")
        assert result is False
        assert team.size == 1  # still only one

    def test_remove_member(self):
        """Test removing a member from a team."""
        team = Team(team_id="t1", name="Test")
        team.add_member("m1")
        team.add_member("m2")
        result = team.remove_member("m1")
        assert result is True
        assert team.size == 1
        assert "m1" not in team.member_ids

    def test_remove_nonexistent_member(self):
        """Test removing a nonexistent member returns False."""
        team = Team(team_id="t1", name="Test")
        result = team.remove_member("nonexistent")
        assert result is False

    def test_utilization_calculation(self):
        """Test team utilization calculation."""
        team = Team(team_id="t1", name="Test", capacity=10)
        assert team.utilization == 0.0

        team.add_member("m1")
        team.add_member("m2")
        assert team.utilization == 0.2  # 2/10

    def test_is_over_capacity(self):
        """Test is_over_capacity check."""
        team = Team(
            team_id="t1",
            name="Test",
            capacity=5,
            utilization_target=0.8,
        )
        # 3/5 = 0.6 < 0.8 -> not over
        for i in range(3):
            team.add_member(f"m{i}")
        assert team.is_over_capacity() is False

        # 5/5 = 1.0 > 0.8 -> over
        for i in range(3, 5):
            team.add_member(f"m{i}")
        assert team.is_over_capacity() is True

    def test_record_metric(self):
        """Test recording a performance metric."""
        team = Team(team_id="t1", name="Test")
        team.record_metric("velocity", 42.0)
        assert team.performance_metrics["velocity"] == 42.0

    def test_increment_metric(self):
        """Test incrementing a performance metric."""
        team = Team(team_id="t1", name="Test")
        team.record_metric("tasks_completed", 10.0)
        team.increment_metric("tasks_completed", 5.0)
        assert team.performance_metrics["tasks_completed"] == 15.0

        # Increment a new metric
        team.increment_metric("new_metric", 3.0)
        assert team.performance_metrics["new_metric"] == 3.0


# ======================================================================
# Department Tests (5 tests)
# ======================================================================

class TestDepartment:
    """Tests for Department management."""

    def test_department_creation(self):
        """Test creating a department."""
        dept = Department(dept_id="d1", name="Engineering")
        assert dept.dept_id == "d1"
        assert dept.name == "Engineering"
        assert len(dept.team_ids) == 0

    def test_department_auto_generates_id(self):
        """Test department auto-generates ID."""
        dept = Department(dept_id="", name="HR")
        assert dept.dept_id.startswith("dept_")

    def test_add_team(self):
        """Test adding a team to a department."""
        dept = Department(dept_id="d1", name="Engineering")
        result = dept.add_team("t1")
        assert result is True
        assert "t1" in dept.team_ids
        assert len(dept.team_ids) == 1

    def test_add_duplicate_team(self):
        """Test adding duplicate team returns False."""
        dept = Department(dept_id="d1", name="Eng")
        dept.add_team("t1")
        result = dept.add_team("t1")
        assert result is False
        assert len(dept.team_ids) == 1

    def test_remove_team(self):
        """Test removing a team from department."""
        dept = Department(dept_id="d1", name="Eng")
        dept.add_team("t1")
        dept.add_team("t2")
        result = dept.remove_team("t1")
        assert result is True
        assert "t1" not in dept.team_ids
        assert len(dept.team_ids) == 1


# ======================================================================
# RBACMatrix Tests (10 tests)
# ======================================================================

class TestRBACMatrix:
    """Tests for RBAC permission matrix."""

    def test_default_permissions_exist(self):
        """Test default RBAC matrix has all roles."""
        rbac = RBACMatrix()
        for role in OrgRole:
            perms = rbac.effective_permissions(role)
            assert isinstance(perms, set)
            assert len(perms) >= 1  # each role has at least some perms

    def test_ic_has_view_dashboard(self):
        """Test individual contributor has view_dashboard permission."""
        rbac = RBACMatrix()
        assert rbac.has_permission(
            OrgRole.INDIVIDUAL_CONTRIBUTOR,
            Permission.VIEW_DASHBOARD,
        )

    def test_director_has_admin(self):
        """Test director has administer_org permission."""
        rbac = RBACMatrix()
        assert rbac.has_permission(
            OrgRole.DIRECTOR,
            Permission.ADMINISTER_ORG,
        )

    def test_ic_does_not_have_hire_fire(self):
        """Test IC does NOT have hire_fire permission."""
        rbac = RBACMatrix()
        assert not rbac.has_permission(
            OrgRole.INDIVIDUAL_CONTRIBUTOR,
            Permission.HIRE_FIRE,
        )

    def test_inheritance_manager_has_ic_perms(self):
        """Test role inheritance: manager has IC's permissions."""
        rbac = RBACMatrix(inherit_junior=True)
        ic_perms = rbac.effective_permissions(OrgRole.INDIVIDUAL_CONTRIBUTOR)
        mgr_perms = rbac.effective_permissions(OrgRole.MANAGER)
        # Manager should have all IC perms plus more
        assert ic_perms.issubset(mgr_perms)
        assert len(mgr_perms) > len(ic_perms)

    def test_no_inheritance(self):
        """Test RBAC without inheritance."""
        rbac = RBACMatrix(inherit_junior=False)
        ic_perms = rbac.effective_permissions(OrgRole.INDIVIDUAL_CONTRIBUTOR)
        mgr_perms = rbac.effective_permissions(OrgRole.MANAGER)
        # Without inheritance, they're separate
        # (Manager may still have some same perms, but not inherited)
        assert isinstance(mgr_perms, set)
        assert isinstance(ic_perms, set)

    def test_grant_permission(self):
        """Test granting a new permission."""
        rbac = RBACMatrix(inherit_junior=False)
        assert not rbac.has_permission(
            OrgRole.INDIVIDUAL_CONTRIBUTOR,
            Permission.REVIEW_WORK,
        )
        rbac.grant(OrgRole.INDIVIDUAL_CONTRIBUTOR, Permission.REVIEW_WORK)
        assert rbac.has_permission(
            OrgRole.INDIVIDUAL_CONTRIBUTOR,
            Permission.REVIEW_WORK,
        )

    def test_revoke_permission(self):
        """Test revoking a permission."""
        rbac = RBACMatrix(inherit_junior=False)
        assert rbac.has_permission(
            OrgRole.DIRECTOR,
            Permission.ADMINISTER_ORG,
        )
        rbac.revoke(OrgRole.DIRECTOR, Permission.ADMINISTER_ORG)
        assert not rbac.has_permission(
            OrgRole.DIRECTOR,
            Permission.ADMINISTER_ORG,
        )

    def test_can_member_do(self):
        """Test can_member_do convenience method."""
        rbac = RBACMatrix()
        member = OrgMember(
            member_id="m1",
            name="Test",
            role=OrgRole.MANAGER,
        )
        assert rbac.can_member_do(member, Permission.APPROVE_SPEND)

    def test_all_permissions_exist(self):
        """Test all expected permissions are defined."""
        perms = list(Permission)
        expected = [
            "VIEW_DASHBOARD", "EDIT_TEAM", "MANAGE_BUDGET",
            "ASSIGN_TASKS", "REVIEW_WORK", "APPROVE_SPEND",
            "HIRE_FIRE", "ACCESS_SENSITIVE", "ADMINISTER_ORG",
        ]
        for p in expected:
            assert hasattr(Permission, p), f"Missing permission: {p}"
        assert len(perms) == 9


# ======================================================================
# Organization Structure Tests (10 tests)
# ======================================================================

class TestOrganization:
    """Tests for Organization structure and traversal."""

    def test_organization_creation(self):
        """Test creating an organization."""
        org = Organization(name="Acme Corp")
        assert org.name == "Acme Corp"
        assert len(org.members) == 0
        assert len(org.teams) == 0
        assert len(org.departments) == 0

    def test_add_and_get_member(self):
        """Test adding and retrieving a member."""
        org = Organization(name="TestOrg")
        member = OrgMember(
            member_id="m1",
            name="Alice",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
        )
        org.add_member(member)
        assert org.total_members() == 1

        found = org.get_member("m1")
        assert found is not None
        assert found.name == "Alice"

    def test_remove_member(self):
        """Test removing a member."""
        org = Organization(name="TestOrg")
        member = OrgMember(
            member_id="m1",
            name="Alice",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
        )
        org.add_member(member)
        assert org.total_members() == 1

        result = org.remove_member("m1")
        assert result is True
        assert org.total_members() == 0

    def test_add_and_get_team(self):
        """Test adding and retrieving a team."""
        org = Organization(name="TestOrg")
        team = Team(team_id="t1", name="Dev Team")
        org.add_team(team)

        found = org.get_team("t1")
        assert found is not None
        assert found.name == "Dev Team"

    def test_team_members(self):
        """Test getting members of a team."""
        org = Organization(name="TestOrg")
        team = Team(team_id="t1", name="Team A")
        org.add_team(team)

        m1 = OrgMember(
            member_id="m1", name="Alice",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR, team_id="t1",
        )
        m2 = OrgMember(
            member_id="m2", name="Bob",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR, team_id="t1",
        )
        org.add_member(m1)
        org.add_member(m2)

        members = org.team_members("t1")
        assert len(members) == 2
        names = [m.name for m in members]
        assert "Alice" in names
        assert "Bob" in names

    def test_add_and_get_department(self):
        """Test adding and retrieving a department."""
        org = Organization(name="TestOrg")
        dept = Department(dept_id="d1", name="Engineering")
        org.add_department(dept)

        found = org.get_department("d1")
        assert found is not None
        assert found.name == "Engineering"

    def test_department_teams(self):
        """Test getting teams in a department."""
        org = Organization(name="TestOrg")
        dept = Department(dept_id="d1", name="Eng")
        org.add_department(dept)

        team = Team(team_id="t1", name="Backend", department_id="d1")
        org.add_team(team)

        teams = org.department_teams("d1")
        assert len(teams) == 1
        assert teams[0].name == "Backend"

    def test_find_manager(self):
        """Test finding a member's manager."""
        org = Organization(name="TestOrg")
        mgr = OrgMember(
            member_id="mgr1", name="Manager",
            role=OrgRole.MANAGER,
        )
        ic = OrgMember(
            member_id="ic1", name="IC",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        org.add_member(mgr)
        org.add_member(ic)

        manager = org.find_manager("ic1")
        assert manager is not None
        assert manager.name == "Manager"

    def test_find_reports(self):
        """Test finding a manager's direct reports."""
        org = Organization(name="TestOrg")
        mgr = OrgMember(
            member_id="mgr1", name="Manager",
            role=OrgRole.MANAGER,
        )
        ic1 = OrgMember(
            member_id="ic1", name="Alice",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        ic2 = OrgMember(
            member_id="ic2", name="Bob",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        org.add_member(mgr)
        org.add_member(ic1)
        org.add_member(ic2)

        reports = org.find_reports("mgr1")
        assert len(reports) == 2

    def test_find_all_subordinates(self):
        """Test finding all subordinates (direct + indirect)."""
        org = Organization(name="TestOrg")
        director = OrgMember(
            member_id="dir1", name="Director",
            role=OrgRole.DIRECTOR,
        )
        manager = OrgMember(
            member_id="mgr1", name="Manager",
            role=OrgRole.MANAGER,
            manager_id="dir1",
        )
        ic1 = OrgMember(
            member_id="ic1", name="IC1",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        ic2 = OrgMember(
            member_id="ic2", name="IC2",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        org.add_member(director)
        org.add_member(manager)
        org.add_member(ic1)
        org.add_member(ic2)

        subs = org.find_all_subordinates("dir1")
        assert len(subs) == 3  # manager + 2 ICs

    def test_find_management_chain(self):
        """Test finding the management chain upward."""
        org = Organization(name="TestOrg")
        director = OrgMember(
            member_id="dir1", name="Director",
            role=OrgRole.DIRECTOR,
        )
        manager = OrgMember(
            member_id="mgr1", name="Manager",
            role=OrgRole.MANAGER,
            manager_id="dir1",
        )
        ic = OrgMember(
            member_id="ic1", name="IC",
            role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
            manager_id="mgr1",
        )
        org.add_member(director)
        org.add_member(manager)
        org.add_member(ic)

        chain = org.find_management_chain("ic1")
        assert len(chain) == 2
        assert chain[0].name == "Manager"
        assert chain[1].name == "Director"
