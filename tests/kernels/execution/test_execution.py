"""Execution Kernel unit tests.

Covers: goal decomposition, plan building (sequential/parallel/auto),
action execution with capability scope checks, verification scoring,
the full Goal->Task->Plan->Action->Verify pipeline (happy path,
retries, partial failure, circular dependency deadlock, checkpoints).

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112: verify outcomes
against criteria). They are expected to fail until the kernel is fixed.
"""
import uuid

import pytest

from src.kernels.capability import CapabilityScope
from src.kernels.event import get_event_bus
from src.kernels.execution import (
    Action,
    ActionResult,
    ExecutionContext,
    ExecutionEngine,
    ExecutionPlan,
    Goal,
    GoalDecomposer,
    PlanBuilder,
    PlanStatus,
    Task,
    TaskStatus,
    Verifier,
    VerifyResult,
    create_execution_engine,
    execute_goal as execute_goal_convenience,
)


@pytest.fixture
def decomposer():
    return GoalDecomposer()


@pytest.fixture
def planner():
    return PlanBuilder()


@pytest.fixture
def executor():
    from src.kernels.execution import ActionExecutor
    return ActionExecutor()


@pytest.fixture
def verifier():
    return Verifier()


def make_task(name="T", capability_id="network_bus", **kwargs):
    defaults = dict(
        id=kwargs.pop("id", str(uuid.uuid4())[:8]),
        goal_id="g1",
        name=name,
        description=name,
        capability_id=capability_id,
        capability_namespace="kernel",
        scope="L1",
    )
    defaults.update(kwargs)
    return Task(**defaults)


def make_action(action_id="a1", capability_id="network_bus", scope="L1"):
    return Action(
        id=action_id,
        task_id="t1",
        capability_id=capability_id,
        capability_namespace="kernel",
        inputs={"query": "test"},
        correlation_id=str(uuid.uuid4()),
        scope=scope,
        timeout_seconds=30,
    )


def make_result(action_id="a1", success=True, output=None, error=None):
    return ActionResult(action_id=action_id, success=success,
                        output=output, error=error)


# =====================================================================
# Goal decomposition
# =====================================================================

class TestGoalDecomposer:
    def test_search_goal_maps_to_network_bus(self, decomposer):
        goal = Goal(id="g1", natural_language="search for cat pictures")
        tasks = decomposer.decompose(goal)
        assert len(tasks) == 1
        assert tasks[0].capability_id == "network_bus"
        assert tasks[0].inputs["query"] == "search for cat pictures"

    def test_plan_goal_maps_to_execution_pipeline(self, decomposer):
        goal = Goal(id="g1", natural_language="organize my week and plan tasks")
        tasks = decomposer.decompose(goal)
        assert any(t.capability_id == "execution_pipeline" for t in tasks)

    def test_memory_goal_maps_to_multi_tier_memory(self, decomposer):
        goal = Goal(id="g1", natural_language="remember this fact")
        tasks = decomposer.decompose(goal)
        assert len(tasks) == 1
        assert tasks[0].capability_id == "multi_tier_memory"

    def test_mixed_goal_produces_multiple_tasks(self, decomposer):
        goal = Goal(id="g1", natural_language="search the web and remember results")
        tasks = decomposer.decompose(goal)
        caps = {t.capability_id for t in tasks}
        assert caps == {"network_bus", "multi_tier_memory"}

    def test_unrecognized_goal_gets_generic_execute_task(self, decomposer):
        goal = Goal(id="g1", natural_language="make me a sandwich")
        tasks = decomposer.decompose(goal)
        assert len(tasks) == 1
        assert tasks[0].capability_id == "execution_pipeline"
        assert tasks[0].name == "Execute"

    def test_tasks_carry_goal_id_and_scope(self, decomposer):
        goal = Goal(id="g-42", natural_language="find the answer", scope="L2")
        tasks = decomposer.decompose(goal)
        for t in tasks:
            assert t.goal_id == "g-42"
            assert t.scope == "L2"
            assert t.status is TaskStatus.PENDING

    def test_decompose_publishes_event(self, decomposer):
        goal = Goal(id="g1", natural_language="search something")
        decomposer.decompose(goal)
        chain = get_event_bus().get_correlation_chain(goal.correlation_id)
        types = [e.type for e in chain]
        assert "goal_decomposed" in types


# =====================================================================
# Plan building
# =====================================================================

