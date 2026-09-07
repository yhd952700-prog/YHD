"""
AI Employee - Multi-Agent Coordination Test

Tests that the Employee framework can coordinate 3+ agent types
and produce coordinated results.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.ai.employee import Employee, AgentPool
from src.ai.providers import get_provider, ProviderType
from src.ai.providers import OpenAIProvider, AnthropicProvider, OllamaProvider


class TestMultiAgentCoordination:
    """Test coordination of 3+ different agent types."""
    
    @pytest.fixture(autouse=True)
    def setup_provider(self):
        """Set up provider for tests."""
        os.environ["AI_PROVIDER_TYPE"] = "openai"
        os.environ["AI_PROVIDER_KEY"] = "[REDACTED]"
        os.environ["AI_PROVIDER_MODEL"] = "gpt-4"
        os.environ["AI_PROVIDER_NAME"] = "test-employee"
        yield
        # Cleanup after test
        from src.ai.providers import reset_provider
        reset_provider()
    
    def test_three_agent_types_coordination(self):
        """Test that 3 different agent types can coordinate."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        # Get provider
        provider = get_provider()
        assert provider is not None
        
        # Create employee with 3 agent types
        employee = Employee(
            name="test-coordination",
            provider=provider,
            agent_count=3,
        )
        
        # Test employee can be created with 3 agents
        assert employee.agent_count == 3
        assert employee.name == "test-coordination"
        
        # Add tasks first
        task1 = employee.add_task("task-a", "general")
        task2 = employee.add_task("task-b", "general")
        task3 = employee.add_task("task-c", "general")
        
        # Test task distribution
        distributed = employee.distribute_tasks()
        
        # Should distribute 3 tasks to 3 agents
        assert len(distributed) == 3
        assert set(distributed.values()) == {task1, task2, task3}
        
        # Verify all agents got a task
        assert "agent_0" in distributed
        assert "agent_1" in distributed
        assert "agent_2" in distributed
    
    def test_agent_result_aggregation(self):
        """Test that employee can aggregate results from multiple agents."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        provider = get_provider()
        employee = Employee(name="test-aggregation", provider=provider, agent_count=3)
        
        # Simulate agent results
        agent_results = {
            "agent_0": {"result": "data-A", "status": "completed"},
            "agent_1": {"result": "data-B", "status": "completed"},
            "agent_2": {"result": "data-C", "status": "completed"},
        }
        
        aggregated = employee.aggregate_results(agent_results)
        
        # Should aggregate all results - check the structure
        assert aggregated["total_agents"] == 3
        assert aggregated["total_tasks"] == 3
        assert aggregated["completed"] == 3
        assert aggregated["success_rate"] == 100.0
        assert "data-A" in aggregated["results"]
        assert "data-B" in aggregated["results"]
        assert "data-C" in aggregated["results"]
    
    def test_kpi_reporting(self):
        """Test that employee can generate KPI reports."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        provider = get_provider()
        employee = Employee(name="test-kpi", provider=provider, agent_count=5)
        
        # Generate KPI report
        kpi = employee.generate_kpi_report()
        
        # Should contain expected KPI fields
        assert "agent_count" in kpi
        assert "completed_tasks" in kpi
        assert "success_rate" in kpi
        assert kpi["agent_count"] == 5
        assert kpi["success_rate"] >= 0
        assert kpi["success_rate"] <= 100
