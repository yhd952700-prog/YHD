"""Multi-Agent Collaboration Protocol — MASTER-SPEC Phase 10 tests."""
from src.ai.collaboration import (
    Role,
    MessageKind,
    AgentMessage,
    MessageBus,
    Collaborator,
    MultiAgentTeam,
)
from src.ai.employee import Agent, AgentStatus


class _EchoProvider:
    """Returns a deterministic transform of the input prompt."""

    def __init__(self):
        self.prompts = []

    def generate_with_retry(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return f"OUT({prompt})"


def _make_team(name="team"):
    return MultiAgentTeam(name=name, provider=_EchoProvider())


class TestMessageBus:
    def test_send_delivers_only_to_receiver(self):
        bus = MessageBus()
        msg = AgentMessage(
            id="m1", sender="a", receiver="b",
            kind=MessageKind.TELL, payload={},
        )
        bus.send(msg)
        assert bus.pending("b") == 1
        assert bus.pending("a") == 0
        assert bus.pending("c") == 0

    def test_broadcast_reaches_all_known_mailboxes(self):
        bus = MessageBus()
        bus.register("a")
        bus.register("b")
        bus.register("c")
        msg = AgentMessage(
            id="m1", sender="a", receiver="*",
            kind=MessageKind.TELL, payload={},
        )
        bus.send(msg)
        assert bus.pending("a") == 1
        assert bus.pending("b") == 1
        assert bus.pending("c") == 1

    def test_poll_drains_mailbox(self):
        bus = MessageBus()
        msg = AgentMessage(
            id="m1", sender="a", receiver="b",
            kind=MessageKind.TELL, payload={},
        )
        bus.send(msg)
        received = bus.poll("b")
        assert len(received) == 1
        assert received[0].id == "m1"
        assert bus.pending("b") == 0


class TestCollaborator:
    def test_send_receive_roundtrip(self):
        bus = MessageBus()
        a = Collaborator(
            agent=Agent(id="a", agent_type="general", name="a",
                        provider=_EchoProvider()),
            role=Role.GENERAL,
            bus=bus,
        )
        b = Collaborator(
            agent=Agent(id="b", agent_type="general", name="b",
                        provider=_EchoProvider()),
            role=Role.GENERAL,
            bus=bus,
        )
        a.send("b", MessageKind.ASK, {"q": "ready?"})
        received = b.receive()
        assert len(received) == 1
        assert received[0].sender == "a"
        assert received[0].kind == MessageKind.ASK
        assert received[0].payload == {"q": "ready?"}
        assert b.pending() == 0


class TestMultiAgentTeam:
    def test_hire_creates_role_specialized_agent(self):
        team = _make_team()
        rid = team.hire(Role.RESEARCHER)
        assert rid in team.members
        assert team.members[rid].role == Role.RESEARCHER
        assert team.members[rid].agent.agent_type == "researcher"

    def test_delegate_routes_to_correct_role(self):
        team = _make_team()
        manager = team.hire(Role.MANAGER)
        researcher = team.hire(Role.RESEARCHER)
        qa = team.hire(Role.QA)

        result = team.delegate(manager, Role.RESEARCHER, "investigate X")
        assert result["status"] == "completed"
        assert result["agent_id"] == researcher  # routed to researcher, not qa
        assert result["role"] == "researcher"
        assert result["delegated_by"] == manager

    def test_delegate_records_report_back_to_manager(self):
        team = _make_team()
        manager = team.hire(Role.MANAGER)
        team.hire(Role.RESEARCHER)

        team.delegate(manager, Role.RESEARCHER, "task")
        manager_collab = team.members[manager]
        reports = manager_collab.receive()
        assert len(reports) == 1
        assert reports[0].kind == MessageKind.REPORT
        assert reports[0].sender != manager  # came from the worker

    def test_delegate_no_available_agent_returns_failed(self):
        team = _make_team()
        manager = team.hire(Role.MANAGER)
        # No researcher hired.
        result = team.delegate(manager, Role.RESEARCHER, "task")
        assert result["status"] == "failed"
        assert "no available researcher agent" in result["error"]

    def test_delegate_unknown_manager_returns_failed(self):
        team = _make_team()
        team.hire(Role.RESEARCHER)
        result = team.delegate("ghost", Role.RESEARCHER, "task")
        assert result["status"] == "failed"
        assert "unknown manager ghost" in result["error"]

    def test_pipeline_sequential_handoff(self):
        team = _make_team()
        team.hire(Role.RESEARCHER)
        team.hire(Role.ANALYST)
        team.hire(Role.QA)

        out = team.pipeline([Role.RESEARCHER, Role.ANALYST, Role.QA], "raw")
        assert out["status"] == "completed"
        # Each stage's output feeds the next: raw -> OUT(raw) -> OUT(OUT(raw)) -> OUT(OUT(OUT(raw)))
        assert out["final"] == "OUT(OUT(OUT(raw)))"
        assert [s["role"] for s in out["stages"]] == ["researcher", "analyst", "qa"]

    def test_pipeline_short_circuits_on_missing_role(self):
        team = _make_team()
        team.hire(Role.RESEARCHER)
        # No analyst hired.
        out = team.pipeline([Role.RESEARCHER, Role.ANALYST], "raw")
        assert out["status"] == "failed"
        assert "no available analyst agent" in out["error"]
        # The researcher stage ran before failing.
        assert len(out["stages"]) == 1

    def test_broadcast_reaches_every_member(self):
        team = _make_team()
        manager = team.hire(Role.MANAGER)
        team.hire(Role.RESEARCHER)
        team.hire(Role.QA)

        team.broadcast(manager, MessageKind.TELL, {"goal": "shipping"})
        for mid, member in team.members.items():
            received = member.receive()
            assert len(received) == 1
            assert received[0].kind == MessageKind.TELL
            assert received[0].payload == {"goal": "shipping"}
