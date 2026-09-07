"""Agent Factory & Runtime Service — MASTER-SPEC Phase 3 (Agent Runtime) tests."""
import uuid

import pytest

from src.ai.employee import Agent, AgentStatus
from src.ai.agent_factory import AgentSpec, AgentRuntimeService, AgentFactory


class _StubProvider:
    """Minimal provider stub — lifecycle tests never invoke it."""

    def generate_with_retry(self, prompt, **kwargs):
        return "stub-response"


def _make_agent() -> Agent:
    return Agent(
        id="a1",
        agent_type="general",
        name="a1",
        provider=_StubProvider(),
    )


class TestAgentStatusLifecycle:
    def test_status_has_lifecycle_states(self):
        assert AgentStatus.PAUSED.value == "paused"
        assert AgentStatus.STOPPED.value == "stopped"
        assert AgentStatus.RECOVERING.value == "recovering"

    def test_pause_resume(self):
        agent = _make_agent()
        assert agent.status == AgentStatus.IDLE
        assert agent.pause() is True
        assert agent.status == AgentStatus.PAUSED
        assert agent.resume() is True
        assert agent.status == AgentStatus.IDLE

    def test_stop_is_terminal_and_not_resumable(self):
        agent = _make_agent()
        assert agent.stop() is True
        assert agent.status == AgentStatus.STOPPED
        assert agent.resume() is False
        assert agent.status == AgentStatus.STOPPED

    def test_checkpoint_and_recover(self):
        agent = _make_agent()
        agent.status = AgentStatus.RUNNING
        agent.current_task = "task-x"
        agent.completed_tasks = 3
        agent.metadata = {"foo": "bar"}
        snap = agent.checkpoint()
        assert snap["status"] == "running"
        assert snap["current_task"] == "task-x"

        # Mutate, then recover from checkpoint.
        agent.stop()
        agent.completed_tasks = 0
        agent.current_task = None
        assert agent.recover() is True
        assert agent.status == AgentStatus.IDLE
        assert agent.current_task == "task-x"
        assert agent.completed_tasks == 3
        assert agent.metadata == {"foo": "bar"}

    def test_recover_without_checkpoint_returns_false(self):
        agent = _make_agent()
        assert agent.recover() is False


class TestAgentRuntimeService:
    def test_eight_methods_exist(self):
        svc = AgentRuntimeService(agent=_make_agent())
        for name in ("start", "pause", "resume", "stop", "cancel",
                     "execute", "checkpoint", "recover"):
            assert callable(getattr(svc, name))

    def test_start_pause_resume_stop_flow(self):
        agent = _make_agent()
        svc = AgentRuntimeService(agent=agent)
        assert svc.start() is True
        assert agent.status == AgentStatus.RUNNING
        assert svc.pause() is True
        assert agent.status == AgentStatus.PAUSED
        assert svc.resume() is True
        assert agent.status == AgentStatus.IDLE
        assert svc.stop() is True
        assert agent.status == AgentStatus.STOPPED

    def test_cancel_clears_current_task(self):
        agent = _make_agent()
        svc = AgentRuntimeService(agent=agent)
        agent.current_task = "pending-task"
        assert svc.cancel() is True
        assert agent.status == AgentStatus.STOPPED
        assert agent.current_task is None


