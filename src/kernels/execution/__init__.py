"""Execution Kernel — Goal→Task→Plan→Action→Verify

The Execution Kernel orchestrates the full execution pipeline:
Goal → Task Decomposition → Execution Plan → Action → Verify.
Supports parallel execution, retries, checkpoints, and feedback loops.

依据 Definition Lock §112: Execution Kernel 必须能够
- Decompose natural language goals into structured tasks
- Create executable plans with dependencies
- Execute actions with retry/circuit breaker
- Verify outcomes against criteria
- Support checkpoints and rollback
- Emit events for traceability
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
import uuid
import os
import re
import hashlib
import threading

# Import dependencies
from src.kernels.context import ContextKernel, ContextInput, ContextInputType, create_context_kernel  # noqa: F401
from src.kernels.capability import get_capability_registry, CapabilityScope, check_capability_scope
from src.kernels.event import get_event_bus, publish_event, EventScope, EventPriority
from src.kernels.resource import get_resource_manager, ResourceType, allocate_resource, release_resource  # noqa: F401
from src.kernels._crosscutting import kernel_action, kernel_action_correlation_id
from ._journal import ExecutionJournal, JournalEvent, new_execution_id  # noqa: F401


# Seam for real capability execution. The Execution Kernel must not depend on
# the ai layer (strict layering: spec -> kernels -> ai -> gateway/console), so
# the actual executor is injected at assembly time by an upper layer.
CapabilityExecutor = Callable[[str, Dict[str, Any]], Any]

#: Opt-in switch for the **simulated** capability path.
#:
#: Why it exists (the real defect fixed here)
#: ----------------------------------------
#: Previously, when no ``capability_executor`` was injected, :meth:`ActionExecutor.execute`
#: fell through to ``_simulate_capability`` and returned ``status="simulated"`` while
#: marking the action **success**. So the production path (gateway -> AgentRuntime ->
#: ExecutionEngine) could report a goal as ``completed`` / ``SUCCESS`` without doing
#: anything. Reporting success while doing nothing is *worse* than failing, so an
#: unwired kernel must fail closed. The simulated path is retained for demo /
#: benchmark / unit tests, but only when this env var is explicitly set, and the
#: result still carries ``status="simulated"`` / ``simulated=True`` so it can never
#: be mistaken for a real execution.
SIMULATION_OPT_IN_ENV = "LIUHAO_ALLOW_SIMULATED_EXECUTION"

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def simulated_execution_enabled() -> bool:
    """Whether the simulated capability path has been explicitly opted in.

    Read from the environment on **every call** (not cached at import) so tests /
    demos can flip it for the duration of a single run.
    """
    return (os.environ.get(SIMULATION_OPT_IN_ENV) or "").strip().lower() in _TRUTHY_ENV_VALUES


class GoalCancelled(Exception):
    """Raised when a running goal is aborted via its stop flag.

    The AgentRuntime / gateway set a :class:`threading.Event` keyed by goal_id;
    the execution loop checks it between tasks (and before each retry) and raises
    this so the goal terminates cleanly as CANCELLED instead of running to
    completion. This is a real, cooperative cancellation -- never a silent
    success or a faked stop.
    """


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    #: The work ran and its side effects are real, but it was CLAIMED for an
    #: agent that could not be resolved -- so it is deliberately NOT recorded as
    #: an ordinary completed task. Added for ARM1: an ``assigned_agent`` that no
    #: registered agent answers to must not be able to silently read as
    #: "completed, performed by that agent". The bytes on disk are unchanged; the
    #: attribution is what is refused.
    #:
    #: DECISION (A) -- this status means "the work is complete; only the
    #: attribution could not be established". It must therefore be treated AS
    #: COMPLETED everywhere the question is "did the work finish?":
    #:   * the task stays in ``completed_tasks`` (so the goal still completes);
    #:   * its artifacts are still collected and reported;
    #:   * it is still counted as completed in summaries.
    #: Rejecting a false attribution must never be allowed to escalate into
    #: "real, finished work is reported as not done" -- that would be trading one
    #: false report for another. The attribution failure is carried separately
    #: (``result["agent_attribution"] == "unattributed"``) so it stays auditable.
    COMPLETED_UNATTRIBUTED = "completed_unattributed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class PlanStatus(str, Enum):
    """Execution plan status."""
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class VerifyResult(str, Enum):
    """Verification outcome.

    ``UNVERIFIED`` is a first-class honesty state: it means "the action ran and
    reported success, but nothing here PROVES it". It is deliberately NOT
    ``SUCCESS``. Before this existed, a task with no verification criteria fell
    into a "no criteria -> assume success" branch that returned
    ``SUCCESS``/``1.0`` -- so an executor that simply claimed success without
    producing any artifact was graded as a fully verified pass. Absence of
    evidence was being reported as evidence of success.
    """
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    OFF_TRACK = "off_track"
    NEEDS_HUMAN = "needs_human"
    #: Action reported success but no independent evidence could confirm it.
    #: Score is 0.0 because nothing was proven; ``replan_required`` stays False
    #: because retrying an unverifiable-but-not-failed task is not obviously
    #: useful (and would risk a replan loop). It is UNPROVEN, not PROVEN.
    UNVERIFIED = "unverified"


@dataclass
class Goal:
    """Natural language execution goal."""
    id: str
    natural_language: str
    scope: str = "L1"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Task:
    """Decomposed task from goal."""
    id: str
    goal_id: str
    name: str
    description: str
    capability_id: str  # Required capability to execute
    capability_namespace: str = "kernel"
    capability_version: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)  # Task IDs that must complete first
    inputs: Dict[str, Any] = field(default_factory=dict)
    expected_outputs: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    scope: str = "L1"
    max_retries: int = 3
    retry_count: int = 0
    timeout_seconds: int = 300
    assigned_agent: Optional[str] = None
    #: True when ``assigned_agent`` names an agent the caller EXPLICITLY injected
    #: (a real claim about who performs this work), vs. a synthetic id the
    #: decomposer derived when no pool was supplied. Only explicit claims are
    #: fail-closed on: a dangling explicit id must not read as "that agent did
    #: it", whereas a synthetic placeholder makes no such claim.
    agent_claimed: bool = False
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ExecutionPlan:
    """Execution plan with ordered tasks."""
    id: str
    goal_id: str
    tasks: List[Task] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: datetime = field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def get_ready_tasks(self, completed_task_ids: Set[str]) -> List[Task]:
        """Get tasks whose dependencies are all satisfied."""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep_id in completed_task_ids for dep_id in task.dependencies):
                ready.append(task)
        return ready

    def get_task(self, task_id: str) -> Optional[Task]:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None


@dataclass
class Action:
    """Executable action from a task."""
    id: str
    task_id: str
    capability_id: str
    capability_namespace: str
    inputs: Dict[str, Any]
    correlation_id: str
    scope: str
    timeout_seconds: int
    #: The agent this action is attributed to (ARM1). Optional with a default so
    #: every existing construction site keeps working unchanged. When set and the
    #: agent cannot be resolved to a real executor, the action still runs its
    #: capability -- but the task is recorded as UNATTRIBUTED rather than as
    #: work that agent performed.
    assigned_agent: Optional[str] = None
    #: Mirrors ``Task.agent_claimed``: True when ``assigned_agent`` is an
    #: EXPLICIT claim about who performs the work (vs. a synthetic placeholder).
    #: Defaults False so every existing Action construction site is unaffected.
    agent_claimed: bool = False


@dataclass
class ActionResult:
    """Result of an action execution."""
    action_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    retry_count: int = 0


@dataclass
class VerificationResult:
    """Result of outcome verification."""
    task_id: str
    result: VerifyResult
    score: float = 0.0  # 0.0 - 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    feedback: str = ""
    replan_required: bool = False
    escalation_required: bool = False


@dataclass
class ExecutionContext:
    """Full execution context for a goal."""
    goal: Goal
    plan: ExecutionPlan
    context_kernel: ContextKernel
    completed_tasks: Set[str] = field(default_factory=set)
    failed_tasks: Set[str] = field(default_factory=set)
    task_results: Dict[str, ActionResult] = field(default_factory=dict)
    verification_results: Dict[str, VerificationResult] = field(default_factory=dict)
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)


class GoalDecomposer:
    """Decomposes natural language goals into structured tasks."""

    # Explicit file-write phrasings the decomposer recognises. These are
    # *shapes*, not understanding: the goal must state a filename and (optionally)
    # the content, because downstream tools need tool-shaped inputs
    # (``path`` / ``content``), not a prose sentence. Anything that does not match
    # gets no file task at all -- inventing a path or content from prose would be
    # exactly the "reports success without knowing what to do" failure mode this
    # kernel must not have.
    _FILE_WRITE_A = re.compile(
        r"(?:create|write|save|make|generate)\s+(?:a\s+|an\s+)?(?:new\s+)?file\s+"
        r"(?:named|called|at|as)?\s*"
        r"(?P<path>[\w./\\-]+\.[A-Za-z0-9]+)"
        r"(?:\s*(?:containing|that\s+contains|with\s+content|with)\s*"
        r"(?:['\"](?P<quoted>.*?)['\"]|(?P<bare>.+)))?",
        re.IGNORECASE | re.DOTALL,
    )
    _FILE_WRITE_B = re.compile(
        r"(?:write|save)\s+['\"](?P<content>.*?)['\"]\s+"
        r"(?:to|into|in)\s+(?:the\s+)?(?:file\s+)?"
        r"(?P<path>[\w./\\-]+\.[A-Za-z0-9]+)",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, agent_ids: Optional[List[str]] = None):
        self.capability_registry = get_capability_registry()
        # Real agent ids (from the persisted Employee) injected by the gateway so
        # a goal's tasks are observably assigned to distinct real agents. When
        # unset (e.g. unit tests / headless), we fall back to synthetic ids.
        self.agent_ids = agent_ids

    @classmethod
    def _extract_file_write(cls, text: str) -> Optional[Dict[str, str]]:
        """Return ``{"path": ..., "content": ...}`` for an explicit file-write
        goal, else ``None`` (no file task is invented)."""
        m = cls._FILE_WRITE_B.search(text)
        if m:
            return {"path": m.group("path"), "content": m.group("content")}
        m = cls._FILE_WRITE_A.search(text)
        if m:
            content = m.group("quoted")
            if content is None:
                content = m.group("bare") or ""
            return {"path": m.group("path"), "content": content}
        return None

    #: Multi-word keywords, kept as phrases so word-boundary matching applies to
    #: the whole phrase rather than to each word independently.
    _MULTIWORD_KEYWORDS = ("run code",)

    #: An explicitly requested numeric/arithmetic computation, e.g.
    #: "calculate the sum of 2 and 3" / "compute 2+3" / "求 2*3 的值".
    #: Matched ONLY when the goal names a calculation AND carries an expression
    #: built from digits and arithmetic operators. Anything vaguer produces no
    #: compute task at all -- inventing an expression from prose would be the
    #: "reports success without knowing what to do" failure mode, and
    #: ``tools_local._resolve_code`` deliberately refuses to guess from prose
    #: too. Both sides must agree on that contract, so both refuse.
    _ARITH_EXPR = re.compile(
        r"(?P<expr>[-+]?\d+(?:\.\d+)?(?:\s*[-+*/%]\s*[-+]?\d+(?:\.\d+)?)+"
        r"|\(\s*[-+*/%\d.\s()]+\))"
    )
    #: "the sum of 2 and 3" / "sum 2 and 3" -- the most natural English phrasing
    #: for an arithmetic request, and one the operator-expression regex alone
    #: cannot see. Requires BOTH a number AND the word "and", so ordinary prose
    #: containing two unrelated numbers is not turned into an expression.
    _WORD_ARITH = re.compile(
        r"(?:sum|total|product|difference|plus|minus|times|divided\s+by|"
        r"divide|add(?:ition)?|multiply|subtract|averag\w+|"
        r"求和|总和|差|积|商|加|乘|减|除|平均|"
        r"加上|减去|乘以|除以)"
        r"[^0-9\-+*/%]{0,24}?"
        r"(?P<a>[-+]?\d+(?:\.\d+)?)"
        r"[^0-9\-+*/%]{0,12}?"
        r"(?:and|to|by|加|乘|减|除以|加上|减去|除)"
        r"\s*"
        r"(?P<b>[-+]?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _COMPUTE_REQUEST = re.compile(
        r"\b(?:calculat\w*|comput\w*|evaluat\w+|sum|total|difference|product|"
        r"arithmetic|math\b|multiply|subtract|divide|averag\w+|"
        r"计算|求和|差|积|商|算|数学)",
        re.IGNORECASE,
    )
    _CODE_DIRECTIVE = re.compile(
        r"(?:python|code)\s*:\s*(?P<code>.+)", re.IGNORECASE | re.DOTALL
    )

    @classmethod
    def _extract_compute(cls, text: str) -> Optional[Dict[str, Any]]:
        """Return ``{"expression": ...}`` or ``{"code": ...}`` for a compute goal.

        Two accepted shapes, in priority order:

        1. an explicit ``python:`` / ``code:`` directive -- passed through as
           ``code`` verbatim, exactly what ``tools_local._resolve_code`` wants;
        2. a stated arithmetic request (``calculate`` / ``计算`` / ...) that
           carries a well-formed numeric expression -- passed as ``expression``.

        Returns ``None`` when neither shape is present, so a vague goal such as
        "run some code" yields NO compute task rather than a fabricated one.
        """
        m = cls._CODE_DIRECTIVE.search(text)
        if m:
            return {"code": m.group("code").strip()}

        if not cls._COMPUTE_REQUEST.search(text):
            return None

        # "sum of 2 and 3" -> the operator is in the WORDS, not the digits.
        w = cls._WORD_ARITH.search(text)
        if w:
            verb = w.group(0).lower()
            a, b = w.group("a"), w.group("b")
            if any(k in verb for k in ("averag", "平均")):
                # Average is (a+b)/2, NOT a sum. Getting this wrong would
                # return a confidently WRONG number, which is worse than
                # refusing -- so it is handled explicitly.
                expr = f"(({a}+{b})/2)"
            elif any(k in verb for k in ("difference", "minus", "subtract",
                                         "减去", "差")):
                expr = f"({a}-{b})"
            elif any(k in verb for k in ("times", "multiply", "product",
                                         "乘", "积")):
                expr = f"({a}*{b})"
            elif any(k in verb for k in ("divided", "divide", "除以", "商")):
                expr = f"({a}/{b})"
            else:
                expr = f"({a}+{b})"
            return {"expression": expr}

        e = cls._ARITH_EXPR.search(text)
        if not e:
            return None
        return {"expression": re.sub(r"\s+", "", e.group("expr"))}

    @classmethod
    def _mentions(cls, text: str, keywords: Iterable[str]) -> bool:
        """True iff any keyword appears in ``text`` as a **whole word/phrase**.

        Why this is not ``any(kw in text for kw in keywords)``:
        substring matching cannot tell a keyword from a word that merely
        contains it, so it invented work the user never asked for. Measured on
        the real decomposer before this fix::

            "write a file named replan.txt containing done"
                -> ['Plan', 'WriteFile', 'WriteChecksum']
            "write a file named airplane.txt containing x"
                -> ['Plan', 'WriteFile', 'WriteChecksum']

        ``replan``/``airplane`` contain the substring "plan", so a phantom
        ``Plan`` task (``capability_id="execution_pipeline"``) was appended to a
        pure file-write goal. Two things then went wrong at once:

        1. **Intent drift** -- the plan executed a capability the user never
           asked for. The goal was not what the user said it was.
        2. **A false terminal state** -- no local tool serves
           ``execution_pipeline``, so that phantom task always fails, the
           context lands in ``PARTIAL`` with a failed task, and the runtime
           reports ``AgentRunState.FAILED`` even though the file the user asked
           for was written correctly. A goal that genuinely succeeded was
           reported as a failure.

        ASCII keywords are matched on word boundaries. CJK keywords have no word
        boundaries, so they keep substring semantics (Chinese has no spaces);
        multi-word ASCII keywords are matched as the whole phrase.
        """
        for kw in keywords:
            if not kw:
                continue
            # CJK (and any other non-ASCII) keyword: substring is the best
            # available test, since \b is defined against [a-zA-Z0-9_] only.
            if any(ord(ch) > 0x7F for ch in kw):
                if kw in text:
                    return True
                continue
            pattern = "|".join(
                re.escape(part)
                for part in kw.split()
            )
            if re.search(rf"(?<![A-Za-z0-9_])(?:{pattern})(?![A-Za-z0-9_])", text):
                return True
        return False

    def decompose(self, goal: Goal) -> List[Task]:
        """Decompose goal into tasks using capability registry."""
        # This is a simplified decomposition - in production would use LLM
        tasks = []

        # Analyze goal for keywords to determine required capabilities.
        # Lower-cased for matching only; the original text is what tasks carry.
        goal_lower = goal.natural_language.lower()
        # A file path in the goal is a literal ARGUMENT, not an instruction.
        # "write a file named plan.txt" asks for a file, not for a plan, so the
        # path is masked out before keyword matching -- otherwise the filename
        # alone would conjure a Plan task the user never mentioned.
        file_write_inputs = self._extract_file_write(goal.natural_language)
        search_text = goal_lower
        if file_write_inputs and file_write_inputs.get("path"):
            search_text = goal_lower.replace(file_write_inputs["path"].lower(), " ")
        mentions = lambda kws: self._mentions(search_text, kws)  # noqa: E731

        # Example decomposition patterns
        if mentions(("search", "find", "lookup", "query")):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Search",
                description="Search for information",
                capability_id="network_bus",
                capability_namespace="kernel",
                inputs={"query": goal.natural_language},
                scope=goal.scope,
            ))

        if mentions(("plan", "schedule", "organize")):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Plan",
                description="Create execution plan",
                capability_id="execution_pipeline",
                capability_namespace="kernel",
                inputs={"goal": goal.natural_language},
                scope=goal.scope,
            ))

        # "save" is AMBIGUOUS: it is a FILE verb here (``_FILE_WRITE_A`` /
        # ``_FILE_WRITE_B`` match "save a file named X") and a MEMORY verb
        # ("save this to memory"). Treating it as a memory verb unconditionally
        # meant every file-save goal silently grew a second task the user never
        # asked for -- and memory was written with the raw sentence as content:
        #     "save a file named report.txt containing 'hello'"
        #         -> [Memory Store(content="save a file named report.txt ..."),
        #             WriteFile, WriteChecksum]
        # When the goal is recognised as a file write, "save" is spent on the
        # file task; memory still fires on the UNAMBIGUOUS memory verbs
        # (remember / store / memory), so "save x.txt and remember it" works.
        memory_keywords = ("remember", "store", "memory")
        if not (file_write_inputs and file_write_inputs.get("path")):
            memory_keywords = memory_keywords + ("save",)
        if mentions(memory_keywords):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Memory Store",
                description="Store in memory",
                capability_id="multi_tier_memory",
                capability_namespace="kernel",
                inputs={"content": goal.natural_language},
                scope=goal.scope,
            ))

        # Local computation: route to the real RestrictedPython-backed tool.
        #
        # The task's INPUTS are tool-shaped (`expression` / `code`) whenever the
        # goal actually yields one. Previously the whole sentence was forwarded
        # as `{"goal": ...}`, which matches none of the three shapes
        # `tools_local._resolve_code` accepts, so EVERY compute-shaped goal
        # failed with an honest "needs executable code" rejection and the
        # capability was unreachable. Extracting the expression is what makes it
        # real.
        #
        # When NO expression can be extracted, we still ROUTE the task -- the
        # keyword contract is unchanged and narrowing it here would silently
        # break goals that legitimately need a human/LLM to supply the code.
        # Such a task keeps the previous `{"goal": ...}` shape and therefore
        # still fails honestly at the tool, which is the correct outcome: better
        # a visible, explained failure than a silent mis-route.
        compute_inputs = self._extract_compute(goal.natural_language)
        if compute_inputs is not None or mentions((
            "compute", "calculate", "calc", "transform", "run code", "python",
            "evaluate", "sum", "total", "difference", "product",
            "执行代码", "数学", "计算", "求和", "算", "差", "积", "商",
        )):
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Compute",
                description="Run a local computation",
                capability_id="python_compute",
                capability_namespace="kernel",
                inputs=compute_inputs or {"goal": goal.natural_language},
                scope=goal.scope,
            ))

        # File write: route to the real, workspace-contained local tool.
        # Inputs are TOOL-SHAPED (path/content), not the whole sentence -- the
        # previous "pass the goal through as {"goal": ...}" shape is why a
        # realistic goal could never actually execute.
        # (``file_write_inputs`` was already extracted above, for path masking.)
        if file_write_inputs is not None:
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="WriteFile",
                description="Write a file inside the workspace",
                capability_id="file_write",
                capability_namespace="kernel",
                inputs=file_write_inputs,
                scope=goal.scope,
            ))

        # Default: at least one generic task
        if not tasks:
            tasks.append(Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="Execute",
                description="Execute goal",
                capability_id="execution_pipeline",
                capability_namespace="kernel",
                inputs={"goal": goal.natural_language},
                scope=goal.scope,
            ))

        # Multi-agent observability: for a real file-write goal, append a DERIVED
        # integrity sidecar task so the goal is observably worked by >=2 distinct
        # agents and a combined result (the file + its sha256 sidecar) is
        # observable end to end. Both tasks are REAL file_write actions executed
        # by the local tool path -- no LLM, no simulation. The sidecar depends on
        # the primary write so it runs second (the plan honours the dependency).
        fw_inputs = self._extract_file_write(goal.natural_language)
        if fw_inputs is not None and tasks and tasks[-1].capability_id == "file_write":
            try:
                digest = hashlib.sha256(
                    fw_inputs["content"].encode("utf-8")
                ).hexdigest()
            except Exception:
                digest = ""
            sidecar = Task(
                id=str(uuid.uuid4())[:8],
                goal_id=goal.id,
                name="WriteChecksum",
                description="Write an integrity sidecar (sha256) for the produced file",
                capability_id="file_write",
                capability_namespace="kernel",
                inputs={"path": fw_inputs["path"] + ".sha256", "content": digest},
                scope=goal.scope,
            )
            sidecar.dependencies.append(tasks[-1].id)
            tasks.append(sidecar)

        # Assign distinct agent ids so the goal is observably worked by multiple
        # agents. Real ids (from the persisted Employee) are injected by the
        # gateway; absent that we fall back to deterministic synthetic ids.
        self._assign_agents(goal, tasks)

        # Emit event
        publish_event(
            type="goal_decomposed",
            source="execution_kernel",
            data={"goal_id": goal.id, "task_count": len(tasks)},
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        return tasks

    def _assign_agents(self, goal: "Goal", tasks: List[Task]) -> None:
        """Assign distinct agent ids to every task in the plan.

        Uses the injected real agent pool when present, otherwise deterministic
        synthetic ids derived from the goal id. Never overwrites an agent id a
        caller already set.
        """
        if not tasks:
            return
        # ARM1: only an EXPLICITLY injected pool is a claim about who performs
        # the work. The synthetic fallback is a placeholder and makes no claim,
        # so it is not fail-closed -- otherwise every unpooled run would be
        # flagged, which would say nothing true about agent causality.
        if self.agent_ids:
            for i, t in enumerate(tasks):
                if t.assigned_agent is None:
                    t.assigned_agent = self.agent_ids[i % len(self.agent_ids)]
                    t.agent_claimed = True
        else:
            for i, t in enumerate(tasks):
                if t.assigned_agent is None:
                    t.assigned_agent = "%s-a%d" % (goal.id, i)
                    t.agent_claimed = False


class PlanBuilder:
    """Builds execution plans from tasks."""

    def __init__(self):
        pass

    def build(self, goal: Goal, tasks: List[Task], mode: str = "parallel") -> ExecutionPlan:
        """Build execution plan from tasks."""
        plan = ExecutionPlan(
            id=str(uuid.uuid4())[:8],
            goal_id=goal.id,
            tasks=tasks,
        )

        # Set up dependencies based on mode
        if mode == "sequential":
            # Chain tasks sequentially
            for i in range(1, len(tasks)):
                tasks[i].dependencies.append(tasks[i - 1].id)
        elif mode == "parallel":
            # No dependencies - all can run in parallel
            pass
        elif mode == "auto":
            # Smart dependency detection based on inputs/outputs
            for i, task in enumerate(tasks):
                for j, other in enumerate(tasks):
                    if i != j and self._output_feeds_input(other, task):
                        task.dependencies.append(other.id)

        # Emit event
        publish_event(
            type="plan_created",
            source="execution_kernel",
            data={"plan_id": plan.id, "goal_id": goal.id, "task_count": len(tasks), "mode": mode},
            correlation_id=goal.correlation_id,
            scope=EventScope(goal.scope),
        )

        return plan

    def _output_feeds_input(self, producer: Task, consumer: Task) -> bool:
        """Check if producer's output feeds consumer's input."""
        # Simplified: check if any expected output key matches input key
        producer_outputs = set(producer.expected_outputs.keys())
        consumer_inputs = set(consumer.inputs.keys())
        return bool(producer_outputs & consumer_inputs)


class ActionExecutor:
    """Executes actions via capability registry."""

    def __init__(self, capability_executor: Optional[CapabilityExecutor] = None) -> None:
        self.capability_registry = get_capability_registry()
        self.event_bus = get_event_bus()
        self._capability_executor = capability_executor
        # ARM1: optional resolver mapping an assigned_agent id -> a callable that
        # performs the capability FOR that agent. When absent (the default), agent
        # attribution cannot be substantiated, so an action carrying an
        # assigned_agent is reported as UNATTRIBUTED rather than silently
        # credited to the named agent. Injecting a resolver is what makes
        # "assigned to agent X" a causal claim rather than a label.
        self._agent_resolver: Optional[Callable[[str], Optional[Callable[..., Any]]]] = None

    def set_capability_executor(self, capability_executor: Optional[CapabilityExecutor]) -> None:
        """Inject (or clear) the real capability executor after construction."""
        self._capability_executor = capability_executor

    def set_agent_resolver(
        self,
        resolver: Optional[Callable[[str], Optional[Callable[..., Any]]]],
    ) -> None:
        """Inject a resolver that maps an agent id to that agent's own executor.

        ``resolver(agent_id)`` returns a callable ``(capability_id, inputs) ->
        output`` that genuinely performs the work for that agent, or ``None`` if
        the agent is unknown. Returning ``None`` is what makes a dangling agent
        reference fail loudly instead of silently succeeding.
        """
        self._agent_resolver = resolver

    def resolve_agent_executor(self, agent_id: str) -> Optional[Callable[..., Any]]:
        """Return the agent-specific executor, or None when unresolvable."""
        if self._agent_resolver is None or not agent_id:
            return None
        try:
            return self._agent_resolver(agent_id)
        except Exception:
            # A resolver that blows up must not be read as "agent exists".
            return None

    @property
    def has_capability_executor(self) -> bool:
        """True when a real capability executor has been injected."""
        return self._capability_executor is not None

    @kernel_action("execution.execute")
    def execute(self, action: Action) -> ActionResult:
        """Execute a single action."""
        start_time = utc_now()

        # Check capability scope
        scope_check = check_capability_scope(
            action.capability_id,
            CapabilityScope(action.scope),
            action.capability_namespace
        )

        if not scope_check.allowed:
            return ActionResult(
                action_id=action.id,
                success=False,
                error=f"Scope check failed: {scope_check.reason}",
                duration_ms=int((utc_now() - start_time).total_seconds() * 1000),
            )

        # In production, this would invoke the actual capability.
        # If a real capability executor was injected at assembly time, use it;
        # otherwise fall back to the simulated path (status stays "simulated").
        #
        # Honesty contract: an injected executor may signal failure either by
        # raising (handled below) or by returning a dict that carries an
        # explicit ``success: False``. We honour that explicit verdict instead
        # of masking it — a rejected/failed capability must never surface as a
        # successful action (the Verifier keys off ``ActionResult.success``).
        # Legacy executors that return a dict *without* a ``success`` key keep
        # the previous behaviour (assumed successful), preserving backward
        # compatibility.
        try:
            simulated = False
            # ARM1: when the action is attributed to an agent, the agent's own
            # executor performs the capability -- this is what makes the pool
            # causal instead of decorative. An unresolvable agent does NOT block
            # the work (the gate forbids "passing" by refusing to execute at all),
            # but it must not be recorded as agent-performed either: we fall back
            # to the shared executor and flag the result as unattributed so the
            # task is not silently credited to an agent that does not exist.
            agent_executor = (
                self.resolve_agent_executor(action.assigned_agent)
                if action.assigned_agent and action.agent_claimed
                else None
            )
            # Only an EXPLICIT claim that cannot be substantiated is fail-closed.
            # A synthetic placeholder (agent_claimed=False) makes no claim, so it
            # is not flagged -- flagging it would be noise, not honesty.
            agent_unattributed = (
                bool(action.assigned_agent)
                and bool(action.agent_claimed)
                and agent_executor is None
            )

            executor = agent_executor if agent_executor is not None else self._capability_executor

            if executor is not None:
                raw = executor(action.capability_id, action.inputs)
                if isinstance(raw, dict):
                    output = dict(raw)
                    output.setdefault("capability", action.capability_id)
                    if agent_unattributed:
                        output["agent_attribution"] = "unattributed"
                        output["unattributed_agent"] = action.assigned_agent
                    explicit = raw.get("success", None)
                    if explicit is None:
                        output["status"] = "executed"
                        ok = True
                        error = None
                    else:
                        ok = bool(explicit)
                        output.setdefault("status", "executed" if ok else "failed")
                        error = None if ok else str(
                            raw.get("error") or "capability reported failure"
                        )
                else:
                    output = {
                        "capability": action.capability_id,
                        "status": "executed",
                        "result": raw,
                    }
                    ok = True
                    error = None
            elif simulated_execution_enabled():
                output = self._simulate_capability(action.capability_id, action.inputs)
                simulated = True
                ok = True
                error = None
            else:
                # FAIL CLOSED -- an unwired kernel did not execute anything and
                # must not be allowed to say otherwise.
                output = {
                    "capability": action.capability_id,
                    "status": "unwired",
                    "executed": False,
                    "inputs_received": list(action.inputs.keys()),
                    "timestamp": utc_now().isoformat(),
                }
                simulated = False
                ok = False
                error = (
                    "no capability executor wired: the Execution Kernel cannot "
                    f"execute capability '{action.capability_id}'. Inject a real "
                    "capability_executor at assembly time to execute for real, or "
                    f"set {SIMULATION_OPT_IN_ENV}=1 to request the simulated path "
                    "explicitly (demo/test only -- a simulated result is NOT a real "
                    "execution)."
                )

            duration_ms = int((utc_now() - start_time).total_seconds() * 1000)

            # Emit event — reflect the *real* outcome, not a blanket success.
            if ok:
                publish_event(
                    type="action_completed",
                    source="execution_kernel",
                    data={"action_id": action.id, "task_id": action.task_id, "success": True, "simulated": simulated},
                    correlation_id=action.correlation_id,
                    scope=EventScope(action.scope),
                )
            else:
                publish_event(
                    type="action_failed",
                    source="execution_kernel",
                    data={"action_id": action.id, "task_id": action.task_id, "error": error},
                    correlation_id=action.correlation_id,
                    scope=EventScope(action.scope),
                    priority=EventPriority.HIGH,
                )

            return ActionResult(
                action_id=action.id,
                success=ok,
                output=output,
                error=error,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((utc_now() - start_time).total_seconds() * 1000)

            publish_event(
                type="action_failed",
                source="execution_kernel",
                data={"action_id": action.id, "task_id": action.task_id, "error": str(e)},
                correlation_id=action.correlation_id,
                scope=EventScope(action.scope),
                priority=EventPriority.HIGH,
            )

            return ActionResult(
                action_id=action.id,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _simulate_capability(self, capability_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate capability execution -- **never** a real side effect.

        Only reachable when the caller explicitly opted in via
        ``LIUHAO_ALLOW_SIMULATED_EXECUTION=1`` (see :meth:`execute`). The output
        is labelled ``status="simulated"`` *and* ``simulated=True`` so that no
        consumer can mistake it for a real execution.
        """
        return {
            "capability": capability_id,
            "status": "simulated",
            "simulated": True,
            "executed": False,
            "inputs_received": list(inputs.keys()),
            "timestamp": utc_now().isoformat(),
        }


