"""Soak + concurrency + crash-recovery test for the audit store.

Covers three owner-listed continuation areas:
- reliability / soak: sustained high-volume appends (no loss, chain intact)
- concurrency: multithreaded appends against one store (shared connection +
  writer lease) must never lose events or fork the seq chain
- crash / recovery: an abrupt connection close (simulated process crash) must
  not lose committed events; a fresh store on the same path must recover and
  continue the chain with no gap

Never opens, reads, or writes the production ``audit_store.db``. All tests use
throwaway temp DBs.
"""

import os
import threading

from src.kernels.audit import (
    AuditStore,
    AuditEventType,
    AuditScope,
)


def _make_store(dirpath: str) -> AuditStore:
    # Explicit path: bypasses the global singleton AND the project-root default,
    # so this test never touches the live evidence DB regardless of env.
    path = os.path.join(dirpath, "audit_soak.db")
    store = AuditStore(db_path=path)
    store.initialize()
    return store


def test_concurrent_soak_appends_no_loss(tmp_path):
    """4 threads x 2500 appends = 10000 events; chain must stay intact."""
    store = _make_store(str(tmp_path))
    n_threads = 4
    per_thread = 2500
    total = n_threads * per_thread

    errors = []

    def worker(tid):
        try:
            for i in range(per_thread):
                store.log_event(
                    AuditEventType.ACCESS_CHECK,
                    f"p{tid}",
                    AuditScope.L1,
                    "allow",
                    details={"tid": tid, "i": i},
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent appends raised: {errors[:3]}"
    ok, count = store.verify_integrity()
    assert ok, "audit chain failed integrity after concurrent soak"
    assert count == total, f"lost appends: expected {total}, got {count}"


def test_crash_recovery_after_append(tmp_path):
    """Abrupt close (no graceful shutdown) must not lose committed events;
    a fresh store on the same path recovers and continues the chain."""
    store = _make_store(str(tmp_path))
    n = 500
    for i in range(n):
        store.log_event(
            AuditEventType.POLICY_EVAL, "p1", AuditScope.L2, "allow",
            details={"i": i},
        )

    # Simulate a process crash: slam the connection shut without shutdown().
    store._conn.close()

    reopened = _make_store(str(tmp_path))
    ok, count = reopened.verify_integrity()
    assert ok, "chain not recoverable after abrupt close"
    assert count == n, f"events lost on recovery: expected {n}, got {count}"

    # Append after recovery must continue with no gap in seq / chain.
    reopened.log_event(AuditEventType.POLICY_EVAL, "p1", AuditScope.L2, "allow")
    ok2, count2 = reopened.verify_integrity()
    assert ok2 and count2 == n + 1, f"append after recovery broke chain: {count2}"
