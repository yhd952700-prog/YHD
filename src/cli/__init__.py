"""Minimal LIUHAO developer CLI / SDK entry point (Q3.4 scaffold).

Non-intrusive: read-only subcommands only. Does not mutate the audit store or
any Evidence file. Callable via ``python -m src.cli`` once wired as a console
script (intentionally NOT registered as an entry point yet, to stay minimal).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import List, Optional

from src.benchmarks.sizing import estimate_sizing
from src.kernels.audit.recovery import detect


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liuhao-cli", description="LIUHAO developer CLI (scaffold)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="audit-chain inspection (read-only)")
    p_audit_sub = p_audit.add_subparsers(dest="audit_command", required=True)
    p_detect = p_audit_sub.add_parser(
        "detect", help="detect fork topology of an audit DB (read-only)"
    )
    p_detect.add_argument(
        "--db", required=True, help="path to an audit SQLite DB (opened read-only)"
    )

    p_bench = sub.add_parser("bench", help="benchmarking / sizing")
    p_bench_sub = p_bench.add_subparsers(dest="bench_command", required=True)
    p_sizing = p_bench_sub.add_parser("sizing", help="storage / bottleneck projection")
    p_sizing.add_argument("--events-per-day", type=int, required=True)
    p_sizing.add_argument("--retention-days", type=int, required=True)
    p_sizing.add_argument("--avg-bytes", type=int, default=512)
    p_sizing.add_argument("--ceiling-eps", type=float, required=True)
    return parser


def _run_audit_detect(db_path: str) -> dict:
    # Normalise Windows backslashes to forward slashes so the ``file:`` URI form
    # (required for ?mode=ro) is valid even when the path contains spaces.
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        report = detect(conn)
        return {
            "total_events": report.total_events,
            "distinct_seq": report.distinct_seq,
            "max_seq": report.max_seq,
            "broken_joins_rowid": report.broken_joins_rowid,
            "duplicate_seq_count": report.duplicate_seq_count,
            "redundant_rows": report.redundant_rows,
            "fork_seqs": report.fork_seqs,
            "chain_state_consistent": report.chain_state_consistent,
        }
    finally:
        conn.close()


def _run_bench_sizing(
    events_per_day: int, retention_days: int, avg_bytes: int, ceiling_eps: float
) -> dict:
    return estimate_sizing(
        events_per_day, retention_days, avg_bytes, ceiling_eps
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit" and args.audit_command == "detect":
        print(json.dumps(_run_audit_detect(args.db), indent=2))
        return 0
    if args.command == "bench" and args.bench_command == "sizing":
        print(
            json.dumps(
                _run_bench_sizing(
                    args.events_per_day,
                    args.retention_days,
                    args.avg_bytes,
                    args.ceiling_eps,
                ),
                indent=2,
            )
        )
        return 0

    parser.print_help()
    return 1