class TestAgentFactory:
    def _patch_provider(self, monkeypatch):
        from src.ai import agent_factory as af
        monkeypatch.setattr(af, "get_provider", lambda: _StubProvider())

    def test_create_produces_agent_identity_runtime(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        spec = AgentSpec(
            agent_type="research",
            goal="summarize X",
            capabilities=["read", "write"],
            permissions=["read:docs"],
            name=name,
        )
        out = AgentFactory().create(spec)

        assert out["agent"] is not None
        assert out["agent"].agent_type == "research"
        assert out["identity"] is not None
        assert out["identity"].principal == name
        assert out["runtime"] is not None
        assert out["capabilities"] == ["read", "write"]

    def test_create_reuses_existing_identity(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        factory = AgentFactory()
        first = factory.create(AgentSpec(agent_type="general", goal="g", name=name))
        second = factory.create(AgentSpec(agent_type="general", goal="g", name=name))
        assert first["identity"].id == second["identity"].id


class TestAgentMemoryBinding:
    def _patch_provider(self, monkeypatch):
        from src.ai import agent_factory as af
        monkeypatch.setattr(af, "get_provider", lambda: _StubProvider())

    def test_memory_is_wired_not_none(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        out = AgentFactory().create(AgentSpec(agent_type="general", goal="g", name=name))
        assert out["memory"] is not None

    def test_store_and_recall_roundtrip(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        mem = AgentFactory().create(
            AgentSpec(agent_type="general", goal="g", name=name)
        )["memory"]
        mem.store("fact", "value-1")
        entry = mem.recall("fact")
        assert entry is not None
        assert entry.value == "value-1"
        assert f"agent:{name}" in entry.tags

    def test_cross_agent_isolation(self, monkeypatch):
        self._patch_provider(monkeypatch)
        a = AgentFactory().create(
            AgentSpec(agent_type="general", goal="g", name=f"a-{uuid.uuid4().hex[:8]}")
        )["memory"]
        b = AgentFactory().create(
            AgentSpec(agent_type="general", goal="g", name=f"b-{uuid.uuid4().hex[:8]}")
        )["memory"]
        a.store("a-key", "A")
        b.store("b-key", "B")
        # agent-scoped recall: each agent sees only its own tagged entries
        assert a.recall("a-key").value == "A"
        assert a.recall("b-key") is None
        assert b.recall("b-key").value == "B"
        assert b.recall("a-key") is None


class TestAgentPolicyBinding:
    def _patch_provider(self, monkeypatch):
        from src.ai import agent_factory as af
        monkeypatch.setattr(af, "get_provider", lambda: _StubProvider())

    def test_policy_is_wired_not_none(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        out = AgentFactory().create(AgentSpec(agent_type="general", goal="g", name=name))
        assert out["policy"] is not None

    def test_authorize_returns_real_decision(self, monkeypatch):
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        out = AgentFactory().create(
            AgentSpec(agent_type="general", goal="g", name=name, capabilities=["read"])
        )
        decision = out["policy"].authorize("read")
        assert decision is not None
        assert hasattr(decision, "decision")
        # default-deny: an action with no explicit allow rule is denied
        assert decision.is_allowed is False
        # traceability proves the policy engine actually ran
        assert isinstance(decision.traceability, list)


class TestEndToEnd:
    def _patch_provider(self, monkeypatch):
        from src.ai import agent_factory as af
        monkeypatch.setattr(af, "get_provider", lambda: _StubProvider())

    def test_minimal_closed_loop(self, monkeypatch):
        """create → execute → store → recall → authorize → audit."""
        self._patch_provider(monkeypatch)
        name = f"test-{uuid.uuid4().hex[:8]}"
        out = AgentFactory().create(
            AgentSpec(
                agent_type="worker",
                goal="do work",
                capabilities=["write"],
                permissions=["write:docs"],
                name=name,
            )
        )
        agent, runtime, memory, policy, identity = (
            out["agent"], out["runtime"], out["memory"], out["policy"], out["identity"],
        )

        # execute (stub provider returns a canned result)
        result = runtime.execute("produce a report")
        assert result["status"] == "completed"
        assert result["result"] == "stub-response"

        # store the result to memory, then recall it back
        memory.store("last_result", result["result"])
        entry = memory.recall("last_result")
        assert entry is not None
        assert entry.value == "stub-response"

        # policy check on the agent's action (default-deny, real decision)
        decision = policy.authorize("write")
        assert decision is not None
        assert decision.is_allowed is False

        # identity is verifiable via the identity kernel
        from src.kernels.identity import get_identity_manager
        mgr = get_identity_manager()
        assert mgr.get_identity_by_principal(name) is not None
        # audit trail recorded the identity creation
        trail = mgr.audit_trail(identity.id)
        assert len(trail) > 0
