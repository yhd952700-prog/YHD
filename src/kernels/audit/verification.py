"""Segmented / incremental verification for the audit hash chain (C2).

Why this exists
---------------
Verification cost is linear in the chain length: MEASURED ~23-26 us per event,
so a million-event chain takes ~26 seconds to re-verify and a ten-million-event
one takes minutes. Re-verifying the whole history every time is therefore not
viable at the scale the roadmap targets -- but "verify less" is exactly how an
audit system quietly stops being an audit system.

The way out is a **cumulative link hash**, not a checkpoint you trust.

    event_hash_i  = H(canonical fields of event i)      -- already existed
    link_hash_i   = H(link_hash_{i-1} || event_hash_i)  -- added here

`link_hash_i` commits to the entire ordered prefix 1..i in ONE value. That is
what makes a segment verifiable in isolation: to verify events a..b you only
need `link_hash_{a-1}`, and to verify a..b you recompute every event in it, so
the work is proportional to the NEW events, not to the history.

The checkpoint rule (the security requirement)
----------------------------------------------
A stored checkpoint is **derived evidence, never a source of truth**:

* a checkpoint is only ever written INSIDE the transaction that verified its
  segment, and only after that segment verified clean;
* a checkpoint is never trusted because it is stored -- `recompute_checkpoint()`
  re-derives it from the raw events and `audit_checkpoints()` re-derives all of
  them;
* a checkpoint does NOT make the history verified. An incremental verification
  reports `rooted_at_genesis=False` unless the checkpoint chain actually reaches
  back to seq 1. "The checkpoint is correct" is never allowed to stand in for
  "the history is correct" -- those are different claims and the API reports
  them separately.

If a checkpoint row disagrees with a recomputation, that is a DEFECT in the
derived state, not a verdict about the evidence: the recomputation wins and the
checkpoint is discarded.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.common.hash_chain import HASH_ALGORITHMS
from .hashutil import canonical_json

# A segment boundary is chosen in whole events; this is only a default for
# "verify the new tail since the last checkpoint".
DEFAULT_SEGMENT_EVENTS = 5000

_CHECKPOINT_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS verification_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_seq INTEGER NOT NULL,
        end_seq INTEGER NOT NULL,
        start_link_hash TEXT,
        end_link_hash TEXT NOT NULL,
        end_event_hash TEXT NOT NULL,
        event_count INTEGER NOT NULL,
        verified_at REAL NOT NULL,
        method TEXT NOT NULL,
        UNIQUE(start_seq, end_seq)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ckpt_end ON verification_checkpoints(end_seq)",
)


def link_hash(prev_link_hash: Optional[str], event_hash: str, alg: str) -> str:
    """The cumulative commitment: H(prev_link || event_hash).

    Uses the SAME algorithm the event itself declares, so algorithm migration
    stays a per-event property and no second algorithm column is needed.
    """
    algorithm = HASH_ALGORITHMS.get(alg)
    if algorithm is None:
        raise ValueError(
            f"unknown hash algorithm {alg!r}; this build can verify "
            f"{sorted(HASH_ALGORITHMS)}"
        )
    return algorithm(((prev_link_hash or "") + event_hash).encode())


# Where a segment's starting anchor came from. This is reported, never hidden,
# because an anchor decides what a "verified" segment actually proves:
#
#   genesis    -- the segment starts at seq 1, so it proves itself.
#   checkpoint -- the anchor equals a checkpoint that is itself reachable from
#                 seq 1 through an unbroken cover, so it is proven.
#   stored     -- the anchor was read from the events table. It is a POINTER,
#                 not a proof: the segment is recomputed but the history behind
#                 the anchor is not.
#   explicit   -- the caller supplied the anchor. Same caveat as `stored`;
#                 provenance is the caller's responsibility.
ANCHOR_GENESIS = "genesis"
ANCHOR_CHECKPOINT = "checkpoint"
ANCHOR_STORED = "stored"
ANCHOR_EXPLICIT = "explicit"


@dataclass
class SegmentResult:
    """Outcome of verifying one contiguous range of events.

    ``verified`` is True only if every event in the range re-hashed correctly,
    the seq values are contiguous, the prev_event_hash links hold, and the
    accumulated link hash matches ``end_link_hash``.

    ``verified`` alone never means "the history is correct". Read
    ``rooted_at_genesis`` for that claim: a segment anchored on an unproven
    value has only proven a conditional statement ("if the anchor is right,
    this range is right"), and the API says so instead of implying otherwise.
    """

    start_seq: int
    end_seq: int
    verified: bool
    event_count: int
    start_link_hash: Optional[str]
    end_link_hash: Optional[str]
    end_event_hash: Optional[str]
    failures: List[str]
    anchor_source: str = ANCHOR_STORED
    rooted_at_genesis: bool = False

    def as_dict(self) -> dict:
        return {
            "start_seq": self.start_seq,
            "end_seq": self.end_seq,
            "verified": self.verified,
            "event_count": self.event_count,
            "start_link_hash": self.start_link_hash,
            "end_link_hash": self.end_link_hash,
            "end_event_hash": self.end_event_hash,
            "failures": list(self.failures),
            "anchor_source": self.anchor_source,
            "rooted_at_genesis": self.rooted_at_genesis,
        }


@dataclass
class IncrementalResult:
    """Outcome of verifying only the new tail, anchored on a checkpoint.

    Deliberately distinguishes two different claims:

    * ``segment_verified`` -- the newly appended events re-verify.
    * ``rooted_at_genesis`` -- the anchor this run relied on traces back to
      seq 1 through an unbroken chain of checkpoints.

    A caller must not report "the chain is verified" from an incremental run
    that is not rooted at genesis. That distinction is the whole point.
    """

    segment_verified: bool
    rooted_at_genesis: bool
    from_seq: int
    to_seq: int
    events_checked: int
    anchor_checkpoint_id: Optional[int]
    new_checkpoint_id: Optional[int]
    failures: List[str]
    anchor_source: str = ANCHOR_GENESIS

    def as_dict(self) -> dict:
        return {
            "segment_verified": self.segment_verified,
            "rooted_at_genesis": self.rooted_at_genesis,
            "from_seq": self.from_seq,
            "to_seq": self.to_seq,
            "events_checked": self.events_checked,
            "anchor_checkpoint_id": self.anchor_checkpoint_id,
            "new_checkpoint_id": self.new_checkpoint_id,
            "failures": list(self.failures),
            "anchor_source": self.anchor_source,
        }


def _row_event_hash(row) -> Tuple[str, str]:
    """Recompute an event's content hash from its stored fields."""
    (seq, event_id, event_type, principal_id, scope, timestamp,
     correlation_id, outcome, details_json, event_hash, prev_event_hash,
     hash_alg) = row[:12]
    import json
    details = json.loads(details_json) if details_json else {}
    data = {
        "event_id": event_id,
        "event_type": event_type,
        "principal_id": principal_id,
        "scope": scope,
        "timestamp": timestamp,
        "correlation_id": correlation_id,
        "outcome": outcome,
        "details": details,
    }
    algorithm = HASH_ALGORITHMS.get(hash_alg)
    if algorithm is None:
        raise ValueError(f"event {event_id} declares unknown algorithm {hash_alg!r}")
    return algorithm_hash(algorithm, data), event_hash


def algorithm_hash(algorithm, data: dict) -> str:
    """Canonical JSON -> hash, matching AuditEvent.compute_hash."""
    return algorithm(canonical_json(data).encode())


def tail_seq(conn: sqlite3.Connection) -> int:
    """Highest seq the anchor row claims exists (0 for an empty log)."""
    row = conn.execute(
        "SELECT last_seq FROM chain_state WHERE id = 1").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def coverage_frontier(conn: sqlite3.Connection, upto_seq: Optional[int] = None) -> int:
    """Highest seq N such that 1..N is covered by an unbroken checkpoint chain.

    Checkpoints are only allowed to extend the frontier; a checkpoint that
    starts beyond frontier+1 leaves a hole and everything after it is ignored.
    """
    rows = conn.execute(
        "SELECT start_seq, end_seq FROM verification_checkpoints "
        "ORDER BY start_seq ASC, end_seq ASC"
    ).fetchall()
    frontier = 0
    for start, end in rows:
        if start > frontier + 1:
            break
        if end > frontier:
            frontier = end
        if upto_seq is not None and frontier >= upto_seq:
            return upto_seq
    return frontier


def anchor_provenance(
    conn: sqlite3.Connection,
    start_seq: int,
    start_link_hash: Optional[str],
) -> Tuple[str, bool]:
    """Classify where a segment's anchor came from, and whether it is proven.

    See the ANCHOR_* constants. This is the guard against the failure mode
    where a cached value quietly becomes a trust root: a segment anchored on
    something unproven is reported as unproven, even when it verifies.
    """
    if start_seq <= 1:
        return ANCHOR_GENESIS, True

    anchor_seq = start_seq - 1
    if coverage_frontier(conn, anchor_seq) >= anchor_seq:
        # The anchor sits inside a genesis-rooted cover. Confirm the value
        # itself matches the checkpoint that ends there, otherwise a stale or
        # edited link_hash would be laundered into a "proven" anchor.
        row = conn.execute(
            "SELECT end_link_hash FROM verification_checkpoints "
            "WHERE end_seq = ? ORDER BY id DESC LIMIT 1", (anchor_seq,)
        ).fetchone()
        if row is not None and row[0] == start_link_hash:
            return ANCHOR_CHECKPOINT, True

    if start_link_hash is None:
        return ANCHOR_STORED, False
    return ANCHOR_EXPLICIT, False


def verify_segment(
    conn: sqlite3.Connection,
    start_seq: int,
    end_seq: int,
    start_link_hash: Optional[str],
    require_link_hash: bool = True,
    anchor_source: Optional[str] = None,
) -> SegmentResult:
    """Recompute every event in [start_seq, end_seq] and accumulate the chain.

    This is the primitive everything else is built on. It reads raw events and
    trusts nothing: no checkpoint, no chain_state row, no cached value.

    Pass ``anchor_source`` to override provenance classification when the
    caller already knows where the anchor came from; otherwise it is derived
    from the database.
    """
    failures: List[str] = []
    rows = conn.execute(
        "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
        "correlation_id, outcome, details, event_hash, prev_event_hash, "
        "hash_alg, link_hash FROM audit_events WHERE seq BETWEEN ? AND ? "
        "ORDER BY seq ASC",
        (start_seq, end_seq),
    ).fetchall()

    if anchor_source is not None:
        provenance = anchor_source
        rooted = provenance in (ANCHOR_GENESIS, ANCHOR_CHECKPOINT)
    else:
        provenance, rooted = anchor_provenance(conn, start_seq, start_link_hash)

    if not rows:
        return SegmentResult(start_seq, end_seq, True, 0, start_link_hash,
                             start_link_hash, None, [], provenance, rooted)

    running_link = start_link_hash
    prev_event_hash = None
    prev_seq = start_seq - 1
    first = True

    for row in rows:
        seq = row[0]
        if seq != prev_seq + 1:
            failures.append(
                f"seq gap or reorder at seq={seq} (expected {prev_seq + 1})")
            prev_seq = seq
        else:
            prev_seq = seq

        try:
            computed, stored = _row_event_hash(row)
        except ValueError as exc:
            failures.append(str(exc))
            break
        if computed != stored:
            failures.append(f"content hash mismatch at seq={seq}")
        if not first and row[10] != prev_event_hash:
            failures.append(f"broken prev_event_hash join at seq={seq}")

        # Cumulative commitment (C2). The chain is defined over the stored
        # event hashes, so it detects any edit to the LINKAGE -- a row removed,
        # reordered, or a link_hash column rewritten -- while the content check
        # above detects edits to the fields. Together they cover both attacker
        # models: change the content and the hash check fires; change the
        # content AND recompute its event_hash and the link check fires.
        stored_link = row[12]
        lh_algs = row[11]
        try:
            expected_link = link_hash(running_link, stored, lh_algs)
        except ValueError as exc:
            failures.append(str(exc))
            break

        if stored_link is None:
            # A row written before C2 existed. The accumulation cannot continue
            # across it, exactly as in full verification.
            running_link = None
        else:
            if expected_link != stored_link:
                failures.append(f"link hash mismatch at seq={seq}")
                # Re-sync to the stored value so one corruption produces ONE
                # pinpointed failure instead of cascading through the rest.
                running_link = stored_link
            else:
                running_link = expected_link

        prev_event_hash = stored
        first = False

    return SegmentResult(
        start_seq=start_seq,
        end_seq=end_seq,
        verified=not failures,
        event_count=len(rows),
        start_link_hash=start_link_hash,
        end_link_hash=running_link,
        end_event_hash=prev_event_hash,
        failures=failures,
        anchor_source=provenance,
        rooted_at_genesis=rooted,
    )


def create_tables(conn: sqlite3.Connection) -> None:
    for statement in _CHECKPOINT_SCHEMA:
        conn.execute(statement)


def latest_checkpoint(conn: sqlite3.Connection) -> Optional[Tuple]:
    row = conn.execute(
        "SELECT id, start_seq, end_seq, start_link_hash, end_link_hash, "
        "end_event_hash, event_count, verified_at, method "
        "FROM verification_checkpoints ORDER BY end_seq DESC, id DESC LIMIT 1"
    ).fetchone()
    return row


def all_checkpoints(conn: sqlite3.Connection) -> List[Tuple]:
    return conn.execute(
        "SELECT id, start_seq, end_seq, start_link_hash, end_link_hash, "
        "end_event_hash, event_count, verified_at, method "
        "FROM verification_checkpoints ORDER BY start_seq ASC"
    ).fetchall()


def rooted_at_genesis(conn: sqlite3.Connection,
                      upto_seq: Optional[int] = None) -> bool:
    """True only if checkpoints form an unbroken cover from seq 1 to the tail.

    Two ways this is False, and both matter:

    * a gap or a non-genesis start (e.g. an imported checkpoint at 51..100) --
      part of the history was never covered;
    * coverage that stops short of the tail -- the newest events were appended
      after the last checkpoint, so "rooted" would otherwise be claimed for
      data nobody re-derived.

    With `upto_seq` given, coverage is required through that seq instead.
    """
    if upto_seq is None:
        upto_seq = tail_seq(conn)
    if upto_seq <= 0:
        # Nothing to cover: vacuously rooted, and there is no unverified data.
        return True
    return coverage_frontier(conn, upto_seq) >= upto_seq


def write_checkpoint(
    conn: sqlite3.Connection,
    start_seq: int,
    end_seq: int,
    start_link_hash: Optional[str],
    end_link_hash: str,
    end_event_hash: Optional[str],
    event_count: int,
    method: str,
) -> int:
    """Persist a DERIVED checkpoint. Caller must have verified the segment."""
    cur = conn.execute(
        """INSERT INTO verification_checkpoints
           (start_seq, end_seq, start_link_hash, end_link_hash, end_event_hash,
            event_count, verified_at, method)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (start_seq, end_seq, start_link_hash, end_link_hash, end_event_hash,
         event_count, time.time(), method),
    )
    return cur.lastrowid


def recompute_checkpoint(conn: sqlite3.Connection, checkpoint_id: int) -> bool:
    """Re-derive one checkpoint from raw events. True if it still holds.

    This is the operation that keeps a checkpoint from becoming a second trust
    root: anything that is stored can and must be re-derived on demand.
    """
    row = conn.execute(
        "SELECT id, start_seq, end_seq, start_link_hash, end_link_hash, "
        "end_event_hash, event_count FROM verification_checkpoints WHERE id = ?",
        (checkpoint_id,),
    ).fetchone()
    if row is None:
        return False
    (_id, start_seq, end_seq, start_link_hash, end_link_hash,
     end_event_hash, event_count) = row
    result = verify_segment(conn, start_seq, end_seq, start_link_hash)
    return (
        result.verified
        and result.event_count == event_count
        and result.end_link_hash == end_link_hash
        and result.end_event_hash == end_event_hash
    )


def audit_checkpoints(conn: sqlite3.Connection) -> Tuple[bool, List[int]]:
    """Re-derive every checkpoint. Returns (all_ok, ids_that_failed)."""
    bad = []
    for row in conn.execute(
        "SELECT id FROM verification_checkpoints ORDER BY id"
    ).fetchall():
        if not recompute_checkpoint(conn, row[0]):
            bad.append(row[0])
    return (not bad), bad


def stale_checkpoints(conn: sqlite3.Connection,
                      max_age_sec: Optional[float] = None,
                      limit: Optional[int] = None) -> List[Tuple]:
    """Checkpoint rows ordered oldest-verified first -- the rolling work queue.

    `max_age_sec` filters to rows not re-verified within that window, so an
    operator can ask for "everything I have not looked at in the last day"
    instead of "everything".
    """
    sql = ("SELECT id, start_seq, end_seq, start_link_hash, end_link_hash, "
           "end_event_hash, event_count, verified_at, method "
           "FROM verification_checkpoints")
    params: List = []
    if max_age_sec is not None:
        sql += " WHERE verified_at <= ?"
        params.append(time.time() - max_age_sec)
    sql += " ORDER BY verified_at ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def refresh_checkpoint(conn: sqlite3.Connection, checkpoint_id: int,
                       end_link_hash: str, event_count: int) -> bool:
    """Stamp a checkpoint as re-verified NOW -- but only if it did not move.

    The guard on `end_link_hash` and `event_count` is what makes this safe to
    do outside the verification transaction: if the region changed between the
    recomputation and the stamp, nothing is stamped and the caller re-checks.
    """
    cur = conn.execute(
        "UPDATE verification_checkpoints SET verified_at = ? "
        "WHERE id = ? AND end_link_hash = ? AND event_count = ?",
        (time.time(), checkpoint_id, end_link_hash, event_count),
    )
    return cur.rowcount == 1


def verification_coverage(conn: sqlite3.Connection) -> dict:
    """How much of the chain is currently covered by a genesis-rooted cover.

    Reported as numbers, not as a verdict: an operator decides what staleness
    is acceptable, the system only refuses to hide it.
    """
    tail = tail_seq(conn)
    rows = all_checkpoints(conn)
    frontier = coverage_frontier(conn, tail)
    verified_at = [r[7] for r in rows]
    oldest = min(verified_at) if verified_at else None
    uncovered = []
    covers = sorted((r[1], r[2]) for r in rows)
    expected = 1
    for start, end in covers:
        if start > expected:
            uncovered.append((expected, start - 1))
        expected = max(expected, end + 1)
    if expected <= tail:
        uncovered.append((expected, tail))
    return {
        "tail_seq": tail,
        "covered_through": frontier,
        "uncovered_events": max(0, tail - frontier),
        "coverage_ratio": (frontier / tail) if tail else 1.0,
        "checkpoint_count": len(rows),
        "uncovered_ranges": uncovered,
        "oldest_verified_at": oldest,
        "newest_verified_at": max(verified_at) if verified_at else None,
        "rooted_at_genesis": frontier >= tail,
    }


def discard_checkpoints_from(conn: sqlite3.Connection, seq: int) -> int:
    """Delete derived checkpoints covering events from `seq` onwards.

    Used after any event at or after `seq` is found to be wrong: the derived
    state that covered it is no longer true and must not be reused.
    """
    cur = conn.execute(
        "DELETE FROM verification_checkpoints WHERE end_seq >= ?", (seq,)
    )
    return cur.rowcount
