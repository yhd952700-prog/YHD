"""
Tests for LangGraph Workflow Engine
"""
import pytest
import os
import sys
import json
from unittest.mock import Mock, patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.ai.langgraph_workflow import (
    LangGraphWorkflowEngine,
    GoalTaskGraph,
    Goal,
    Task,
    GoalStatus,
    TaskStatus,
    AgentRole,
    WorkflowState,
    PlannerNode,
    ExecutorNode,
    CriticNode,
    CoordinatorNode,
    should_continue,
    create_workflow_engine,
    create_goal_task_graph,
    LANGGRAPH_AVAILABLE,
)


class TestWorkflowState:
    """Test WorkflowState TypedDict"""

    def test_initial_state(self):
        state: WorkflowState = {
            "goal": None,
            "current_task_id": None,
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 0,
            "max_iterations": 50
        }
        assert state["iteration"] == 0
        assert state["messages"] == []


class TestGoalAndTask:
    """Test Goal and Task dataclasses"""

    def test_goal_creation(self):
        goal = Goal(title="Test Goal", description="Test Description")
        assert goal.title == "Test Goal"
        assert goal.status == GoalStatus.PENDING
        assert len(goal.tasks) == 0

    def test_task_creation(self):
        task = Task(name="Test Task", description="Test Description")
        assert task.name == "Test Task"
        assert task.status == TaskStatus.PENDING
        assert task.agent_role == AgentRole.EXECUTOR

    def test_task_dependencies(self):
        task1 = Task(id="task-1", name="Task 1")
        task2 = Task(id="task-2", name="Task 2", dependencies=["task-1"])
        task3 = Task(id="task-3", name="Task 3", dependencies=["task-1", "task-2"])

        completed = {"task-1"}
        assert task2.is_ready(completed)  # task-1 done, so task-2 IS ready
        assert not task3.is_ready(completed)  # needs both task-1 and task-2

        completed = {"task-1", "task-2"}
        assert task2.is_ready(completed)
        assert task3.is_ready(completed)

    def test_goal_task_management(self):
        goal = Goal(title="Test")
        task1 = Task(id="t1", name="Task 1")
        task2 = Task(id="t2", name="Task 2", dependencies=["t1"])

        goal.add_task(task1)
        goal.add_task(task2)

        assert len(goal.tasks) == 2
        assert goal.get_task("t1") == task1
        assert goal.get_task("t2") == task2
        assert goal.get_task("nonexistent") is None

    def test_goal_status_checks(self):
        goal = Goal(title="Test")
        task1 = Task(id="t1", name="Task 1", status=TaskStatus.COMPLETED)
        task2 = Task(id="t2", name="Task 2", status=TaskStatus.PENDING)

        goal.add_task(task1)
        goal.add_task(task2)

        assert not goal.is_complete()
        assert not goal.has_failed_tasks()

        task2.status = TaskStatus.COMPLETED
        assert goal.is_complete()

        task3 = Task(id="t3", name="Task 3", status=TaskStatus.FAILED, max_retries=0)
        goal.add_task(task3)
        assert goal.has_failed_tasks()


class TestShouldContinue:
    """Test the conditional edge function"""

    def test_continue_on_executing(self):
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        state: WorkflowState = {
            "goal": goal,
            "current_task_id": "task-1",
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 1,
            "max_iterations": 50
        }
        assert should_continue(state) == "continue"

    def test_end_on_completed(self):
        goal = Goal(title="Test", status=GoalStatus.COMPLETED)
        state: WorkflowState = {"goal": goal, "iteration": 1, "max_iterations": 50,
            "current_task_id": None, "messages": [], "agent_outputs": {},
            "human_feedback": None, "error": None}
        assert should_continue(state) == "end"

    def test_end_on_failed(self):
        goal = Goal(title="Test", status=GoalStatus.FAILED)
        state: WorkflowState = {"goal": goal, "iteration": 1, "max_iterations": 50,
            "current_task_id": None, "messages": [], "agent_outputs": {},
            "human_feedback": None, "error": None}
        assert should_continue(state) == "end"

    def test_human_on_requires_human(self):
        goal = Goal(title="Test", status=GoalStatus.REQUIRES_HUMAN)
        state: WorkflowState = {"goal": goal, "iteration": 1, "max_iterations": 50,
            "current_task_id": None, "messages": [], "agent_outputs": {},
            "human_feedback": None, "error": None}
        assert should_continue(state) == "human"

    def test_end_on_error(self):
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        state: WorkflowState = {"goal": goal, "iteration": 1, "max_iterations": 50,
            "current_task_id": "task-1", "messages": [], "agent_outputs": {},
            "human_feedback": None, "error": "Something failed"}
        assert should_continue(state) == "end"

    def test_end_on_max_iterations(self):
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        state: WorkflowState = {"goal": goal, "iteration": 50, "max_iterations": 50,
            "current_task_id": "task-1", "messages": [], "agent_outputs": {},
            "human_feedback": None, "error": None}
        assert should_continue(state) == "end"


