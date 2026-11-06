"""Regression tests for the real "鎏灏管家" chain.

Covers the defects that were verified against HEAD d2a62b0e:

* LLM-first decomposition (and its deterministic fallback, never 0 tasks);
* the previously phantom ``execution_pipeline`` / ``network_bus`` capabilities;
* independent tasks running concurrently;
* dependency-failure semantics (not "deadlock");
* a synthesized, non-empty evaluation summary.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from src.ai.agent_runtime import AgentRuntime
from src.ai.llm_decomposer import LLMTaskDecomposer
from src.ai.llm_support import ModelUnavailable
from src.ai.tool_registry import ToolRegistry
from src.ai.tools_local import register_default_local_tools
from src.kernels.execution import (
    ExecutionContext,
    ExecutionEngine,
    Goal,
    GoalDecomposer,
    Task,
    TaskStatus,
)


TARGET_GOAL = "帮我查一下今天有什么值得关注的 AI 开源项目，然后整理成三条要点"


def _goal(text: str = TARGET_GOAL, goal_id: str = "goal-llm") -> Goal:
    return Goal(id=goal_id, natural_language=text, scope="L1")


def _model_plan() -> str:
    return json.dumps(
        [
            {
                "name": "调研今日 AI 开源项目",
                "description": "找出今天值得关注的 AI 开源项目",
                "capability": "network_bus",
                "inputs": {"query": "今天值得关注的 AI 开源项目"},
                "depends_on": [],
            },
            {
                "name": "整理三条要点",
                "description": "把调研结果整理成三条要点",
                "capability": "execution_pipeline",
                "inputs": {"instruction": "整理成三条要点"},
                "depends_on": ["调研今日 AI 开源项目"],
            },
        ],
        ensure_ascii=False,
    )


class TestLLMDecomposition:
    def test_model_plan_is_used_and_dependency_is_wired(self):
        decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=lambda prompt: _model_plan(),
            capability_provider=lambda: {"network_bus", "execution_pipeline"},
        )
        tasks = decomposer.decompose(_goal())

        assert [t.capability_id for t in tasks] == ["network_bus", "execution_pipeline"]
        assert tasks[1].dependencies == [tasks[0].id], (
            "organize task must consume the research task's real output"
        )
        assert tasks[0].name == "调研今日 AI 开源项目"

    @pytest.mark.parametrize(
        "bad_reply",
        [
            "抱歉，我无法拆解这个目标。",            # non-JSON
            json.dumps([{"name": "x", "capability": "magic_capability", "inputs": {}}]),
            json.dumps([{"name": "x", "capability": "network_bus", "inputs": {}}]),
        ],
    )
    def test_invalid_model_output_falls_back_without_empty_plan(self, bad_reply):
        decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=lambda prompt, reply=bad_reply: reply,
            capability_provider=lambda: {"network_bus", "execution_pipeline"},
        )
        tasks = decomposer.decompose(_goal())

        # The deterministic Chinese keyword path still plans research + organize,
        # so the user never sees a silent "0 tasks" failure.
        assert {t.capability_id for t in tasks} == {"network_bus", "execution_pipeline"}
        assert len(tasks) >= 2

    def test_model_unavailable_or_timeout_falls_back(self):
        def _boom(prompt: str) -> str:
            raise ModelUnavailable("模型未配置")

        decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=_boom,
            capability_provider=lambda: {"network_bus", "execution_pipeline"},
        )
        assert len(decomposer.decompose(_goal())) >= 2

        def _slow(prompt: str) -> str:
            time.sleep(0.5)
            return _model_plan()

        slow_decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=_slow,
            capability_provider=lambda: {"network_bus", "execution_pipeline"},
            timeout_seconds=0.1,
        )
        started = time.monotonic()
        tasks = slow_decomposer.decompose(_goal())
        assert time.monotonic() - started < 0.45, "decomposition timeout must fall back promptly"
        assert len(tasks) >= 2

    def test_retry_hint_uses_cached_plan_names(self):
        calls = []

        def _generator(prompt: str) -> str:
            calls.append(prompt)
            return _model_plan()

        decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=_generator,
            capability_provider=lambda: {"network_bus", "execution_pipeline"},
        )
        first = decomposer.decompose(_goal())
        decomposer.only_task_name = "整理三条要点"
        second = decomposer.decompose(_goal())

        assert [t.name for t in second] == [t.name for t in first]
        assert len(calls) == 1, "a retry must reuse the cached decomposition, not re-plan"

    def test_explicit_tool_goal_keeps_the_deterministic_plan(self):
        """A named file write must never be "improved" into a model-only step.

        Regression: with model-first planning, ``write "hello" to notes/retry.txt``
        was decomposed into a single ``execution_pipeline`` synthesis which wrote
        nothing -- real work replaced by prose. An executable keyword plan is
        authoritative and the model must not be consulted at all.
        """
        calls = []

        def _generator(prompt: str) -> str:
            calls.append(prompt)
            return "[]"

        decomposer = LLMTaskDecomposer(
            GoalDecomposer(),
            generator=_generator,
            capability_provider=lambda: {
                "file_write", "python_compute", "network_bus", "execution_pipeline",
            },
        )
        tasks = decomposer.decompose(
            _goal('write "hello" to notes/retry.txt', goal_id="goal-explicit-tool")
        )

        assert {t.capability_id for t in tasks} == {"file_write"}
        assert len(tasks) >= 2, "the file write plus its integrity sidecar must survive"
        assert calls == [], "the model must not override an executable deterministic plan"


class TestExecutionConcurrency:
    def test_independent_tasks_run_at_the_same_time(self):
        engine = ExecutionEngine(scope="L1")
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        state = {"active": 0, "max_active": 0}

        def executor(capability_id, inputs):
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                # If the engine is serial, the first task blocks until the barrier
                # times out and fails -> this test fails loudly.
                barrier.wait(timeout=2.0)
            finally:
                with lock:
                    state["active"] -= 1
            return {"success": True, "status": "executed", "result": capability_id}

        engine.set_capability_executor(executor)
        goal = _goal("parallel probe", goal_id="goal-parallel")
        left = Task(
            id="left", goal_id=goal.id, name="Left",
            description="left", capability_id="python_compute", capability_namespace="kernel",
            inputs={"expression": "1+1"}, scope=goal.scope,
        )
        right = Task(
            id="right", goal_id=goal.id, name="Right",
            description="right", capability_id="file_write", capability_namespace="kernel",
            inputs={"path": "a.txt", "content": "x"}, scope=goal.scope,
        )
        plan = engine.planner.build(goal, [left, right], mode="parallel")
        ctx = ExecutionContext(goal=goal, plan=plan, context_kernel=engine.context_kernel)

        engine._execute_plan(ctx)

        assert state["max_active"] >= 2, "two independent tasks must be RUNNING together"
        assert all(t.status == TaskStatus.COMPLETED for t in plan.tasks)

    def test_dependency_failure_is_reported_as_dependency_failure(self):
        engine = ExecutionEngine(scope="L1")

        def executor(capability_id, inputs):
            if inputs.get("expression") == "boom":
                raise RuntimeError("boom")
            return {"success": True, "status": "executed", "result": "ok"}

        engine.set_capability_executor(executor)
        goal = _goal("dependency probe", goal_id="goal-dep")
        first = Task(
            id="a", goal_id=goal.id, name="A", description="fails",
            capability_id="python_compute", capability_namespace="kernel",
            inputs={"expression": "boom"}, scope=goal.scope, max_retries=0,
        )
        second = Task(
            id="b", goal_id=goal.id, name="B", description="depends on A",
            capability_id="file_write", capability_namespace="kernel",
            inputs={"path": "b.txt", "content": "y"}, scope=goal.scope, dependencies=["a"],
        )
        plan = engine.planner.build(goal, [first, second], mode="sequential")
        ctx = ExecutionContext(goal=goal, plan=plan, context_kernel=engine.context_kernel)

        engine._execute_plan(ctx)

        assert first.status == TaskStatus.FAILED
        assert second.status == TaskStatus.FAILED
        assert "依赖任务失败（任务 A）" in (second.error or "")
        assert "deadlock" not in (second.error or "").lower()


class TestSummaryAndTools:
    @staticmethod
    def _executor(capability_id, inputs):
        return {
            "success": True,
            "status": "executed",
            "result": "项目 A、B、C 值得关注。",
        }

    def test_evaluation_summary_is_synthesized_from_outputs(self):
        runtime = AgentRuntime(
            scope="L1",
            capability_executor=self._executor,
            model_generator=lambda prompt: "今天值得关注的项目有 A、B、C。建议从 C 开始验证。",
        )
        result = runtime.run_goal("search for cats and plan the rollout", persist=False)

        assert result.evaluation is not None
        assert result.evaluation.summary.strip(), "summary must not be empty"
        assert "A、B、C" in result.evaluation.summary

    def test_summary_falls_back_deterministically_when_model_fails(self):
        def _boom(prompt: str) -> str:
            raise RuntimeError("model down")

        runtime = AgentRuntime(
            scope="L1",
            capability_executor=self._executor,
            model_generator=_boom,
        )
        result = runtime.run_goal("search for cats and plan the rollout", persist=False)

        assert result.evaluation is not None
        assert result.evaluation.summary.strip()
        assert "已完成" in result.evaluation.summary

    def test_abstract_capabilities_have_real_executors(self, tmp_path, monkeypatch):
        import src.ai.tools_local as tools_local

        monkeypatch.setenv("LIUHAO_WEB_RESEARCH", "0")
        monkeypatch.setattr(
            tools_local,
            "default_generate",
            lambda prompt: "调研结论：项目 A、B、C 值得关注。",
        )
        registry = ToolRegistry()
        register_default_local_tools(registry, root=str(tmp_path))
        assert registry.get("local_llm_research") is not None
        assert registry.get("local_llm_synthesis") is not None

        research = registry.execute(
            "local_llm_research", {"query": "今天值得关注的 AI 开源项目"}
        )
        assert research.success, research.error
        assert "A、B、C" in research.output["result"]
        assert "未接入实时联网检索" in research.output["result"]

        synthesis = registry.execute(
            "local_llm_synthesis",
            {
                "goal": TARGET_GOAL,
                "instruction": "整理成三条要点",
                "upstream": [
                    {"task": "调研", "capability": "network_bus",
                     "output": {"result": "项目 A、B、C"}}
                ],
            },
        )
        assert synthesis.success, synthesis.error
        assert synthesis.output["result"].strip()

    def test_missing_capability_rejection_is_readable(self):
        from src.ai.planner_contract import ExecutableAwareDecomposer, PlannerContractError

        wrapped = ExecutableAwareDecomposer(
            GoalDecomposer(), capability_provider=lambda: {"file_write"}
        )
        with pytest.raises(PlannerContractError) as excinfo:
            wrapped.decompose(_goal("remember my favorite color", goal_id="goal-missing"))

        message = str(excinfo.value)
        assert "multi_tier_memory" in message
        assert "没有可用的执行器" in message
