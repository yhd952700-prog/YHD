"""Tool Registry/Router + L-Core — MASTER-SPEC Phase 9 tests."""
import os

from src.ai.tool_registry import Tool, ToolStatus, ToolRegistry, ToolRouter
from src.ai.lcore import LCore
from src.kernels.event import EventScope, get_event_bus, subscribe_event
from src.kernels.execution import ExecutionJournal, task_result_text


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

    def test_handle_intent_runs_full_pipeline(self, tmp_path):
        # ``execution_pipeline`` 是硬副作用能力：L-Core 直接路由工具，必须像
        # ExecutionEngine 一样先接账本，否则会被同一道闸门拒绝。
        lcore = LCore(journal=ExecutionJournal(str(tmp_path / "journal.db")))
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

    def test_handle_intent_no_tool_for_capability(self, tmp_path):
        # 接上账本以保留本用例的原意：闸门放行后，失败原因必须是「没有可用工具」，
        # 而不是被硬副作用闸门拦下。
        lcore = LCore(journal=ExecutionJournal(str(tmp_path / "journal.db")))
        out = lcore.handle_intent("do the thing")
        assert out["status"] == "failed"
        assert any(not r["success"] for r in out["results"].values())
        assert any(
            "no active tool" in (r["error"] or "")
            for r in out["results"].values()
        )

    def test_handle_intent_multi_task_decomposition(self, tmp_path):
        lcore = LCore(journal=ExecutionJournal(str(tmp_path / "journal.db")))
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

    def test_hard_side_effect_is_refused_without_journal(self):
        """无账本时，直路由的硬副作用与 ExecutionEngine 一样 fail-closed。"""
        lcore = LCore()  # no journal
        calls = []

        def execute_tool(goal=None, **kw):
            calls.append(goal)
            return {"ran": True}

        lcore.register_tool(Tool(
            tool_id="exec-1", name="exec", version="1.0.0",
            description="execute a goal", capability="execution_pipeline",
            schema={}, fn=execute_tool,
        ))
        out = lcore.handle_intent("do the thing")

        assert out["status"] == "failed"
        assert calls == [], "工具必须一次都没有被调用（没有产生副作用）"
        errors = [r["error"] for r in out["results"].values()]
        assert errors and all("幂等账本" in e for e in errors)
        assert all("ExecutionJournal" in e for e in errors)

    def test_reentrant_capability_runs_without_journal(self):
        """只读/可安全重入能力不需要账本，照常执行并留 advisory（不阻断）。"""
        lcore = LCore()  # no journal
        calls = []

        def compute_tool(expression=None, **kw):
            calls.append(expression)
            return {"result": 5}

        lcore.register_tool(Tool(
            tool_id="calc-1", name="calc", version="1.0.0",
            description="calculate", capability="python_compute",
            schema={"expression": "string"}, fn=compute_tool,
        ))
        out = lcore.handle_intent("calculate 2+3")

        assert out["status"] == "completed"
        assert calls == ["2+3"], "可安全重入能力应真实执行一次"

    # ---- 稳定幂等键（跨调用占坑/回放） --------------------------------

    @staticmethod
    def _effects(path) -> int:
        if not os.path.exists(path):
            return 0
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def _memory_lcore(self, tmp_path):
        """multi_tier_memory 是硬副作用能力：每执行一次就往文件追加一行。"""
        log = str(tmp_path / "effects.log")
        journal = ExecutionJournal(str(tmp_path / "journal.db"))
        lcore = LCore(journal=journal)

        def store(content=None, **kw):
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("stored\n")
            return {"success": True, "stored": True, "content": content}

        lcore.register_tool(Tool(
            tool_id="mem-1", name="mem", version="1.0.0",
            description="store", capability="multi_tier_memory",
            schema={}, fn=store,
        ))
        return lcore, journal, log

    def test_same_action_id_writes_once_and_replays(self, tmp_path):
        """同一稳定 action id 投递两次：只落一份，第二次回放首次结果。"""
        lcore, journal, log = self._memory_lcore(tmp_path)
        try:
            first = lcore.handle_intent("remember the report", action_id="req-42")
            second = lcore.handle_intent("remember the report", action_id="req-42")
        finally:
            journal.close()

        assert self._effects(log) == 1, "同一 action id 不能写两份"
        assert first["status"] == "completed"
        assert second["status"] == "completed"
        assert all(not r["replayed"] for r in first["results"].values())
        assert all(r["replayed"] for r in second["results"].values())
        outputs = [r["output"] for r in second["results"].values()]
        assert any(o.get("replayed_from") for o in outputs)
        # 与 ExecutionEngine 共用同一个渲染器：回放必须能被人看见「未重复执行」，
        # 而不是两条结果长得一模一样。
        rendered = [task_result_text(o) for o in outputs]
        assert any("已复用首次执行结果" in line for line in rendered)

    def test_different_action_ids_both_execute(self, tmp_path):
        """不同 action id 是两次真实不同的请求：必须各执行一次，不能被吞。"""
        lcore, journal, log = self._memory_lcore(tmp_path)
        try:
            lcore.handle_intent("remember the report", action_id="req-a")
            lcore.handle_intent("remember the report", action_id="req-b")
        finally:
            journal.close()

        assert self._effects(log) == 2

    def test_without_action_id_behaviour_is_unchanged(self, tmp_path):
        """缺省 action id：保持旧行为（每次新 goal id、不复用、不占坑）。"""
        lcore, journal, log = self._memory_lcore(tmp_path)
        try:
            lcore.handle_intent("remember the report")
            lcore.handle_intent("remember the report")
        finally:
            journal.close()

        assert self._effects(log) == 2, "没有稳定键时不能假装做到了跨调用去重"

    def test_refusal_publishes_action_failed_event(self):
        """拒绝必须进事件流，否则接进网关后会出现「界面成功、实际被拒」。"""
        received = []

        def handler(event):
            if event.source == "lcore":
                received.append(event)

        sub = subscribe_event(
            "action_failed", handler, scope=EventScope.L1
        )
        try:
            lcore = LCore()  # no journal -> hard side effect refused
            self._register_exec(lcore)
            out = lcore.handle_intent("do the thing")
        finally:
            get_event_bus().unsubscribe(sub)

        assert out["status"] == "failed"
        assert received, "拒绝不能只有日志，必须发布 action_failed 事件"
        event = received[-1]
        assert event.data["status"] == "idempotency_ledger_missing"
        assert event.data["capability_id"] == "execution_pipeline"
        assert "幂等账本" in event.data["error"]
