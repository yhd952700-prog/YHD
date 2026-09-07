"""Product console UI module for LiuHao AI OS.

This package provides the frontend/productization UI scaffold for the LiuHao AI OS
(Y1). All modules are additive and non-invasive — they do not modify any backend
business logic, providers, workflows, or security modules.

Available modules:
- console: FutureConsole — the central product console
- dashboard: CEODashboard, SystemStatusCard, AIWorkerCard
- employees: AIEmployeeCenter, AgentCard
- workflow: TaskWorkflowConsole
- security: SecurityAuditConsole
- models: ModelCenter
- metrics: MetricDashboard
- onboarding: OnboardingWizard, DemoFlow
"""

from src.ui.console import FutureConsole
from src.ui.dashboard import CEODashboard, SystemStatusCard, AIWorkerCard
from src.ui.employees import AIEmployeeCenter, AgentCard
from src.ui.workflow import TaskWorkflowConsole
from src.ui.security import SecurityAuditConsole
from src.ui.models import ModelCenter
from src.ui.metrics import MetricDashboard, MetricPoint, MetricType
from src.ui.onboarding import OnboardingWizard, DemoFlow, OnboardingStep

__all__ = [
    "FutureConsole",
    "CEODashboard",
    "SystemStatusCard",
    "AIWorkerCard",
    "AIEmployeeCenter",
    "AgentCard",
    "TaskWorkflowConsole",
    "SecurityAuditConsole",
    "ModelCenter",
    "MetricDashboard",
    "MetricPoint",
    "MetricType",
    "OnboardingWizard",
    "DemoFlow",
    "OnboardingStep",
]