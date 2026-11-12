#!/usr/bin/env python3
"""verify_fence_single_writer.py -- fail-closed CI gate for the audit
single-writer fence (Q3.5 / #99 / HD-02=A).

The fence is one of the most load-bearing audit-integrity invariants after the
hash chain: it makes "exactly one writer may append" a HARD guarantee, closing
the cross-process fork gap (RCA-1) and the C3 split-brain risk. The primitive
lives in src/kernels/audit/fencing.py and is wired into the REAL append path
(src/kernels/audit/__init__.py::_attempt_log_event -> acquire_within +
require_writer_lease, all inside one atomic transaction). It has pytest
coverage, but it is NOT yet a fail-closed CI gate and is NOT part of the
unified verify_audit_integrity.py aggregate -- so a regression of the append-
path fence wiring would leave the canonical aggregate green.

This gate closes that gap. It drives the REAL AuditStore against a HERMETIC
temporary SQLite database (never the deployed audit_store.db) and proves two
things end-to-end:

  1. NORMAL PATH: 5 sequential writes succeed and verify_integrity() reports
     (ok=True, total=5) -- the fence does NOT trap the 2nd+ writer in the same
     process (a known design trap the lease must avoid).
  2. FENCE PATH: after a newer writer has fenced the store (higher token,
     foreign owner, unexpired), the NEXT log_event is REFUSED with
     StaleWriterError / FencedWriterError. A fenced writer must not append.
     It also asserts the fence primitive require_writer_lease() itself rejects a
     stale token.

Fail-closed: any assertion failure or unexpected exception => non-zero exit
(default 2). Exit 0 = both invariants hold on this machine.

This is a SELF-TEST of the wired invariant (it proves the capability EXISTS and
is fail-closed on a fresh, isolated store); it does NOT assert the live deployed
audit_store.db is fenced -- that integration remains gated on HC-01 stabilisation
(D22).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

# Deterministic: test the DEFAULT (pid) identity model, the production posture.
os.environ["LIUHAO_AUDIT_LEASE_IDENTITY"] = "pid"

from src.kernels.audit import AuditEventType, AuditScope, AuditStore  # noqa: E402
from src.kernels.audit.fencing import (  # noqa: E402
    StaleWriterError,
    require_writer_lease,
)


def _write(store: AuditStore, i: int):
    return store.log_event(
        AuditEventType.STATE_CHANGE, f"p{i}", AuditScope.L0, "ok",
        details={"i": i}, correlation_id=f"c{i}",
    )


def run_fence_check(db_path: str) -> None:
    """Drive a hermetic AuditStore and assert both fence invariants.

    Returns normally if both hold; raises AssertionError (or any Exception) on
    any violation.
    """
    store = AuditStore(db_path=db_path)
    try:
        # (1) NORMAL PATH: 5 sequential writes must succeed and verify.
        for i in range(5):
            ev = _write(store, i)
            if ev is None:
                raise AssertionError(
                    "fence gate: log_event returned None on normal write %d" % i)
        ok, total = store.verify_integrity()
        if not ok:
            raise AssertionError(
                "fence gate: verify_integrity() returned ok=False on normal writes")
        if total != 5:
            raise AssertionError(
                "fence gate: verify_integrity() total=%r, expected 5" % total)

        # (2a) FENCE PATH: simulate a newer writer having fenced this store.
        store._conn.execute(
            "UPDATE writer_lease SET token=?, owner=?, expires_at=? WHERE id=1",
            (999, "intruder", time.time() + 100),
        )
        store._conn.commit()
        try:
            _write(store, 5)
        except StaleWriterError:
            pass  # correct: the fenced writer was refused on the next append
        else:
            raise AssertionError(
                "FENCE NOT ENFORCED: log_event accepted an intruder writer "
                "(token=999, owner='intruder', unexpired) -- the single-writer "
                "fence on the audit append path has regressed")

        # (2b) FENCE PRIMITIVE: require_writer_lease() must reject a stale token.
        try:
            require_writer_lease(store._lease, 12345)
        except StaleWriterError:
            pass  # correct: a stale token is refused
        else:
            raise AssertionError(
                "FENCE NOT ENFORCED: require_writer_lease accepted a stale token "
                "-- the single-writer fence primitive has regressed")
    finally:
        try:
            store._conn.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="liuhao-fence-gate-") as tmp:
        db_path = str(Path(tmp) / "audit.db")
        try:
            run_fence_check(db_path)
        except Exception as exc:  # noqa: BLE001
            print("FENCE GATE FAIL: %s" % exc)
            return 2
    print(
        "FENCE GATE PASS: single-writer fence on audit append path holds "
        "(normal 5 writes + integrity verified; intruder writer refused; "
        "stale token refused)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
