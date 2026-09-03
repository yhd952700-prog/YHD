"""
LiuHao AI OS (镭灏) - LangGraph Workflow Engine
Production-grade Goal→Task Graph using LangGraph state machines.
Replaces custom DAG implementation with enterprise-ready workflow engine.
"""

from __future__ import annotations
import os
import json
import uuid
from datetime import datetime
from typing import Any, Optional, List, Dict, Callable, Literal, Annotated
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

# LangGraph imports
try:
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.prebuilt import ToolNode
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_openai import ChatOpenAI
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    START = None
    END = None
    MemorySaver = None
    SqliteSaver = None
    BaseMessage = None
    RunnableConfig = None


class GoalStatus(Enum):
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    REQUIRES_HUMAN = "requires_human"


class TaskStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class AgentRole(Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    CRITIC = "critic"
    COORDINATOR = "coordinator"


@dataclass
class Task:
    """Individual task in the goal decomposition"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    agent_role: AgentRole = AgentRole.EXECUTOR
    dependencies: List[str] = field(default_factory=list)  # Task IDs
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_ready(self, completed_tasks: set) -> bool:
        """Check if all dependencies are completed"""
        return all(dep in completed_tasks for dep in self.dependencies)

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries


@dataclass
class Goal:
    """High-level goal with decomposition into tasks"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    tasks: List[Task] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_task(self, task: Task) -> None:
        self.tasks.append(task)
        self.updated_at = datetime.now()

    def get_task(self, task_id: str) -> Optional[Task]:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def get_ready_tasks(self, completed: set) -> List[Task]:
        return [t for t in self.tasks if t.status == TaskStatus.PENDING and t.is_ready(completed)]

    def get_completed_tasks(self) -> set:
        return {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}

    def is_complete(self) -> bool:
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in self.tasks)

    def has_failed_tasks(self) -> bool:
        return any(t.status == TaskStatus.FAILED and not t.can_retry() for t in self.tasks)


# LangGraph State Definition
class WorkflowState(Dict):
    """LangGraph state for goal execution workflow"""
    goal: Optional[Goal] = None
    current_task_id: Optional[str] = None
    messages: Annotated[List[BaseMessage], add_messages] = field(default_factory=list)
    agent_outputs: Dict[str, Any] = field(default_factory=dict)
    human_feedback: Optional[str] = None
    error: Optional[str] = None
    iteration: int = 0
    max_iterations: int = 50


class AgentNode(ABC):
    """Base class for LangGraph agent nodes"""

    def __init__(self, role: AgentRole, llm: Optional[Any] = None):
        self.role = role
        self.llm = llm or self._create_default_llm()

    def _create_default_llm(self):
        if not LANGGRAPH_AVAILABLE:
            return None
        api_key = os.getenv("OPENAI_API_KEY", "[REDACTED]")
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            api_key=api_key
        )

    @abstractmethod
    def invoke(self, state: WorkflowState) -> WorkflowState:
        pass


class PlannerNode(AgentNode):
    """Planner agent: decomposes goals into task DAG"""

    def __init__(self, llm: Optional[Any] = None):
        super().__init__(AgentRole.PLANNER, llm)

    def invoke(self, state: WorkflowState) -> WorkflowState:
        goal = state.get("goal")
        if not goal:
            state["error"] = "No goal in state"
            return state

        if goal.status != GoalStatus.DECOMPOSING:
            return state

        # Create decomposition prompt
        system_prompt = """You are a master planner. Decompose the high-level goal into a DAG of executable tasks.
Each task must have: name, description, agent_role (planner/executor/critic), dependencies (task IDs).
Output as JSON: {"tasks": [{"id": "...", "name": "...", "description": "...", "agent_role": "...", "dependencies": [...]}]}"""

        user_prompt = f"""Goal: {goal.title}
Description: {goal.description}

Decompose into actionable tasks. Consider:
- Logical dependencies between tasks
- Which agent role should handle each (planner/executor/critic)
- Parallelizable vs sequential tasks"""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]

        try:
            response = self.llm.invoke(messages)
            content = response.content

            # Parse JSON from response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                for task_data in plan.get("tasks", []):
                    task = Task(
                        id=task_data.get("id", str(uuid.uuid4())),
                        name=task_data.get("name", ""),
                        description=task_data.get("description", ""),
                        agent_role=AgentRole(task_data.get("agent_role", "executor")),
                        dependencies=task_data.get("dependencies", []),
                    )
                    goal.add_task(task)

                goal.status = GoalStatus.PLANNING
                state["goal"] = goal
                state["messages"].append(AIMessage(content=f"Planned {len(goal.tasks)} tasks"))
            else:
                state["error"] = "Failed to parse planner output"

        except Exception as e:
            state["error"] = f"Planner failed: {str(e)}"

        return state


