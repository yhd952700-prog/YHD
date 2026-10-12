"""Tests for extended CLI command modules.

Covers all 10 CLI command extensions:
- agent_commands
- memory_commands
- health_commands
- cost_commands
- backup_commands
- plugin_commands
- org_commands
- solution_commands
- benchmark_commands
- shell_commands
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add src to path
SRC_PATH = Path(__file__).resolve().parents[1] / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.cli.agent_commands import AgentManager, AgentStatus
from src.cli.memory_commands import MemoryManager, MemoryType
from src.cli.health_commands import HealthChecker, HealthStatus
from src.cli.cost_commands import CostManager
from src.cli.backup_commands import BackupManager, BackupType
from src.cli.plugin_commands import PluginManager, PluginStatus
from src.cli.org_commands import OrgManager, OrgRole
from src.cli.solution_commands import SolutionManager, SolutionStatus
from src.cli.benchmark_commands import BenchmarkManager, BenchmarkType
from src.cli.shell_commands import ShellSession


# =====================================================================
# Agent CLI Tests
# =====================================================================


class TestAgentCommands:
    """Tests for agent CLI commands."""

    @pytest.fixture
    def manager(self):
        return AgentManager()

    def test_create_agent(self, manager):
        result = manager.create_agent(name="Test Agent", role="researcher")
        assert result["name"] == "Test Agent"
        assert result["role"] == "researcher"
        assert result["status"] == "idle"
        assert "id" in result

    def test_list_agents_empty(self, manager):
        result = manager.list_agents()
        assert result["total"] == 0
        assert result["items"] == []

    def test_list_agents_with_filter(self, manager):
        manager.create_agent(name="A1", role="researcher")
        manager.create_agent(name="A2", role="analyst")
        result = manager.list_agents(role="researcher")
        assert result["total"] == 1
        assert result["items"][0]["role"] == "researcher"

    def test_get_agent(self, manager):
        created = manager.create_agent(name="Test", role="manager")
        result = manager.get_agent(created["id"])
        assert result["name"] == "Test"
        assert result["role"] == "manager"

    def test_get_agent_not_found(self, manager):
        with pytest.raises(ValueError, match="Agent not found"):
            manager.get_agent("nonexistent")

    def test_delete_agent(self, manager):
        created = manager.create_agent(name="ToDelete", role="analyst")
        result = manager.delete_agent(created["id"])
        assert result["status"] == "deleted"
        with pytest.raises(ValueError):
            manager.get_agent(created["id"])

    def test_run_agent(self, manager):
        created = manager.create_agent(name="Runner", role="executor")
        result = manager.run_agent(created["id"], task="test task")
        assert result["status"] == "running"
        assert result["task"] == "test task"

    def test_stop_agent(self, manager):
        created = manager.create_agent(name="Stoppable", role="executor")
        manager.run_agent(created["id"])
        result = manager.stop_agent(created["id"])
        assert result["status"] == "stopped"

    def test_agent_metrics(self, manager):
        created = manager.create_agent(name="MetricAgent", role="analyst")
        result = manager.agent_metrics(created["id"])
        assert "total_tasks" in result
        assert "success_rate" in result

    def test_agent_logs(self, manager):
        created = manager.create_agent(name="LogAgent", role="analyst")
        result = manager.agent_logs(created["id"])
        assert "entries" in result
        assert result["agent_id"] == created["id"]


# =====================================================================
# Memory CLI Tests
# =====================================================================


class TestMemoryCommands:
    """Tests for memory CLI commands."""

    @pytest.fixture
    def manager(self):
        return MemoryManager()

    def test_add_memory(self, manager):
        result = manager.add_memory(content="Test memory", memory_type="episodic")
        assert result["content"] == "Test memory"
        assert result["type"] == "episodic"
        assert "id" in result

    def test_get_memory(self, manager):
        created = manager.add_memory(content="Hello", memory_type="semantic")
        result = manager.get_memory(created["id"])
        assert result["content"] == "Hello"
        assert result["access_count"] == 1

    def test_search_memory(self, manager):
        manager.add_memory(content="Python programming guide", tags=["python", "code"])
        manager.add_memory(content="Java programming guide", tags=["java", "code"])
        result = manager.search_memory("python")
        assert result["total"] >= 1
        assert any("Python" in item["content"] for item in result["items"])

    def test_delete_memory(self, manager):
        created = manager.add_memory(content="ToDelete")
        result = manager.delete_memory(created["id"])
        assert result["status"] == "deleted"

    def test_memory_stats(self, manager):
        manager.add_memory(content="M1", memory_type="episodic")
        manager.add_memory(content="M2", memory_type="semantic")
        result = manager.memory_stats()
        assert result["total_memories"] == 2
        assert "by_type" in result

    def test_clear_memory(self, manager):
        manager.add_memory(content="M1", memory_type="episodic")
        manager.add_memory(content="M2", memory_type="semantic")
        result = manager.clear_memory(memory_type="episodic")
        assert result["cleared"] == 1
        assert result["remaining"] == 1

    def test_export_import_memory(self, manager):
        manager.add_memory(content="Export test", tags=["test"])
        exported = manager.export_memory()
        assert exported["count"] == 1
        assert "memories" in exported

        new_manager = MemoryManager()
        result = new_manager.import_memory(exported)
        assert result["imported"] == 1


# =====================================================================
# Health CLI Tests
# =====================================================================


class TestHealthCommands:
    """Tests for health CLI commands."""

    @pytest.fixture
    def checker(self):
        return HealthChecker()

    def test_run_check(self, checker):
        result = checker.run_check()
        assert "overall_status" in result
        assert "components" in result
        assert result["component_count"] > 0

    def test_get_status(self, checker):
        result = checker.get_status()
        assert "overall_status" in result
        assert "healthy_count" in result

    def test_list_components(self, checker):
        result = checker.list_components()
        assert "components" in result
        assert len(result["components"]) > 0

    def test_check_liveness(self, checker):
        result = checker.check_liveness()
        assert result["alive"] is True
        assert result["status"] == "alive"

    def test_check_readiness(self, checker):
        result = checker.check_readiness()
        assert "ready" in result
        assert "unready_components" in result

    def test_set_component_status(self, checker):
        result = checker.set_component_status("database", "degraded", "Slow queries")
        assert result["status"] == "degraded"
        assert result["message"] == "Slow queries"

    def test_set_component_not_found(self, checker):
        with pytest.raises(ValueError, match="Component not found"):
            checker.set_component_status("nonexistent", "healthy")

    def test_history(self, checker):
        checker.run_check()
        result = checker.get_history(limit=5)
        assert "entries" in result
        assert result["total"] >= 1


# =====================================================================
# Cost CLI Tests
# =====================================================================


class TestCostCommands:
    """Tests for cost CLI commands."""

    @pytest.fixture
    def manager(self):
        return CostManager()

    def test_get_overview(self, manager):
        result = manager.get_overview()
        assert "total_cost" in result
        assert "by_category" in result
        assert result["currency"] == "USD"

    def test_get_breakdown(self, manager):
        result = manager.get_breakdown(period="daily")
        assert "breakdown" in result
        assert result["period"] == "daily"

    def test_get_daily_cost(self, manager):
        result = manager.get_daily_cost(days=7)
        assert result["days"] == 7
        assert "daily" in result

    def test_create_budget(self, manager):
        result = manager.create_budget(name="test-budget", amount=100.0, period="monthly")
        assert result["name"] == "test-budget"
        assert result["amount"] == 100.0

    def test_create_duplicate_budget(self, manager):
        manager.create_budget(name="dup", amount=100.0)
        with pytest.raises(ValueError, match="already exists"):
            manager.create_budget(name="dup", amount=200.0)

    def test_list_budgets(self, manager):
        manager.create_budget(name="b1", amount=100.0)
        manager.create_budget(name="b2", amount=200.0)
        result = manager.list_budgets()
        assert len(result["budgets"]) == 2

    def test_add_budget_alert(self, manager):
        manager.create_budget(name="alert-budget", amount=100.0)
        result = manager.add_budget_alert(
            budget_name="alert-budget", alert_name="warn", threshold_pct=80.0
        )
        assert result["threshold_pct"] == 80.0

    def test_get_forecast(self, manager):
        result = manager.get_forecast(days=7)
        assert result["forecast_days"] == 7
        assert "total_forecast" in result
        assert "daily_forecast" in result


# =====================================================================
# Backup CLI Tests
# =====================================================================


class TestBackupCommands:
    """Tests for backup CLI commands."""

    @pytest.fixture
    def manager(self):
        return BackupManager()

    def test_create_backup(self, manager):
        result = manager.create_backup(backup_type="full", description="Test backup")
        assert result["type"] == "full"
        assert result["status"] == "completed"
        assert "backup_id" in result

    def test_list_backups(self, manager):
        result = manager.list_backups()
        assert "total" in result
        assert result["total"] > 0  # seeded with sample data

    def test_get_backup_info(self, manager):
        created = manager.create_backup(backup_type="incremental")
        result = manager.get_backup_info(created["backup_id"])
        assert result["backup_id"] == created["backup_id"]
        assert "size_human" in result

    def test_restore_backup(self, manager):
        created = manager.create_backup()
        result = manager.restore_backup(created["backup_id"])
        assert result["status"] == "restored"

    def test_verify_backup(self, manager):
        created = manager.create_backup()
        result = manager.verify_backup(created["backup_id"])
        assert result["verified"] is True
        assert result["checksum_match"] is True

    def test_prune_backups(self, manager):
        result = manager.prune_backups(keep_last=3)
        assert "pruned" in result
        assert "remaining" in result

    def test_backup_not_found(self, manager):
        with pytest.raises(ValueError, match="Backup not found"):
            manager.get_backup_info("nonexistent")


# =====================================================================
# Plugin CLI Tests
# =====================================================================


class TestPluginCommands:
    """Tests for plugin CLI commands."""

    @pytest.fixture
    def manager(self):
        return PluginManager()

    def test_list_plugins(self, manager):
        result = manager.list_plugins()
        assert result["total"] > 0  # seeded with sample data

    def test_get_plugin_info(self, manager):
        result = manager.get_plugin_info("plugin-slack")
        assert result["name"] == "Slack Integration"
        assert result["status"] == "enabled"

    def test_plugin_not_found(self, manager):
        with pytest.raises(ValueError, match="Plugin not found"):
            manager.get_plugin_info("nonexistent")

    def test_install_plugin(self, manager):
        result = manager.install_plugin("plugin-trello")
        assert result["status"] == "installed"
        assert result["id"] == "plugin-trello"

    def test_install_already_installed(self, manager):
        with pytest.raises(ValueError, match="already installed"):
            manager.install_plugin("plugin-slack")

    def test_enable_plugin(self, manager):
        result = manager.enable_plugin("plugin-jira")
        assert result["status"] == "enabled"

    def test_disable_plugin(self, manager):
        result = manager.disable_plugin("plugin-slack")
        assert result["status"] == "disabled"

    def test_uninstall_plugin(self, manager):
        result = manager.uninstall_plugin("plugin-notion")
        assert result["status"] == "uninstalled"

    def test_upgrade_plugin(self, manager):
        result = manager.upgrade_plugin("plugin-github")
        assert result["status"] == "upgraded"
        assert "new_version" in result

    def test_search_plugins(self, manager):
        result = manager.search_plugins("integration")
        assert result["total"] > 0


# =====================================================================
# Org CLI Tests
# =====================================================================


class TestOrgCommands:
    """Tests for organization CLI commands."""

    @pytest.fixture
    def manager(self):
        return OrgManager()

    def test_get_org_tree(self, manager):
        result = manager.get_org_tree()
        assert "teams" in result
        assert "total_teams" in result
        assert result["total_teams"] > 0

    def test_list_teams(self, manager):
        result = manager.list_teams()
        assert "teams" in result
        assert len(result["teams"]) > 0

    def test_list_members(self, manager):
        result = manager.list_members()
        assert "members" in result
        assert result["total"] > 0

    def test_add_member(self, manager):
        result = manager.add_member(
            name="New Member", role="individual_contributor", team_id="team-research"
        )
        assert result["name"] == "New Member"
        assert result["role"] == "individual_contributor"

    def test_remove_member(self, manager):
        result = manager.remove_member("m-005")
        assert result["status"] == "removed"

    def test_list_roles(self, manager):
        result = manager.list_roles()
        assert "roles" in result
        assert len(result["roles"]) > 0

    def test_get_capacity(self, manager):
        result = manager.get_capacity()
        assert "total_members" in result
        assert "avg_capacity_pct" in result

    def test_get_performance(self, manager):
        result = manager.get_performance()
        assert "tasks_completed" in result
        assert "quality_score" in result


# =====================================================================
# Solution CLI Tests
# =====================================================================


class TestSolutionCommands:
    """Tests for solution CLI commands."""

    @pytest.fixture
    def manager(self):
        return SolutionManager()

    def test_list_solutions(self, manager):
        result = manager.list_solutions()
        assert "total" in result
        assert result["total"] > 0

    def test_get_solution_info(self, manager):
        result = manager.get_solution_info("sol-customer-support")
        assert result["name"] == "Customer Support Agent"
        assert "required_agents" in result

    def test_solution_not_found(self, manager):
        with pytest.raises(ValueError, match="Solution not found"):
            manager.get_solution_info("nonexistent")

    def test_deploy_solution(self, manager):
        result = manager.deploy_solution(
            "sol-code-review", name="Code Review Prod"
        )
        assert result["status"] == "deployed"
        assert result["name"] == "Code Review Prod"

    def test_get_deployment_status(self, manager):
        result = manager.get_deployment_status("dep-001")
        assert "status" in result
        assert "deployment_id" in result

    def test_configure_solution(self, manager):
        result = manager.configure_solution(
            "dep-001", {"tier": "premium"}
        )
        assert result["status"] == "updating"

    def test_get_solution_metrics(self, manager):
        result = manager.get_solution_metrics("dep-001")
        assert "metrics" in result
        assert "success_rate" in result["metrics"]


# =====================================================================
# Benchmark CLI Tests
# =====================================================================


class TestBenchmarkCommands:
    """Tests for benchmark CLI commands."""

    @pytest.fixture
    def manager(self):
        return BenchmarkManager()

    def test_run_benchmark(self, manager):
        result = manager.run_benchmark(name="Test Benchmark", benchmark_type="performance")
        assert result["name"] == "Test Benchmark"
        assert result["status"] == "completed"
        assert "score" in result

    def test_list_benchmarks(self, manager):
        result = manager.list_benchmarks()
        assert "total" in result
        assert result["total"] > 0

    def test_compare_benchmarks(self, manager):
        result = manager.compare_benchmarks(["run-001", "run-002"])
        assert "score_comparison" in result
        assert "winner" in result

    def test_generate_report(self, manager):
        result = manager.generate_report(run_id="run-001")
        assert result["format"] == "json"
        assert "run" in result

    def test_generate_report_type_filter(self, manager):
        result = manager.generate_report(benchmark_type="performance")
        assert "by_type" in result

    def test_get_baseline(self, manager):
        result = manager.get_baseline()
        assert "baselines" in result

    def test_set_baseline(self, manager):
        result = manager.set_baseline("run-002")
        assert result["status"] == "baseline_set"

    def test_benchmark_not_found(self, manager):
        with pytest.raises(ValueError, match="Benchmark run not found"):
            manager.generate_report(run_id="nonexistent")


# =====================================================================
# Shell CLI Tests
# =====================================================================


class TestShellCommands:
    """Tests for interactive shell commands."""

    @pytest.fixture
    def session(self):
        return ShellSession()

    def test_execute_help(self, session):
        result = session.execute("help")
        assert "available_commands" in result

    def test_execute_help_specific(self, session):
        result = session.execute("help echo")
        assert result["command"] == "echo"

    def test_execute_echo(self, session):
        result = session.execute("echo hello world")
        assert result["echo"] == "hello world"

    def test_execute_history(self, session):
        session.execute("echo first")
        session.execute("echo second")
        result = session.execute("history")
        assert result["count"] == 3  # includes the history command itself

    def test_execute_status(self, session):
        session.execute("echo test")
        result = session.execute("status")
        assert result["commands_executed"] == 2

    def test_execute_set_and_vars(self, session):
        session.execute("set myvar hello")
        result = session.execute("vars")
        assert result["variables"]["myvar"] == "hello"

    def test_execute_time(self, session):
        result = session.execute("time")
        assert "utc" in result
        assert "timestamp" in result

    def test_execute_info(self, session):
        result = session.execute("info")
        assert result["system"] == "LiuHao AI OS"

    def test_execute_whoami(self, session):
        result = session.execute("whoami")
        assert "user" in result
        assert "session_id" in result

    def test_execute_version(self, session):
        result = session.execute("version")
        assert result["product"] == "LiuHao AI OS"

    def test_execute_unknown_command(self, session):
        result = session.execute("nonexistent")
        assert "error" in result

    def test_execute_empty(self, session):
        result = session.execute("")
        assert result == {}

    def test_exit_command(self, session):
        assert session._running is False
        result = session.execute("exit")
        assert result["status"] == "exiting"