#: Output-dict keys whose string value names a file the capability wrote.
#: Kept identical to ``Verifier._FILE_WRITE_KEYS`` on purpose: "what counts as a
#: claimed artifact" must not have two different answers in one kernel.
_FILE_WRITE_KEYS = ("written", "path", "file")


def artifact_path_from_output(output: Any) -> Optional[str]:
    """Best-effort extraction of the file a capability claims to have written.

    Returns ``None`` when the output declares no artifact -- which is a
    *statement about the contract*, not proof that nothing was produced. Used by
    Defect-E journal verification: only a task that DECLARES an artifact can have
    that artifact checked later.
    """
    if not isinstance(output, dict):
        return None
    for key in _FILE_WRITE_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def artifact_currently_exists(path: Optional[str]) -> Optional[bool]:
    """Prove a declared artifact still exists, or report that it cannot be proven.

    Returns:
      * ``True``  -- the file exists and is non-empty (proof it is still there).
      * ``False`` -- the file is absent or empty (proof the side effect is GONE).
      * ``None``  -- no artifact was declared, so there is nothing to prove.

    ``None`` is deliberately NOT ``True``: "cannot be checked" must never be
    silently upgraded to "verified". Callers decide what an unprovable claim
    means; they cannot mistake it for evidence.
    """
    if not path:
        return None
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        # A path we cannot even stat is not a path we can vouch for.
        return False


