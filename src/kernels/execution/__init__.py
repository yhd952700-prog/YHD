"""Execution Kernel — Goal→Task→Plan→Action→Verify

The Execution Kernel orchestrates the full execution pipeline:
Goal → Task Decomposition → Execution Plan → Action → Verify.
Supports parallel execution, retries, checkpoints, and feedback loops.

依据 Definition Lock §112: Execution Kernel 必须能够
- Decompose natural language goals into structured tasks
- Create executable plans with dependencies
- Execute actions with retry/circuit breaker
- Verify outcomes against criteria
- Support checkpoints and rollback
- Emit events for traceability
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid

# Import dependencies
from src.kernels.context import ContextKernel, ContextInput, ContextInputType, create_context_kernel  # noqa: F401
from src.kernels.capability import get_capability_registry, CapabilityScope, check_capability_scope
from src.kernels.event import get_event_bus, publish_event, EventScope, EventPriority
from src.kernels.resource import get_resource_manager, ResourceType, allocate_resource, release_resource  # noqa: F401
from src.kernels._crosscutting import kernel_action


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class PlanStatus(str, Enum):
    """Execution plan status."""
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class VerifyResult(str, Enum):
    """Verification outcome."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    OFF_TRACK = "off_track"
    NEEDS_HUMAN = "needs_human"


@dataclass
class Goal:
    """Natural language execution goal."""
    id: str
    natural_language: str
    scope: str = "L1"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Task:
    """Decomposed task from goal."""
    id: str
    goal_id: str
    name: str
    description: str
    capability_id: str  # Required capability to execute
    capability_namespace: str = "kernel"
    capability_version: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)  # Task IDs that must complete first
    inputs: Dict[str, Any] = field(default_factory=dict)
    expected_outputs: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    scope: str = "L1"
    max_retries: int = 3
    retry_count: int = 0
    timeout_seconds: int = 300
    assigned_agent: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ExecutionPlan:
    """Execution plan with ordered tasks."""
    id: str
    goal_id: str
    tasks: List[Task] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def get_ready_tasks(self, completed_task_ids: Set[str]) -> List[Task]:
        """Get tasks whose dependencies are all satisfied."""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep_id in completed_task_ids for dep_id in task.dependencies):
                ready.append(task)
        return ready

    def get_task(self, task_id: str) -> Optional[Task]:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None


@dataclass
class Action:
    """Executable action from a task."""
    id: str
    task_id: str
    capability_id: str
    capability_namespace: str
    inputs: Dict[str, Any]
    correlation_id: str
    scope: str
    timeout_seconds: int


@dataclass
class ActionResult:
    """Result of an action execution."""
    action_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    retry_count: int = 0


@dataclass
class VerificationResult:
    """Result of outcome verification."""
    task_id: str
    result: VerifyResult
    score: float = 0.0  # 0.0 - 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    feedback: str = ""
    replan_required: bool = False
    escalation_required: bool = False


@dataclass
class ExecutionContext:
    """Full execution context for a goal."""
    goal: Goal
    plan: ExecutionPlan
    context_kernel: ContextKernel
    completed_tasks: Set[str] = field(default_factory=set)
    failed_tasks: Set[str] = field(default_factory=set)
    task_results: Dict[str, ActionResult] = field(default_factory=dict)
    verification_results: Dict[str, VerificationResult] = field(default_factory=dict)
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)


