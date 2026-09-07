"""AI Organization Engine — MASTER-SPEC Phase 12 tests."""
import uuid

import pytest

from src.ai.organization import (
    Organization,
    MemberStatus,
    GoalStatus,
)
from src.ai.collaboration import Role


class _StubProvider:
    def generate_with_retry(self, prompt, **kwargs):
        return "stub-response"


@pytest.fixture
def provider(monkeypatch):
    from src.ai import agent_factory as af
    stub = _StubProvider()
    monkeypatch.setattr(af, "get_provider", lambda: stub)
    return stub


def _make_org(provider, budget=1000.0):
    return Organization(name=f"org-{uuid.uuid4().hex[:8]}", budget=budget)


class TestNoFakeOrganization:
    def test_owns_ten_real_attributes(self, provider):
        """§161: Members/Roles/Departments/Teams/Goals/Policies/Budget/Memory/KPIs/Audit."""
        org = _make_org(provider)
        dep = org.create_department("engineering")
        org.create_goal("ship v1")
        org.create_team("platform", goal="reliability")
        org.add_policy("default-deny")
        org.hire(Role.RESEARCHER, dep)
        org.remember("vision", "autonomous agents")

        assert org.members  # Members — non-empty
        assert all(isinstance(m.role, Role) for m in org.members.values())  # Roles
        assert org.departments  # Departments
        assert org.teams  # Teams
        assert org.goals  # Goals
        assert org.policies  # Policies
        assert org.budget.total == 1000.0  # Budget
        assert org.recall("vision") == "autonomous agents"  # Memory
        assert org.report()["member_count"] == 1  # KPIs (aggregated in report)
        assert len(org.audit_log) > 0  # Audit


class TestHire:
    def test_hire_creates_real_agent_member(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)
        member = org.members[mid]
        assert member.role == Role.RESEARCHER
        assert member.agent is not None
        assert member.agent.agent_type == "researcher"  # real Agent entity
        assert member.status == MemberStatus.ACTIVE

    def test_hire_updates_department_membership(self, provider):
        org = _make_org(provider)
        dep = org.create_department("engineering")
        mid = org.hire(Role.QA, dep)
        assert mid in org.departments[dep].member_ids


class TestDelegate:
    def test_delegate_executes_real_task_and_updates_kpi(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)

        result = org.delegate("exec-0", Role.RESEARCHER, "investigate X")
        assert result["status"] == "completed"
        assert result["result"] == "stub-response"  # real provider call

        member = org.members[mid]
        assert member.tasks_submitted == 1
        assert member.tasks_completed == 1
        assert member.tasks_failed == 0

    def test_delegate_no_active_member(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        org.hire(Role.RESEARCHER, dep)
        # No analyst hired.
        result = org.delegate("exec-0", Role.ANALYST, "analyze")
        assert result["status"] == "failed"
        assert "no active analyst member" in result["error"]

    def test_delegate_skips_suspended_member(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)
        org.suspend(mid)
        result = org.delegate("exec-0", Role.RESEARCHER, "task")
        assert result["status"] == "failed"  # suspended member is not active


class TestBudget:
    def test_spend_and_overrun_rejection(self, provider):
        org = _make_org(provider, budget=100.0)
        assert org.spend(60.0) is True
        assert org.budget.spent == 60.0
        assert org.budget.remaining() == 40.0
        assert org.spend(40.0) is True  # exactly hits the cap
        assert org.spend(0.01) is False  # overspend rejected
        assert org.budget.spent == 100.0


class TestEvaluate:
    def test_evaluate_returns_real_kpi(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)
        org.delegate("exec-0", Role.RESEARCHER, "t1")
        org.delegate("exec-0", Role.RESEARCHER, "t2")

        kpi = org.evaluate(mid)
        assert kpi["tasks_submitted"] == 2
        assert kpi["tasks_completed"] == 2
        assert kpi["success_rate"] == 1.0


class TestLifecycle:
    def test_promote_suspend_terminate(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)

        assert org.promote(mid, Role.ANALYST) is True
        assert org.members[mid].role == Role.ANALYST

        assert org.suspend(mid) is True
        assert org.members[mid].status == MemberStatus.SUSPENDED

        assert org.terminate(mid) is True
        assert org.members[mid].status == MemberStatus.TERMINATED

    def test_operations_on_unknown_member_return_false(self, provider):
        org = _make_org(provider)
        assert org.promote("ghost", Role.QA) is False
        assert org.suspend("ghost") is False
        assert org.terminate("ghost") is False


class TestAuditAndReport:
    def test_audit_trail_records_every_operation(self, provider):
        org = _make_org(provider)
        dep = org.create_department("research")
        mid = org.hire(Role.RESEARCHER, dep)
        org.delegate("exec-0", Role.RESEARCHER, "t1")
        org.spend(10.0)

        actions = [e["action"] for e in org.audit_log]
        assert "create_organization" in actions
        assert "create_department" in actions
        assert "hire" in actions
        assert "delegate" in actions
        assert "spend" in actions

    def test_report_aggregates_real_numbers(self, provider):
        org = _make_org(provider, budget=500.0)
        dep = org.create_department("research")
        org.hire(Role.RESEARCHER, dep)
        org.hire(Role.RESEARCHER, dep)
        org.delegate("exec-0", Role.RESEARCHER, "t1")
        org.spend(100.0)

        report = org.report()
        assert report["member_count"] == 2
        assert report["department_count"] == 1
        assert report["tasks_submitted"] == 1
        assert report["tasks_completed"] == 1
        assert report["success_rate"] == 1.0
        assert report["budget_spent"] == 100.0
        assert report["budget_remaining"] == 400.0
