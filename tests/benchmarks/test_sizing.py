"""Tests for the benchmarking / sizing harness (src/benchmarks/sizing).

All benchmarks run against in-memory or throwaway temp DBs. They never open,
read, or write the production audit_store.db or any Evidence file.
"""

import sqlite3

import pytest

from src.benchmarks.sizing import (
    benchmark_audit_append,
    benchmark_verify_cost,
    estimate_sizing,
)


def test_benchmark_audit_append():
    conn = sqlite3.connect(":memory:")
    r = benchmark_audit_append(conn, 300, commit_per_event=True)
    assert r["events_written"] == 300
    assert r["elapsed_sec"] > 0
    assert r["events_per_sec"] > 0
    cnt = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert cnt == 300


def test_benchmark_verify_cost_clean_chain():
    conn = sqlite3.connect(":memory:")
    benchmark_audit_append(conn, 300)
    v = benchmark_verify_cost(conn, 300)
    assert v["rows_verified"] == 300
    assert v["rows_per_sec"] > 0
    # A clean, sequentially-built chain has zero broken links.
    assert v["broken_links"] == 0.0


def test_estimate_sizing_no_bottleneck():
    r = estimate_sizing(
        events_per_day=1_000_000,
        retention_days=365,
        avg_event_bytes=512,
        ceiling_events_per_sec=5000,
    )
    assert r["total_events"] == 1_000_000 * 365
    assert r["storage_bytes"] == 1_000_000 * 365 * 512
    assert r["storage_bytes_with_overhead"] == r["storage_bytes"] * 2.0
    assert r["sustained_events_per_sec"] == pytest.approx(1_000_000 / 86400)
    # 1M/day ~= 11.6 eps sustained; usable ceiling 2500 eps -> no bottleneck.
    assert r["single_writer_bottleneck"] is False


def test_estimate_sizing_bottleneck():
    r = estimate_sizing(
        events_per_day=300_000_000,
        retention_days=30,
        avg_event_bytes=512,
        ceiling_events_per_sec=5000,
    )
    # 300M/day ~= 3472 eps sustained; usable ceiling 2500 eps -> bottleneck.
    assert r["single_writer_bottleneck"] is True


def test_harness_temp_db_cleanup_is_caller_responsibility():
    # Sanity: the helper returns a usable connection with the schema present.
    from src.benchmarks.sizing import harness_temp_db
    import os

    path, conn = harness_temp_db()
    try:
        assert os.path.exists(path)
        r = benchmark_audit_append(conn, 50)
        assert r["events_written"] == 50
    finally:
        conn.close()
        os.remove(path)
    assert not os.path.exists(path)
