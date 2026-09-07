"""Phase 7 productization UI tests for LiuHao AI OS."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ui import (
    FutureConsole, CEODashboard, SystemStatusCard, AIWorkerCard,
    AIEmployeeCenter, AgentCard, TaskWorkflowConsole,
    SecurityAuditConsole, ModelCenter, MetricDashboard, MetricType,
    OnboardingWizard, DemoFlow, OnboardingStep
)


def test_future_console_construction():
    """Test FutureConsole can be constructed."""
    console = FutureConsole()
    assert console.theme.value == "cyberpunk"
    assert console.registered_modules == {}
    assert console.systems == {}


def test_ceo_dashboard_construction():
    """Test CEODashboard can be constructed."""
    dashboard = CEODashboard()
    assert dashboard.risk_score == 0.0
    assert dashboard.overall_health == "excellent"
    assert dashboard.system_cards == {}
    assert dashboard.ai_worker_cards == {}


def test_system_status_card():
    """Test SystemStatusCard creation and to_dict."""
    card = SystemStatusCard(system_name="test-system", status="healthy", cpu_percent=25.0)
    d = card.to_dict()
    assert d["system_name"] == "test-system"
    assert d["status"] == "healthy"
    assert d["cpu_percent"] == 25.0


def test_ai_worker_card():
    """Test AIWorkerCard creation and to_dict."""
    card = AIWorkerCard(worker_id="worker-1", model_name="gpt-4", status="active", active_sessions=3)
    d = card.to_dict()
    assert d["worker_id"] == "worker-1"
    assert d["model_name"] == "gpt-4"
    assert d["active_sessions"] == 3


def test_ai_employee_center():
    """Test AIEmployeeCenter construction and agent management."""
    center = AIEmployeeCenter()
    agent = AgentCard(agent_id="agent-1", agent_type="worker", status="active")
    center.add_agent(agent)
    assert len(center.agents) == 1
    retrieved = center.get_agent("agent-1")
    assert retrieved is not None
    assert retrieved.agent_id == "agent-1"


def test_task_workflow_console():
    """Test TaskWorkflowConsole basic operation."""
    console = TaskWorkflowConsole()
    console.add_workflow("wf-1", status="pending")
    assert "wf-1" in console.workflows
    assert console.total_count == 1


def test_security_audit_console():
    """Test SecurityAuditConsole event logging."""
    audit = SecurityAuditConsole()
    audit.add_audit_event({"user": "admin", "action": "login"}, severity="low")
    events = audit.get_recent_events()
    assert len(events) >= 1
    assert events[0]["severity"] == "low"


def test_model_center():
    """Test ModelCenter model registration."""
    center = ModelCenter()
    center.register_model("gpt-4", status="available")
    center.register_model("claude-3", status="busy")
    assert "gpt-4" in center.models
    assert "claude-3" in center.models
    assert center.model_statuses["gpt-4"] == "available"
    assert center.model_statuses["claude-3"] == "busy"


def test_metric_dashboard():
    """Test MetricDashboard metric tracking."""
    dashboard = MetricDashboard()
    dashboard.add_metric_point(MetricType.CPU, 75.0)
    dashboard.add_metric_point(MetricType.MEMORY, 60.0)
    latest_cpu = dashboard.get_latest(MetricType.CPU)
    assert latest_cpu == 75.0


def test_onboarding_wizard():
    """Test OnboardingWizard basic operation."""
    wizard = OnboardingWizard()
    assert wizard.current_step == OnboardingStep.WELCOME
    wizard.complete_step(OnboardingStep.WELCOME)
    assert OnboardingStep.WELCOME in wizard.completed_steps


def test_demo_flow():
    """Test DemoFlow basic operation."""
    demo = DemoFlow(flow_name="product_demo", total_steps=5)
    assert demo.flow_name == "product_demo"
    assert demo.total_steps == 5
    assert demo.is_running == False