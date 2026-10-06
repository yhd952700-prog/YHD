"""Planner ↔ executor contract guard (2026-10-04).

Why this exists
---------------
Measured with ``scripts/verify_agent_causality_coverage.py``: of six realistic
goal shapes only two complete. The other four fail, and they all fail for the
*same* reason::

    search  -> capability='network_bus'        "no active tool for capability network_bus"
    memory  -> capability='multi_tier_memory'  "no active tool for capability multi_tier_memory"
    plan    -> capability='execution_pipeline' "no active tool for capability execution_pipeline"

The deterministic ``GoalDecomposer`` matches keywords and emits a
``capability_id`` without ever asking whether **anything can execute it**. Only
``file_write`` and ``python_compute`` have executors, so every other branch
produces a task that is *doomed at plan time*.

That is not "the model picked the wrong tool" — it is a missing contract
between planning and capability availability. And the failure mode matters more
than the coverage number: a goal like "search for information and create a file
named notes.txt" **writes the file first and only then fails**, because the
doomed Search task runs alongside two executable ones. Real side effects are
produced for a goal that was never completable.

What this module does
---------------------
:class:`ExecutableAwareDecomposer` wraps any decomposer and validates, *before
execution begins*, that every planned capability has a registered executor. If
one does not, it raises :class:`PlannerContractError` naming the missing
capability — so the run fails fast with an actionable reason and **no partial
side effects**.

Deliberately NOT done
---------------------
* It does not *drop* the un-executable tasks and run the rest. Silently
  discarding part of the user's goal is exactly the "reports success / did
  something else" failure this project refuses.
* It does not invent a substitute capability.
* It is opt-in: no caller supplies ``available_capabilities`` => no wrapping and
  behaviour is byte-for-byte unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Set


class PlannerContractError(Exception):
    """A plan contains capabilities that have no executor.

    Raised at plan time so the goal fails *before* any task runs. The message
    names every missing capability, because "I cannot do this, and here is the
    specific thing I lack" is actionable while "failed" is not.
    """

    def __init__(self, missing: Iterable[str], goal_id: Optional[str] = None) -> None:
        self.missing: Set[str] = set(missing)
        self.goal_id = goal_id
        listed = ", ".join(sorted(self.missing))
        super().__init__(
            f"goal {goal_id or '<unknown>'} cannot be executed: no executor is "
            f"registered for capability {listed}. The planner must not emit work "
            f"it cannot perform -- register an executor for {listed}, or restate "
            f"the goal in terms of a capability that exists."
        )


class PlannerInputError(PlannerContractError):
    """A planned task has an executor but not the inputs that executor requires.

    Second contract layer (2026-10-04). The capability-existence check catches
    "no executor at all"; this catches the subtler case where an executor
    exists but the planned task cannot possibly satisfy it. Concretely, the
    deterministic decomposer emits a ``python_compute`` task whose only input
    is the raw goal text (``goal=...``); ``python_compute`` does NOT guess code
    from prose and rejects such a task. Left unchecked, that doomed task runs
    beside the executable ones, the goal is reported failed, and any real side
    effect already happened — the mirror image of "did nothing but said done".

    Raising here moves the failure to PLAN time, before any task runs, with a
    message that names both the capability and the inputs it needed.
    """

    def __init__(self, capability: str, task_name: str, required: Iterable[str],
                 goal_id: Optional[str] = None) -> None:
        self.capability = capability
        self.task_name = task_name
        self.required = tuple(required)
        alts = " or ".join(f"'{k}'" for k in self.required)
        super().__init__(
            {capability},
            goal_id=goal_id,
        )
        # Replace the base message with the more specific one.
        self.args = (
            f"goal {goal_id or '<unknown>'} cannot be executed: task "
            f"'{task_name}' needs capability '{capability}', which IS "
            f"registered, but the plan supplies none of its required inputs "
            f"({alts}). The executor would reject this task at run time, so it "
            f"is refused at plan time instead of failing mid-run.",
        )


#: Minimum inputs a capability's executor must receive for the task to have any
#: chance of succeeding. A task whose inputs satisfy NONE of a capability's
#: alternatives is doomed and is refused at plan time. Keyed by the capability
#: namespace (the same namespace the router routes by). Extend as more real
#: executors gain required inputs.
DEFAULT_INPUT_REQUIREMENTS: dict = {
    # python_compute executes code / evaluates an expression; it refuses to
    # guess Python from prose, so a task carrying only `goal` is doomed.
    "python_compute": ("code", "expression"),
}


class ExecutableAwareDecomposer:
    """Wraps a decomposer and enforces the planner ↔ executor contract.

    Transparent by design: unknown attributes are forwarded to the wrapped
    decomposer, and ``agent_ids`` is proxied in both directions so the gateway's
    existing ``engine.decomposer.agent_ids = [...]`` injection keeps working
    (``src/gateway/ai_management.py`` and ``tests/ai/test_agent_causality.py``
    both write to that attribute).
    """

    def __init__(
        self,
        inner: Any,
        available_capabilities: Optional[Iterable[str]] = None,
        capability_provider: Optional[Callable[[], Iterable[str]]] = None,
        input_requirements: Optional[Dict[str, Iterable[str]]] = None,
    ) -> None:
        self._inner = inner
        # ``None`` means "use the built-in table"; pass ``{}`` to disable the
        # input layer entirely (capability-existence check only).
        self._input_requirements: Dict[str, Iterable[str]] = (
            DEFAULT_INPUT_REQUIREMENTS if input_requirements is None
            else {k: tuple(v) for k, v in input_requirements.items()}
        )
        # A provider is preferred over a frozen set: the tool registry can gain
        # capabilities after wiring, and a stale snapshot would reject work that
        # became executable.
        if capability_provider is not None:
            self._provider: Optional[Callable[[], Iterable[str]]] = capability_provider
        elif available_capabilities is not None:
            frozen = set(available_capabilities)

            def _frozen() -> Iterable[str]:
                return frozen

            self._provider = _frozen
        else:
            self._provider = None

    # -- agent_ids proxy: the gateway writes to this attribute ---------------
    @property
    def agent_ids(self) -> Any:
        return getattr(self._inner, "agent_ids", None)

    @agent_ids.setter
    def agent_ids(self, value: Any) -> None:
        setattr(self._inner, "agent_ids", value)

    def __getattr__(self, name: str) -> Any:
        # Only called when normal lookup fails, so __init__-set attributes and
        # the agent_ids property win.
        return getattr(self._inner, name)

    # -- the contract --------------------------------------------------------
    @property
    def available_capabilities(self) -> Set[str]:
        if self._provider is None:
            return set()
        try:
            return set(self._provider())
        except Exception:
            # A broken introspection must not be read as "nothing is executable"
            # (that would reject every goal). Fail open on *introspection* only;
            # the real executor still fails closed at execution time.
            return set()

    def decompose(self, goal: Any) -> Any:
        tasks = self._inner.decompose(goal)
        if self._provider is None:
            return tasks
        available = self.available_capabilities
        if not available:
            # Nothing to check against: do not reject the plan on the basis of
            # an empty/unknown registry (would reject everything).
            return tasks
        missing = sorted(
            {
                t.capability_id
                for t in (tasks or [])
                if getattr(t, "capability_id", None) not in available
            }
        )
        if missing:
            raise PlannerContractError(missing, goal_id=getattr(goal, "id", None))
        # Second layer: the capability exists, but does the planned task supply
        # the inputs that executor actually requires? A task that cannot satisfy
        # its executor is doomed; refuse it now (plan time) rather than let it
        # run beside executable tasks and fail mid-run after real side effects.
        for t in (tasks or []):
            cap = getattr(t, "capability_id", None)
            required = self._input_requirements.get(cap)
            if not required:
                continue
            inputs = getattr(t, "inputs", None) or {}
            if not any(inputs.get(k) for k in required):
                raise PlannerInputError(
                    cap,
                    getattr(t, "name", "<unnamed>"),
                    required,
                    goal_id=getattr(goal, "id", None),
                )
        return tasks