class TestPlanBuilder:
    def test_sequential_mode_chains_dependencies(self, planner):
        goal = Goal(id="g1", natural_language="x")
        tasks = [make_task("T1"), make_task("T2"), make_task("T3")]
        plan = planner.build(goal, tasks, mode="sequential")
        assert tasks[0].dependencies == []
        assert tasks[1].dependencies == [tasks[0].id]
        assert tasks[2].dependencies == [tasks[1].id]
        assert plan.goal_id == "g1"
        assert plan.status is PlanStatus.DRAFT

    def test_parallel_mode_has_no_dependencies(self, planner):
        goal = Goal(id="g1", natural_language="x")
        tasks = [make_task("T1"), make_task("T2")]
        plan = planner.build(goal, tasks, mode="parallel")
        assert all(t.dependencies == [] for t in tasks)
        assert len(plan.tasks) == 2

    def test_auto_mode_links_output_to_input(self, planner):
        goal = Goal(id="g1", natural_language="x")
        producer = make_task("Producer", expected_outputs={"data": None})
        consumer = make_task("Consumer", inputs={"data": None})
        plan = planner.build(goal, [producer, consumer], mode="auto")
        assert producer.id in consumer.dependencies

    def test_auto_mode_no_link_without_shared_keys(self, planner):
        goal = Goal(id="g1", natural_language="x")
        a = make_task("A", expected_outputs={"foo": None})
        b = make_task("B", inputs={"bar": None})
        planner.build(goal, [a, b], mode="auto")
        assert a.dependencies == [] and b.dependencies == []

    def test_build_publishes_event(self, planner):
        goal = Goal(id="g1", natural_language="x")
        planner.build(goal, [make_task("T1")], mode="parallel")
        chain = get_event_bus().get_correlation_chain(goal.correlation_id)
        assert "plan_created" in [e.type for e in chain]


class TestExecutionPlanHelpers:
    def test_get_ready_tasks_requires_dependencies_done(self):
        t1, t2 = make_task("T1"), make_task("T2")
        t2.dependencies = [t1.id]
        plan = ExecutionPlan(id="p1", goal_id="g1", tasks=[t1, t2])
        ready = plan.get_ready_tasks(set())
        assert [t.id for t in ready] == [t1.id]
        ready = plan.get_ready_tasks({t1.id})
        assert {t.id for t in ready} == {t1.id, t2.id}

    def test_get_ready_tasks_skips_non_pending(self):
        t1 = make_task("T1")
        t1.status = TaskStatus.COMPLETED
        plan = ExecutionPlan(id="p1", goal_id="g1", tasks=[t1])
        assert plan.get_ready_tasks(set()) == []

    def test_get_task_found_and_missing(self):
        t1 = make_task("T1", id="task-1")
        plan = ExecutionPlan(id="p1", goal_id="g1", tasks=[t1])
        assert plan.get_task("task-1") is t1
        assert plan.get_task("nope") is None


# =====================================================================
# Action execution
# =====================================================================

class TestActionExecutor:
    def test_success_within_capability_scope(self, executor):
        action = make_action(capability_id="network_bus", scope="L1")
        result = executor.execute(action)
        assert result.success
        assert result.output["capability"] == "network_bus"
        assert result.output["inputs_received"] == ["query"]

    def test_scope_above_capability_rejected(self, executor):
        # network_bus is L3; requesting L5 must fail the scope check
        action = make_action(capability_id="network_bus", scope="L5")
        result = executor.execute(action)
        assert not result.success
        assert "Scope check failed" in result.error

    def test_unknown_capability_rejected(self, executor):
        action = make_action(capability_id="no_such_capability", scope="L1")
        result = executor.execute(action)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_success_publishes_action_completed(self, executor):
        action = make_action()
        executor.execute(action)
        chain = get_event_bus().get_correlation_chain(action.correlation_id)
        assert "action_completed" in [e.type for e in chain]

    def test_capability_exception_publishes_action_failed(self, executor):
        action = make_action()

        def boom(capability_id, inputs):
            raise RuntimeError("capability exploded")

        executor._simulate_capability = boom
        result = executor.execute(action)
        assert not result.success
        assert "capability exploded" in result.error
        chain = get_event_bus().get_correlation_chain(action.correlation_id)
        assert "action_failed" in [e.type for e in chain]


# =====================================================================
# Verification
# =====================================================================

