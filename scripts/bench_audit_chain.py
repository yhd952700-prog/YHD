"""Audit-chain throughput & sizing benchmark harness (LIUHAO reliability).

OWNS the benchmarking/sizing design (U9). Measures, against an ISOLATED temp
SQLite DB (never the production audit store):

  * per-event-commit append throughput  (the real production write path)
  * batched-commit append throughput     (theoretical single-writer ceiling)
  * on-disk bytes/row and projected ANNUAL storage at configurable rates
  * a coarse full-chain integrity-scan time (measured on a sample, extrapolated
    to 1,000,000 rows) for capacity-planning the F5/F6 verification gate

Usage
-----
    python scripts/bench_audit_chain.py            # full run
    python scripts/bench_audit_chain.py --quick    # CI-friendly small N
    python scripts/bench_audit_chain.py --json out.json

Exit code 0 on success, 1 on a sanity violation (throughput <= 0, etc.).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

# Make the repo importable when run as a standalone script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kernels.audit import AuditStore, AuditEventType, AuditScope  # noqa: E402

# Projection rates (events/sec) used for annual-storage sizing.
DEFAULT_RATES = (10, 100)
_SECONDS_PER_YEAR = 365 * 24 * 3600
_GIB = 1024**3


def _make_event_kwargs(i: int) -> dict:
    return dict(
        event_type=AuditEventType.KERNEL_IMPLEMENTATION,
        principal_id="bench",
        scope=AuditScope.L0,
        outcome="allow",
        details={"i": i},
        correlation_id=f"bench-{i}",
    )


def bench_per_event_commit(store: AuditStore, n: int) -> float:
    """Real production write path: one commit per log_event call."""
    start = time.perf_counter()
    for i in range(n):
        store.log_event(**_make_event_kwargs(i))
    elapsed = time.perf_counter() - start
    return n / elapsed if elapsed > 0 else 0.0


def bench_raw_batch(db_path: str, n: int, batch: int = 5000) -> float:
    """Theoretical single-writer ceiling: bulk INSERT in transactions."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS t("
        "event_id TEXT PRIMARY KEY, seq INTEGER, payload TEXT)"
    )
    start = time.perf_counter()
    for base in range(0, n, batch):
        rows = [
            (f"r{base + k}", base + k, "x" * 200) for k in range(min(batch, n - base))
        ]
        conn.executemany("INSERT INTO t VALUES (?,?,?)", rows)
        conn.commit()
    elapsed = time.perf_counter() - start
    conn.close()
    return n / elapsed if elapsed > 0 else 0.0


def measure_sizing(db_path: str, n: int, rates=DEFAULT_RATES) -> dict:
    size = os.path.getsize(db_path)
    bytes_per_row = size / n if n else 0.0
    projection = {
        f"{rate}_eps": round(bytes_per_row * rate * _SECONDS_PER_YEAR / _GIB, 3)
        for rate in rates
    }
    return {
        "db_bytes": size,
        "rows": n,
        "bytes_per_row": round(bytes_per_row, 2),
        "annual_gib_projection": projection,
    }


def bench_verify_extrapolate(store: AuditStore, n: int, target_rows: int = 1_000_000) -> dict:
    """Time a full integrity scan on the sample, extrapolate to target_rows."""
    start = time.perf_counter()
    ok, verified = store.verify_integrity()
    elapsed = time.perf_counter() - start
    per_row = (elapsed / n) if n else 0.0
    return {
        "verified_ok": bool(ok),
        "verified_rows": verified,
        "sample_seconds": round(elapsed, 4),
        "seconds_per_row_us": round(per_row * 1e6, 3),
        "extrapolated_seconds_at_1M": round(per_row * target_rows, 2),
    }


def run_benchmark(quick: bool = False) -> dict:
    workdir = tempfile.mkdtemp(prefix="liuhao-bench-")
    per_event_db = os.path.join(workdir, "per_event.db")
    batch_db = os.path.join(workdir, "batch.db")
    try:
        n_per_event = 500 if quick else 20_000
        n_batch = 5_000 if quick else 200_000

        store = AuditStore(per_event_db)
        eps_per_event = bench_per_event_commit(store, n_per_event)
        sizing = measure_sizing(per_event_db, n_per_event)
        verify = bench_verify_extrapolate(store, n_per_event)

        eps_batch = bench_raw_batch(batch_db, n_batch)

        report = {
            "throughput": {
                "per_event_commit_eps": round(eps_per_event, 1),
                "raw_batch_eps": round(eps_batch, 1),
            },
            "sizing": sizing,
            "verify": verify,
            "quick": quick,
        }
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LIUHAO audit-chain benchmark")
    parser.add_argument("--quick", action="store_true", help="small N for CI")
    parser.add_argument("--json", metavar="PATH", help="write report JSON to PATH")
    args = parser.parse_args(argv)

    report = run_benchmark(quick=args.quick)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"report written to {args.json}")

    print(json.dumps(report, indent=2))

    # Sanity gate: throughput must be strictly positive.
    ok = (
        report["throughput"]["per_event_commit_eps"] > 0
        and report["throughput"]["raw_batch_eps"] > 0
        and report["sizing"]["bytes_per_row"] > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
