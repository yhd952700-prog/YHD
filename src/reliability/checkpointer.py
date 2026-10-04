"""Production checkpoint scheduler for the audit chain (C2 wiring).

WHY THIS EXISTS
---------------
``AuditStore.verify_incremental()`` and ``verify_rolling()`` are fully
implemented and unit-tested, but until now **nothing in production called
them**. Consequences that were observable from outside:

  * ``audit_verification_coverage()['checkpoint_count']`` stayed ``0`` forever;
  * ``/v1/ready`` therefore reported ``evidence_status="unverified"``
    permanently -- an honest gap that could never close on its own;
  * "verified" decayed into "we verified it once, on an unknown date".

This module is the missing producer. It is deliberately a **new, opt-in
module**: it does not edit the audit kernel, does not change the ``/verify``
endpoint (which stays read-only on purpose -- scanning a 300k-row chain per
request is not acceptable), and does not gate readiness.

DESIGN CONSTRAINTS
------------------
1. **Off by default.** A library must never silently start threads inside a
   consumer's process. Call :func:`start_background_verifier` explicitly.
2. **Daemon thread, one at a time.** A single daemon thread; a second call
   returns the existing worker instead of spawning a rival writer.
3. **Never raises into the host.** Verification problems are *observations*,
   not availability failures. Every failure is counted and exposed; the loop
   keeps running. (Mirrors CRIT-1C Layer 1: make the evidence layer observable
   without changing availability semantics.)
4. **Bounded work per tick.** Cost is proportional to the segment, not to
   history, so a tick cannot walk the whole chain.
5. **No fake green.** A failing segment is recorded as a failure with its
   ``failures`` detail; it is never reported as verified.

PERFORMANCE BUDGET (measured on this repo, temp DB, Python 3.12)
----------------------------------------------------------------
    verify_incremental(5000 events)  ~0.15 s   (~30 us/event)
    verify_rolling(5000 events)      ~0.15 s
Cost is flat in history size (the whole point of C2), so the budget is a
function of the *segment* size, not of the chain length. At the measured rate:

    5,000 events/tick  ~= 0.15 s   ~= 3% of a 5 s tick budget
   50,000 events/tick  ~= 1.5 s

Defaults below are sized from those numbers, with headroom for slower disks:
``segment=5000`` per incremental tick and a rolling pass every N ticks.

SCHEDULING FREQUENCY
--------------------
``incremental_interval_sec`` bounds how long the chain tail can stay
uncheckpointed; ``rolling_every_n_ticks`` bounds how long any already-checkpointed
region can go without being re-derived. Both are constructor arguments so a
deployment can trade CPU for evidence freshness explicitly and visibly.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from .structured_log import get_struct_logger

_log = get_struct_logger("liuhao.reliability.checkpointer")

# Measured on this repo; see module docstring. Exposed so operators and tests
# can reason about the budget instead of guessing.
US_PER_EVENT_VERIFY = 30.0
DEFAULT_SEGMENT_EVENTS = 5000
DEFAULT_INCREMENTAL_INTERVAL_SEC = 300.0
DEFAULT_ROLLING_EVERY_N_TICKS = 12  # => rolling pass ~every hour at 5 min ticks


def estimate_verify_seconds(events: int, us_per_event: float = US_PER_EVENT_VERIFY) -> float:
    """Predicted wall time to verify ``events`` events (budget helper)."""
    return max(0.0, float(events) * float(us_per_event) / 1_000_000.0)


class CheckpointStats:
    """Observable counters for the background verifier."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ticks = 0
        self.incremental_runs = 0
        self.rolling_runs = 0
        self.events_verified = 0
        self.events_reverified = 0
        self.checkpoints_written = 0
        self.segment_failures = 0
        self.tick_errors = 0
        self.last_tick_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_coverage: Optional[Dict[str, Any]] = None

    def record_tick(self, coverage: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self.ticks += 1
            self.last_tick_at = time.time()
            if coverage is not None:
                self.last_coverage = coverage

    def record_incremental(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self.incremental_runs += 1
            self.events_verified += int(result.get("events_checked") or 0)
            if result.get("new_checkpoint_id") is not None:
                self.checkpoints_written += 1
            if not result.get("segment_verified", False):
                self.segment_failures += 1
                self.last_error = f"incremental segment failed: {result.get('failures')}"

    def record_rolling(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self.rolling_runs += 1
            self.events_reverified += int(result.get("events_reverified") or 0)
            if result.get("failures"):
                self.segment_failures += 1
                self.last_error = f"rolling failures: {result.get('failures')}"

    def record_error(self, exc: BaseException) -> None:
        with self._lock:
            self.tick_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ticks": self.ticks,
                "incremental_runs": self.incremental_runs,
                "rolling_runs": self.rolling_runs,
                "events_verified": self.events_verified,
                "events_reverified": self.events_reverified,
                "checkpoints_written": self.checkpoints_written,
                "segment_failures": self.segment_failures,
                "tick_errors": self.tick_errors,
                "last_tick_at": self.last_tick_at,
                "last_error": self.last_error,
                "last_coverage": self.last_coverage,
            }


class BackgroundVerifier:
    """Single daemon thread that keeps the chain checkpointed.

    The store is resolved lazily on every tick (callable) rather than captured,
    so a store created after the worker starts is still used, and a rebound
    ``AUDIT_DB_PATH`` is not silently pinned to a stale handle.
    """

    def __init__(
        self,
        store_factory: Callable[[], Any],
        *,
        segment_events: int = DEFAULT_SEGMENT_EVENTS,
        incremental_interval_sec: float = DEFAULT_INCREMENTAL_INTERVAL_SEC,
        rolling_every_n_ticks: int = DEFAULT_ROLLING_EVERY_N_TICKS,
        rolling_max_age_sec: Optional[float] = None,
    ) -> None:
        self._store_factory = store_factory
        self.segment_events = int(segment_events)
        self.incremental_interval_sec = float(incremental_interval_sec)
        self.rolling_every_n_ticks = max(1, int(rolling_every_n_ticks))
        self.rolling_max_age_sec = rolling_max_age_sec
        self.stats = CheckpointStats()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- one pass ------------------------------------------------------- #
    def tick(self) -> Dict[str, Any]:
        """Run one incremental verify (+ rolling when due). Never raises."""
        out: Dict[str, Any] = {"rolling": None, "incremental": None}
        try:
            store = self._store_factory()

            inc = store.verify_incremental(max_events=self.segment_events)
            self.stats.record_incremental(inc)
            out["incremental"] = inc

            # Cadence: run rolling on every Nth COMPLETED tick. Two guards:
            #  - `every == 1` means "every tick, including the first";
            #  - otherwise skip tick 1, because `0 % N == 0` is True in Python
            #    and would fire rolling immediately at startup.
            completed = self.stats.snapshot()["ticks"]
            every = self.rolling_every_n_ticks
            due = (every == 1) or (completed > 0 and completed % every == 0)
            if due:
                rol = store.verify_rolling(
                    budget_events=self.segment_events,
                    max_age_sec=self.rolling_max_age_sec,
                )
                self.stats.record_rolling(rol)
                out["rolling"] = rol
                _log.info(
                    "rolling_pass_complete",
                    events_reverified=rol.get("events_reverified"),
                    failures=len(rol.get("failures") or []),
                    remaining_stale=rol.get("remaining_stale"),
                )

            cov = store.verification_coverage()
            self.stats.record_tick(cov)
            _log.info(
                "checkpoint_tick_complete",
                checkpoints=cov.get("checkpoint_count"),
                uncovered=cov.get("uncovered_events"),
                coverage_ratio=cov.get("coverage_ratio"),
                rooted_at_genesis=cov.get("rooted_at_genesis"),
            )
        except Exception as exc:  # never propagate into the host process
            self.stats.record_error(exc)
            _log.error("checkpoint_tick_failed", error=f"{type(exc).__name__}: {exc}")
        return out

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.incremental_interval_sec)

    # -- lifecycle ------------------------------------------------------ #
    def start(self) -> bool:
        """Start the daemon thread. Returns False if already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            t = threading.Thread(
                target=self._run,
                name="liuhao-audit-checkpointer",
                daemon=True,  # never blocks interpreter exit
            )
            self._thread = t
            t.start()
        _log.info(
            "background_verifier_started",
            segment_events=self.segment_events,
            incremental_interval_sec=self.incremental_interval_sec,
            rolling_every_n_ticks=self.rolling_every_n_ticks,
            predicted_seconds_per_tick=round(
                estimate_verify_seconds(self.segment_events), 4
            ),
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        with self._lock:
            self._thread = None
        _log.info("background_verifier_stopped", stats=self.stats.snapshot())

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()


_worker: Optional[BackgroundVerifier] = None
_worker_lock = threading.Lock()


def start_background_verifier(
    store_factory: Optional[Callable[[], Any]] = None,
    **kwargs: Any,
) -> BackgroundVerifier:
    """Start (or return) the process-wide background verifier.

    ``store_factory`` defaults to the audit kernel's ``get_audit_store``.
    Idempotent: a second call returns the already-running worker instead of
    starting a rival thread (two threads writing checkpoints would be exactly
    the kind of concurrency bug this project exists to prevent).
    """
    global _worker
    if store_factory is None:
        from src.kernels.audit import get_audit_store

        store_factory = get_audit_store

    with _worker_lock:
        if _worker is not None and _worker.running:
            return _worker
        _worker = BackgroundVerifier(store_factory, **kwargs)
        _worker.start()
        return _worker


def get_background_verifier() -> Optional[BackgroundVerifier]:
    return _worker


def stop_background_verifier(timeout: float = 5.0) -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop(timeout=timeout)
        _worker = None
