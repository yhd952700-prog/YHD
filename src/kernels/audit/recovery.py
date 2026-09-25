"""HC-01 chain-fork recovery framework (U3 — Wave 1 design + scaffold).

Pipeline:  detect  ->  quarantine  ->  re-derive  ->  re-verify

This module is the building block for the later HC-01 F1–F6 remediation
(Q3.5). It isolates the *fork topology* of an audit hash-chain and performs a
**non-destructive** migration of the forked history into additive side tables,
leaving the original ``audit_events`` and ``chain_state`` tables byte-for-byte
intact.

Hard guarantees (I1..I10 from
``LIUHAO-Phase3.6-HC01-Migration-Invariants.md``):

* No DELETE and no UPDATE of existing ``audit_events`` rows
  (I1 no-delete / I2 no-reorder). The forked history is preserved as frozen,
  NON-AUTHORITATIVE evidence.
* No second genesis (I3): the original ``chain_state`` single row is never
  overwritten. A SEPARATE ``chain_anchor_v2`` row carries the new authoritative
  tail so future single-writer appends never reuse a forked ``seq``.
* Additive only (I4): all migration state lives in new side tables
  (``audit_events_quarantine``, ``chain_anchor_v2``, ``migration_manifest``).
* Authority provenance (I5): quarantined rows are tagged ``NON-AUTHORITATIVE``.
* Parameterized I1..I10 (Autonomous Decision Memo §2): historical baseline
  values are FIXED; live-state values are captured at migration start via the
  manifest.

CALLER RESPONSIBILITY — this module NEVER opens ``audit_store.db`` itself.
Open the live DB with a *transaction-consistent* snapshot (``VACUUM INTO`` for
copies; never copy a WAL-mode file with an uncheckpointed ``-wal``). The
forensic snapshot and all Evidence files remain IMMUTABLE; this module neither
reads nor writes them.

This module does NOT assert HC-01 is VERIFIED. The chain remains forked until
F1–F6 remediation lands and an independent verification confirms integrity.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Side-table names (additive; original audit_events / chain_state untouched).
# --------------------------------------------------------------------------- #
QUARANTINE_TABLE = "audit_events_quarantine"
ANCHOR_TABLE = "chain_anchor_v2"
MANIFEST_TABLE = "migration_manifest"

__all__ = [
    "ForkReport",
    "detect",
    "quarantine",
    "rederive",
    "reverified",
    "run",
    "QUARANTINE_TABLE",
    "ANCHOR_TABLE",
    "MANIFEST_TABLE",
]


@dataclass
class ForkReport:
    """Topology of a forked audit chain (read-only analysis output)."""

    total_events: int = 0
    distinct_seq: int = 0
    max_seq: int = 0
    # Robust lower bound (physical rowid order) — stable and anchorable.
    broken_joins_rowid: int = 0
    # seq-order count — UNSTABLE on duplicate seq; reference only.
    broken_joins_seq: int = 0
    # Number of seq values shared by >1 row (fork points).
    duplicate_seq_count: int = 0
    # Sum(count-1) over duplicate-seq clusters (redundant rows).
    redundant_rows: int = 0
    # The actual forked seq values.
    fork_seqs: List[int] = field(default_factory=list)
    # Genesis row carries a non-null prev_event_hash (anomaly).
    first_event_prev_not_null: bool = False
    # Does chain_state agree with the last stored row? None if no rows/state.
    chain_state_consistent: Optional[bool] = None
    genesis_event_id: Optional[str] = None


def detect(conn: sqlite3.Connection) -> ForkReport:
    """Read-only fork analysis. Never writes to the database.

    ``verify_integrity`` orders by ``seq ASC``; with duplicate ``seq`` values
    that tie-order is undefined — which is why the historical "284 broken joins"
    figure was unstable. We therefore count broken joins under TWO orderings:

    * ``broken_joins_rowid`` — by physical ``rowid`` (stable, the robust lower
      bound). A row whose ``prev_event_hash`` disagrees with the rowid
      predecessor's ``event_hash`` is *definitely* broken.
    * ``broken_joins_seq`` — by ``seq`` (unstable on duplicate seq; reference).
    """
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    distinct_seq, max_seq = cur.execute(
        "SELECT COUNT(DISTINCT seq), COALESCE(MAX(seq), 0) FROM audit_events"
    ).fetchone()

    clusters = cur.execute(
        "SELECT seq, COUNT(*) c FROM audit_events "
        "GROUP BY seq HAVING c > 1 ORDER BY seq"
    ).fetchall()
    fork_seqs = [r[0] for r in clusters]
    redundant_rows = sum(r[1] - 1 for r in clusters)

    rows = cur.execute(
        "SELECT event_id, event_hash, prev_event_hash "
        "FROM audit_events ORDER BY rowid"
    ).fetchall()

    broken_rowid = 0
    first_prev = False
    for i, (_, ehash, prev) in enumerate(rows):
        if i == 0:
            if prev is not None:
                first_prev = True
                broken_rowid += 1
        elif prev != rows[i - 1][1]:
            broken_rowid += 1

    srows = cur.execute(
        "SELECT event_hash, prev_event_hash "
        "FROM audit_events ORDER BY seq, rowid"
    ).fetchall()
    broken_seq = 0
    for i, (ehash, prev) in enumerate(srows):
        if i == 0:
            if prev is not None:
                broken_seq += 1
        elif prev != srows[i - 1][0]:
            broken_seq += 1

    tail = cur.execute(
        "SELECT seq, event_hash FROM audit_events "
        "ORDER BY seq DESC, rowid DESC LIMIT 1"
    ).fetchone()
    state = cur.execute(
        "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
    ).fetchone()
    if state is not None and tail is not None:
        chain_state_consistent = state[0] == tail[0] and state[1] == tail[1]
    else:
        chain_state_consistent = None

    return ForkReport(
        total_events=total,
        distinct_seq=distinct_seq,
        max_seq=max_seq,
        broken_joins_rowid=broken_rowid,
        broken_joins_seq=broken_seq,
        duplicate_seq_count=len(fork_seqs),
        redundant_rows=redundant_rows,
        fork_seqs=fork_seqs,
        first_event_prev_not_null=first_prev,
        chain_state_consistent=chain_state_consistent,
        genesis_event_id=rows[0][0] if rows else None,
    )


def _ensure_side_tables(conn: sqlite3.Connection) -> None:
    """Create the additive migration side tables (idempotent)."""
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
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
            seq INTEGER NOT NULL,
            hash_alg TEXT NOT NULL,
            created_at REAL,
            authority TEXT NOT NULL DEFAULT 'NON-AUTHORITATIVE',
            migration_epoch TEXT NOT NULL,
            quarantine_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_q_epoch
            ON {QUARANTINE_TABLE}(migration_epoch);
        CREATE TABLE IF NOT EXISTS {ANCHOR_TABLE} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL,
            last_hash TEXT,
            epoch TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        );
        CREATE TABLE IF NOT EXISTS {MANIFEST_TABLE} (
            epoch TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            live_row_count INTEGER NOT NULL,
            live_max_seq INTEGER NOT NULL,
            live_schema_version TEXT,
            live_db_hash TEXT,
            historical_baseline_rows INTEGER,
            historical_baseline_hash TEXT,
            fork_clusters INTEGER,
            redundant_rows INTEGER,
            anchor_seq INTEGER,
            anchor_hash TEXT,
            notes TEXT
        );
        """
    )