class TestVerifier:
    def test_failed_action_is_failed_with_replan(self, verifier):
        task = make_task("T1")
        result = make_result(success=False, error="boom")
        v = verifier.verify(task, result)
        assert v.result is VerifyResult.FAILED
        assert v.score == 0.0
        assert v.replan_required
        assert "boom" in v.feedback

    def test_no_criteria_means_success(self, verifier):
        task = make_task("T1")  # empty expected_outputs
        v = verifier.verify(task, make_result(output={"anything": 1}))
        assert v.result is VerifyResult.SUCCESS
        assert v.score == 1.0
        assert not v.replan_required

    def test_full_match_is_success(self, verifier):
        task = make_task("T1", expected_outputs={"status": "ok"})
        v = verifier.verify(task, make_result(output={"status": "ok"}))
        assert v.result is VerifyResult.SUCCESS
        assert v.score == 1.0

    def test_string_match_is_case_insensitive(self, verifier):
        task = make_task("T1", expected_outputs={"status": "OK"})
        v = verifier.verify(task, make_result(output={"status": "ok"}))
        assert v.details["status"] == "match"

    def test_numeric_match_within_tolerance(self, verifier):
        task = make_task("T1", expected_outputs={"score": 1.0})
        v = verifier.verify(task, make_result(output={"score": 1.0005}))
        assert v.result is VerifyResult.SUCCESS
        task2 = make_task("T2", expected_outputs={"score": 1.0})
        v2 = verifier.verify(task2, make_result(output={"score": 1.01}))
        assert v2.result is not VerifyResult.SUCCESS

    def test_missing_key_counts_as_mismatch(self, verifier):
        task = make_task("T1", expected_outputs={"a": 1})
        v = verifier.verify(task, make_result(output={"b": 1}))
        assert v.details["a"] == "missing"
        assert v.result is VerifyResult.FAILED

    def test_three_of_four_is_partial_without_replan(self, verifier):
        expected = {f"k{i}": i for i in range(4)}
        actual = {f"k{i}": i for i in range(3)}
        task = make_task("T1", expected_outputs=expected)
        v = verifier.verify(task, make_result(output=actual))
        assert v.result is VerifyResult.PARTIAL
        assert v.score == pytest.approx(0.75)
        assert not v.replan_required

    def test_score_at_seven_tenths_is_partial(self, verifier):
        expected = {f"k{i}": i for i in range(10)}
        actual = {f"k{i}": i for i in range(7)}
        task = make_task("T1", expected_outputs=expected)
        v = verifier.verify(task, make_result(output=actual))
        assert v.result is VerifyResult.PARTIAL
        assert v.score == pytest.approx(0.7)

    def test_two_of_five_is_off_track_with_replan(self, verifier):
        expected = {f"k{i}": i for i in range(5)}
        actual = {f"k{i}": i for i in range(2)}
        task = make_task("T1", expected_outputs=expected)
        v = verifier.verify(task, make_result(output=actual))
        assert v.result is VerifyResult.OFF_TRACK
        assert v.replan_required

    def test_one_of_five_is_failed(self, verifier):
        expected = {f"k{i}": i for i in range(5)}
        task = make_task("T1", expected_outputs=expected)
        v = verifier.verify(task, make_result(output={"k0": 0}))
        assert v.result is VerifyResult.FAILED
        assert v.replan_required


# =====================================================================
# Full pipeline
# =====================================================================

