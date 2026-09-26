"""Benchmarking / sizing harness for the LIUHAO audit chain (Q3.3, os-systems).

Answers the scalability questions raised by discovery (U11 / U14):

* What is the **single-writer append throughput ceiling** on this hardware?
  (The production audit write path commits once per event
  — see ``src/kernels/audit/__init__.py`` — so a single-writer, per-event-commit
  loop is the realistic ceiling, not a batched optimum.)
* How does **chain re-verification cost** scale with row count?
* Given a daily event rate and retention window, what **storage** is required,
  and when does the single-writer ceiling become the bottleneck?

This harness writes only to a CALLER-SUPPLIED SQLite connection or temp file.
It NEVER opens or writes the production ``audit_store.db`` or any Evidence file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
# Tuple is imported even though ``from __future__ import annotations`` makes the
# annotation lazy: anything that RESOLVES the hints (typing.get_type_hints,
# documentation tooling) would otherwise raise NameError on harness_temp_db().
from typing import Dict, Optional, Tuple

# Mirror of the audit_events schema subset the harness exercises.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    timestamp REAL NOT NULL,
    correlation_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details TEXT,
    event_hash TEXT NOT NULL,
    prev_event_hash TEXT,
    seq INTEGER NOT NULL DEFAULT 0,
    hash_alg TEXT NOT NULL DEFAULT 'sha256',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_seq ON audit_events(seq);
"""


def _event_hash(event_id: str, prev) -> str:
    """Canonical audit-event hash (matches AuditEvent.compute_hash linkage)."""
    data = {
        "event_id": event_id,
        "prev": prev,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


def build_schema(conn: sqlite3.Connection) -> None:
    """Create the harness schema on a caller-supplied connection."""
    conn.executescript(_SCHEMA)


def benchmark_audit_append(
    conn: sqlite3.Connection,
    n_events: int,
    *,
    commit_per_event: bool = True,
) -> Dict[str, float]:
    """Measure single-writer append throughput.

    Writes ``n_events`` synthetic audit events sequentially with the same
    seq / prev_hash linkage the production chain uses, committing per event
    (``commit_per_event=True``) to reflect the real write path ceiling.

    Returns events/sec, total wall time, and events written.
    """
    if n_events <= 0:
        raise ValueError("n_events must be > 0")
    build_schema(conn)
    prev = None
    start = time.perf_counter()
    for i in range(1, n_events + 1):
        eid = f"b{i:08d}"
        h = _event_hash(eid, prev)
        conn.execute(
            "INSERT INTO audit_events "
            "(event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details, event_hash, prev_event_hash, seq, "
            "hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, "state_change", "bench", "L0", time.time(), "c", "allow",
                None, h, prev, i, "sha256",
            ),
        )
        prev = h
        if commit_per_event:
            conn.commit()
    if not commit_per_event:
        conn.commit()
    elapsed = time.perf_counter() - start
    return {
        "events_written": float(n_events),
        "elapsed_sec": elapsed,
        "events_per_sec": n_events / elapsed if elapsed > 0 else float("inf"),
    }


def benchmark_verify_cost(
    conn: sqlite3.Connection, n: int
) -> Dict[str, float]:
    """Measure chain re-verification cost over the first ``n`` rows.

    Walks ``seq`` order, checking each row's ``prev_event_hash`` equals the
    predecessor's ``event_hash`` — the O(n) core of ``verify_integrity``.
    Returns rows/sec verified and elapsed time.
    """
    if n <= 0:
        raise ValueError("n must be > 0")
    rows = conn.execute(
        "SELECT event_hash, prev_event_hash FROM audit_events "
        "ORDER BY seq ASC LIMIT ?",
        (n,),
    ).fetchall()
    start = time.perf_counter()
    broken = 0
    for i, (ehash, prev) in enumerate(rows):
        if i == 0:
            if prev is not None:
                broken += 1
        elif prev != rows[i - 1][0]:
            broken += 1
    elapsed = time.perf_counter() - start
    return {
        "rows_verified": float(len(rows)),
        "broken_links": float(broken),
        "elapsed_sec": elapsed,
        "rows_per_sec": len(rows) / elapsed if elapsed > 0 else float("inf"),
    }


def estimate_sizing(
    events_per_day: int,
    retention_days: int,
    avg_event_bytes: int,
    ceiling_events_per_sec: float,
    *,
    safety_margin: float = 0.5,
) -> Dict[str, float]:
    """Project storage and single-writer bottleneck.

    ``safety_margin`` = fraction of the measured ceiling we are willing to
    sustain continuously (0.5 = plan to use <=50% of ceiling).
    """
    if events_per_day <= 0 or retention_days <= 0 or avg_event_bytes <= 0:
        raise ValueError("events_per_day/retention_days/avg_event_bytes must be > 0")
    if ceiling_events_per_sec <= 0:
        raise ValueError("ceiling_events_per_sec must be > 0")

    total_events = events_per_day * retention_days
    storage_bytes = total_events * avg_event_bytes
    # SQLite row + index overhead is typically ~1.5-2x the raw payload.
    storage_bytes_with_overhead = storage_bytes * 2.0

    sustained_eps = events_per_day / 86400.0
    usable_ceiling = ceiling_events_per_sec * safety_margin
    bottleneck = sustained_eps > usable_ceiling

    return {
        "total_events": float(total_events),
        "storage_bytes": float(storage_bytes),
        "storage_bytes_with_overhead": float(storage_bytes_with_overhead),
        "sustained_events_per_sec": sustained_eps,
        "usable_ceiling_events_per_sec": usable_ceiling,
        "single_writer_bottleneck": bool(bottleneck),
    }


def harness_temp_db() -> Tuple[str, sqlite3.Connection]:
    """Create a throwaway temp SQLite DB for a benchmark run.

    Caller is responsible for closing the connection and removing the file.
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="liuhao_bench_")
    os.close(fd)
    conn = sqlite3.connect(path)
    build_schema(conn)
    return path, conn
