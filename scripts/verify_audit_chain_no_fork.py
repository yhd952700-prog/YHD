#!/usr/bin/env python3
"""verify_audit_chain_no_fork.py -- fail-closed monitor for the LIVE deployed
audit_store.db: it must NOT be forked (RCA-1 recurrence guard, F6).

This closes the gap left by the rest of the audit-integrity aggregate
(scripts/verify_audit_integrity.py): those gates drive HERMETIC temporary
stores or re-derive the seven JSON hash chains (HC-02..HC-08), but NONE of
them open the actual DEPLOYED SQLite audit_store.db. So a re-fork of the live
store -- the RCA-1 concurrency defect: concurrent writers assigned the same
sequence number because ``seq`` allocation was not atomic under a single-writer
fence -> duplicate ``seq`` numbers and broken hash-chain joins -- would go
UNNOTICED by CI.

This gate reads the LIVE store READ-ONLY and asserts two things:

  1. ``verify_integrity()`` reports ``ok=True``  (no broken joins / tamper /
     missing hash_alg).
  2. ``duplicate_seq_count() == 0``  (no fork signature -- every seq unique).

Fail-closed: any violation => non-zero exit (default 2). A missing live store
(no deployed DB in this environment) is reported as a SKIP (exit 0) -- honest,
not a silent pass; the aggregate flags it PASS (NOTE).

READ-ONLY POSTURE: this gate NEVER writes, repairs, restores, migrates, or
rebuilds the live store. Those actions are frozen human-sovereign decisions
under HC-01 (HUMAN DECISION PENDING / GO=BLOCKED) and must not be taken by
automation. This gate is team-owned engineering (F1-F6) under
HC-01-Autonomous-Decision-Memo.md; it only prevents a SILENT re-fork of the
live store. It does NOT assert HC-01 is VERIFIED.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit import AuditStore  # noqa: E402


def _resolve_live_db_path() -> str:
    # Mirror AuditStore.__init__ default resolution so the gate watches the
    # SAME store production writes to.
    env = os.environ.get("AUDIT_DB_PATH")
    if env:
        return env
    return str(REPO / "audit_store.db")


def main() -> int:
    db_path = _resolve_live_db_path()
    if not os.path.exists(db_path):
        # Nothing deployed to verify in this environment. Honest SKIP -- the
        # aggregate's NOTE_MARKERS will flag this as PASS (NOTE), never a
        # silent PASS.
        print(
            "NO-FORK GATE SKIP: live audit store not present at %s "
            "(no deployed DB to verify in this environment)" % db_path
        )
        return 0

    # Constructing the store opens it read-write, but this gate only ever READS
    # through the store's read-only snapshot path (verify_integrity /
    # duplicate_seq_count). It never issues a write, repair, or rebuild.
    try:
        store = AuditStore(db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        # A forked/corrupt live store may refuse to open (e.g. the UNIQUE(seq)
        # backstop rejects a duplicate-seq store). That is still a hard alert --
        # fail-closed, never swallow.
        print("NO-FORK GATE FAIL: cannot open live store %s: %s" % (db_path, exc))
        return 2
    try:
        ok, total = store.verify_integrity()
        if not ok:
            print(
                "NO-FORK GATE FAIL: verify_integrity()=False on live store "
                "(%s) -- broken joins / tamper / missing hash_alg detected; "
                "this is the RCA-1 fork-or-tamper signature and must be "
                "escalated, not silently passed" % db_path
            )
            return 2

        dup = store.duplicate_seq_count()
        if dup != 0:
            print(
                "NO-FORK GATE FAIL: duplicate_seq_count()=%d on live store "
                "(%s) -- RCA-1 fork signature RECURRED (non-unique seq); the "
                "audit chain is forked and must be escalated, not silently "
                "passed" % (dup, db_path)
            )
            return 2
    except Exception as exc:  # noqa: BLE001
        print("NO-FORK GATE FAIL: %s" % exc)
        return 2
    finally:
        try:
            store._conn.close()
        except Exception:  # noqa: BLE001
            pass

    print(
        "NO-FORK GATE PASS: live audit store at %s is not forked "
        "(verify_integrity ok=True on %d events; duplicate_seq_count=0)"
        % (db_path, total)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