class TestExecutionEngine:
    def test_happy_path_completes_plan(self):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        ctx = engine.execute_goal(goal)
        assert ctx.plan.status is PlanStatus.COMPLETED
        assert ctx.completed_tasks == {t.id for t in ctx.plan.tasks}
        assert not ctx.failed_tasks
        for t in ctx.plan.tasks:
            assert t.status is TaskStatus.COMPLETED
            assert ctx.verification_results[t.id].result is VerifyResult.SUCCESS

    def test_happy_path_publishes_lifecycle_events(self):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        engine.execute_goal(goal)
        types = [e.type for e in get_event_bus().get_correlation_chain(goal.correlation_id)]
        for expected in ("execution_started", "goal_decomposed", "plan_created",
                         "execution_completed"):
            assert expected in types, expected

    def test_plan_status_partial_when_one_task_fails(self, monkeypatch):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search and remember the news")

        original = engine.executor._simulate_capability

        def flaky(capability_id, inputs):
            if capability_id == "multi_tier_memory":
                raise RuntimeError("memory backend down")
            return original(capability_id, inputs)

        monkeypatch.setattr(engine.executor, "_simulate_capability", flaky)
        ctx = engine.execute_goal(goal)
        assert ctx.plan.status is PlanStatus.PARTIAL
        assert len(ctx.completed_tasks) == 1
        assert len(ctx.failed_tasks) == 1
        failed = [t for t in ctx.plan.tasks if t.id in ctx.failed_tasks][0]
        assert failed.status is TaskStatus.FAILED
        assert "Max retries" in failed.error

    def test_retries_exhausted_marks_task_failed(self, monkeypatch):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        tasks = engine.decomposer.decompose(goal)
        tasks[0].max_retries = 1
        plan = engine.planner.build(goal, tasks, mode="parallel")
        ctx = ExecutionContext(goal=goal, plan=plan,
                               context_kernel=engine.context_kernel)

        def boom(capability_id, inputs):
            raise RuntimeError("always fails")

        monkeypatch.setattr(engine.executor, "_simulate_capability", boom)
        engine._execute_plan(ctx)

        task = tasks[0]
        assert task.status is TaskStatus.FAILED
        # 1 initial attempt + 1 retry = 2 executions
        assert task.retry_count == 2
        assert "Max retries (1) exceeded" in task.error
        assert task.id in ctx.failed_tasks
        types = [e.type for e in get_event_bus().get_correlation_chain(task.correlation_id)]
        assert "task_retrying" in types
        assert "task_failed" in types

    def test_all_tasks_failed_marks_plan_failed(self, monkeypatch):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")

        def boom(capability_id, inputs):
            raise RuntimeError("down")

        monkeypatch.setattr(engine.executor, "_simulate_capability", boom)
        ctx = engine.execute_goal(goal)
        assert ctx.plan.status is PlanStatus.FAILED
        assert all(t.status is TaskStatus.FAILED for t in ctx.plan.tasks)

    def test_scope_above_capability_fails_every_task(self):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats", scope="L5")
        ctx = engine.execute_goal(goal)
        # network_bus is L3; an L5-scoped goal must not execute it
        assert ctx.plan.status is PlanStatus.FAILED
        for t in ctx.plan.tasks:
            assert t.status is TaskStatus.FAILED

    def test_circular_dependency_detected_as_deadlock(self):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="x")
        a, b = make_task("A"), make_task("B")
        a.dependencies = [b.id]
        b.dependencies = [a.id]
        plan = engine.planner.build(goal, [a, b], mode="parallel")
        ctx = ExecutionContext(goal=goal, plan=plan,
                               context_kernel=engine.context_kernel)
        engine._execute_plan(ctx)
        for t in (a, b):
            assert t.status is TaskStatus.FAILED
            assert "Circular dependency" in t.error
        assert ctx.failed_tasks == {a.id, b.id}

    def test_checkpoint_records_state(self):
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        ctx = engine.execute_goal(goal)
        checkpoint = engine.create_checkpoint(ctx)
        assert checkpoint["goal_id"] == "g1"
        assert checkpoint["plan_id"] == ctx.plan.id
        assert checkpoint["completed_tasks"] == list(ctx.completed_tasks)
        assert ctx.checkpoints == [checkpoint]


class TestConvenienceFunction:
    def test_execute_goal_convenience_runs_pipeline(self):
        ctx = execute_goal_convenience("search for cats", scope="L1")
        assert ctx.plan.status is PlanStatus.COMPLETED
        assert ctx.completed_tasks


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112 (verify outcomes against
# criteria).
# =====================================================================

class TestDefects:
    def test_defect_verification_criteria_are_applied(self):
        """DEFECT EXEC-1: verification_criteria parameter is silently ignored.

        ExecutionEngine.execute_goal accepts verification_criteria and
        forwards it to Verifier.verify, but verify() only looks at
        task.expected_outputs and never reads the criteria. A goal
        executed with clearly unsatisfiable criteria still verifies as
        SUCCESS with score 1.0. Expected: unmet criteria must not
        produce a SUCCESS verification.
        """
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        ctx = engine.execute_goal(goal, verification_criteria={"status": "failed"})
        for verification in ctx.verification_results.values():
            assert verification.result is not VerifyResult.SUCCESS, (
                "verification_criteria was ignored; unmet criteria still "
                "verified as SUCCESS"
            )

    def test_defect_task_events_share_goal_correlation_id(self):
        """DEFECT EXEC-3: task events detach from the goal correlation chain.

        Definition Lock section 112 requires events for traceability.
        Task lifecycle events (task_started / task_completed /
        task_failed) are published with the task's own freshly generated
        correlation_id instead of the goal's, so the goal's correlation
        chain cannot be traced end to end. Expected: task events appear
        in the goal's correlation chain.
        """
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats")
        engine.execute_goal(goal)
        types = [e.type for e in get_event_bus().get_correlation_chain(goal.correlation_id)]
        assert "task_started" in types, "task_started missing from goal chain"
        assert "task_completed" in types, "task_completed missing from goal chain"

    def test_defect_retry_exhaustion_preserves_root_cause(self):
        """DEFECT EXEC-2: retry exhaustion overwrites the root-cause error.

        When an action fails the scope check (or any other underlying
        error), the task's error field is finally set to
        "Max retries (N) exceeded", discarding the original reason.
        Expected: the root-cause message (e.g. "Scope check failed")
        remains visible in task.error.
        """
        engine = create_execution_engine(scope="L1")
        goal = Goal(id="g1", natural_language="search for cats", scope="L5")
        ctx = engine.execute_goal(goal)
        for t in ctx.plan.tasks:
            assert t.status is TaskStatus.FAILED
            assert "Scope check failed" in t.error, (
                f"root cause lost; task.error={t.error!r}"
            )
