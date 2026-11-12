"""Execution journal — connection-access serialization (regression).

Why this file exists
--------------------
``ExecutionJournal`` opens ONE ``sqlite3.Connection`` with
``check_same_thread=False`` and documents the contract "Serialize **all**
connection access" (``_journal.py``, ``__init__`` comment). ``record()`` and
``events()`` honoured it; ``executions()``, ``completed_task_names()``,
``completed_task_artifacts()`` and ``failed_task_names()`` did not -- they ran
their SELECTs straight off the shared connection with no lock. That is the same
defect class the audit store's ADR already ruled a defect (Finding 5:
"execute on the shared connection without self._lock ... this is an
inconsistency, not a considered exemption").

The recovery path makes it bite: ``ExecutionEngine._adopt_journaled_progress``
calls ``completed_task_names()`` and ``completed_task_artifacts()`` in sequence
to decide which completed work to reclaim. A concurrent ``task_completed``
commit landing between an unlocked read and the next one yields a
completed-set and an artifact-map that describe two different points in time.

These tests assert the CONTRACT directly and deterministically (an instrumented
lock counts entries) rather than hoping a timing race reproduces.
"""

from __future__ import annotations

import threading

import pytest

from src.kernels.execution._journal import ExecutionJournal


class _RecordingLock:
    """A reentrant lock that records how many times ``__enter__`` ran."""

    def __init__(self) -> None:
        self._inner = threading.RLock()
        self.enters = 0
        self.exits = 0

    def __enter__(self):
        self.enters += 1
        return self._inner.__enter__()

    def __exit__(self, *exc) -> bool:
        self.exits += 1
        return self._inner.__exit__(*exc)


@pytest.fixture
def journal(tmp_path):
    j = ExecutionJournal(str(tmp_path / "j.db"))
    j.record("e1", "execution_started")
    j.record("e1", "task_completed", task_name="A", artifact_path="/tmp/a")
    j.record("e1", "task_failed", task_name="B")
    yield j
    j.close()


class TestReadPathsHoldTheLock:
    """Every read method must go through ``self._lock``."""

    def test_executions_takes_lock(self, journal):
        lock = _RecordingLock()
        journal._lock = lock
        journal.executions()
        assert lock.enters == 1

    def test_events_takes_lock(self, journal):
        lock = _RecordingLock()
        journal._lock = lock
        journal.events("e1")
        assert lock.enters == 1

    def test_completed_task_names_takes_lock(self, journal):
        lock = _RecordingLock()
        journal._lock = lock
        journal.completed_task_names("e1")
        assert lock.enters == 1

    def test_completed_task_artifacts_takes_lock(self, journal):
        lock = _RecordingLock()
        journal._lock = lock
        journal.completed_task_artifacts("e1")
        assert lock.enters == 1

    def test_failed_task_names_holds_lock_across_both_selects(self, journal):
        """The "failed minus completed" subtraction must be ONE critical section.

        It composes ``completed_task_names`` internally, so a correct
        implementation enters the lock twice (outer + reentrant) and -- crucially
        -- does not deadlock. That requires a reentrant lock.
        """
        lock = _RecordingLock()
        journal._lock = lock
        result = journal.failed_task_names("e1")
        assert result == {"B"}  # A completed, B failed-and-not-succeeded
        assert lock.enters == 2, "failed_task_names must hold the lock across both SELECTs"
        assert lock.exits == 2


class TestReentrancy:
    def test_lock_is_reentrant_not_plain_lock(self, tmp_path):
        """A plain ``threading.Lock`` would self-deadlock in ``failed_task_names``."""
        j = ExecutionJournal(str(tmp_path / "j.db"))
        try:
            j.record("e1", "task_failed", task_name="X")
            done = threading.Event()
            box: dict = {}

            def call() -> None:
                box["result"] = j.failed_task_names("e1")
                done.set()

            t = threading.Thread(target=call)
            t.start()
            t.join(timeout=10)
            assert done.is_set(), "failed_task_names deadlocked -> lock is not reentrant"
            assert box["result"] == {"X"}
        finally:
            j.close()

    def test_isinstance_rlock(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        try:
            assert isinstance(j._lock, type(threading.RLock()))
        finally:
            j.close()


class TestConcurrentWriterReader:
    """Stress: a writer thread appends while a reader thread reads."""

    def test_no_errors_and_monotonic_completed_count(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        errors: list = []
        seen: list = []
        stop = threading.Event()
        N = 300

        def writer() -> None:
            try:
                for i in range(N):
                    j.record("e1", "task_completed", task_name=f"t{i}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            try:
                last = 0
                while not stop.is_set() or True:
                    n = len(j.completed_task_names("e1"))
                    # append-only + serialized reads -> never goes backwards
                    assert n >= last, f"completed count went backwards: {last} -> {n}"
                    last = n
                    seen.append(n)
                    if stop.is_set():
                        break
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        try:
            tw = threading.Thread(target=writer)
            tr = threading.Thread(target=reader)
            tw.start()
            tr.start()
            tw.join(timeout=30)
            tr.join(timeout=30)
            assert not tw.is_alive() and not tr.is_alive(), "threads did not finish"
            assert not errors, f"concurrent access raised: {errors!r}"
            # Final state is complete and consistent.
            assert len(j.completed_task_names("e1")) == N
            assert j.failed_task_names("e1") == set()
        finally:
            j.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