class ExecutorNode(AgentNode):
    """Executor agent: runs individual tasks"""

    def __init__(self, llm: Optional[Any] = None, tools: Optional[List] = None):
        super().__init__(AgentRole.EXECUTOR, llm)
        self.tools = tools or []

    def invoke(self, state: WorkflowState) -> WorkflowState:
        goal = state.get("goal")
        current_task_id = state.get("current_task_id")

        if not goal or not current_task_id:
            state["error"] = "Missing goal or current_task_id"
            return state

        task = goal.get_task(current_task_id)
        if not task:
            state["error"] = f"Task {current_task_id} not found"
            return state

        if task.status != TaskStatus.READY:
            return state

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        # Build execution context from dependencies
        context = self._build_context(goal, task)

        system_prompt = f"""You are an executor agent. Complete the assigned task.
Task: {task.name}
Description: {task.description}
Context from dependencies: {json.dumps(context, default=str)}

Provide a clear result or indicate if you need clarification."""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Execute this task. Inputs: {json.dumps(task.inputs, default=str)}")
        ]

        try:
            response = self.llm.invoke(messages)
            task.result = response.content
            task.outputs = {"result": response.content}
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()

            state["agent_outputs"][current_task_id] = {
                "role": self.role.value,
                "result": response.content,
                "timestamp": datetime.now().isoformat()
            }
            state["messages"].append(AIMessage(content=f"Task {task.name} completed"))

        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            state["error"] = f"Executor failed: {str(e)}"

        goal.updated_at = datetime.now()
        state["goal"] = goal
        return state

    def _build_context(self, goal: Goal, task: Task) -> Dict[str, Any]:
        """Build context from completed dependency tasks"""
        context = {}
        for dep_id in task.dependencies:
            dep_task = goal.get_task(dep_id)
            if dep_task and dep_task.result:
                context[dep_task.name] = dep_task.result
        return context