class TestAgentNodes:
    """Test individual agent nodes with mocked LLM"""

    def setup_method(self):
        self.mock_llm = Mock()
        self.mock_response = Mock()
        self.mock_response.content = '{"tasks": [{"id": "task-1", "name": "Test Task", "description": "Test", "agent_role": "executor", "dependencies": []}]}'
        self.mock_llm.invoke.return_value = self.mock_response

    def test_planner_node(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

        planner = PlannerNode(llm=self.mock_llm)
        goal = Goal(title="Test Goal", description="Test", status=GoalStatus.DECOMPOSING)

        state: WorkflowState = {
            "goal": goal,
            "current_task_id": None,
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 0,
            "max_iterations": 50
        }

        result = planner.invoke(state)

        assert result["goal"].status == GoalStatus.PLANNING
        assert len(result["goal"].tasks) == 1
        assert result["goal"].tasks[0].name == "Test Task"
        self.mock_llm.invoke.assert_called_once()

    def test_executor_node(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        executor = ExecutorNode(llm=self.mock_llm)
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        task = Task(id="task-1", name="Test Task", description="Execute this", status=TaskStatus.READY)
        goal.add_task(task)

        state: WorkflowState = {
            "goal": goal,
            "current_task_id": "task-1",
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 1,
            "max_iterations": 50
        }

        result = executor.invoke(state)

        assert result["goal"].get_task("task-1").status == TaskStatus.COMPLETED
        assert "task-1" in result["agent_outputs"]
        self.mock_llm.invoke.assert_called_once()

    def test_critic_node_approved(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        self.mock_response.content = "APPROVED: Good work"
        critic = CriticNode(llm=self.mock_llm)
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        task = Task(id="task-1", name="Test Task", status=TaskStatus.COMPLETED, result="Done")
        goal.add_task(task)

        state: WorkflowState = {
            "goal": goal,
            "current_task_id": "task-1",
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 1,
            "max_iterations": 50
        }

        result = critic.invoke(state)

        assert result["goal"].get_task("task-1").status == TaskStatus.COMPLETED
        assert "task-1_critique" in result["agent_outputs"]

    def test_critic_node_needs_revision(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        self.mock_response.content = "NEEDS_REVISION: Missing details"
        critic = CriticNode(llm=self.mock_llm)
        goal = Goal(title="Test", status=GoalStatus.EXECUTING)
        task = Task(id="task-1", name="Test Task", status=TaskStatus.COMPLETED, result="Done", retry_count=0)
        goal.add_task(task)

        state: WorkflowState = {
            "goal": goal,
            "current_task_id": "task-1",
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 1,
            "max_iterations": 50
        }

        result = critic.invoke(state)

        assert result["goal"].get_task("task-1").status == TaskStatus.PENDING
        assert result["goal"].get_task("task-1").retry_count == 1

    def test_coordinator_node_planning_to_executing(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        coordinator = CoordinatorNode(llm=self.mock_llm)
        goal = Goal(title="Test", status=GoalStatus.PLANNING)
        task = Task(id="task-1", name="Task 1", status=TaskStatus.PENDING)
        goal.add_task(task)

        state: WorkflowState = {
            "goal": goal,
            "current_task_id": None,
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 0,
            "max_iterations": 50
        }

        result = coordinator.invoke(state)

        assert result["goal"].status == GoalStatus.EXECUTING
        assert result["current_task_id"] == "task-1"
        assert result["goal"].get_task("task-1").status == TaskStatus.READY


class TestGoalTaskGraphAdapter:
    """Test the backward-compatible GoalTaskGraph adapter"""

    def test_create_goal(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        gtg = create_goal_task_graph()
        goal = gtg.add_goal("Test Goal", "Test Description")

        assert goal.title == "Test Goal"
        assert goal.description == "Test Description"
        assert goal.id in gtg._goals

    def test_get_goal(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        gtg = create_goal_task_graph()
        goal = gtg.add_goal("Test Goal")

        retrieved = gtg.get_goal(goal.id)
        assert retrieved == goal

        assert gtg.get_goal("nonexistent") is None

    def test_decompose_goal(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        gtg = create_goal_task_graph()
        goal = gtg.add_goal("Test Goal", "Create a report")

        # Mock the planner LLM
        with patch.object(gtg.engine.planner, 'llm') as mock_llm:
            mock_response = Mock()
            mock_response.content = '{"tasks": [{"id": "task-1", "name": "Research", "description": "Research topic", "agent_role": "executor", "dependencies": []}, {"id": "task-2", "name": "Write", "description": "Write report", "agent_role": "executor", "dependencies": ["task-1"]}]}'
            mock_llm.invoke.return_value = mock_response

            tasks = gtg.decompose_goal(goal.id)

            assert len(tasks) == 2
            assert tasks[0].name == "Research"
            assert tasks[1].name == "Write"
            assert tasks[1].dependencies == ["task-1"]

    def test_visualize_goal(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        gtg = create_goal_task_graph()
        goal = gtg.add_goal("Test Goal")
        task1 = Task(id="task-1", name="Task 1", status=TaskStatus.COMPLETED)
        task2 = Task(id="task-2", name="Task 2", dependencies=["task-1"], status=TaskStatus.PENDING)
        goal.add_task(task1)
        goal.add_task(task2)

        mermaid = gtg.visualize_goal(goal.id)

        assert "graph TD" in mermaid
        assert "Task 1" in mermaid
        assert "Task 2" in mermaid
        assert "task-1" in mermaid or "task-2" in mermaid


class TestLangGraphWorkflowEngine:
    """Test the main workflow engine"""

    def test_engine_creation(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        engine = create_workflow_engine()
        assert engine is not None
        assert engine.app is not None
        assert engine.planner is not None
        assert engine.executor is not None
        assert engine.critic is not None
        assert engine.coordinator is not None

    def test_visualize_graph(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        engine = create_workflow_engine()
        mermaid = engine.visualize()

        assert "graph TD" in mermaid
        assert "planner" in mermaid
        assert "executor" in mermaid
        assert "critic" in mermaid
        assert "coordinator" in mermaid

    def test_get_state(self):
        if not LANGGRAPH_AVAILABLE:
            pytest.skip("LangGraph not available")

        engine = create_workflow_engine()
        state = engine.get_state("nonexistent")
        assert state is None


class TestIntegration:
    """Integration tests with mocked LLM"""

    @pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="LangGraph not available")
    def test_full_workflow_mock(self):
        """Test full workflow execution with mocked LLM"""
        engine = create_workflow_engine()

        goal = Goal(
            title="Simple Goal",
            description="Test goal",
            status=GoalStatus.DECOMPOSING
        )

        # Mock all LLM calls
        with patch.object(engine.planner, 'llm') as mock_planner_llm, \
             patch.object(engine.executor, 'llm') as mock_executor_llm, \
             patch.object(engine.critic, 'llm') as mock_critic_llm:

            # Planner response
            planner_response = Mock()
            planner_response.content = json.dumps({
                "tasks": [
                    {"id": "task-1", "name": "Step 1", "description": "First step", "agent_role": "executor", "dependencies": []},
                    {"id": "task-2", "name": "Step 2", "description": "Second step", "agent_role": "executor", "dependencies": ["task-1"]}
                ]
            })
            mock_planner_llm.invoke.return_value = planner_response

            # Executor responses
            exec_response = Mock()
            exec_response.content = "Task completed successfully"
            mock_executor_llm.invoke.return_value = exec_response

            # Critic responses
            critic_response = Mock()
            critic_response.content = "APPROVED: Good"
            mock_critic_llm.invoke.return_value = critic_response

            # Execute
            result = engine.execute_goal(goal, thread_id="test-thread")

            assert result.status == GoalStatus.COMPLETED
            assert len(result.tasks) == 2
            assert all(t.status == TaskStatus.COMPLETED for t in result.tasks)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])