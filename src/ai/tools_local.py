"""Real, locally-executable tools — the capabilities that need no external service.

These close the gap left by the Execution Kernel's simulated ``ActionExecutor``:
a goal that maps to one of these capabilities now executes *for real* (locally,
sandboxed) instead of returning ``status: "simulated"``.

The flagship local tool is ``python_compute`` — it runs pure-compute Python through
the RestrictedPython backend (capability isolation: no ``import``/``open``/``eval``,
CPU timeout via a child process; **no resource isolation** — see
``src/plugins/sandbox/backends/restricted_python_backend.py``). It is genuinely
real: it executes the user's code and returns the computed value.

Why this matters for launch
---------------------------
"Autonomous agent" is dishonest if the execution layer only ever simulates. With
these tools registered, the autonomous loop can *actually do* local work, and
capabilities that require an external service (network, LLM provider) still fail
honestly with ``no active tool`` / backend-unavailable instead of pretending.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, Optional, Tuple

from .tool_registry import Tool, ToolRegistry
from ..plugins.sandbox.backends.restricted_python_backend import (
    RestrictedPythonBackend,
    RESULT_VAR,
)

#: A goal may carry an executable payload behind an explicit directive, e.g.
#: ``"python: result = sum(range(5))"`` or ``"code: result = 6*7"``. We only
#: treat the goal as code when such a directive is present — never by guessing
#: that arbitrary natural language is executable.
_CODE_DIRECTIVE = re.compile(
    r"^\s*(?:python|py|code|计算|执行代码)\s*[:：]\s*(?P<code>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _resolve_code(inputs: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the Python source to execute from the action inputs.

    Returns ``(code, reason)`` — exactly one is not ``None``. The planner (an
    LLM, or a human) is responsible for producing executable code; this helper
    only recognises three *explicit* shapes and never invents code from prose:

    1. ``code``       — a full program that assigns its answer to ``result``.
    2. ``expression`` — a pure expression, wrapped as ``result = <expr>``.
    3. ``goal``       — only when it carries a ``python:``/``code:`` directive.
    """
    raw = inputs.get("code")
    if isinstance(raw, str) and raw.strip():
        return raw, None

    expr = inputs.get("expression")
    if isinstance(expr, str) and expr.strip():
        return f"{RESULT_VAR} = {expr.strip()}", None

    goal = inputs.get("goal")
    if isinstance(goal, str):
        m = _CODE_DIRECTIVE.match(goal.strip())
        if m:
            return m.group("code").strip(), None

    return None, (
        "python_compute needs executable code. Provide `code` (a program that "
        "assigns its answer to `result`), an `expression`, or a goal carrying a "
        "`python:`/`code:` directive. The planner must produce the code — this "
        "capability will not guess Python from prose."
    )


def _python_compute_fn(**inputs: Any) -> Dict[str, Any]:
    """Run pure-compute Python locally via the RestrictedPython backend.

    Contract: the resolved code must assign its result to the variable
    ``result``. Unsafe code (``import``/``open``/``eval``) is rejected at compile
    time; runaway loops are killed by the child-process timeout. If RestrictedPython
    is not installed, the backend reports the reason instead of failing silently.
    """
    code, reason = _resolve_code(inputs)
    if code is None:
        return {
            "success": False,
            "status": "rejected",
            "error": reason,
        }

    try:
        timeout = int(inputs.get("timeout", 10))
    except (TypeError, ValueError):
        timeout = 10

    backend = RestrictedPythonBackend()
    res = backend.execute(
        "local-python-compute",
        [sys.executable, "-c", code],
        timeout=timeout,
    )

    if not res.success:
        # Distinguish a security rejection from a plain runtime failure so the
        # caller (and any human reading the audit log) knows *why* it didn't run.
        rejected = "REJECTED" in str(res.status)
        return {
            "success": False,
            "status": "rejected" if rejected else "failed",
            "error": res.error,
            "stage": str(res.status),
        }

    return {
        "success": True,
        "status": "executed",
        "result": res.output,
        "stdout": res.stdout,
    }


def register_default_local_tools(registry: ToolRegistry) -> None:
    """Register the real, locally-executable tools into ``registry``.

    Walks each tool through the §40 lifecycle (REGISTER -> VALIDATE -> APPROVE ->
    ACTIVE) so it is immediately executable. Idempotent: re-registering is a no-op
    beyond re-asserting ACTIVE.
    """
    if registry.get("local_python_compute") is not None:
        # Already registered — keep it simple and idempotent.
        return

    registry.register(Tool(
        tool_id="local_python_compute",
        name="Local Python Compute",
        version="1.0.0",
        description=(
            "Execute pure-compute Python locally via the RestrictedPython backend. "
            "Supply a `code` program (assigning its answer to `result`), an "
            "`expression`, or a goal carrying a `python:`/`code:` directive. "
            "import/open/eval are blocked at compile time and there is a CPU "
            "timeout. Provides capability isolation only — not resource isolation."
        ),
        capability="python_compute",
        schema={"code": "str", "expression": "str", "goal": "str", "timeout": "int"},
        fn=_python_compute_fn,
        risk="LOW",
        sandbox_policy="restricted_python",
        audit_policy="p9.tool.execute",
    ))
    registry.validate("local_python_compute")
    registry.approve("local_python_compute")
    registry.activate("local_python_compute")
