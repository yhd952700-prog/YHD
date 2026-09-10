"""
Multi-Agent Collaboration Protocol — MASTER-SPEC Phase 10.

Adds agent-to-agent message passing, role specialization, hierarchical
delegation, and sequential pipeline execution on top of the existing
`Agent` primitive (``src/ai/employee.py``). Reuses ``Agent.execute()`` which
is provider-backed and real — no fake agents (CODEX-CONTRACT NO FAKE AI).

Layering (existing -> new):

    Agent         (lifecycle + execute)      src/ai/employee.py
      L-- Employee     (fan-out task dist)   src/ai/employee.py
      L-- AgentPool    (fan-out across emp)  src/ai/employee.py
      L-- MultiAgentTeam (NEW: roles + message bus + hierarchy + pipeline)

Where the existing ``Employee``/``AgentPool`` model a single manager pushing
tasks down to homogeneous workers (fan-out/fan-in), this module models
*peer* collaboration: role-specialized agents that route addressed messages to
one another, delegate down a hierarchy, report results back, and hand data
down a sequential pipeline (MASTER-SPEC scenario §228: Manager -> Researchers
-> Analyst -> QA).
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import uuid
import time

from .employee import Agent, AgentStatus
from .providers import BaseProvider, get_provider
from .observability import observe, get_logger


class Role(Enum):
    """Specialized agent roles (MASTER-SPEC §63, scenario §228)."""
    MANAGER = "manager"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    QA = "qa"
    GENERAL = "general"


class MessageKind(Enum):
    """Inter-agent message kinds (collaboration primitives)."""
    ASK = "ask"            # request info/action; expect a reply
    TELL = "tell"          # inform without expecting action
    DELEGATE = "delegate"  # assign a subtask down the hierarchy
    REPORT = "report"      # deliver a result back to a delegator
    REVIEW = "review"      # emit/receive a review verdict
    RESULT = "result"      # raw pipeline hand-off payload


@dataclass
class AgentMessage:
    """A message addressed between two agents on a shared bus."""
    id: str
    sender: str
    receiver: str  # agent id, or "*" for broadcast
    kind: MessageKind
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    in_reply_to: Optional[str] = None


class MessageBus:
    """Addressable in-memory message bus.

    Real routing: a message is delivered to its named receiver's mailbox (or to
    every mailbox on broadcast) and nowhere else. ``poll`` drains a mailbox.
    """

    def __init__(self) -> None:
        self._mailboxes: Dict[str, List[AgentMessage]] = {}

    def register(self, agent_id: str) -> None:
        """Create a (possibly empty) mailbox so broadcast reaches this agent."""
        self._mailboxes.setdefault(agent_id, [])

    def send(self, message: AgentMessage) -> None:
        if message.receiver == "*":
            for box in self._mailboxes.values():
                box.append(message)
        else:
            self._mailboxes.setdefault(message.receiver, []).append(message)

    def poll(self, agent_id: str) -> List[AgentMessage]:
        """Return and drain the mailbox for ``agent_id``."""
        box = self._mailboxes.get(agent_id, [])
        if box:
            self._mailboxes[agent_id] = []
        return box

    def poll_one(self, agent_id: str) -> Optional[AgentMessage]:
        """Pop and return a single message (FIFO), leaving the rest queued.

        Unlike ``poll`` (which drains the whole mailbox), this lets a
        consuming loop process one message per step without dropping any
        backlog — the behaviour ``RuntimeLoop`` relies on.
        """
        box = self._mailboxes.get(agent_id)
        if not box:
            return None
        return box.pop(0)

    def pending(self, agent_id: str) -> int:
        return len(self._mailboxes.get(agent_id, []))


@dataclass
class Collaborator:
    """An ``Agent`` wrapped with a role and a mailbox on the shared bus."""

    agent: Agent
    role: Role
    bus: MessageBus

    def __post_init__(self) -> None:
        # Register the mailbox up front so broadcast reaches this member.
        self.bus.register(self.agent.id)

    @property
    def id(self) -> str:
        return self.agent.id

    def send(self, receiver: str, kind: MessageKind, payload: Dict[str, Any],
             in_reply_to: Optional[str] = None) -> str:
        message = AgentMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            sender=self.id,
            receiver=receiver,
            kind=kind,
            payload=payload,
            in_reply_to=in_reply_to,
        )
        self.bus.send(message)
        return message.id

    def receive(self) -> List[AgentMessage]:
        return self.bus.poll(self.id)

    def pending(self) -> int:
        return self.bus.pending(self.id)


class MultiAgentTeam:
    """A role-specialized team with message passing, hierarchy, and pipelines.

    Members are real ``Agent`` instances (provider-backed). Roles are used for
    routing; messages flow through a shared ``MessageBus``.
    """

    # A member is "available" when it has not run yet (IDLE) or has finished
    # its previous task (COMPLETED). FAILED/STOPPED/PAUSED members are not
    # auto-picked for new work.
    _AVAILABLE = (AgentStatus.IDLE, AgentStatus.COMPLETED)

    def __init__(self, name: str, provider: Optional[BaseProvider] = None) -> None:
        self.name = name
        self.provider = provider or get_provider()
        self.bus = MessageBus()
        self.members: Dict[str, Collaborator] = {}
        self._by_role: Dict[Role, List[str]] = {}
        self._log = get_logger("collaboration")

    def hire(self, role: Role, name: Optional[str] = None,
             agent_id: Optional[str] = None,
             system_prompt: Optional[str] = None,
             provider: Optional[BaseProvider] = None) -> str:
        """Create a role-specialized agent and add it to the team."""
        agent_id = agent_id or f"{role.value}_{uuid.uuid4().hex[:8]}"
        name = name or f"{self.name}-{role.value}"
        agent = Agent(
            id=agent_id,
            agent_type=role.value,
            name=name,
            provider=provider or self.provider,
            system_prompt=system_prompt or "",
        )
        self.members[agent_id] = Collaborator(agent=agent, role=role, bus=self.bus)
        self._by_role.setdefault(role, []).append(agent_id)
        return agent_id

    def member_ids(self, role: Role) -> List[str]:
        return list(self._by_role.get(role, []))

    def _first_available(self, role: Role) -> Optional[str]:
        for agent_id in self._by_role.get(role, []):
            if self.members[agent_id].agent.status in self._AVAILABLE:
                return agent_id
        return None

    @observe("MultiAgentTeam.delegate")
    def delegate(self, manager_id: str, role: Role, task: str, **kwargs) -> Dict[str, Any]:
        """Manager delegates a task to the first available ``role`` agent.

        Routes a DELEGATE message through the bus; the worker receives it from
        its mailbox, executes (a real provider call), and sends a REPORT back
        to the manager. The report remains in the manager's mailbox.
        """
        manager = self.members.get(manager_id)
        if manager is None:
            return {"status": "failed", "error": f"unknown manager {manager_id}"}

        target = self._first_available(role)
        if target is None:
            return {"status": "failed", "error": f"no available {role.value} agent"}

        msg_id = manager.send(target, MessageKind.DELEGATE, {"task": task})

        worker = self.members[target]
        received = worker.receive()
        if not received:
            return {"status": "failed", "error": f"delegate message lost for {target}"}

        # The task actually travels in the DELEGATE message payload.
        result = worker.agent.execute(received[0].payload.get("task", task), **kwargs)
        result["delegated_by"] = manager_id
        result["role"] = role.value

        worker.send(manager_id, MessageKind.REPORT,
                    {"task": task, "result": result}, in_reply_to=msg_id)
        return result

    @observe("MultiAgentTeam.pipeline")
    def pipeline(self, stages: List[Role], seed: str, **kwargs) -> Dict[str, Any]:
        """Run a sequential pipeline: each stage's output feeds the next.

        Each stage picks the first available agent of that role and hands its
        output to the following stage. Returns per-stage results and the final
        output.
        """
        current = seed
        stage_results: List[Dict[str, Any]] = []

        for role in stages:
            target = self._first_available(role)
            if target is None:
                return {
                    "status": "failed",
                    "error": f"no available {role.value} agent",
                    "stages": stage_results,
                }
            worker = self.members[target]
            result = worker.agent.execute(current, **kwargs)
            stage_results.append({
                "role": role.value,
                "agent_id": target,
                "status": result.get("status"),
                "result": result.get("result"),
            })
            if result.get("status") != "completed":
                return {
                    "status": "failed",
                    "error": f"stage {role.value} failed",
                    "stages": stage_results,
                }
            current = result.get("result", "")

        return {"status": "completed", "stages": stage_results, "final": current}

    @observe("MultiAgentTeam.broadcast")
    def broadcast(self, sender_id: str, kind: MessageKind, payload: Dict[str, Any]) -> str:
        """Broadcast a message to every member from ``sender_id``."""
        sender = self.members.get(sender_id)
        if sender is None:
            return ""
        return sender.send("*", kind, payload)
