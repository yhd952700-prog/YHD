"""Bridge test: Execution Kernel's ActionExecutor <-> ai ToolRegistry/ToolRouter.

Verifies the dependency-injection seam that lets the kernel execute *real*
capabilities (via ``ToolRouter.as_capability_executor``) instead of always
simulating them, while preserving strict layering (kernels must not import ai).
"""
import uuid

from src.kernels.execution import Action, ActionExecutor
from src.ai.tool_registry import Tool, ToolRegistry, ToolRouter


def _make_action(action_id="a1", capability_id="network_bus", scope="L1", inputs=None):
    """Mirror tests/kernels/execution/test_execution.py::make_action."""
    return Action(
        id=action_id,
        task_id="t1",
        capability_id=capability_id,
        capability_namespace="kernel",
        inputs=inputs if inputs is not None else {"query": "test"},
        correlation_id=str(uuid.uuid4()),
        scope=scope,
        timeout_seconds=30,
    )


# ---------------------------------------------------------------------------
# (a) Backward compatibility: no injection => simulated path unchanged
# ---------------------------------------------------------------------------
def test_no_injection_stays_simulated():
    executor = ActionExecutor()
    assert not executor.has_capability_executor
    result = executor.execute(_make_action())
    assert result.success is True
    assert result.output["status"] == "simulated"
    assert result.output["capability"] == "network_bus"


# ---------------------------------------------------------------------------
# (b) Injected executor is invoked with correct args and output normalized
# ---------------------------------------------------------------------------
def test_injected_executor_is_called_and_normalized():
    calls = []

    def fake_executor(capability_id, inputs):
        calls.append((capability_id, inputs))
        return {"capability": capability_id, "status": "ignored", "extra": 1}

    executor = ActionExecutor(capability_executor=fake_executor)
    assert executor.has_capability_executor

    sent_inputs = {"query": "test"}
    action = _make_action(capability_id="network_bus", inputs=sent_inputs)
    result = executor.execute(action)

    # Called with the exact capability_id / inputs
    assert len(calls) == 1
    assert calls[0][0] == "network_bus"
    assert calls[0][1] == sent_inputs

    # Normalized output
    assert result.success is True
    assert result.output["status"] == "executed"
    assert result.output["capability"] == "network_bus"
    assert result.output["extra"] == 1  # other keys preserved

    # Caller's inputs dict was NOT mutated in place
    assert sent_inputs == {"query": "test"}


# ---------------------------------------------------------------------------
# (c) End-to-end with the real registry + router
# ---------------------------------------------------------------------------
def test_real_registry_roundtrip_and_pre_activation_failure():
    registry = ToolRegistry()
    router = ToolRouter(registry)

    sentinel = {"answer": 42}

    def fn(**inputs):
        return sentinel

    tool = Tool(
        tool_id="t_net",
        name="net",
        version="1.0",
        description="network tool",
        capability="network_bus",
        schema={"query": "str"},
        fn=fn,
    )

    # Before activation: routing fails -> action failure
    executor_pre = ActionExecutor(capability_executor=router.as_capability_executor())
    pre_result = executor_pre.execute(_make_action(capability_id="network_bus"))
    assert pre_result.success is False
    assert "no active tool for capability" in pre_result.error

    # Lifecycle: register -> validate -> approve -> activate
    registry.register(tool)
    assert registry.validate("t_net")
    assert registry.approve("t_net")
    assert registry.activate("t_net")

    executor = ActionExecutor(capability_executor=router.as_capability_executor())
    post_result = executor.execute(_make_action(capability_id="network_bus"))
    assert post_result.success is True
    assert post_result.output["status"] == "executed"
    assert post_result.output["capability"] == "network_bus"
    # The real tool's return value must surface in the action output
    assert post_result.output["answer"] == 42


# ---------------------------------------------------------------------------
# (d) Layering guardrail: kernel must not import the ai layer
# ---------------------------------------------------------------------------
def test_kernel_does_not_import_ai_layer():
    kernel_src = open(
        "src/kernels/execution/__init__.py", encoding="utf-8"
    ).read()
    forbidden = ["src.ai", "from ..ai", "from .ai"]
    for token in forbidden:
        assert token not in kernel_src, f"forbidden reverse dependency: {token!r}"


# ---------------------------------------------------------------------------
# (e) Failure propagation: raising executor -> success=False with message
# ---------------------------------------------------------------------------
def test_raising_executor_propagates_failure():
    def boom(capability_id, inputs):
        raise RuntimeError("capability exploded")

    executor = ActionExecutor(capability_executor=boom)
    result = executor.execute(_make_action())
    assert result.success is False
    assert "capability exploded" in result.error


# ---------------------------------------------------------------------------
# (f) ExecutionEngine forwards the injected executor through the whole pipeline
# ---------------------------------------------------------------------------
def test_execution_engine_forwards_injected_executor_end_to_end():
    from src.kernels.execution import ExecutionEngine, Goal

    calls = []

    def fake_executor(capability_id, inputs):
        calls.append((capability_id, dict(inputs)))
        return {"from": "real"}

    engine = ExecutionEngine(capability_executor=fake_executor)
    assert engine.executor.has_capability_executor is True

    ctx = engine.execute_goal(
        Goal(id="g1", natural_language="summarise the log", scope="L1")
    )

    # The injected executor must actually have been reached by the pipeline
    assert calls, "injected executor was never invoked by execute_goal"
    assert ctx.task_results, "no task results recorded"
    for result in ctx.task_results.values():
        assert result.success is True
        assert result.output["status"] == "executed"

    # And it can be swapped/cleared after construction
    engine.set_capability_executor(None)
    assert engine.executor.has_capability_executor is False


# ---------------------------------------------------------------------------
# (g) Cross-subsystem bridge: LCore's registry drives the Execution Kernel
# ---------------------------------------------------------------------------
def test_lcore_registry_drives_execution_engine_end_to_end():
    from src.ai.lcore import LCore
    from src.kernels.execution import ExecutionEngine, Goal

    lcore = LCore()
    lcore.register_tool(Tool(
        tool_id="t_pipe",
        name="pipeline",
        version="1.0",
        description="pipeline capability backed by a real callable",
        capability="execution_pipeline",
        schema={"goal": "str"},
        fn=lambda **kwargs: {"handled_by": "real_tool", "goal": kwargs.get("goal")},
    ))

    engine = ExecutionEngine(capability_executor=lcore.capability_executor())
    # "plan the schedule" decomposes to exactly one execution_pipeline task
    ctx = engine.execute_goal(
        Goal(id="g2", natural_language="plan the schedule", scope="L1")
    )

    assert ctx.task_results, "no task results recorded"
    outputs = [r.output for r in ctx.task_results.values()]
    assert any(
        isinstance(o, dict) and o.get("handled_by") == "real_tool" for o in outputs
    ), f"real tool was not reached through the bridge: {outputs}"