class GoalDecomposer:
    """Decomposes natural language goals into structured tasks."""

    def __init__(self):
        self.capability_registry = get_capability_registry()

    def decompose(self, goal: Goal) -> List[Task]:
        """Decompose goal into tasks using capability registry."""
        # This is a simplified decomposition - in production would use LLM
        tasks = []

        # Analyze goal for keywords to determine required capabilities
        goal_lower = goal.natural_language.lower()

        # Example decomposition patterns
        if any(kw in goal_lower for kw in ["search", "find", "lookup", "query"]):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Search",
                description="Search for information",
                capability_id="network_bus",
                capability_namespace="kernel",
                inputs={"query": goal.natural_language},
                scope=goal.scope,
            ))

        if any(kw in goal_lower for kw in ["plan", "schedule", "organize"]):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Plan",
                description="Create execution plan",
                capability_id="execution_pipeline",
                capability_namespace="kernel",
                inputs={"goal": goal.natural_language},
                scope=goal.scope,
            ))

        if any(kw in goal_lower for kw in ["remember", "store", "save", "memory"]):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Memory Store",
                description="Store in memory",
                capability_id="multi_tier_memory",
                capability_namespace="kernel",
                inputs={"content": goal.natural_language},
                scope=goal.scope,
            ))

        # Default: at least one generic task
        if not tasks:
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Execute",
                description="Execute goal",
                capability_id="execution_pipeline",
                capability_namespace="kernel",
                inputs={"goal": goal.natural_language},
                scope=goal.scope,
            ))

        # Emit event
        publish_event(
            type="goal_decomposed",
            source="execution_kernel",
            data={"goal_id": goal.id, "task_count": len(tasks)},
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        return tasks


class PlanBuilder:
    """Builds execution plans from tasks."""

    def __init__(self):
        pass

    def build(self, goal: Goal, tasks: List[Task], mode: str = "parallel") -> ExecutionPlan:
        """Build execution plan from tasks."""
        plan = ExecutionPlan(
            id=str(uuid.uuid4())[:8],
            goal_id=goal.id,
            tasks=tasks,
        )

        # Set up dependencies based on mode
        if mode == "sequential":
            # Chain tasks sequentially
            for i in range(1, len(tasks)):
                tasks[i].dependencies.append(tasks[i - 1].id)
        elif mode == "parallel":
            # No dependencies - all can run in parallel
            pass
        elif mode == "auto":
            # Smart dependency detection based on inputs/outputs
            for i, task in enumerate(tasks):
                for j, other in enumerate(tasks):
                    if i != j and self._output_feeds_input(other, task):
                        task.dependencies.append(other.id)

        # Emit event
        publish_event(
            type="plan_created",
            source="execution_kernel",
            data={"plan_id": plan.id, "goal_id": goal.id, "task_count": len(tasks), "mode": mode},
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        return plan

    def _output_feeds_input(self, producer: Task, consumer: Task) -> bool:
        """Check if producer's output feeds consumer's input."""
        # Simplified: check if any expected output key matches input key
        producer_outputs = set(producer.expected_outputs.keys())
        consumer_inputs = set(consumer.inputs.keys())
        return bool(producer_outputs & consumer_inputs)


class ActionExecutor:
    """Executes actions via capability registry."""

    def __init__(self):
        self.capability_registry = get_capability_registry()
        self.event_bus = get_event_bus()

    @kernel_action("execution.execute")
    def execute(self, action: Action) -> ActionResult:
        """Execute a single action."""
        start_time = datetime.utcnow()

        # Check capability scope
        scope_check = check_capability_scope(
            action.capability_id,
            CapabilityScope(action.scope),
            action.capability_namespace
        )

        if not scope_check.allowed:
            return ActionResult(
                action_id=action.id,
                success=False,
                error=f"Scope check failed: {scope_check.reason}",
                duration_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
            )

        # In production, this would invoke the actual capability
        # For now, simulate execution
        try:
            # Simulate capability execution
            output = self._simulate_capability(action.capability_id, action.inputs)

            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Emit event
            publish_event(
                type="action_completed",
                source="execution_kernel",
                data={"action_id": action.id, "task_id": action.task_id, "success": True},
                correlation_id=action.correlation_id,
                scope=EventScope(action.scope),
            )

            return ActionResult(
                action_id=action.id,
                success=True,
                output=output,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            publish_event(
                type="action_failed",
                source="execution_kernel",
                data={"action_id": action.id, "task_id": action.task_id, "error": str(e)},
                correlation_id=action.correlation_id,
                scope=EventScope(action.scope),
                priority=EventPriority.HIGH,
            )

            return ActionResult(
                action_id=action.id,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _simulate_capability(self, capability_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate capability execution (replace with real invocation)."""
        # This would call the actual capability implementation
        return {
            "capability": capability_id,
            "status": "simulated",
            "inputs_received": list(inputs.keys()),
            "timestamp": datetime.utcnow().isoformat(),
        }


class Verifier:
    """Verifies task outcomes against criteria."""

    def __init__(self):
        pass

    def verify(
        self,
        task: Task,
        action_result: ActionResult,
        criteria: Optional[Dict[str, Any]] = None
    ) -> VerificationResult:
        """Verify task outcome."""
        if not action_result.success:
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.FAILED,
                score=0.0,
                feedback=f"Action failed: {action_result.error}",
                replan_required=True,
            )

        # Check against expected outputs.
        # Goal-level criteria act as base criteria; the task's own
        # expected_outputs refine/override them where both define the
        # same key.
        expected: Dict[str, Any] = dict(criteria) if criteria else {}
        if task.expected_outputs:
            expected.update(task.expected_outputs)
        actual = action_result.output

        if not expected:
            # No criteria - assume success if action succeeded
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.SUCCESS,
                score=1.0,
                feedback="Action completed successfully (no verification criteria)",
            )

        # Simple matching
        matches = 0
        total = len(expected)
        details = {}

        for key, expected_value in expected.items():
            if key in actual:
                actual_value = actual[key]
                if self._values_match(expected_value, actual_value):
                    matches += 1
                    details[key] = "match"
                else:
                    details[key] = f"mismatch: expected {expected_value}, got {actual_value}"
            else:
                details[key] = "missing"

        score = matches / total if total > 0 else 1.0

        if score == 1.0:
            result = VerifyResult.SUCCESS
            feedback = "All criteria matched"
        elif score >= 0.7:
            result = VerifyResult.PARTIAL
            feedback = f"Partial match: {matches}/{total} criteria"
        elif score >= 0.3:
            result = VerifyResult.OFF_TRACK
            feedback = f"Off track: {matches}/{total} criteria"
        else:
            result = VerifyResult.FAILED
            feedback = f"Failed: {matches}/{total} criteria"

        return VerificationResult(
            task_id=task.id,
            result=result,
            score=score,
            details=details,
            feedback=feedback,
            replan_required=score < 0.7,
        )

    def _values_match(self, expected: Any, actual: Any) -> bool:
        """Check if values match (with type coercion)."""
        if expected == actual:
            return True
        # Type coercion for common cases
        try:
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                return abs(expected - actual) < 0.001
            if isinstance(expected, str) and isinstance(actual, str):
                return expected.lower() == actual.lower()
        except (TypeError, ValueError):
            pass
        return False


class ExecutionEngine:
    """Main execution engine orchestrating the full pipeline."""

    def __init__(self, scope: str = "L1"):
        self.scope = scope
        self.decomposer = GoalDecomposer()
        self.planner = PlanBuilder()
        self.executor = ActionExecutor()
        self.verifier = Verifier()
        self.context_kernel = create_context_kernel(scope=scope)

    def execute_goal(
        self,
        goal: Goal,
        plan_mode: str = "auto",
        verification_criteria: Optional[Dict[str, Any]] = None
    ) -> ExecutionContext:
        """Execute a goal through the full pipeline."""
        ctx = ExecutionContext(goal=goal, plan=None, context_kernel=self.context_kernel)

        # Step 1: Decompose
        publish_event(
            type="execution_started",
            source="execution_kernel",
            data={"goal_id": goal.id, "goal": goal.natural_language},
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        tasks = self.decomposer.decompose(goal)

        # Step 2: Plan
        plan = self.planner.build(goal, tasks, mode=plan_mode)
        ctx.plan = plan
        plan.status = PlanStatus.ACTIVE
        plan.started_at = datetime.utcnow()

        # Step 3: Execute
        self._execute_plan(ctx, verification_criteria)

        # Step 4: Final verification
        plan.completed_at = datetime.utcnow()
        if ctx.failed_tasks:
            plan.status = PlanStatus.FAILED if len(ctx.failed_tasks) == len(ctx.plan.tasks) else PlanStatus.PARTIAL
        else:
            plan.status = PlanStatus.COMPLETED

        publish_event(
            type="execution_completed",
            source="execution_kernel",
            data={
                "goal_id": goal.id,
                "plan_id": plan.id,
                "status": plan.status.value,
                "completed": len(ctx.completed_tasks),
                "failed": len(ctx.failed_tasks),
            },
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        return ctx

    def _execute_plan(
        self,
        ctx: ExecutionContext,
        verification_criteria: Optional[Dict[str, Any]] = None
    ) -> None:
        """Execute all tasks in the plan."""
        max_iterations = 100  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            ready_tasks = ctx.plan.get_ready_tasks(ctx.completed_tasks)
            pending_tasks = [t for t in ctx.plan.tasks if t.status == TaskStatus.PENDING]

            if not ready_tasks and not pending_tasks:
                break  # All done

            if not ready_tasks and pending_tasks:
                # Deadlock or circular dependency
                for task in pending_tasks:
                    task.status = TaskStatus.FAILED
                    task.error = "Circular dependency or deadlock detected"
                    ctx.failed_tasks.add(task.id)
                break

            # Execute ready tasks (in parallel conceptually)
            for task in ready_tasks:
                self._execute_task(ctx, task, verification_criteria)

        if iteration >= max_iterations:
            # Timeout
            for task in ctx.plan.tasks:
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.FAILED
                    task.error = "Execution timeout"
                    ctx.failed_tasks.add(task.id)

    def _execute_task(
        self,
        ctx: ExecutionContext,
        task: Task,
        verification_criteria: Optional[Dict[str, Any]] = None
    ) -> None:
        """Execute a single task with retries."""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        # Join the goal's correlation chain so task lifecycle events are
        # traceable end to end (Definition Lock section 112).
        task.correlation_id = ctx.goal.correlation_id

        publish_event(
            type="task_started",
            source="execution_kernel",
            data={"task_id": task.id, "goal_id": task.goal_id, "name": task.name},
            correlation_id=task.correlation_id,
            scope=EventScope(task.scope),
        )

        # Create action
        action = Action(
            id=str(uuid.uuid4())[:8],
            task_id=task.id,
            capability_id=task.capability_id,
            capability_namespace=task.capability_namespace,
            inputs=task.inputs,
            correlation_id=task.correlation_id,
            scope=task.scope,
            timeout_seconds=task.timeout_seconds,
        )

        # Execute with retries
        while task.retry_count <= task.max_retries:
            result = self.executor.execute(action)
            ctx.task_results[task.id] = result

            if result.success:
                task.status = TaskStatus.COMPLETED
                task.result = result.output
                task.completed_at = datetime.utcnow()
                ctx.completed_tasks.add(task.id)

                # Verify
                verification = self.verifier.verify(task, result, verification_criteria)
                ctx.verification_results[task.id] = verification

                if verification.replan_required:
                    # Could trigger replan here
                    pass

                publish_event(
                    type="task_completed",
                    source="execution_kernel",
                    data={
                        "task_id": task.id,
                        "goal_id": task.goal_id,
                        "verification": verification.result.value,
                        "score": verification.score,
                    },
                    correlation_id=task.correlation_id,
                    scope=EventScope(task.scope),
                )
                return

            # Retry
            task.retry_count += 1
            if task.retry_count <= task.max_retries:
                task.status = TaskStatus.RETRYING
                publish_event(
                    type="task_retrying",
                    source="execution_kernel",
                    data={"task_id": task.id, "attempt": task.retry_count, "max": task.max_retries},
                    correlation_id=task.correlation_id,
                    scope=EventScope(task.scope),
                    priority=EventPriority.HIGH,
                )

        # All retries exhausted - preserve the root cause of the last
        # failed attempt alongside the exhaustion notice.
        last_result = ctx.task_results.get(task.id)
        root_cause = (last_result.error if last_result and last_result.error
                      else "unknown error")
        task.status = TaskStatus.FAILED
        task.error = f"Max retries ({task.max_retries}) exceeded; last error: {root_cause}"
        task.completed_at = datetime.utcnow()
        ctx.failed_tasks.add(task.id)

        publish_event(
            type="task_failed",
            source="execution_kernel",
            data={"task_id": task.id, "goal_id": task.goal_id, "error": task.error},
            correlation_id=task.correlation_id,
            scope=EventScope(task.scope),
            priority=EventPriority.CRITICAL,
        )

    @kernel_action("execution.create_checkpoint")
    def create_checkpoint(self, ctx: ExecutionContext) -> Dict[str, Any]:
        """Create execution checkpoint for rollback."""
        checkpoint = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat(),
            "goal_id": ctx.goal.id,
            "plan_id": ctx.plan.id,
            "completed_tasks": list(ctx.completed_tasks),
            "failed_tasks": list(ctx.failed_tasks),
            "task_results": {k: v.__dict__ for k, v in ctx.task_results.items()},
            "verification_results": {k: v.__dict__ for k, v in ctx.verification_results.items()},
        }
        ctx.checkpoints.append(checkpoint)
        return checkpoint


# Convenience functions
def create_execution_engine(scope: str = "L1") -> ExecutionEngine:
    """Factory function to create ExecutionEngine."""
    return ExecutionEngine(scope=scope)


def execute_goal(
    natural_language: str,
    scope: str = "L1",
    plan_mode: str = "auto"
) -> ExecutionContext:
    """Convenience function to execute a goal."""
    goal = Goal(
        id=str(uuid.uuid4())[:8],
        natural_language=natural_language,
        scope=scope,
    )
    engine = create_execution_engine(scope)
    return engine.execute_goal(goal, plan_mode=plan_mode)
