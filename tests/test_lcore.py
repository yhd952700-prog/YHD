"""Tool Registry/Router + L-Core — MASTER-SPEC Phase 9 tests."""
from src.ai.tool_registry import Tool, ToolStatus, ToolRegistry, ToolRouter
from src.ai.lcore import LCore


def _tool(tool_id="t1", capability="execution_pipeline", fn=None):
    return Tool(
        tool_id=tool_id,
        name=f"tool-{tool_id}",
        version="1.0.0",
        description=f"does {capability}",
        capability=capability,
        schema={},
        fn=fn or (lambda **kw: {"ok": True}),
    )


class TestToolRegistryLifecycle:
    def test_full_lifecycle(self):
        reg = ToolRegistry()
        tid = reg.register(_tool())
        assert reg.status(tid) == ToolStatus.REGISTERED
        assert reg.validate(tid) is True
        assert reg.status(tid) == ToolStatus.VALIDATED
        assert reg.approve(tid) is True
        assert reg.status(tid) == ToolStatus.APPROVED
        assert reg.activate(tid) is True
        assert reg.status(tid) == ToolStatus.ACTIVE

    def test_activate_requires_approval(self):
        reg = ToolRegistry()
        tid = reg.register(_tool())
        # Skip validate/approve -> activate must fail.
        assert reg.activate(tid) is False
        assert reg.status(tid) == ToolStatus.REGISTERED

    def test_approve_requires_validate(self):
        reg = ToolRegistry()
        tid = reg.register(_tool())
        assert reg.approve(tid) is False
        assert reg.status(tid) == ToolStatus.REGISTERED

    def test_validate_rejects_incomplete_tool(self):
        reg = ToolRegistry()
        bad = Tool(tool_id="bad", name="", version="1", description="",
                   capability="", schema={}, fn=None)
        reg.register(bad)
        assert reg.validate("bad") is False
        assert reg.status("bad") == ToolStatus.REGISTERED

    def test_suspend_and_revoke(self):
        reg = ToolRegistry()
        tid = reg.register(_tool())
        reg.validate(tid); reg.approve(tid); reg.activate(tid)
        assert reg.suspend(tid) is True
        assert reg.status(tid) == ToolStatus.SUSPENDED
        assert reg.revoke(tid) is True
        assert reg.status(tid) == ToolStatus.REVOKED


class TestToolRegistryExecute:
    def test_execute_only_when_active(self):
        reg = ToolRegistry()
        tid = reg.register(_tool())
        # Not yet active -> execution refused.
        result = reg.execute(tid, {})
        assert result.success is False
        assert "not active" in result.error

    def test_execute_calls_real_fn(self):
        reg = ToolRegistry()
        calls = []
        tid = reg.register(_tool(fn=lambda **kw: calls.append(kw) or {"ran": True}))
        reg.validate(tid); reg.approve(tid); reg.activate(tid)

        result = reg.execute(tid, {"x": 1})
        assert result.success is True
        assert result.output == {"ran": True}
        assert calls == [{"x": 1}]  # fn actually invoked with inputs

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("ghost", {})
        assert result.success is False
        assert "unknown tool" in result.error

    def test_execute_fn_exception(self):
        reg = ToolRegistry()

        def boom(**kw):
            raise ValueError("boom")

        tid = reg.register(_tool(fn=boom))
        reg.validate(tid); reg.approve(tid); reg.activate(tid)
        result = reg.execute(tid, {})
        assert result.success is False
        assert result.error == "boom"


class TestToolRouter:
    def test_route_to_matching_capability(self):
        reg = ToolRegistry()
        tid = reg.register(_tool(tool_id="a", capability="execution_pipeline"))
        reg.validate(tid); reg.approve(tid); reg.activate(tid)

        router = ToolRouter(reg)
        tool = router.route("execution_pipeline")
        assert tool is not None
        assert tool.tool_id == "a"

    def test_execute_no_active_tool(self):
        reg = ToolRegistry()
        router = ToolRouter(reg)
        result = router.execute("execution_pipeline", {})
        assert result.success is False
        assert "no active tool" in result.error

    def test_route_skips_inactive_tool(self):
        reg = ToolRegistry()
        tid = reg.register(_tool(tool_id="a", capability="execution_pipeline"))
        # Registered but never activated -> not routed.
        router = ToolRouter(reg)
        assert router.route("execution_pipeline") is None


class TestLCore:
    def _register_exec(self, lcore):
        def execute_tool(goal=None, **kw):
            return {"ran": True, "goal": goal}
        lcore.register_tool(Tool(
            tool_id="exec-1", name="exec", version="1.0.0",
            description="execute a goal", capability="execution_pipeline",
            schema={}, fn=execute_tool,
        ))

    def test_handle_intent_runs_full_pipeline(self):
        lcore = LCore()
        self._register_exec(lcore)
        out = lcore.handle_intent("do the thing")
        assert out["status"] == "completed"
        assert out["task_count"] == 1
        assert all(r["success"] for r in out["results"].values())
        assert out["response"]  # synthesized outputs non-empty

    def test_handle_intent_policy_denied(self):
        lcore = LCore(authorize=lambda goal, plan: False)
        self._register_exec(lcore)
        out = lcore.handle_intent("do the thing")
        assert out["status"] == "policy_denied"
        assert out["results"] == {}  # nothing executed

    def test_handle_intent_no_tool_for_capability(self):
        lcore = LCore()  # no tools registered
        out = lcore.handle_intent("do the thing")
        assert out["status"] == "failed"
        assert any(not r["success"] for r in out["results"].values())

    def test_handle_intent_multi_task_decomposition(self):
        lcore = LCore()
        self._register_exec(lcore)  # covers execution_pipeline
        lcore.register_tool(Tool(
            tool_id="mem-1", name="mem", version="1.0.0",
            description="store", capability="multi_tier_memory",
            schema={}, fn=lambda **kw: {"stored": True},
        ))
        # "remember" -> memory task, "plan"/"schedule" -> execution task
        out = lcore.handle_intent("remember and plan the schedule")
        assert out["task_count"] == 2
        assert out["status"] == "completed"
