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

from typing import Any, Callable, Iterable, Optional, Set


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
    ) -> None:
        self._inner = inner
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
        return tasks