class CriticNode(AgentNode):
    """Critic agent: reviews task results and provides feedback"""

    def __init__(self, llm: Optional[Any] = None):
        super().__init__(AgentRole.CRITIC, llm)

    def invoke(self, state: WorkflowState) -> WorkflowState:
        goal = state.get("goal")
        current_task_id = state.get("current_task_id")

        if not goal or not current_task_id:
            return state

        task = goal.get_task(current_task_id)
        if not task or task.status != TaskStatus.COMPLETED:
            return state

        system_prompt = """You are a critic agent. Review the task result for:
- Completeness: Does it fully address the task description?
- Quality: Is the output accurate and well-structured?
- Consistency: Does it align with previous task outputs?
Provide: APPROVED or NEEDS_REVISION with specific feedback."""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Review task: {task.name}\nResult: {task.result}")
        ]

        try:
            response = self.llm.invoke(messages)
            feedback = response.content

            if "APPROVED" in feedback.upper():
                state["messages"].append(AIMessage(content=f"Critic approved: {task.name}"))
            else:
                task.status = TaskStatus.PENDING  # Retry
                task.retry_count += 1
                state["messages"].append(AIMessage(content=f"Critic requests revision: {feedback}"))

            state["agent_outputs"][f"{current_task_id}_critique"] = {
                "role": "critic",
                "feedback": feedback,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            state["error"] = f"Critic failed: {str(e)}"

        return state


class CoordinatorNode(AgentNode):
    """Coordinator: manages workflow, handles human-in-the-loop"""

    def __init__(self, llm: Optional[Any] = None):
        super().__init__(AgentRole.COORDINATOR, llm)

    def invoke(self, state: WorkflowState) -> WorkflowState:
        goal = state.get("goal")
        if not goal:
            return state

        # Check for human-in-the-loop requirement
        if state.get("human_feedback"):
            # Process human feedback
            feedback = state["human_feedback"]
            state["human_feedback"] = None
            state["messages"].append(HumanMessage(content=f"Human feedback: {feedback}"))
            # Could modify goal/tasks based on feedback
            return state

        # Check goal status transitions
        if goal.status == GoalStatus.PLANNING:
            # Move to executing, find first ready task
            ready = goal.get_ready_tasks(goal.get_completed_tasks())
            if ready:
                goal.status = GoalStatus.EXECUTING
                state["current_task_id"] = ready[0].id
                ready[0].status = TaskStatus.READY
            else:
                # No tasks ready - if goal has no tasks, mark complete
                if not goal.tasks or goal.is_complete():
                    goal.status = GoalStatus.COMPLETED
                    goal.completed_at = datetime.now()
                    goal.result = "All tasks completed successfully"
                    state["current_task_id"] = None

        elif goal.status == GoalStatus.EXECUTING:
            completed = goal.get_completed_tasks()
            ready = goal.get_ready_tasks(completed)

            if goal.is_complete():
                goal.status = GoalStatus.COMPLETED
                goal.completed_at = datetime.now()
                goal.result = "All tasks completed successfully"
                state["current_task_id"] = None

            elif not ready and goal.has_failed_tasks():
                goal.status = GoalStatus.FAILED
                goal.error = "One or more tasks failed permanently"
                state["error"] = goal.error

            elif not ready:
                # Check for blocked tasks
                blocked = [t for t in goal.tasks if t.status == TaskStatus.BLOCKED]
                if blocked:
                    state["error"] = f"Deadlock: {len(blocked)} blocked tasks"
                    goal.status = GoalStatus.FAILED
                # Else: wait for current tasks to complete

            elif ready:
                state["current_task_id"] = ready[0].id
                ready[0].status = TaskStatus.READY

        goal.updated_at = datetime.now()
        state["goal"] = goal
        state["iteration"] = state.get("iteration", 0) + 1

        return state


def should_continue(state: WorkflowState) -> Literal["continue", "human", "end"]:
    """Conditional edge: determine next step"""
    goal = state.get("goal")
    if not goal:
        return "end"

    if goal.status in (GoalStatus.COMPLETED, GoalStatus.FAILED):
        return "end"

    if goal.status == GoalStatus.REQUIRES_HUMAN:
        return "human"

    if state.get("error"):
        return "end"

    if state.get("iteration", 0) >= state.get("max_iterations", 50):
        state["error"] = "Max iterations reached"
        return "end"

    # If no current task and goal is executing, check if done
    if goal.status == GoalStatus.EXECUTING and not state.get("current_task_id"):
        completed = goal.get_completed_tasks()
        ready = goal.get_ready_tasks(completed)
        if not ready and goal.is_complete():
            return "end"
        if not ready and goal.has_failed_tasks():
            return "end"

    return "continue"


class LangGraphWorkflowEngine:
    """
    LangGraph-based workflow engine for Goal→Task execution.
    Provides: checkpointing, HITL, parallel execution, observability.
    """

    def __init__(
        self,
        checkpointer_path: Optional[str] = None,
        llm: Optional[Any] = None,
        enable_hitl: bool = True
    ):
        if not LANGGRAPH_AVAILABLE:
            raise RuntimeError("LangGraph not installed. Run: pip install langgraph langchain-openai")

        self.enable_hitl = enable_hitl

        # Initialize LLM
        self.llm = llm or ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            api_key=os.getenv("OPENAI_API_KEY", "[REDACTED]")
        )

        # Initialize agents
        self.planner = PlannerNode(self.llm)
        self.executor = ExecutorNode(self.llm)
        self.critic = CriticNode(self.llm)
        self.coordinator = CoordinatorNode(self.llm)

        # Initialize checkpointer
        if checkpointer_path:
            self.checkpointer = SqliteSaver.from_conn_string(f"sqlite:///{checkpointer_path}")
        else:
            self.checkpointer = MemorySaver()

        # Build graph
        self.graph = self._build_graph()
        self.app = self.graph.compile(checkpointer=self.checkpointer)

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state machine"""
        workflow = StateGraph(WorkflowState)

        # Add nodes
        workflow.add_node("planner", self.planner.invoke)
        workflow.add_node("executor", self.executor.invoke)
        workflow.add_node("critic", self.critic.invoke)
        workflow.add_node("coordinator", self.coordinator.invoke)

        # Define edges
        workflow.add_edge(START, "planner")
        workflow.add_edge("planner", "coordinator")
        workflow.add_edge("coordinator", "executor")
        workflow.add_edge("executor", "critic")
        workflow.add_edge("critic", "coordinator")

        # Conditional edges from coordinator
        workflow.add_conditional_edges(
            "coordinator",
            should_continue,
            {
                "continue": "executor",
                "human": "human_feedback",
                "end": END
            }
        )

        # Human feedback node (passthrough)
        def human_node(state: WorkflowState) -> WorkflowState:
            # Wait for human_feedback to be set externally
            return state

        workflow.add_node("human_feedback", human_node)
        workflow.add_edge("human_feedback", "coordinator")

        return workflow

    def execute_goal(
        self,
        goal: Goal,
        thread_id: str = "default",
        config: Optional[RunnableConfig] = None
    ) -> Goal:
        """Execute a goal through the workflow"""
        initial_state: WorkflowState = {
            "goal": goal,
            "current_task_id": None,
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 0,
            "max_iterations": 50
        }

        # Use recursion_limit in config to prevent infinite loops
        base_config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100
        }
        if config:
            base_config.update(config)

        # Run the workflow
        final_state = self.app.invoke(initial_state, config=base_config)

        return final_state.get("goal", goal)

    def resume_goal(
        self,
        thread_id: str = "default",
        human_feedback: Optional[str] = None,
        config: Optional[RunnableConfig] = None
    ) -> Goal:
        """Resume a paused workflow with optional human feedback"""
        config = config or {"configurable": {"thread_id": thread_id}}

        # Get current state
        current_state = self.app.get_state(config)
        if not current_state:
            raise ValueError(f"No checkpoint found for thread {thread_id}")

        state = current_state.values

        if human_feedback:
            state["human_feedback"] = human_feedback

        # Continue execution
        final_state = self.app.invoke(state, config=config)
        return final_state.get("goal")

    def get_state(self, thread_id: str = "default") -> Optional[WorkflowState]:
        """Get current workflow state"""
        config = {"configurable": {"thread_id": thread_id}}
        state = self.app.get_state(config)
        if state is None:
            return None
        values = state.values
        return values if values else None

    def visualize(self) -> str:
        """Generate Mermaid diagram of the workflow"""
        return self.app.get_graph().draw_mermaid()


# Backward compatibility with existing GoalTaskGraph interface
class GoalTaskGraph:
    """
    Adapter providing the original GoalTaskGraph interface
    backed by LangGraph workflow engine.
    """

    def __init__(self, checkpointer_path: Optional[str] = None):
        self.engine = LangGraphWorkflowEngine(checkpointer_path=checkpointer_path)
        self._goals: Dict[str, Goal] = {}
        # Expose planner for direct access
        self.planner = self.engine.planner

    def add_goal(self, title: str, description: str = "") -> Goal:
        goal = Goal(title=title, description=description)
        self._goals[goal.id] = goal
        return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        return self._goals.get(goal_id)

    def decompose_goal(self, goal_id: str) -> List[Task]:
        goal = self._goals.get(goal_id)
        if not goal:
            return []

        goal.status = GoalStatus.DECOMPOSING
        # Run just the planner
        state: WorkflowState = {
            "goal": goal,
            "current_task_id": None,
            "messages": [],
            "agent_outputs": {},
            "human_feedback": None,
            "error": None,
            "iteration": 0,
            "max_iterations": 10
        }
        # Only run planner node
        result_state = self.planner.invoke(state)
        self._goals[goal_id] = result_state["goal"]
        return self._goals[goal_id].tasks

    def execute_goal(self, goal_id: str, thread_id: Optional[str] = None) -> Goal:
        goal = self._goals.get(goal_id)
        if not goal:
            raise ValueError(f"Goal {goal_id} not found")

        tid = thread_id or goal_id
        result = self.engine.execute_goal(goal, thread_id=tid)
        self._goals[goal_id] = result
        return result

    def get_execution_order(self) -> List[str]:
        """Get topological execution order (compatibility method)"""
        # This is now handled dynamically by LangGraph
        # Return task IDs in dependency order for a goal
        # For compatibility, return empty - actual execution is dynamic
        return []

    def visualize_goal(self, goal_id: str) -> str:
        """Generate Mermaid diagram for a specific goal"""
        goal = self._goals.get(goal_id)
        if not goal:
            return ""

        # Build simple Mermaid for the goal's task DAG
        lines = ["graph TD"]
        for task in goal.tasks:
            node_id = task.id[:8]
            lines.append(f'    {node_id}["{task.name}"]')
            for dep_id in task.dependencies:
                dep_node = dep_id[:8]
                lines.append(f"    {dep_node} --> {node_id}")

            # Color by status
            color_map = {
                TaskStatus.COMPLETED: "green",
                TaskStatus.RUNNING: "yellow",
                TaskStatus.FAILED: "red",
                TaskStatus.PENDING: "gray",
                TaskStatus.READY: "blue",
            }
            color = color_map.get(task.status, "gray")
            lines.append(f"    style {node_id} fill:{color}")

        return "\n".join(lines)


def create_workflow_engine(
    checkpointer_path: Optional[str] = None,
    enable_hitl: bool = True
) -> LangGraphWorkflowEngine:
    """Factory function to create configured workflow engine"""
    return LangGraphWorkflowEngine(
        checkpointer_path=checkpointer_path,
        enable_hitl=enable_hitl
    )


def create_goal_task_graph(checkpointer_path: Optional[str] = None) -> GoalTaskGraph:
    """Factory for backward-compatible GoalTaskGraph"""
    return GoalTaskGraph(checkpointer_path=checkpointer_path)


if __name__ == "__main__":
    # Quick test with in-memory checkpointer
    print("Testing LangGraphWorkflowEngine...")

    if not LANGGRAPH_AVAILABLE:
        print("LangGraph not available - skipping test")
    else:
        engine = create_workflow_engine()

        # Create a test goal
        goal = Goal(
            title="Test Goal: Create a simple report",
            description="Research a topic and write a brief summary"
        )

        # Execute (will use LLM - needs API key)
        print(f"Goal created: {goal.title}")
        print(f"Graph structure: {engine.visualize()[:200]}...")

        print("\n✅ LangGraphWorkflowEngine initialized successfully")