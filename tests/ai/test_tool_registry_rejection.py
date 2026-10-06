"""Regression: ``ToolRegistry`` must not record a tool's IN-BAND rejection as
a successful action.

Defect (approved by team-lead, src/ai domain)
---------------------------------------------
``ToolRegistry.execute`` used to do, unconditionally::

    output = tool.fn(**inputs)
    return ActionResult(action_id=tool_id, success=True, output=output)

Several tools (``python_compute``, ``file_write``, ...) report a refusal by
*returning* ``{"success": False, "status": "rejected", "error": ...}`` instead
of raising. Wrapping that payload in ``ActionResult(success=True)`` is a
silent-success lie: the tool explicitly said it refused, yet the registry
recorded a successful action. Only a downstream consumer that re-inspected the
output dict would notice, so the honesty of the action depended on every caller
doing that extra check.

Fix
---
If the tool returns a Mapping whose ``success`` is exactly ``False``, propagate
that verdict as ``ActionResult(success=False, error=<tool's own message>)``.

Conservative by design: only an explicit ``False`` triggers the failure (not any
falsy value), and a tool that omits ``success`` entirely is still treated as
successful, preserving backward compatibility with legacy executors.

This file is falsifiable: on the UNMODIFIED code every ``..._false_case`` below
goes RED (the rejection is still wrapped as ``success=True``); on the FIXED code
they go GREEN.
"""
from __future__ import annotations

from typing import Any, Dict

from src.ai.tool_registry import Tool, ToolRegistry


def _active(reg: ToolRegistry, tool_id: str, fn) -> str:
    reg.register(Tool(
        tool_id=tool_id,
        name=tool_id,
        version="1.0.0",
        description="test tool",
        capability=tool_id,
        schema={},
        fn=fn,
        risk="LOW",
    ))
    reg.validate(tool_id)
    reg.approve(tool_id)
    reg.activate(tool_id)
    return tool_id


# --------------------------------------------------------------------------- #
# 1. The three behavioural cases the team-lead asked to be pinned.
# --------------------------------------------------------------------------- #
def test_in_band_rejection_becomes_failed_action():
    """Tool returns {'success': False, ...} (no raise) => ActionResult.success False."""
    def fn(**_i: Any) -> Dict[str, Any]:
        return {"success": False, "status": "rejected", "error": "refused by tool"}

    reg = ToolRegistry()
    tid = _active(reg, "t_reject", fn)
    res = reg.execute(tid, {})
    assert res.success is False, (
        "silent success: tool rejected in-band but ActionResult.success is True"
    )
    assert "refused by tool" in (res.error or ""), res.error
    # The original payload is preserved for callers that want the detail.
    assert isinstance(res.output, dict) and res.output.get("status") == "rejected"


def test_payload_without_success_key_still_succeeds():
    """Legacy tool returning {} (no 'success' key) => still ActionResult.success True."""
    def fn(**_i: Any) -> Dict[str, Any]:
        return {"result": "legacy"}

    reg = ToolRegistry()
    tid = _active(reg, "t_legacy", fn)
    res = reg.execute(tid, {})
    assert res.success is True, res
    assert res.output == {"result": "legacy"}


def test_raising_tool_still_uses_except_branch():
    """A tool that RAISES keeps going through the except branch (unchanged)."""
    def fn(**_i: Any) -> Dict[str, Any]:
        raise ValueError("boom")

    reg = ToolRegistry()
    tid = _active(reg, "t_raise", fn)
    res = reg.execute(tid, {})
    assert res.success is False
    assert "boom" in (res.error or "")


# --------------------------------------------------------------------------- #
# 2. The real defect: python_compute's in-band rejection must not be silent.
# --------------------------------------------------------------------------- #
def test_real_python_compute_rejection_is_not_silent_success():
    """Integration: a goal python_compute cannot compile must FAIL, not succeed.

    ``python_compute`` with a goal that carries no executable expression rejects
    in-band. Before the fix the router returned ActionResult(success=True)
    wrapping that rejection; now it must surface as a failure with the tool's
    own message.
    """
    from src.ai.lcore import LCore

    lcore = LCore(scope="L1", register_local_tools=True)
    res = lcore.router.execute(
        "python_compute", {"goal": "create a file named notes containing 'second run'"}
    )
    assert res.success is False, (
        "silent success: python_compute rejected but ActionResult.success is True"
    )
    assert "executable code" in (res.error or ""), res.error


def test_real_python_compute_success_path_unaffected():
    """A goal python_compute CAN evaluate still succeeds (no false failure).

    python_compute does NOT guess Python from prose (it explicitly refuses),
    so the caller must supply the executable ``expression``/``code``. The
    success path must stay ActionResult.success=True after the fix.
    """
    from src.ai.lcore import LCore

    lcore = LCore(scope="L1", register_local_tools=True)
    res = lcore.router.execute("python_compute", {"expression": "2+3"})
    assert res.success is True, res