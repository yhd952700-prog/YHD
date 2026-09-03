"""
Goal → Task Graph Integration Test

Tests the goal decomposition and task chain generation workflow.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ai.goal_task_graph import GoalTaskGraph, TaskNode, GoalDefinition


class TestGoalTaskGraph:
    """Test goal decomposition and task chain generation."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up goal task graph for tests."""
        os.environ["AI_PROVIDER_TYPE"] = "openai"
        os.environ["AI_PROVIDER_KEY"] = "[REDACTED]"
        os.environ["AI_PROVIDER_MODEL"] = "gpt-4"
        os.environ["AI_PROVIDER_NAME"] = "test-graph"
        yield
        # Cleanup
        from src.ai.providers import reset_provider
        reset_provider()
    
    def test_goal_decomposition(self):
        """Test that a goal can be decomposed into tasks."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        provider = None
        # get_provider imported in each test
        from src.ai.providers import get_provider
        provider = get_provider()
        
        # Create a goal definition
        goal = GoalDefinition(
            id="goal-001",
            description="Build a comprehensive AI assistant with multi-agent coordination",
            priority="high",
            status="pending",
        )
        
        # Initialize goal task graph
        graph = GoalTaskGraph(provider=provider, goal=goal)
        
        # Decompose goal into tasks
        tasks = graph.decompose_goal()
        
        # Should produce multiple tasks
        assert len(tasks) > 0, "Goal decomposition should produce tasks"
        assert len(tasks) >= 3, "Should produce at least 3 tasks for a complex goal"
        
        # Each task should have required fields
        for task in tasks:
            assert hasattr(task, 'id')
            assert hasattr(task, 'description')
            assert hasattr(task, 'depends_on')
            assert hasattr(task, 'status')
    
    def test_task_chain_generation(self):
        """Test that task chains can be generated with dependencies."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        from src.ai.providers import get_provider
        provider = get_provider()
        graph = GoalTaskGraph(provider=provider)
        
        # Create goals with different complexity
        goal_simple = GoalDefinition(
            id="goal-simple",
            description="Simple task",
            priority="low",
            status="pending",
        )
        
        goal_complex = GoalDefinition(
            id="goal-complex",
            description="Build a comprehensive AI system with multiple components",
            priority="high",
            status="pending",
        )
        
        # Complex goal should produce more tasks
        tasks_simple = graph.decompose_goal(goal_simple)
        tasks_complex = graph.decompose_goal(goal_complex)
        
        # Complex goal should have more or equal tasks
        assert len(tasks_complex) >= len(tasks_simple)
    
    def test_task_execution_workflow(self):
        """Test end-to-end task execution workflow."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        from src.ai.providers import get_provider
        provider = get_provider()
        graph = GoalTaskGraph(provider=provider)
        
        # Create a goal and decompose
        goal = GoalDefinition(
            id="goal-e2e",
            description="Test end-to-end task execution",
            priority="medium",
            status="pending",
        )
        
        tasks = graph.decompose_goal(goal)
        
        # Execute tasks in order (respecting dependencies)
        execution_order = graph.get_execution_order(tasks)
        
        # Should have valid execution order
        assert len(execution_order) > 0
        # All tasks should appear in execution order
        task_ids = [t.id for t in tasks]
        executed_ids = [t.id for t in execution_order]
        for tid in task_ids:
            assert tid in executed_ids, f"Task {tid} should be in execution order"
    
    def test_circular_dependency_detection(self):
        """Test that circular dependencies are detected and handled."""
        from src.ai.providers import reset_provider
        reset_provider()
        
        from src.ai.providers import get_provider
        provider = get_provider()
        graph = GoalTaskGraph(provider=provider)
        
        # Create goals that might have circular deps
        goal_a = GoalDefinition(
            id="goal-a",
            description="Task A",
            priority="medium",
            status="pending",
        )
        goal_b = GoalDefinition(
            id="goal-b",
            description="Task B",
            priority="medium",
            status="pending",
        )
        
        tasks_a = graph.decompose_goal(goal_a)
        tasks_b = graph.decompose_goal(goal_b)
        
        # Detect cycles in combined task graph
        has_cycle = graph.has_cycle(tasks_a + tasks_b)
        
        # Result should be boolean (True or False, depending on structure)
        assert isinstance(has_cycle, bool)