class Verifier:
    """Verifies task outcomes against criteria."""

    def __init__(self):
        pass

    def verify(
        self,
        task: Task,
        action_result: ActionResult,
        criteria: Optional[Dict[str, Any]] = None
    ) -> VerificationResult:
        """Verify task outcome."""
        if not action_result.success:
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.FAILED,
                score=0.0,
                feedback=f"Action failed: {action_result.error}",
                replan_required=True,
            )

        # Check against expected outputs.
        # Goal-level criteria act as base criteria; the task's own
        # expected_outputs refine/override them where both define the
        # same key.
        expected: Dict[str, Any] = dict(criteria) if criteria else {}
        if task.expected_outputs:
            expected.update(task.expected_outputs)
        actual = action_result.output

        # Root-cause guard (Phase 3): a capability may return None or a
        # non-dict output (e.g. a real tool failure). The previous code did
        # `if key in actual` and raised TypeError, crashing the whole run
        # instead of failing the task gracefully. Treat non-dict output as a
        # verification failure that triggers replan — consistent with the
        # `action_result.success is False` branch above.
        if not isinstance(actual, dict):
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.FAILED,
                score=0.0,
                feedback=f"Action output is not a dict: {type(actual).__name__}",
                replan_required=True,
            )

        # Independent file-side-effect verification (additive).
        # A capability that merely *claims* success (e.g. returns
        # {"written": path}) must not self-prove verification. When the
        # outcome looks like a file write, re-read the disk to confirm the
        # artifact actually exists (and, optionally, matches expected content).
        file_failure = self._verify_file_side_effect(task, action_result, expected)
        if file_failure is not None:
            return file_failure

        if not expected:
            # HONESTY FIX (was: "No criteria - assume success if action
            # succeeded" -> SUCCESS/1.0).
            #
            # The defect: with no criteria there is nothing to check the output
            # against, so this branch could only ever return SUCCESS/1.0. An
            # executor that merely *claimed* success -- returning
            # {"success": True} and writing nothing -- therefore received a
            # fully-verified pass. "Nothing was proven" was reported as
            # "everything was proven".
            #
            # The fix: absence of criteria is NOT evidence of success. Report
            # UNVERIFIED (score 0.0) so the gap is visible and cannot be read
            # as a verified pass. We deliberately do NOT invent criteria here and
            # we do NOT fail the task -- the action may well have succeeded; it
            # simply is not provable from what this kernel can observe. Callers
            # that need a hard guarantee must supply expected_outputs.
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.UNVERIFIED,
                score=0.0,
                feedback=(
                    "action reported success but NO verification criteria were "
                    "defined, so its outcome cannot be independently confirmed; "
                    "reported as UNVERIFIED rather than a verified success"
                ),
                replan_required=False,
            )

        # Simple matching. The "content" key (when present) is consumed by the
        # independent file-side-effect check above and is not a dict-match
        # criterion, so exclude it from the remaining criteria set to avoid a
        # spurious "missing" detail against the action output.
        match_expected = dict(expected)
        match_expected.pop("content", None)

        matches = 0
        total = len(match_expected)
        details = {}

        for key, expected_value in match_expected.items():
            if key in actual:
                actual_value = actual[key]
                if self._values_match(expected_value, actual_value):
                    matches += 1
                    details[key] = "match"
                else:
                    details[key] = f"mismatch: expected {expected_value}, got {actual_value}"
            else:
                details[key] = "missing"

        score = matches / total if total > 0 else 1.0

        if score == 1.0:
            result = VerifyResult.SUCCESS
            feedback = "All criteria matched"
        elif score >= 0.7:
            result = VerifyResult.PARTIAL
            feedback = f"Partial match: {matches}/{total} criteria"
        elif score >= 0.3:
            result = VerifyResult.OFF_TRACK
            feedback = f"Off track: {matches}/{total} criteria"
        else:
            result = VerifyResult.FAILED
            feedback = f"Failed: {matches}/{total} criteria"

        return VerificationResult(
            task_id=task.id,
            result=result,
            score=score,
            details=details,
            feedback=feedback,
            replan_required=score < 0.7,
        )

    def _values_match(self, expected: Any, actual: Any) -> bool:
        """Check if values match (with type coercion)."""
        if expected == actual:
            return True
        # Type coercion for common cases
        try:
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                return abs(expected - actual) < 0.001
            if isinstance(expected, str) and isinstance(actual, str):
                return expected.lower() == actual.lower()
        except (TypeError, ValueError):
            pass
        return False

    # Keys whose string value is treated as a filesystem path for a file write.
    _FILE_WRITE_KEYS = ("written", "path", "file")

    def _verify_file_side_effect(
        self,
        task: "Task",
        action_result: "ActionResult",
        expected: Dict[str, Any],
    ) -> Optional["VerificationResult"]:
        """Independently confirm a claimed file write actually hit the disk.

        This is the anti-self-proving check: verification must not trust the
        executor's returned ``output`` dict. When the outcome looks like a file
        write (a path under a write-key in the output or in the expected
        criteria), we re-read the disk.

        Returns:
          * ``None`` if no file-write outcome is detected (so the normal
            dict-matching in :meth:`verify` proceeds unchanged), or if the
            artifact exists and any requested content check passes.
          * a FAILED ``VerificationResult`` (``replan_required=True``) when the
            claimed artifact is missing or its content does not match.
        """
        output = action_result.output

        # 1) Detect a claimed path: prefer a write-key in the action output.
        claimed_path: Optional[str] = None
        if isinstance(output, dict):
            for key in self._FILE_WRITE_KEYS:
                value = output.get(key)
                if isinstance(value, str) and value:
                    claimed_path = value
                    break

        # 2) Fall back to a path under a write-key in the expected criteria
        #    (e.g. task.expected_outputs carrying the intended artifact path).
        if claimed_path is None:
            for key in self._FILE_WRITE_KEYS:
                value = expected.get(key)
                if isinstance(value, str) and value:
                    claimed_path = value
                    break

        if claimed_path is None:
            return None

        # 3) Independent disk check. The capability claimed to write a file;
        #    prove it. A missing artifact means success was self-proclaimed.
        if not os.path.exists(claimed_path):
            return VerificationResult(
                task_id=task.id,
                result=VerifyResult.FAILED,
                score=0.0,
                feedback=(
                    f"file not present on disk: action claimed success "
                    f"but no artifact at {claimed_path}"
                ),
                replan_required=True,
            )

        # 4) Optional content verification. ``content`` is the file's expected
        #    contents (not an output-dict key), provided via criteria.
        expected_content = expected.get("content")
        if expected_content is not None:
            try:
                with open(claimed_path, "r", encoding="utf-8") as f:
                    actual_content = f.read()
            except (OSError, UnicodeDecodeError) as exc:
                return VerificationResult(
                    task_id=task.id,
                    result=VerifyResult.FAILED,
                    score=0.0,
                    feedback=(
                        f"could not read back written file {claimed_path}: {exc}"
                    ),
                    replan_required=True,
                )
            if actual_content != expected_content:
                return VerificationResult(
                    task_id=task.id,
                    result=VerifyResult.FAILED,
                    score=0.0,
                    feedback=(
                        f"content mismatch for {claimed_path}: "
                        f"expected {len(str(expected_content))} chars, "
                        f"got {len(actual_content)} chars"
                    ),
                    replan_required=True,
                )

        # Artifact exists (and content matches, if requested). Let normal
        # dict-matching proceed for any remaining non-file criteria.
        return None