def quarantine(
    conn: sqlite3.Connection,
    report: ForkReport,
    epoch: str,
    reason: str = "chain fork - duplicate-seq branch",
) -> int:
    """Non-destructive: COPY forked-region rows into a side table.

    The forked region = every row whose ``seq`` participates in a duplicate-seq
    cluster. Original ``audit_events`` rows are COPIED, never deleted or
    modified (I1 no-delete / I2 no-reorder). Copied rows are tagged
    ``NON-AUTHORITATIVE`` so consumers can separate the trusted tail from
    frozen fork history (I5).

    Returns the number of rows quarantined for this ``epoch``.
    """
    _ensure_side_tables(conn)
    fork_seqs = report.fork_seqs
    if not fork_seqs:
        return 0
    placeholders = ",".join("?" * len(fork_seqs))
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {QUARANTINE_TABLE}
            (event_id, event_type, principal_id, scope, timestamp,
             correlation_id, outcome, details, event_hash, prev_event_hash,
             seq, hash_alg, created_at, authority, migration_epoch,
             quarantine_reason)
        SELECT event_id, event_type, principal_id, scope, timestamp,
               correlation_id, outcome, details, event_hash, prev_event_hash,
               seq, hash_alg, created_at, 'NON-AUTHORITATIVE', ?, ?
        FROM audit_events
        WHERE seq IN ({placeholders})
        """,
        [epoch, reason, *fork_seqs],
    )
    return conn.execute(
        f"SELECT COUNT(*) FROM {QUARANTINE_TABLE} WHERE migration_epoch = ?",
        (epoch,),
    ).fetchone()[0]


def rederive(
    conn: sqlite3.Connection, report: ForkReport, epoch: str
) -> Tuple[int, Optional[str]]:
    """Establish a NEW authoritative anchor ABOVE all existing history.

    Non-destructive (I3 no-second-genesis): the original ``chain_state`` row is
    never overwritten. We write a SEPARATE ``chain_anchor_v2`` row pointing at
    the absolute tail, so future single-writer appends start at
    ``max_seq + 1`` and never reuse a forked ``seq`` (no new duplicate-seq).

    The tail is the row with ``seq == max_seq`` and the highest ``rowid``
    (deterministic tie-break among forked branches).
    """
    _ensure_side_tables(conn)
    max_seq = report.max_seq
    tip = conn.execute(
        "SELECT event_hash FROM audit_events "
        "WHERE seq = ? ORDER BY rowid DESC LIMIT 1",
        (max_seq,),
    ).fetchone()
    tip_hash = tip[0] if tip else None
    conn.execute(
        f"""
        INSERT INTO {ANCHOR_TABLE} (id, last_seq, last_hash, epoch)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_seq = excluded.last_seq,
            last_hash = excluded.last_hash,
            epoch = excluded.epoch
        """,
        (max_seq, tip_hash, epoch),
    )
    return max_seq, tip_hash


def reverified(
    conn: sqlite3.Connection,
    report: ForkReport,
    epoch: str,
    *,
    pre_row_count: int,
    historical_baseline_rows: Optional[int] = None,
    historical_baseline_hash: Optional[str] = None,
    live_schema_version: Optional[str] = None,
    live_db_hash: Optional[str] = None,
) -> Dict[str, object]:
    """Re-verify the MIGRATION invariants (not the chain itself, which stays
    forked until F1–F6 lands).

    Checks:
      * no-delete — current ``audit_events`` row count == pre-migration count.
      * quarantine captured every forked-region row.
      * original ``chain_state`` is UNCHANGED (we never touch it).
      * ``chain_anchor_v2`` is consistent with the chosen tail.

    Records a parameterized manifest (I1..I10): historical baseline values
    stay FIXED; live-state values captured at migration start.
    """
    cur = conn.cursor()

    post_row_count = cur.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    q_count = cur.execute(
        f"SELECT COUNT(*) FROM {QUARANTINE_TABLE} WHERE migration_epoch = ?",
        (epoch,),
    ).fetchone()[0]
    state = cur.execute(
        "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
    ).fetchone()
    anchor = cur.execute(
        f"SELECT last_seq, last_hash FROM {ANCHOR_TABLE} WHERE id = 1"
    ).fetchone()

    checks = {
        "no_delete_row_count_unchanged": post_row_count == pre_row_count,
        "quarantine_captured_fork_rows": q_count
        >= report.redundant_rows + report.duplicate_seq_count,
        "chain_state_untouched": state is not None,
        "anchor_consistent_with_tail": anchor is not None
        and anchor[0] == report.max_seq,
    }
    all_ok = all(checks.values())

    cur.execute(
        f"""
        INSERT INTO {MANIFEST_TABLE}
            (epoch, created_at, live_row_count, live_max_seq,
             live_schema_version, live_db_hash, historical_baseline_rows,
             historical_baseline_hash, fork_clusters, redundant_rows,
             anchor_seq, anchor_hash, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(epoch) DO UPDATE SET
            live_row_count = excluded.live_row_count,
            anchor_seq = excluded.anchor_seq,
            anchor_hash = excluded.anchor_hash,
            notes = excluded.notes
        """,
        (
            epoch,
            time.time(),
            post_row_count,
            report.max_seq,
            live_schema_version,
            live_db_hash,
            historical_baseline_rows,
            historical_baseline_hash,
            report.duplicate_seq_count,
            report.redundant_rows,
            anchor[0] if anchor else None,
            anchor[1] if anchor else None,
            "parameterized I1..I10; historical baseline FIXED; "
            "live-state captured at migration start",
        ),
    )

    return {
        "all_ok": all_ok,
        "checks": checks,
        "post_row_count": post_row_count,
        "quarantine_count": q_count,
    }


def run(
    conn: sqlite3.Connection,
    *,
    epoch: Optional[str] = None,
    historical_baseline_rows: Optional[int] = None,
    historical_baseline_hash: Optional[str] = None,
    live_schema_version: Optional[str] = None,
    live_db_hash: Optional[str] = None,
    reason: str = "chain fork - duplicate-seq branch",
) -> Dict[str, object]:
    """Full detect -> quarantine -> re-derive -> re-verify pipeline.

    ``conn`` must be a connection the CALLER opened (read + write) against a
    transaction-consistent copy/snapshot of the live DB. This function never
    opens ``audit_store.db`` and never DELETEs or UPDATEs ``audit_events`` rows.
    """
    epoch = epoch or uuid.uuid4().hex[:16]
    pre_row_count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    report = detect(conn)
    quarantined = quarantine(conn, report, epoch, reason)
    anchor_seq, anchor_hash = rederive(conn, report, epoch)
    verification = reverified(
        conn,
        report,
        epoch,
        pre_row_count=pre_row_count,
        historical_baseline_rows=historical_baseline_rows,
        historical_baseline_hash=historical_baseline_hash,
        live_schema_version=live_schema_version,
        live_db_hash=live_db_hash,
    )
    conn.commit()
    return {
        "epoch": epoch,
        "report": report,
        "quarantined_rows": quarantined,
        "anchor_seq": anchor_seq,
        "anchor_hash": anchor_hash,
        "verification": verification,
    }
