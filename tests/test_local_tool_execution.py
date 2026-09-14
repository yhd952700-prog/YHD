"""Real local tool execution — prove the Execution Kernel executes *real* code.

These tests verify the WS1 deliverable: when a real ``CapabilityExecutor`` is
wired in, the kernel no longer simulates. The flagship local tool is
``python_compute`` (RestrictedPython-backed) — it must actually run the code and
return the computed value, and it must *reject* unsafe code (compile-time
isolation) rather than passing it through silently.

This is the honesty gate for "launch": an autonomous agent that only ever
simulates is not autonomous. Here we prove a capability genuinely executes.
"""
import uuid

from src.kernels.execution import Action, ActionExecutor
from src.ai.lcore import LCore
from src.ai.tools_local import register_default_local_tools


def _action(capability_id="python_compute", code="result = 1 + 1", scope="L1", inputs=None):
    return Action(
        id="a1",
        task_id="t1",
        capability_id=capability_id,
        capability_namespace="kernel",
        inputs=inputs if inputs is not None else {"code": code},
        correlation_id=str(uuid.uuid4()),
        scope=scope,
        timeout_seconds=10,
    )


def test_local_python_tool_executes_real_code():
    """python_compute runs the code and returns the real computed value."""
    lcore = LCore(register_local_tools=True)
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(_action(code="result = sum(i * i for i in range(1, 11))"))
    assert result.success is True
    # Normalized by ActionExecutor: status -> executed
    assert result.output["status"] == "executed"
    # The ACTUAL computed answer to sum of squares 1..10, not a simulation flag.
    assert result.output["result"] == "385"


def test_local_python_tool_rejects_unsafe_code():
    """The sandbox must reject import/open/eval — not execute it silently."""
    lcore = LCore(register_local_tools=True)
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(_action(code="import os; os.system('echo pwned')"))
    assert result.success is False
    # The error must come from the sandbox, proving real isolation is in force.
    assert "ImportError" in (result.error or "") or "REJECTED" in str(result.error or "")


def test_local_tool_fails_honestly_when_not_registered():
    """Without a registered tool, fail loudly — never pretend success."""
    lcore = LCore()  # default: no local tools
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(_action())
    assert result.success is False
    assert "no active tool for capability" in result.error


def test_register_default_local_tools_is_idempotent():
    from src.ai.tool_registry import ToolRegistry

    registry = ToolRegistry()
    register_default_local_tools(registry)
    # Second call must not raise or double-register.
    register_default_local_tools(registry)
    active = registry.list_active_by_capability("python_compute")
    assert len(active) == 1
    assert active[0].tool_id == "local_python_compute"


def test_expression_input_is_evaluated_to_real_value():
    """A bare `expression` is wrapped and really evaluated."""
    lcore = LCore(register_local_tools=True)
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(_action(inputs={"expression": "7 * 6"}))
    assert result.success is True
    assert result.output["result"] == "42"


def test_directive_goal_is_extracted_and_executed():
    """A goal carrying a `python:` directive has its payload executed."""
    lcore = LCore(register_local_tools=True)
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(
        _action(inputs={"goal": "python: result = sum(range(1, 11))"})
    )
    assert result.success is True
    assert result.output["result"] == "55"


def test_prose_goal_without_code_is_rejected_honestly():
    """Prose is never guessed into Python — the capability asks for code."""
    lcore = LCore(register_local_tools=True)
    executor = ActionExecutor(capability_executor=lcore.capability_executor())

    result = executor.execute(_action(inputs={"goal": "calculate the sum of squares"}))
    assert result.success is False
    assert "needs executable code" in (result.error or "")
