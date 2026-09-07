"""Load testing baseline for Phase 6 SRE."""

import sys
import os
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'sre'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'cost'))

from sre.scaling.scaling import ScalingPolicy, ScalingDecision, ResourceSample, ResourceType
from sre.cost.cost_manager import CostManager, UsageRecord, BudgetPolicy, Provider


def test_scaling_policy_decide():
    """Test ScalingPolicy decide method."""
    policy = ScalingPolicy(cpu_threshold=80.0, memory_threshold=80.0)
    
    # Test scale up on high CPU
    decision = policy.decide({ResourceType.CPU: 90.0})
    assert decision == ScalingDecision.SCALE_UP
    
    # Test maintain on normal CPU
    decision = policy.decide({ResourceType.CPU: 50.0})
    assert decision == ScalingDecision.MAINTAIN


def test_scaling_capacity_planner():
    """Test CapacityPlanner plan method."""
    from sre.scaling.scaling import CapacityPlanner
    
    planner = CapacityPlanner(policy=ScalingPolicy(cpu_threshold=80.0))
    result = planner.plan({ResourceType.CPU: 90.0})
    
    assert "decision" in result
    assert "policy_name" in result
    assert result["decision"] == "scale_up"


def test_resource_sample():
    """Test ResourceSample creation."""
    sample = ResourceSample(
        resource_type=ResourceType.CPU,
        value=75.0,
    )
    assert sample.resource_type == ResourceType.CPU
    assert sample.value == 75.0


def test_cost_manager_track():
    """Test CostManager track method."""
    manager = CostManager()
    record = UsageRecord(
        provider=Provider.OPENAI,
        model="gpt-4",
        input_tokens=1000,
        output_tokens=500,
    )
    manager.track(record)
    assert manager.total_cost > 0


def test_cost_manager_budget_status():
    """Test CostManager budget_status method."""
    manager = CostManager()
    manager.budget_policies["test"] = BudgetPolicy(
        name="test",
        max_cost=100.0,
        provider=Provider.OPENAI,
    )
    status = manager.budget_status("test")
    assert "status" in status


def test_cost_manager_apply_budget_policy():
    """Test CostManager apply_budget_policy method."""
    manager = CostManager()
    manager.budget_policies["test"] = BudgetPolicy(
        name="test",
        max_cost=100.0,
        provider=Provider.OPENAI,
    )
    result = manager.apply_budget_policy("test")
    assert isinstance(result, bool)


def test_budget_policy_creation():
    """Test BudgetPolicy creation."""
    policy = BudgetPolicy(
        name="test",
        max_cost=50.0,
        provider=Provider.OPENAI,
        period="monthly",
    )
    assert policy.name == "test"
    assert policy.max_cost == 50.0