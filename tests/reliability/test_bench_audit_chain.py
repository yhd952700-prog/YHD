"""Tests for scripts/bench_audit_chain.py (benchmarking/sizing harness, U9)."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import bench_audit_chain as bench  # noqa: E402


def test_run_benchmark_quick_sane():
    report = bench.run_benchmark(quick=True)
    # Throughput must be strictly positive (real production write path).
    assert report["throughput"]["per_event_commit_eps"] > 0
    assert report["throughput"]["raw_batch_eps"] > 0
    # Sizing: at least some bytes per row.
    assert report["sizing"]["bytes_per_row"] > 0
    assert report["sizing"]["rows"] > 0
    # Integrity scan on the sample must pass and cover the written rows.
    assert report["verify"]["verified_ok"] is True
    assert report["verify"]["verified_rows"] == report["sizing"]["rows"]
    # Extrapolation to 1M must be a finite, positive number.
    assert report["verify"]["extrapolated_seconds_at_1M"] > 0


def test_main_quick_returns_zero():
    assert bench.main(["--quick"]) == 0
