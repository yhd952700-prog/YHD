"""Tests for organization, specialized agents, cost tracking, and backup modules.

Covers:
- src.ai.organization (OrgRole, Team, Department, OrgMember, RBACMatrix, Organization)
- src.ai.specialized_agents (CodeAgent, DataAnalystAgent, ResearchAgent)
- src.cost.cost_tracker (CostRecord, Budget, CostTracker, alerts, forecasting)
- src.backup.backup_manager (BackupManager, retention, verification, restore)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.ai.organization import (
    OrgRole,
    OrgMember,
    Team,
    Department,
    Permission,
    RBACMatrix,
    Organization,
)
from src.ai.specialized_agents import (
    CodeAgent,
    CodeLanguage,
    CodeStyle,
    ComplexityLevel,
    DataAnalystAgent,
    DataFormat,
    AnalysisMethod,
    DataInsight,
    ResearchAgent,
    Citation,
    SourceReliability,
    ResearchFinding,
)
from src.cost.cost_tracker import (
    CostCategory,
    CostRecord,
    Budget,
    AlertLevel,
    AggregationPeriod,
    CostTracker,
    CostForecast,
)
from src.backup.backup_manager import (
    BackupType,
    BackupSource,
    BackupStatus,
    RetentionPolicy,
    RetentionPolicyType,
    BackupMetadata,
    BackupManager,
    RestoreStatus,
)


# =====================================================================
# Fixtures
# =====================================================================


class _StubProvider:
    """Minimal provider stub — tests never invoke it."""

    def generate_with_retry(self, prompt, **kwargs):
        return "stub-response"


def _make_agent(name="test-agent", agent_type="general"):
    """Create a minimal Agent for composition tests."""
    from src.ai.employee import Agent
    return Agent(
        id=f"agent-{name}",
        agent_type=agent_type,
        name=name,
        provider=_StubProvider(),
        system_prompt="You are a test agent.",
    )


@pytest.fixture
def sample_org():
    """Build a sample organization with departments, teams, and members."""
    org = Organization(name="Test Org")

    # Department
    eng_dept = Department(dept_id="dept-eng", name="Engineering",
                          description="Product engineering team")
    org.add_department(eng_dept)

    # Teams
    backend_team = Team(
        team_id="team-backend",
        name="Backend Team",
        description="Backend services",
        department_id="dept-eng",
        capacity=5,
        utilization_target=0.8,
    )
    frontend_team = Team(
        team_id="team-frontend",
        name="Frontend Team",
        description="Frontend applications",
        department_id="dept-eng",
        capacity=4,
        utilization_target=0.8,
    )
    org.add_team(backend_team)
    org.add_team(frontend_team)

    # Members
    director = OrgMember(
        member_id="mem-dir",
        name="Director Diaz",
        role=OrgRole.DIRECTOR,
        agent_role="manager",
    )
    org.add_member(director)

    manager = OrgMember(
        member_id="mem-mgr",
        name="Manager Smith",
        role=OrgRole.MANAGER,
        agent_role="manager",
        manager_id="mem-dir",
        team_id="team-backend",
    )
    org.add_member(manager)

    lead = OrgMember(
        member_id="mem-lead",
        name="Lead Jones",
        role=OrgRole.LEAD,
        agent_role="executor",
        manager_id="mem-mgr",
        team_id="team-backend",
    )
    org.add_member(lead)

    ic1 = OrgMember(
        member_id="mem-ic1",
        name="IC One",
        role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
        agent_role="executor",
        manager_id="mem-lead",
        team_id="team-backend",
    )
    ic2 = OrgMember(
        member_id="mem-ic2",
        name="IC Two",
        role=OrgRole.INDIVIDUAL_CONTRIBUTOR,
        agent_role="analyst",
        manager_id="mem-lead",
        team_id="team-frontend",
    )
    org.add_member(ic1)
    org.add_member(ic2)

    return org


# =====================================================================
# Organization tests — OrgRole
# =====================================================================


class TestOrgRole:
    def test_hierarchy_order(self):
        """Roles are ordered from junior to senior."""
        h = OrgRole.hierarchy()
        assert h == [
            OrgRole.INDIVIDUAL_CONTRIBUTOR,
            OrgRole.LEAD,
            OrgRole.MANAGER,
            OrgRole.DIRECTOR,
        ]

    def test_is_senior_to(self):
        assert OrgRole.DIRECTOR.is_senior_to(OrgRole.MANAGER)
        assert OrgRole.MANAGER.is_senior_to(OrgRole.LEAD)
        assert OrgRole.LEAD.is_senior_to(OrgRole.INDIVIDUAL_CONTRIBUTOR)
        assert not OrgRole.INDIVIDUAL_CONTRIBUTOR.is_senior_to(OrgRole.LEAD)

    def test_is_junior_to(self):
        assert OrgRole.INDIVIDUAL_CONTRIBUTOR.is_junior_to(OrgRole.LEAD)
        assert OrgRole.LEAD.is_junior_to(OrgRole.MANAGER)
        assert not OrgRole.DIRECTOR.is_junior_to(OrgRole.MANAGER)

    def test_same_role_not_senior_or_junior(self):
        assert not OrgRole.MANAGER.is_senior_to(OrgRole.MANAGER)
        assert not OrgRole.MANAGER.is_junior_to(OrgRole.MANAGER)


# =====================================================================
# Organization tests — Team
# =====================================================================


class TestTeam:
    def test_team_size_initially_zero(self):
        team = Team(team_id="t1", name="Test Team")
        assert team.size == 0

    def test_add_and_remove_member(self):
        team = Team(team_id="t1", name="Test Team")
        assert team.add_member("m1") is True
        assert team.add_member("m1") is False  # duplicate
        assert team.size == 1
        assert team.remove_member("m1") is True
        assert team.remove_member("m1") is False  # not found
        assert team.size == 0

    def test_utilization_calculation(self):
        team = Team(team_id="t1", name="Test Team", capacity=10)
        for i in range(5):
            team.add_member(f"m{i}")
        assert team.utilization == 0.5

    def test_utilization_zero_capacity(self):
        team = Team(team_id="t1", name="Test Team", capacity=0)
        assert team.utilization == 0.0

    def test_is_over_capacity(self):
        team = Team(team_id="t1", name="Test Team", capacity=5,
                    utilization_target=0.8)
        for i in range(3):
            team.add_member(f"m{i}")
        assert team.is_over_capacity() is False  # 3/5 = 0.6 < 0.8
        team.add_member("m4")
        team.add_member("m5")
        assert team.is_over_capacity() is True  # 5/5 = 1.0 > 0.8

    def test_performance_metrics(self):
        team = Team(team_id="t1", name="Test Team")
        team.record_metric("velocity", 42.0)
        assert team.performance_metrics["velocity"] == 42.0
        team.increment_metric("velocity", 8.0)
        assert team.performance_metrics["velocity"] == 50.0


# =====================================================================
# Organization tests — Department
# =====================================================================


class TestDepartment:
    def test_add_and_remove_team(self):
        dept = Department(dept_id="d1", name="Test Dept")
        assert dept.add_team("t1") is True
        assert dept.add_team("t1") is False  # duplicate
        assert len(dept.team_ids) == 1
        assert dept.remove_team("t1") is True
        assert dept.remove_team("t1") is False  # not found
        assert len(dept.team_ids) == 0


# =====================================================================
# Organization tests — RBAC
# =====================================================================


class TestRBACMatrix:
    def test_default_permissions_ic(self):
        rbac = RBACMatrix()
        assert rbac.has_permission(OrgRole.INDIVIDUAL_CONTRIBUTOR,
                                   Permission.VIEW_DASHBOARD)
        assert not rbac.has_permission(OrgRole.INDIVIDUAL_CONTRIBUTOR,
                                       Permission.MANAGE_BUDGET)

    def test_inheritance_director_has_all(self):
        rbac = RBACMatrix()
        # Director should have all permissions via inheritance
        for perm in Permission:
            assert rbac.has_permission(OrgRole.DIRECTOR, perm)

    def test_no_inheritance_mode(self):
        rbac = RBACMatrix(inherit_junior=False)
        # Without inheritance, IC shouldn't have director-only perms
        assert not rbac.has_permission(OrgRole.INDIVIDUAL_CONTRIBUTOR,
                                       Permission.ADMINISTER_ORG)

    def test_grant_and_revoke(self):
        rbac = RBACMatrix(inherit_junior=False)
        rbac.grant(OrgRole.INDIVIDUAL_CONTRIBUTOR, Permission.MANAGE_BUDGET)
        assert rbac.has_permission(OrgRole.INDIVIDUAL_CONTRIBUTOR,
                                   Permission.MANAGE_BUDGET)
        rbac.revoke(OrgRole.INDIVIDUAL_CONTRIBUTOR, Permission.MANAGE_BUDGET)
        assert not rbac.has_permission(OrgRole.INDIVIDUAL_CONTRIBUTOR,
                                       Permission.MANAGE_BUDGET)

    def test_can_member_do(self):
        rbac = RBACMatrix()
        member = OrgMember(member_id="m1", name="Test", role=OrgRole.LEAD)
        assert rbac.can_member_do(member, Permission.VIEW_DASHBOARD)
        assert rbac.can_member_do(member, Permission.REVIEW_WORK)
        assert not rbac.can_member_do(member, Permission.ADMINISTER_ORG)


# =====================================================================
# Organization tests — Organization (full traversal)
# =====================================================================


class TestOrganization:
    def test_total_counts(self, sample_org):
        assert sample_org.total_members() == 5
        assert sample_org.total_teams() == 2
        assert sample_org.total_departments() == 1

    def test_find_manager(self, sample_org):
        mgr = sample_org.find_manager("mem-lead")
        assert mgr is not None
        assert mgr.name == "Manager Smith"

    def test_find_manager_top_level(self, sample_org):
        assert sample_org.find_manager("mem-dir") is None

    def test_find_reports(self, sample_org):
        reports = sample_org.find_reports("mem-lead")
        assert len(reports) == 2
        report_names = {r.name for r in reports}
        assert "IC One" in report_names
        assert "IC Two" in report_names

    def test_find_all_subordinates(self, sample_org):
        subs = sample_org.find_all_subordinates("mem-dir")
        # Director has manager as direct report, who has lead, who has 2 ICs
        assert len(subs) == 4  # manager + lead + 2 ICs

    def test_find_management_chain(self, sample_org):
        chain = sample_org.find_management_chain("mem-ic1")
        assert len(chain) == 3
        assert chain[0].name == "Lead Jones"
        assert chain[1].name == "Manager Smith"
        assert chain[2].name == "Director Diaz"

    def test_team_members(self, sample_org):
        members = sample_org.team_members("team-backend")
        assert len(members) == 3  # manager + lead + ic1

    def test_department_members(self, sample_org):
        members = sample_org.department_members("dept-eng")
        assert len(members) == 4  # backend 3 + frontend 1 (ic2)

    def test_over_capacity_teams(self, sample_org):
        # Backend team: 3 members, 5 capacity, 0.8 target → 0.6 utilization → OK
        over = sample_org.over_capacity_teams()
        assert len(over) == 0

    def test_remove_member(self, sample_org):
        assert sample_org.total_members() == 5
        assert sample_org.remove_member("mem-ic2") is True
        assert sample_org.total_members() == 4
        # ic2 should be removed from frontend team
        frontend_members = sample_org.team_members("team-frontend")
        assert len(frontend_members) == 0

    def test_remove_member_not_found(self, sample_org):
        assert sample_org.remove_member("nonexistent") is False

    def test_can_member_do_convenience(self, sample_org):
        assert sample_org.can_member_do("mem-dir", Permission.ADMINISTER_ORG)
        assert not sample_org.can_member_do("mem-ic1", Permission.HIRE_FIRE)
        assert not sample_org.can_member_do("nonexistent", Permission.VIEW_DASHBOARD)

    def test_team_performance_summary(self, sample_org):
        summary = sample_org.team_performance_summary()
        assert "team-backend" in summary
        assert summary["team-backend"]["size"] == 3.0
        assert "utilization" in summary["team-backend"]

    def test_agent_role_integration(self, sample_org):
        """Org members carry agent_role that maps to AgentRole values."""
        ic2 = sample_org.get_member("mem-ic2")
        assert ic2 is not None
        assert ic2.agent_role == "analyst"  # matches AgentRole.ANALYST.value
        director = sample_org.get_member("mem-dir")
        assert director.agent_role == "manager"  # matches AgentRole.MANAGER.value


# =====================================================================
# Specialized Agents — CodeAgent
# =====================================================================


class TestCodeAgent:
    def test_composition_wraps_agent(self):
        agent = _make_agent("code-bot", "executor")
        ca = CodeAgent(agent=agent)
        assert ca.name == "code-bot"
        assert ca.id == "agent-code-bot"

    def test_language_preferences(self):
        agent = _make_agent()
        ca = CodeAgent(
            agent=agent,
            language_preferences=[CodeLanguage.PYTHON, CodeLanguage.TYPESCRIPT],
        )
        assert ca.prefers_language(CodeLanguage.PYTHON) is True
        assert ca.prefers_language(CodeLanguage.RUST) is False
        assert ca.primary_language() == CodeLanguage.PYTHON

    def test_estimate_complexity_trivial(self):
        agent = _make_agent()
        ca = CodeAgent(agent=agent)
        result = ca.estimate_complexity("fix a simple typo")
        assert result in (ComplexityLevel.TRIVIAL, ComplexityLevel.EASY)

    def test_estimate_complexity_hard(self):
        agent = _make_agent()
        ca = CodeAgent(agent=agent)
        result = ca.estimate_complexity(
            "redesign the distributed system architecture from scratch"
        )
        assert result in (ComplexityLevel.HARD, ComplexityLevel.VERY_HARD)

    def test_code_review_finds_todos(self):
        agent = _make_agent()
        ca = CodeAgent(agent=agent)
        code = "def foo():\n    # TODO: implement this\n    pass\n"
        findings = ca.review_code(code, language=CodeLanguage.PYTHON)
        assert any("TODO" in f.description for f in findings)

    def test_code_review_finds_long_lines(self):
        agent = _make_agent()
        ca = CodeAgent(agent=agent)
        long_line = "x = " + "a" * 130
        findings = ca.review_code(long_line)
        assert any("exceeds 120" in f.description for f in findings)

    def test_record_completion(self):
        agent = _make_agent()
        ca = CodeAgent(agent=agent)
        ca.record_completion(loc=100)
        assert ca.completed_tasks == 1
        assert ca.lines_of_code == 100
        ca.record_completion(loc=50)
        assert ca.completed_tasks == 2
        assert ca.lines_of_code == 150


# =====================================================================
# Specialized Agents — DataAnalystAgent
# =====================================================================


class TestDataAnalystAgent:
    def test_composition_wraps_agent(self):
        agent = _make_agent("data-bot", "analyst")
        da = DataAnalystAgent(agent=agent)
        assert da.name == "data-bot"
        assert da.id == "agent-data-bot"

    def test_supports_format(self):
        agent = _make_agent()
        da = DataAnalystAgent(
            agent=agent,
            supported_formats=[DataFormat.CSV, DataFormat.JSON],
        )
        assert da.supports_format(DataFormat.CSV) is True
        assert da.supports_format(DataFormat.PARQUET) is False

    def test_suggest_method_predictive(self):
        agent = _make_agent()
        da = DataAnalystAgent(agent=agent)
        method = da.suggest_method(1000, "predict future sales")
        assert method == AnalysisMethod.PREDICTIVE

    def test_suggest_method_descriptive(self):
        agent = _make_agent()
        da = DataAnalystAgent(agent=agent)
        method = da.suggest_method(100, "show summary of data")
        assert method == AnalysisMethod.DESCRIPTIVE

    def test_analyze_list_of_dicts(self):
        agent = _make_agent()
        da = DataAnalystAgent(agent=agent)
        data = [
            {"name": "Alice", "age": 30, "score": 95},
            {"name": "Bob", "age": 25, "score": 87},
            {"name": "Charlie", "age": 35, "score": 92},
        ]
        insights = da.analyze(data, goal="describe")
        assert len(insights) > 0
        assert any(i.insight_type == "summary" for i in insights)
        assert da.analyses_completed == 1

    def test_analyze_dict_data(self):
        agent = _make_agent()
        da = DataAnalystAgent(agent=agent)
        insights = da.analyze({"key": "value"}, goal="explore")
        assert len(insights) >= 1
        assert isinstance(insights[0], DataInsight)


# =====================================================================
# Specialized Agents — ResearchAgent
# =====================================================================


class TestResearchAgent:
    def test_composition_wraps_agent(self):
        agent = _make_agent("research-bot", "researcher")
        ra = ResearchAgent(agent=agent)
        assert ra.name == "research-bot"
        assert ra.id == "agent-research-bot"

    def test_evaluate_source_trusted(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        reliability = ra.evaluate_source(
            "Nature Journal",
            domain="nature.com",
            has_peer_review=True,
        )
        assert reliability in (SourceReliability.VERIFIED, SourceReliability.HIGH)

    def test_evaluate_source_low(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        # Plain .com domain with no peer review / official status → score 0 → LOW
        reliability = ra.evaluate_source(
            "Random Website",
            domain="example.com",
        )
        assert reliability == SourceReliability.LOW

    def test_evaluate_source_unverified(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        # Blog domain → score -1 → UNVERIFIED
        reliability = ra.evaluate_source(
            "Random Blog Post",
            domain="someblog.com/blog",
        )
        assert reliability == SourceReliability.UNVERIFIED

    def test_add_citation_and_bibliography(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        citation = Citation(
            source="Test Paper",
            url="https://example.com/paper",
            author="Alice Smith",
            reliability=SourceReliability.HIGH,
        )
        ra.add_citation(citation)
        assert len(ra.citations) == 1
        bib = ra.bibliography(style="simple")
        assert len(bib) == 1
        assert "Test Paper" in bib[0]

    def test_add_finding_and_verified(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        citation = Citation(
            source="Verified Source",
            reliability=SourceReliability.VERIFIED,
        )
        finding = ResearchFinding(
            claim="The sky is blue",
            confidence=0.95,
            citations=[citation],
        )
        ra.add_finding(finding)
        assert len(ra.findings) == 1
        verified = ra.verified_findings()
        assert len(verified) == 1

    def test_find_supporting_citations(self):
        agent = _make_agent()
        ra = ResearchAgent(agent=agent)
        ra.add_citation(Citation(source="AI Research", quote="Machine learning"))
        ra.add_citation(Citation(source="Biology Paper", quote="Cell structure"))
        results = ra.find_supporting_citations("machine")
        assert len(results) == 1
        assert "AI Research" in results[0].source


# =====================================================================
# Cost Tracker tests
# =====================================================================


class TestCostRecord:
    def test_cost_record_creation(self):
        record = CostRecord(
            category=CostCategory.API_CALLS,
            amount=10.5,
            provider="openai",
        )
        assert record.amount == 10.5
        assert record.category == CostCategory.API_CALLS
        assert record.record_id.startswith("cost_")

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError, match="negative"):
            CostRecord(category=CostCategory.API_CALLS, amount=-5, provider="x")

    def test_to_dict(self):
        record = CostRecord(
            category=CostCategory.COMPUTE,
            amount=100.0,
            provider="aws",
            resource_id="i-123",
            description="EC2 instance",
        )
        d = record.to_dict()
        assert d["category"] == "compute"
        assert d["amount"] == 100.0
        assert d["provider"] == "aws"


class TestBudget:
    def test_budget_utilization(self):
        b = Budget(name="test", limit=100.0, current_spend=75.0)
        assert b.utilization == 0.75
        assert b.remaining == 25.0
        assert b.is_over_budget is False

    def test_budget_over(self):
        b = Budget(name="test", limit=100.0, current_spend=150.0)
        assert b.is_over_budget is True
        assert b.remaining == -50.0

    def test_budget_reset(self):
        b = Budget(name="test", limit=100.0, current_spend=80.0)
        b.alerts_fired.add(0.5)
        b.reset()
        assert b.current_spend == 0.0
        assert len(b.alerts_fired) == 0


class TestCostTracker:
    def test_record_cost(self):
        tracker = CostTracker()
        record = tracker.record_cost(
            category=CostCategory.API_CALLS,
            amount=25.0,
            provider="openai",
            description="GPT-4 calls",
        )
        assert record.amount == 25.0
        assert len(tracker.records) == 1

    def test_total_cost(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "aws")
        tracker.record_cost(CostCategory.STORAGE, 5.0, "aws")
        assert tracker.total_cost() == 35.0
        assert tracker.total_cost(category=CostCategory.API_CALLS) == 10.0
        assert tracker.total_cost(provider="aws") == 25.0

    def test_by_category(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "aws")
        breakdown = tracker.by_category()
        assert breakdown[CostCategory.API_CALLS] == 10.0
        assert breakdown[CostCategory.COMPUTE] == 20.0

    def test_by_provider(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        tracker.record_cost(CostCategory.API_CALLS, 5.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "aws")
        breakdown = tracker.by_provider()
        assert breakdown["openai"] == 15.0
        assert breakdown["aws"] == 20.0

    def test_budget_and_alerts(self):
        tracker = CostTracker()
        tracker.set_budget("monthly_api", 100.0, AggregationPeriod.MONTHLY,
                           CostCategory.API_CALLS)
        # Spend 60 → should fire 50% alert
        tracker.record_cost(CostCategory.API_CALLS, 60.0, "openai")
        budget = tracker.get_budget("monthly_api")
        assert budget is not None
        assert budget.current_spend == 60.0
        assert 0.5 in budget.alerts_fired
        # Should have at least one alert
        assert len(tracker.alerts) >= 1

    def test_budget_100_percent_critical(self):
        tracker = CostTracker()
        tracker.set_budget("small", 10.0)
        tracker.record_cost(CostCategory.API_CALLS, 15.0, "openai")
        alerts = tracker.recent_alerts(level=AlertLevel.CRITICAL)
        assert len(alerts) >= 1

    def test_aggregate_daily(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        agg = tracker.aggregate(AggregationPeriod.DAILY)
        assert len(agg) >= 1

    def test_forecast(self):
        tracker = CostTracker()
        # Add some records with different timestamps
        import time
        now = time.time()
        for i in range(5):
            ts = now - (5 - i) * 86400  # one per day
            tracker.record_cost(
                CostCategory.API_CALLS, 10.0 + i, "openai",
                timestamp=ts,
            )
        forecast = tracker.forecast_cost(AggregationPeriod.DAILY)
        assert isinstance(forecast, CostForecast)
        assert forecast.basis_points == 5
        assert forecast.projected_cost > 0

    def test_forecast_no_data(self):
        tracker = CostTracker()
        forecast = tracker.forecast_cost()
        assert forecast.projected_cost == 0.0
        assert forecast.confidence == 0.0

    def test_optimization_suggestions(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 100.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 200.0, "aws")
        suggestions = tracker.get_optimization_suggestions()
        assert len(suggestions) >= 2
        assert any(s.category == CostCategory.API_CALLS for s in suggestions)

    def test_export_csv(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai",
                            description="test")
        csv_data = tracker.to_csv()
        assert "record_id" in csv_data
        assert "api_calls" in csv_data
        assert "openai" in csv_data

    def test_export_json(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.STORAGE, 5.0, "gcp")
        json_data = tracker.to_json()
        data = json.loads(json_data)
        assert data["total_cost"] == 5.0
        assert data["total_records"] == 1
        assert "budgets" in data
        assert "by_category" in data

    def test_export_to_file(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = tracker.export_to_file(Path(tmpdir) / "costs.json")
            assert path.exists()
            content = json.loads(path.read_text())
            assert content["total_cost"] == 10.0


# =====================================================================
# Backup Manager tests
# =====================================================================


class TestBackupManager:
    def _make_source_dir(self, tmp_path, name, files):
        """Helper: create a source directory with files."""
        src_dir = tmp_path / name
        src_dir.mkdir()
        for fname, content in files.items():
            (src_dir / fname).write_text(content)
        return src_dir

    def test_create_full_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config", {
            "settings.json": '{"key": "value"}',
            "config.yaml": "setting: true",
        })
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        meta = manager.create_backup(
            backup_type=BackupType.FULL,
            description="Test backup",
        )
        assert meta.status == BackupStatus.COMPLETED
        assert meta.backup_type == BackupType.FULL
        assert meta.checksum != ""
        assert meta.size_bytes > 0
        assert len(manager.backups) == 1

    def test_list_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        manager.create_backup(description="first")
        manager.create_backup(description="second")
        backups = manager.list_backups()
        assert len(backups) == 2
        # Newest first
        assert backups[0].description == "second"

    def test_latest_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        assert manager.latest_backup() is None
        manager.create_backup(description="first")
        latest = manager.latest_backup()
        assert latest is not None
        assert latest.description == "first"

    def test_verify_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        meta = manager.create_backup()
        assert manager.verify_backup(meta.backup_id) is True
        assert manager.verify_backup("nonexistent") is False

    def test_restore_full(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config", {
            "settings.json": '{"key": "value"}',
            "config.yaml": "setting: true",
        })
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        meta = manager.create_backup()

        # Restore to a new directory
        restore_dir = tmp_path / "restored"
        result = manager.restore_backup(meta.backup_id, target_dir=restore_dir)
        assert result.success is True
        assert result.status == RestoreStatus.COMPLETED
        assert result.total_files_restored == 2
        assert BackupSource.CONFIG in result.restored_sources

        # Verify files
        assert (restore_dir / "config" / "settings.json").exists()
        assert (restore_dir / "config" / "config.yaml").exists()

    def test_restore_nonexistent(self, tmp_path):
        backup_dir = tmp_path / "backups"
        manager = BackupManager(backup_dir=backup_dir)
        result = manager.restore_backup("nonexistent")
        assert result.success is False
        assert result.status == RestoreStatus.FAILED

    def test_retention_count_based(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
            retention_policy=RetentionPolicy(
                policy_type=RetentionPolicyType.COUNT_BASED,
                max_backups=3,
                always_keep_full=False,
            ),
        )
        # Create 5 backups
        for i in range(5):
            manager.create_backup(description=f"backup-{i}")
        # After retention, should have 3
        assert len(manager.backups) == 3

    def test_statistics(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        manager.create_backup(backup_type=BackupType.FULL)
        manager.create_backup(backup_type=BackupType.INCREMENTAL)
        stats = manager.get_statistics()
        assert stats.total_backups == 2
        assert stats.full_backups == 1
        assert stats.incremental_backups == 1
        assert stats.successful_backups == 2
        assert stats.total_size_bytes > 0

    def test_generate_report(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        manager.create_backup()
        report = manager.generate_report()
        assert "statistics" in report
        assert "recent_backups" in report
        assert report["statistics"]["total_backups"] == 1

    def test_uncompressed_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        source_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "hello"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={BackupSource.CONFIG: source_dir},
        )
        meta = manager.create_backup(compress=False)
        assert meta.compressed is False
        assert manager.verify_backup(meta.backup_id) is True

    def test_selective_restore(self, tmp_path):
        backup_dir = tmp_path / "backups"
        config_dir = self._make_source_dir(tmp_path, "config",
                                           {"a.txt": "config"})
        memory_dir = self._make_source_dir(tmp_path, "memory",
                                           {"b.txt": "memory"})
        manager = BackupManager(
            backup_dir=backup_dir,
            source_paths={
                BackupSource.CONFIG: config_dir,
                BackupSource.MEMORY_DATA: memory_dir,
            },
        )
        meta = manager.create_backup(
            sources=[BackupSource.CONFIG, BackupSource.MEMORY_DATA]
        )

        # Restore only config
        restore_dir = tmp_path / "restored"
        result = manager.restore_backup(
            meta.backup_id,
            sources=[BackupSource.CONFIG],
            target_dir=restore_dir,
        )
        assert result.success is True
        assert result.total_files_restored == 1
        assert BackupSource.CONFIG in result.restored_sources
        assert BackupSource.MEMORY_DATA not in result.restored_sources
