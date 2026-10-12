"""LiuHao AI OS developer CLI / SDK entry point.

Expanded CLI with comprehensive command modules for agent management,
memory operations, health checks, cost tracking, backup management,
plugin management, organization structure, solution templates,
benchmarking, and an interactive shell.

Callable via ``python -m src.cli``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import List, Optional

from src.benchmarks.sizing import estimate_sizing
from src.kernels.audit.recovery import detect

# Extended CLI modules
from src.cli.agent_commands import (
    AgentManager,
    handle_agent_command,
    register_agent_commands,
)
from src.cli.backup_commands import (
    BackupManager,
    handle_backup_command,
    register_backup_commands,
)
from src.cli.benchmark_commands import (
    BenchmarkManager,
    handle_benchmark_command,
    register_benchmark_commands,
)
from src.cli.cost_commands import (
    CostManager,
    handle_cost_command,
    register_cost_commands,
)
from src.cli.health_commands import (
    HealthChecker,
    handle_health_command,
    register_health_commands,
)
from src.cli.memory_commands import (
    MemoryManager,
    handle_memory_command,
    register_memory_commands,
)
from src.cli.org_commands import (
    OrgManager,
    handle_org_command,
    register_org_commands,
)
from src.cli.plugin_commands import (
    PluginManager,
    handle_plugin_command,
    register_plugin_commands,
)
from src.cli.shell_commands import (
    ShellSession,
    handle_shell_command,
    register_shell_commands,
)
from src.cli.solution_commands import (
    SolutionManager,
    handle_solution_command,
    register_solution_commands,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the main CLI argument parser with all subcommands.

    Returns:
        Configured argparse.ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="liuhao-cli",
        description="LiuHao AI OS Command Line Interface",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -----------------------------------------------------------------
    # Existing commands (audit, bench)
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # Extended CLI command modules
    # -----------------------------------------------------------------
    register_agent_commands(sub)
    register_memory_commands(sub)
    register_health_commands(sub)
    register_cost_commands(sub)
    register_backup_commands(sub)
    register_plugin_commands(sub)
    register_org_commands(sub)
    register_solution_commands(sub)
    register_benchmark_commands(sub)
    register_shell_commands(sub)

    return parser


# =====================================================================
# Original command handlers
# =====================================================================


def _run_audit_detect(db_path: str) -> dict:
    """Run audit fork detection on the given SQLite DB path.

    Args:
        db_path: Path to the audit SQLite database.

    Returns:
        Dict with detection report.
    """
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
    """Run the storage sizing estimation.

    Args:
        events_per_day: Events per day.
        retention_days: Retention period in days.
        avg_bytes: Average event size in bytes.
        ceiling_eps: Ceiling events per second.

    Returns:
        Dict with sizing estimate.
    """
    return estimate_sizing(
        events_per_day, retention_days, avg_bytes, ceiling_eps
    )


# =====================================================================
# Main entry point
# =====================================================================


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point.

    Args:
        argv: Optional argument list (uses sys.argv if not provided).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Original commands
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

    # Extended commands - each handled by its own module
    if args.command == "agent":
        return handle_agent_command(args, AgentManager())
    if args.command == "memory":
        return handle_memory_command(args, MemoryManager())
    if args.command == "health":
        return handle_health_command(args, HealthChecker())
    if args.command == "cost":
        return handle_cost_command(args, CostManager())
    if args.command == "backup":
        return handle_backup_command(args, BackupManager())
    if args.command == "plugin":
        return handle_plugin_command(args, PluginManager())
    if args.command == "org":
        return handle_org_command(args, OrgManager())
    if args.command == "solution":
        return handle_solution_command(args, SolutionManager())
    if args.command == "benchmark":
        return handle_benchmark_command(args, BenchmarkManager())
    if args.command == "shell":
        return handle_shell_command(args, ShellSession())

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