class ExecutionEngine:
    """Main execution engine orchestrating the full pipeline."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def __init__(
        self,
        scope: str = "L1",
        capability_executor: Optional[CapabilityExecutor] = None,
        journal: Optional[ExecutionJournal] = None,
    ) -> None:
        self.scope = scope
        self.decomposer = GoalDecomposer()
        self.planner = PlanBuilder()
        self.executor = ActionExecutor(capability_executor=capability_executor)
        self.verifier = Verifier()
        self.context_kernel = create_context_kernel(scope=scope)
        # journal=None 时行为与历史完全逐字节一致（不注入即不生效），
        # 与 capability_executor 那条接缝同一哲学 —— 见 Round 83。
        self.journal: Optional[ExecutionJournal] = journal
        self._execution_id: Optional[str] = None

    def set_capability_executor(self, capability_executor: Optional[CapabilityExecutor]) -> None:
        """Inject (or clear) the real capability executor used by the pipeline."""
        self.executor.set_capability_executor(capability_executor)

    def set_journal(self, journal: Optional[ExecutionJournal]) -> None:
        """Attach (or detach) the durable journal that enables crash recovery."""
        self.journal = journal

    def execute_goal(
        self,
        goal: Goal,
        plan_mode: str = "auto",
        verification_criteria: Optional[Dict[str, Any]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> ExecutionContext:
        """Execute a goal through the full pipeline."""
        ctx = ExecutionContext(goal=goal, plan=None, context_kernel=self.context_kernel)
        # Audit traceability: thread the goal's correlation id into every
        # kernel-action audit event so the hash-chain audit can tie each action
        # back to the goal that ran it (goal -> action link). Backward compatible:
        # a direct action outside any goal still gets a per-action random id.
        with kernel_action_correlation_id(goal.correlation_id):
            # Step 1: Decompose
            publish_event(
                type="execution_started",
                source="execution_kernel",
                data={"goal_id": goal.id, "goal": goal.natural_language},
                correlation_id=goal.correlation_id,
                scope=EventScope(goal.scope),
            )

            tasks = self.decomposer.decompose(goal)

            # Step 2: Plan
            plan = self.planner.build(goal, tasks, mode=plan_mode)
            ctx.plan = plan
            plan.status = PlanStatus.ACTIVE
            plan.started_at = utc_now()

            # Step 2.5: Adopt durable progress (crash recovery)
            # execution_id 刻意取 goal.id（确定性）：同一个 goal 重启后再提交，
            # 才能对上上一次执行留下来的那串事件。
            resumed_count = 0
            if self.journal is not None:
                self._execution_id = goal.id
                self.journal.record(
                    self._execution_id,
                    "execution_started",
                    goal=goal.natural_language,
                    plan_id=plan.id,
                    task_count=len(plan.tasks),
                )
                resumed_count = self._adopt_journaled_progress(ctx)
                if resumed_count:
                    publish_event(
                        type="execution_resumed",
                        source="execution_kernel",
                        data={
                            "goal_id": goal.id,
                            "resumed_tasks": resumed_count,
                            "total_tasks": len(plan.tasks),
                        },
                        correlation_id=goal.correlation_id,
                        scope=EventScope(goal.scope),
                    )

            # Step 3: Execute
            self._execute_plan(ctx, verification_criteria, stop_event)

            # Step 4: Final verification
            plan.completed_at = utc_now()
            if ctx.failed_tasks:
                plan.status = PlanStatus.FAILED if len(ctx.failed_tasks) == len(ctx.plan.tasks) else PlanStatus.PARTIAL
            else:
                plan.status = PlanStatus.COMPLETED

            if self.journal is not None and self._execution_id is not None:
                self.journal.record(
                    self._execution_id,
                    "execution_completed",
                    plan_id=plan.id,
                    status=plan.status.value,
                    completed=len(ctx.completed_tasks),
                    failed=len(ctx.failed_tasks),
                    resumed=resumed_count,
                )

            publish_event(
                type="execution_completed",
                source="execution_kernel",
                data={
                    "goal_id": goal.id,
                    "plan_id": plan.id,
                    "status": plan.status.value,
                    "completed": len(ctx.completed_tasks),
                    "failed": len(ctx.failed_tasks),
                },
                correlation_id=goal.correlation_id,
                scope=EventScope(goal.scope),
            )

            return ctx

    def _execute_plan(
        self,
        ctx: ExecutionContext,
        verification_criteria: Optional[Dict[str, Any]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Execute all tasks in the plan."""
        max_iterations = 100  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # Cooperative cancellation: a human operator (or the gateway) may
            # have asked this goal to stop. Honour it between tasks so we never
            # start a new task after a stop was requested.
            if stop_event is not None and stop_event.is_set():
                self._record_cancel(ctx)
                raise GoalCancelled(ctx.goal.id)

            ready_tasks = ctx.plan.get_ready_tasks(ctx.completed_tasks)
            pending_tasks = [t for t in ctx.plan.tasks if t.status == TaskStatus.PENDING]

            if not ready_tasks and not pending_tasks:
                break  # All done

            if not ready_tasks and pending_tasks:
                # Deadlock or circular dependency
                for task in pending_tasks:
                    task.status = TaskStatus.FAILED
                    task.error = "Circular dependency or deadlock detected"
                    ctx.failed_tasks.add(task.id)
                break

            # Execute ready tasks (in parallel conceptually)
            #
            # ``stop_event`` MUST be forwarded: it is what makes aborting a
            # goal responsive. Without it the "honour a stop request before
            # each attempt" check inside ``_execute_task`` saw ``None`` and was
            # dead code, so a human STOP arriving during a long-running task
            # was only observed after the task's whole retry budget burned
            # down (~40s for a task that times out at 10s with max_retries=3).
            for task in ready_tasks:
                self._execute_task(ctx, task, verification_criteria, stop_event)

        if iteration >= max_iterations:
            # Timeout
            for task in ctx.plan.tasks:
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.FAILED
                    task.error = "Execution timeout"
                    ctx.failed_tasks.add(task.id)

    def _adopt_journaled_progress(self, ctx: ExecutionContext) -> int:
        """把 journal 里已完成的任务直接标成 COMPLETED —— 崩溃续跑的核心。

        匹配依据是 **task 名字**而不是 id：id 每次都由 uuid4 生成，跨进程必然
        对不上；而 `GoalDecomposer` 对同一段目标描述是确定性的，名字稳定。

        返回跳过的任务数，并在 ctx 里放一个标明来自 journal 的 ActionResult，
        使后续 Verifier 看到的形状与真实执行一致（不是空成功，是「已恢复」）。
        """
        assert self.journal is not None and self._execution_id is not None
        done = self.journal.completed_task_names(self._execution_id)
        if not done:
            return 0
        artifacts = self.journal.completed_task_artifacts(self._execution_id)

        resumed = 0
        for task in ctx.plan.tasks:
            if task.status != TaskStatus.PENDING or task.name not in done:
                continue

            # DEFECT E -- "it finished once" is NOT "it is still finished".
            #
            # Previously ANY name match was stamped COMPLETED with a synthetic
            # success=True ActionResult and zero evidence, so an artifact that
            # had since been deleted (or never flushed) was reported as done and
            # the goal declared complete over work that is not there.
            #
            # Now a claim is only reclaimable when it is PROVEN:
            #   * the task declared an artifact  -> the file must still exist and
            #     be non-empty, else we do NOT reclaim (it stays PENDING and
            #     re-executes, which is what actually restores the artifact);
            #   * the task declared no artifact  -> there is no persistent side
            #     effect that could have gone away, so there is nothing to prove.
            #     Note this is NOT the same as "verified"; it is "nothing was
            #     claimed", and it is kept distinct so the two are never conflated.
            declared_path = artifacts.get(task.name)
            still_there = artifact_currently_exists(declared_path)
            if still_there is False:
                # Proven ABSENT -- refuse the claim. Leaving it PENDING means it
                # re-runs, which is the only way the missing artifact comes back.
                self._journal_event(
                    task,
                    "journal_claim_unverified",
                    task_id=task.id,
                    goal_id=task.goal_id,
                    artifact_path=declared_path,
                    reason="declared artifact missing or empty on resume",
                )
                continue

            task.status = TaskStatus.COMPLETED
            task.started_at = task.started_at or utc_now()
            task.completed_at = utc_now()
            task.result = {"restored_from_journal": True}
            ctx.completed_tasks.add(task.id)
            ctx.task_results[task.id] = ActionResult(
                action_id=f"restored:{task.id}",
                success=True,
                output={
                    "restored_from_journal": True,
                    "task_name": task.name,
                    # Honest provenance of HOW this was accepted back:
                    # "verified" (artifact proved present) vs "unverifiable"
                    # (nothing was declared, so nothing could be checked).
                    "resume_basis": (
                        "artifact_verified" if still_there is True
                        else "no_artifact_declared"
                    ),
                    "artifact_path": declared_path,
                },
                duration_ms=0,
            )
            resumed += 1
        return resumed

    def _journal_event(
        self, task: Task, event_type: str, **payload: Any
    ) -> None:
        """写一条任务级事件 —— journal 未挂载时必须是彻底的 no-op。"""
        if self.journal is None or self._execution_id is None:
            return
        self.journal.record(self._execution_id, event_type, task_name=task.name, **payload)

    def _record_cancel(self, ctx: ExecutionContext) -> None:
        """Write a goal-level CANCELLED marker to the journal (no-op if unjournaled)."""
        if self.journal is None or self._execution_id is None:
            return
        try:
            self.journal.record(
                self._execution_id,
                "execution_cancelled",
                status="cancelled",
                completed=len(ctx.completed_tasks),
                failed=len(ctx.failed_tasks),
            )
        except Exception:
            # Journal write must never crash a cancellation.
            pass

    def _execute_task(
        self,
        ctx: ExecutionContext,
        task: Task,
        verification_criteria: Optional[Dict[str, Any]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Execute a single task with retries."""
        task.status = TaskStatus.RUNNING
        task.started_at = utc_now()
        # Join the goal's correlation chain so task lifecycle events are
        # traceable end to end (Definition Lock section 112).
        task.correlation_id = ctx.goal.correlation_id

        # 先记 task_started 再动手：崩溃时才能知道「当时正在跑哪个任务」，
        # 以及区分「从没开始」和「开始了但没完成」——后者可能需要人工看一眼副作用。
        self._journal_event(task, "task_started", task_id=task.id, goal_id=task.goal_id)

        publish_event(
            type="task_started",
            source="execution_kernel",
            data={"task_id": task.id, "goal_id": task.goal_id, "name": task.name},
            correlation_id=task.correlation_id,
            scope=EventScope(task.scope),
        )

        # Create action. ARM1: carry the assigned agent across the Action
        # boundary so it actually reaches the executor (previously it stopped at
        # the Task dataclass and was physically unable to influence execution).
        action = Action(
            id=str(uuid.uuid4())[:8],
            task_id=task.id,
            capability_id=task.capability_id,
            capability_namespace=task.capability_namespace,
            inputs=task.inputs,
            correlation_id=task.correlation_id,
            scope=task.scope,
            timeout_seconds=task.timeout_seconds,
            assigned_agent=task.assigned_agent,
            agent_claimed=task.agent_claimed,
        )

        # Execute with retries
        while task.retry_count <= task.max_retries:
            # Honour a stop request before each attempt.
            if stop_event is not None and stop_event.is_set():
                self._record_cancel(ctx)
                raise GoalCancelled(ctx.goal.id)
            result = self.executor.execute(action)
            ctx.task_results[task.id] = result

            if result.success:
                # ARM1 honesty: the side effects are real, but if the task was
                # attributed to an agent that could not be resolved, do NOT
                # record it as an ordinary COMPLETED task -- that is exactly the
                # "ghost id still completes cleanly" defect. Record it as
                # UNATTRIBUTED: the work happened, the claimed performer did not
                # exist. The task still counts as completed for the goal (the
                # bytes are on disk) so we never fake a failure either.
                _unattributed = (
                    isinstance(result.output, dict)
                    and result.output.get("agent_attribution") == "unattributed"
                )
                task.status = (
                    TaskStatus.COMPLETED_UNATTRIBUTED
                    if _unattributed
                    else TaskStatus.COMPLETED
                )
                task.result = result.output
                task.completed_at = utc_now()
                ctx.completed_tasks.add(task.id)
                # 必须在已被正式标记 COMPLETED 之后立刻落盘：崩溃随时可能发生，
                # 晚一步写就等于这个任务下次还要重跑（副作用执行第二次）。
                #
                # Defect E: record WHAT was produced, not just that it finished.
                # A bare "task_completed" row only proves the task ran at some
                # point; the artifact path is what lets a later recovery ask
                # whether the side effect is STILL there (and is therefore not
                # silently reclaimable after someone deleted it).
                self._journal_event(
                    task,
                    "task_completed",
                    task_id=task.id,
                    goal_id=task.goal_id,
                    artifact_path=artifact_path_from_output(result.output),
                )

                # Verify
                verification = self.verifier.verify(task, result, verification_criteria)
                ctx.verification_results[task.id] = verification

                if verification.replan_required:
                    # Could trigger replan here
                    pass

                publish_event(
                    type="task_completed",
                    source="execution_kernel",
                    data={
                        "task_id": task.id,
                        "goal_id": task.goal_id,
                        "verification": verification.result.value,
                        "score": verification.score,
                    },
                    correlation_id=task.correlation_id,
                    scope=EventScope(task.scope),
                )
                return

            # Retry
            task.retry_count += 1
            if task.retry_count <= task.max_retries:
                task.status = TaskStatus.RETRYING
                publish_event(
                    type="task_retrying",
                    source="execution_kernel",
                    data={"task_id": task.id, "attempt": task.retry_count, "max": task.max_retries},
                    correlation_id=task.correlation_id,
                    scope=EventScope(task.scope),
                    priority=EventPriority.HIGH,
                )

        # All retries exhausted - preserve the root cause of the last
        # failed attempt alongside the exhaustion notice.
        last_result = ctx.task_results.get(task.id)
        root_cause = (last_result.error if last_result and last_result.error
                      else "unknown error")
        task.status = TaskStatus.FAILED
        task.error = f"Max retries ({task.max_retries}) exceeded; last error: {root_cause}"
        task.completed_at = utc_now()
        ctx.failed_tasks.add(task.id)
        self._journal_event(
            task, "task_failed", task_id=task.id, goal_id=task.goal_id, error=task.error
        )

        publish_event(
            type="task_failed",
            source="execution_kernel",
            data={"task_id": task.id, "goal_id": task.goal_id, "error": task.error},
            correlation_id=task.correlation_id,
            scope=EventScope(task.scope),
            priority=EventPriority.CRITICAL,
        )

    @kernel_action("execution.create_checkpoint")
    def create_checkpoint(self, ctx: ExecutionContext) -> Dict[str, Any]:
        """Create execution checkpoint for rollback."""
        checkpoint = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": utc_now().isoformat(),
            "goal_id": ctx.goal.id,
            "plan_id": ctx.plan.id,
            "completed_tasks": list(ctx.completed_tasks),
            "failed_tasks": list(ctx.failed_tasks),
            "task_results": {k: v.__dict__ for k, v in ctx.task_results.items()},
            "verification_results": {k: v.__dict__ for k, v in ctx.verification_results.items()},
        }
        ctx.checkpoints.append(checkpoint)
        return checkpoint

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Convenience functions
def create_execution_engine(
    scope: str = "L1",
    journal: Optional[ExecutionJournal] = None,
) -> ExecutionEngine:
    """Factory function to create ExecutionEngine."""
    return ExecutionEngine(scope=scope, journal=journal)


def execute_goal(
    natural_language: str,
    scope: str = "L1",
    plan_mode: str = "auto"
) -> ExecutionContext:
    """Convenience function to execute a goal."""
    goal = Goal(
        id=str(uuid.uuid4())[:8],
        natural_language=natural_language,
        scope=scope,
    )
    engine = create_execution_engine(scope)
    return engine.execute_goal(goal, plan_mode=plan_mode)
