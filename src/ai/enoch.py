"""ENOCH Long-Running Mission Engine — MASTER-SPEC Phase 13 (§58-60).

Implements the LONG-RUNNING MISSION loop (§59):

    Mission -> Persistent Agent -> Checkpoint -> Scheduler ->
    Observe -> Event Detection -> Replan -> Execute -> Verify ->
    Memory -> Continue

Core invariant (§59): a mission MUST NOT depend on a single HTTP request
lifecycle. All mission state is persisted to disk (JSON) via ``MissionStore``
so that a new process can reconstruct the in-flight mission from the same
directory — i.e. state survives "process restart".

This module EXTENDS existing primitives — it reuses
``src.kernels.execution`` (Goal / Task / ExecutionPlan / Verifier / GoalDecomposer /
PlanBuilder / ExecutionEngine) and the Agent runtime (``AgentRuntimeService`` /
``Agent`` from ``agent_factory`` / ``employee``). It does NOT reimplement them.

Per §158 (NO FAKE): capabilities we cannot honestly deliver are marked
``NOT_IMPLEMENTED`` rather than faked as successful. Replanning here is a
deterministic heuristic built on the real ``GoalDecomposer``; an LLM-driven
replan is explicitly out of scope and signalled, not fabricated.

§60 Scheduler: minimal capability-based assignment of missions to agents.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .employee import Agent
from .agent_factory import AgentRuntimeService
from .observability import observe


# ---------------------------------------------------------------------------
# §58 / §59 Mission model
# ---------------------------------------------------------------------------
class MissionStatus(str, Enum):
    """Lifecycle state of a long-running mission.

    CREATED        — instantiated, not yet started
    RUNNING        — an observation/execute/verify cycle is in flight
    CHECKPOINTED   — paused mid-flight with a persisted snapshot
    NEEDS_REPLAN   — verify failed; requires a replan before continuing
    COMPLETED      — verify passed, mission finished
    FAILED         — exhausted attempts / terminal failure
    """

    CREATED = "created"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    NEEDS_REPLAN = "needs_replan"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Mission:
    """A long-running mission that persists across process restarts."""

    id: str
    description: str
    status: MissionStatus = MissionStatus.CREATED
    checkpoint: Optional[Dict[str, Any]] = None
    attempts: int = 0
    required_capability: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # ---- (de)serialization for file persistence ----
    def to_payload(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "checkpoint": self.checkpoint,
            "attempts": self.attempts,
            "required_capability": self.required_capability,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Mission":
        """Reconstruct a Mission from a JSON-friendly dict."""
        return cls(
            id=payload["id"],
            description=payload["description"],
            status=MissionStatus(payload["status"]),
            checkpoint=payload.get("checkpoint"),
            attempts=int(payload.get("attempts", 0)),
            required_capability=payload.get("required_capability"),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )


# ---------------------------------------------------------------------------
# File-backed persistence (survives process restart)
# ---------------------------------------------------------------------------
class MissionStore:
    """JSON file store for missions, keyed by mission id.

    Constructed with a directory path. A brand-new ``MissionStore`` pointed
    at the same directory transparently reads missions written by a previous
    instance — this is what makes state survive a process restart.
    """

    def __init__(self, directory: Optional[str] = None):
        if directory is None:
            directory = tempfile.mkdtemp(prefix="enoch-")
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, mission_id: str) -> str:
        # mission ids may contain chars unsafe for filenames; hex/uuid safe.
        safe_id = mission_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self.directory, f"{safe_id}.json")

    @observe("enoch.mission_store.save")
    def save(self, mission: Mission) -> None:
        """Persist a mission (overwrites by id)."""
        mission.updated_at = datetime.utcnow()
        with open(self._path(mission.id), "w", encoding="utf-8") as fh:
            json.dump(mission.to_payload(), fh, ensure_ascii=False, indent=2)

    @observe("enoch.mission_store.load")
    def load(self, mission_id: str) -> Optional[Mission]:
        """Load a mission by id, or None if absent."""
        path = self._path(mission_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return Mission.from_payload(json.load(fh))

    def list_missions(self) -> List[Mission]:
        """Return all persisted missions (newest updated_at first)."""
        missions: List[Mission] = []
        if not os.path.isdir(self.directory):
            return missions
        for name in os.listdir(self.directory):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.directory, name), "r", encoding="utf-8") as fh:
                try:
                    missions.append(Mission.from_payload(json.load(fh)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        missions.sort(key=lambda m: m.updated_at, reverse=True)
        return missions

    def delete(self, mission_id: str) -> bool:
        """Delete a mission; returns True if a file was removed."""
        path = self._path(mission_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


# ---------------------------------------------------------------------------
# §60 Scheduler — minimal capability-based assignment
# ---------------------------------------------------------------------------
def _agent_capabilities(agent: Any) -> set:
    """Extract the capability set an agent advertises.

    Supports real ``Agent`` objects (via ``metadata['capabilities']``) as well
    as lightweight doubles exposing ``capability`` (str) or ``capabilities``
    (list[str]). Decoupled on purpose so the scheduler is unit-testable
    without spinning up a provider.
    """
    caps: set = set()
    single = getattr(agent, "capability", None)
    if single:
        caps.add(single)
    multi = getattr(agent, "capabilities", None)
    if multi:
        caps.update(multi)
    meta = getattr(agent, "metadata", None)
    if isinstance(meta, dict) and "capabilities" in meta:
        caps.update(meta["capabilities"])
    return caps


def _agent_id(agent: Any, index: int) -> str:
    aid = getattr(agent, "id", None)
    if aid:
        return str(aid)
    return f"agent-{index}"


class Scheduler:
    """§60 minimal scheduler: match mission.required_capability to an agent."""

    def schedule(self, missions: List[Mission], agents: List[Any]) -> Dict[str, str]:
        """Assign missions to agents by capability.

        Returns {mission_id: agent_id}. A mission with no matching agent is
        left unassigned (absent from the result). Each agent is used at most
        once (first-fit).
        """
        assignments: Dict[str, str] = {}
        used: set = set()
        for mission in missions:
            required = mission.required_capability
            if not required:
                continue
            for i, agent in enumerate(agents):
                if i in used:
                    continue
                if required in _agent_capabilities(agent):
                    assignments[mission.id] = _agent_id(agent, i)
                    used.add(i)
                    break
        return assignments


# ---------------------------------------------------------------------------
# §59 MissionRunner — one observe/execute/verify cycle per run() call
# ---------------------------------------------------------------------------
class MissionRunner:
    """Drives the §59 LONG-RUNNING MISSION loop for a single mission.

    The runner is deterministic and fully testable: execution and verification
    are injected as callables so no real LLM/HTTP request is required.

        run(mission_id, observation):
            load mission
            -> status RUNNING, record checkpoint
            -> execute_fn(observation)  -> result dict
            -> verify_fn(result)        -> bool
            on success: status COMPLETED, save, return summary
            on failure: attempts += 1, status NEEDS_REPLAN,
                        record replan checkpoint, save, return summary
            every step persisted to the store (survives restart)
    """

    def __init__(
        self,
        store: MissionStore,
        execute_fn: Callable[[Any], Dict[str, Any]],
        verify_fn: Callable[[Dict[str, Any]], bool],
        max_attempts: int = 3,
        replanner: Optional[Any] = None,
    ):
        self.store = store
        self.execute_fn = execute_fn
        self.verify_fn = verify_fn
        self.max_attempts = max_attempts
        self.replanner = replanner

    @observe("enoch.mission_runner.run")
    def run(self, mission_id: str, observation: Any) -> Dict[str, Any]:
        mission = self.store.load(mission_id)
        if mission is None:
            return {
                "mission_id": mission_id,
                "status": None,
                "error": "not_found",
                "completed": False,
            }

        # --- Observe / running ---
        mission.status = MissionStatus.RUNNING
        mission.checkpoint = {
            "stage": "running",
            "attempt": mission.attempts + 1,
            "observation": observation,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.store.save(mission)

        # --- Execute (injected, deterministic) ---
        result = self.execute_fn(observation)

        # --- Verify (injected, deterministic) ---
        verify_passed = bool(self.verify_fn(result))

        if verify_passed:
            mission.status = MissionStatus.COMPLETED
            mission.checkpoint = {
                "stage": "completed",
                "attempt": mission.attempts,
                "observation": observation,
                "result": result,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self.store.save(mission)
            return self._summary(mission, result, verify_passed, needs_replan=False)

        # --- Failure: replan marker + bump attempt ---
        mission.attempts += 1
        replan = self._build_replan(mission, observation, result)
        mission.status = (
            MissionStatus.FAILED
            if mission.attempts >= self.max_attempts
            else MissionStatus.NEEDS_REPLAN
        )
        mission.checkpoint = {
            "stage": "replan",
            "attempt": mission.attempts,
            "observation": observation,
            "last_result": result,
            "verify_passed": False,
            "replan": replan,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.store.save(mission)
        return self._summary(mission, result, verify_passed, needs_replan=True)

    def _build_replan(
        self, mission: Mission, observation: Any, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Honest, deterministic replan hint built on the real kernel.

        Uses ``GoalDecomposer`` from the Execution Kernel to propose candidate
        tasks from the mission description. If the kernel cannot be imported,
        we mark the replan as NOT_IMPLEMENTED (§158) rather than fabricate one.
        """
        try:
            from ..kernels.execution import Goal, GoalDecomposer

            goal = Goal(id=mission.id, natural_language=mission.description)
            tasks = GoalDecomposer().decompose(goal)
            return {
                "strategy": "heuristic_decomposition",
                "source": "GoalDecomposer",
                "suggested_tasks": [
                    {"name": t.name, "capability_id": t.capability_id}
                    for t in tasks
                ],
                "attempts": mission.attempts,
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "strategy": "NOT_IMPLEMENTED",
                "reason": f"replan kernel unavailable: {exc}",
                "attempts": mission.attempts,
            }

    @staticmethod
    def _summary(
        mission: Mission,
        result: Dict[str, Any],
        verify_passed: bool,
        needs_replan: bool,
    ) -> Dict[str, Any]:
        return {
            "mission_id": mission.id,
            "status": mission.status.value,
            "attempts": mission.attempts,
            "verify_passed": verify_passed,
            "needs_replan": needs_replan,
            "completed": mission.status == MissionStatus.COMPLETED,
            "checkpoint_saved": mission.checkpoint is not None,
        }


def create_mission(
    description: str,
    required_capability: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> Mission:
    """Factory helper to create a mission with a fresh id."""
    return Mission(
        id=mission_id or f"mission-{uuid.uuid4().hex[:12]}",
        description=description,
        status=MissionStatus.CREATED,
        required_capability=required_capability,
    )


__all__ = [
    "MissionStatus",
    "Mission",
    "MissionStore",
    "Scheduler",
    "_agent_capabilities",
    "MissionRunner",
    "create_mission",
    "Agent",
    "AgentRuntimeService",
]
