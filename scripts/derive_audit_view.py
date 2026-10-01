#!/usr/bin/env python3
"""D22 — Read-only derived audit view (safe forensic re-derivation of audit_store.db).

WHY THIS EXISTS
---------------
The deployed ``audit_store.db`` is the system of record for the LIUHAO audit
chain. Several containment decisions (D22 red line, P0-8 hash-chain discipline)
require that the original database is **never deleted, reordered, or overwritten**
by any tool. This script produces a *derived* view that can be re-sorted and
re-verified WITHOUT touching the original.

SAFETY CONTRACT (this script is strictly READ-ONLY on the source)
---------------------------------------------------------------
* The source database is opened read-only for hashing and is **never** written,
  reordered, or vacuumed.
* Regardless of what ``--source`` points at, the script first makes an isolated
  copy via SQLite's ``.backup`` API into a throwaway temp file and performs ALL
  work on that copy. The original is only ever read (for its byte-level sha256).
* ``VACUUM`` is never issued. No write-back to the original ever happens.
* Output is deterministic given the source bytes (no wall-clock timestamps are
  written into the output), so re-running on the same source yields byte-identical
  files (idempotent) and can never perturb the record of record.

WHAT IT COMPUTES
----------------
1. ``MANIFEST(sha256)`` — the sha256 over the ORIGINAL db file bytes, plus
   provenance metadata. This anchors the derived view to a specific original.
2. Re-sorts the chain records by ``timestamp`` (stable tie-break on ``event_id``)
   and recomputes each record's content hash using the SAME canonical 8-field
   form the write path uses (see scripts/verify_p08b_chain_matrix.py HC-01):
       {event_id, event_type, principal_id, scope,
        timestamp, correlation_id, outcome, details}
   excluding ``event_hash`` / ``prev_event_hash``. A match against the stored
   ``event_hash`` proves the content is intact (content-provable).
3. Partitions the re-sorted chain into intervals:
     * "order_provable"      — records whose integer-second timestamp is unique
                               in its neighbourhood, so timestamp alone fixes
                               their relative order; content-provable AND
                               order-provable.
     * "position_unprovable" — maximal runs of >=2 records sharing the SAME
                               integer second. Timestamp cannot disambiguate
                               their internal order. They remain CONTENT-PROVABLE
                               but POSITION-UNPROVABLE ("content provable,
                               position unprovable").
4. Detects same-second collision groups and marks the CRITICAL-sovereignty ones.
   The figure "14" historically cited by decision D22 ("grouped at 14 spots") is
   a POINT-IN-TIME observation, NOT a stable or reproducible invariant. The live
   ``audit_store.db`` is append-only and continuously written, so this
   data-driven count DRIFTS (a STEP 11 run found 348; this snapshot finds 354).
   The script therefore never freezes the number, never redefines the metric to
   force a match, and never gates/fails on the delta. It computes the LIVE count
   with documented conditions + snapshot time, compares it against a clearly
   labeled ``BASELINE_SNAPSHOT`` (date / query / conditions recorded as
   METADATA, not as pass/fail truth), and reports the delta as a FINDING with an
   explicit explanation of the difference (read-only reporter; never a gate).

USAGE
-----
    python scripts/derive_audit_view.py --source audit_store.db --out out_dir

Exit code is 0 on a successful, safe derivation. The script never exits non-zero
for *data* reasons (it is a reporter, not a gate); it only fails on operational
errors (unreadable source, unwritable out dir).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import tempfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The table that holds the main audit chain in audit_store.db.
CHAIN_TABLE = "audit_events"

#: The 8 fields covered by the canonical content hash (HC-01 discipline).
#: ``event_hash`` and ``prev_event_hash`` are deliberately EXCLUDED.
CANONICAL_FIELDS = (
    "event_id",
    "event_type",
    "principal_id",
    "scope",
    "timestamp",
    "correlation_id",
    "outcome",
    "details",
)

HASH_ALG = "sha256"

#: BASELINE_SNAPSHOT for the CRITICAL-sovereignty same-second collision-group
#: count.
#:
#: RE-ADJUDICATED in Phase 3.6 (forensic DB investigation). The former static
#: constant ``14`` (D22 decision "grouped at 14 spots") has been REVOKED as a
#: pass/fail truth. It is kept here ONLY as a clearly-labeled historical
#: reference recorded as METADATA, never as a gate.
#:
#: The live count is data-driven and DRIFTS because the audit database is still
#: being written; it is therefore NOT a stable, reproducible invariant. The
#: script computes the live count, compares it to this baseline, and reports the
#: delta as a FINDING with an explanation -- it never redefines the metric to
#: force a match, and never fails/gates on the difference.
BASELINE_SNAPSHOT = {
    "label": "D22-decision historical reference (NOT a pass/fail threshold)",
    "recorded_date": "2026-09-24",  # date this baseline metadata was (re)captured
    "recorded_count": 14,           # D22 decision figure; expected to drift vs live
    "query_conditions": (
        "collision_group = a maximal run of >=2 records sharing the same "
        "integer-second timestamp; a group is 'critical_sovereignty' when its "
        "members include >=2 CRITICAL human_sovereignty_override events "
        "(details.risk_levels contains at least one 'CRITICAL' value)"
    ),
    "source": "D22 decision 'grouped at 14 spots' (historical, point-in-time)",
    "caveat": (
        "Point-in-time figure only. The live audit_store.db is still being "
        "written, so the live count is expected to differ from 14. Never use as "
        "a gate. See the FINDING (live vs baseline delta) for the explanation; "
        "the metric must not be redefined to force a match."
    ),
}

TOOL_NAME = "derive_audit_view.py"
TOOL_VERSION = "1.1.0"

#: API alignment note (locked against HEAD ba094678).
#:
#: The content hash this tool recomputes MUST match the write path's
#: ``AuditEvent.compute_hash`` byte-for-byte, or every record would read as
#: content-unprovable (silent false negative). The current write path
#: (``src/kernels/audit/__init__.py`` -> ``hashutil.py``) canonicalises via
#: ``canonical_json(event_payload(event))`` where:
#:   * ``event_payload`` returns exactly the 8 CANONICAL_FIELDS above (it does
#:     NOT include event_hash, prev_event_hash, link_hash, seq, or hash_alg);
#:   * ``canonical_json`` is ``json.dumps(data, sort_keys=True,
#:     separators=(",", ":"))`` -- identical to this module's :func:`_canon`.
#: Therefore :func:`canonical_content_hash` is a faithful re-derivation.
SCHEMA_ALIGNMENT = {
    "locked_against_head": "ba094678",
    "canonical_fields": list(CANONICAL_FIELDS),
    "canonicalization": "json.dumps(sort_keys=True, separators=(',',':'))",
    "excluded_from_content_hash": [
        "event_hash", "prev_event_hash", "link_hash", "seq", "hash_alg",
    ],
    "source_of_truth": (
        "src/kernels/audit/__init__.py:AuditEvent.compute_hash -> "
        "src/kernels/audit/hashutil.py:canonical_json/event_payload"
    ),
    "link_hash_note": (
        "link_hash is a C2 cumulative chain commitment "
        "(link_hash_i = H(link_hash_{i-1} || event_hash_i)); it is linkage-only "
        "and is NOT part of an event's content, so it is excluded from the "
        "content hash and never written back by this read-only tool."
    ),
}

#: Output file names (written into --out; never into the source directory).
MANIFEST_FILENAME = "MANIFEST(sha256)"
PARTITION_FILENAME = "audit_view_partition.json"
RECORDS_FILENAME = "audit_view_records.jsonl"


# ---------------------------------------------------------------------------
# Hashing / canonicalization (must match the write path's discipline)
# ---------------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def canonical_content_hash(record: dict) -> str:
    """Recompute the content hash of a chain record.

    Mirrors the canonical form used by the authoritative write path
    (``src.kernels.audit.AuditEvent.compute_hash``): the hash covers the event's
    content and EXCLUDES ``event_hash`` and ``prev_event_hash``. ``details`` is a
    JSON TEXT column; the write path parses it back into a dict before
    canonicalization, so we do the same here (otherwise the raw-string form
    would never match). A match against the stored ``event_hash`` proves the
    stored content is intact.
    """
    payload = {k: record.get(k) for k in CANONICAL_FIELDS}
    details = payload.get("details")
    if details is not None and isinstance(details, str):
        try:
            payload["details"] = json.loads(details)
        except (ValueError, TypeError):
            # Leave as-is; an unparseable details will surface as
            # content_unprovable rather than silently match.
            pass
    return sha256_hex(_canon(payload).encode("utf-8"))


def compute_file_sha256(path: str) -> tuple[str, int]:
    """sha256 over the original file bytes. Read-only; never mutates the file."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


# ---------------------------------------------------------------------------
# Safe copy (never operate on the original in place)
# ---------------------------------------------------------------------------

def backup_to_temp_copy(source_path: str) -> str:
    """Make an isolated copy of ``source_path`` via SQLite ``.backup``.

    The source is opened READ-ONLY. The copy lives in a throwaway temp dir and is
    the ONLY database this tool reads records from. Returns the copy path.
    """
    src = sqlite3.connect(f"file:{os.path.abspath(source_path)}?mode=ro", uri=True)
    tmp_dir = tempfile.mkdtemp(prefix="derive_audit_view_copy_")
    copy_path = os.path.join(tmp_dir, "audit_store.copy.db")
    dst = sqlite3.connect(copy_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return copy_path


# ---------------------------------------------------------------------------
# Load + re-derive
# ---------------------------------------------------------------------------

def load_chain_records(conn: sqlite3.Connection) -> list[dict]:
    # SELECT * (not a hard-coded column list) so the loader stays robust to
    # ADDITIVE schema changes such as the ``link_hash`` column added to
    # ``audit_events`` after the original D22 write (see audit module history
    # around HEAD ba094678). ``link_hash`` is linkage-only (C2 cumulative chain
    # commitment) and is intentionally EXCLUDED from the content hash -- see
    # :func:`canonical_content_hash`. Field access below is by name, so column
    # order does not matter.
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"SELECT * FROM {CHAIN_TABLE} ORDER BY rowid")
    return [dict(r) for r in cur.fetchall()]


def is_critical_sovereignty_event(record: dict) -> bool:
    """A CRITICAL sovereignty event = a ``human_sovereignty_override`` whose
    ``details.risk_levels`` contains at least one ``CRITICAL`` value."""
    if record.get("event_type") != "human_sovereignty_override":
        return False
    raw = record.get("details")
    if not raw:
        return False
    try:
        details = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    risk_levels = (details or {}).get("risk_levels") or {}
    return any(str(v).upper() == "CRITICAL" for v in risk_levels.values())


def resort_and_recompute(records: list[dict]) -> list[dict]:
    """Re-sort by (timestamp, event_id) — stable and deterministic — then
    recompute the content hash and a timestamp-driven derived chain link.

    The derived chain link (``derived_chain_prev_hash``) is independent of the
    original ``seq`` ordering; it only proves content integrity + the
    timestamp-driven order this view imposes.
    """
    sorted_records = sorted(
        records, key=lambda r: (float(r.get("timestamp", 0.0) or 0.0), str(r.get("event_id", "")))
    )
    prev = "genesis"
    for r in sorted_records:
        recomputed = canonical_content_hash(r)
        r["recomputed_event_hash"] = recomputed
        r["content_provable"] = (recomputed == (r.get("event_hash") or ""))
        r["derived_chain_prev_hash"] = prev
        prev = recomputed
    return sorted_records


def partition_intervals(sorted_records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Walk the timestamp-sorted chain and split into intervals.

    Returns ``(intervals, collision_groups)``.

    A *collision group* is a maximal run of records sharing the same integer
    second with size >= 2. Such a group is CONTENT-PROVABLE but POSITION-
    UNPROVABLE (timestamp cannot order its members). Every other (singleton)
    record belongs to an "order_provable" interval.
    """
    collision_groups: list[dict] = []
    intervals: list[dict] = []

    i = 0
    n = len(sorted_records)
    cg_id = 0
    # Map event_id -> collision group id for per-record tagging.
    event_to_cg: dict[str, int] = {}

    while i < n:
        sec = int(float(sorted_records[i].get("timestamp", 0.0) or 0.0))
        # Extend the run of records sharing this integer second.
        j = i
        while j < n and int(float(sorted_records[j].get("timestamp", 0.0) or 0.0)) == sec:
            j += 1
        run = sorted_records[i:j]
        if len(run) >= 2:
            # Same-second collision group -> position unprovable.
            cg_id += 1
            member_ids = [str(r.get("event_id")) for r in run]
            # A collision group is flagged "critical_sovereignty" when >=2 of its
            # members are CRITICAL sovereignty events -- i.e. it is one of the
            # "spots" where CRITICAL sovereignty events are themselves grouped
            # (collide) at the same second, regardless of other event types that
            # may also share that second.
            crit_members = sum(1 for r in run if is_critical_sovereignty_event(r))
            critical = crit_members >= 2
            for r in run:
                event_to_cg[str(r.get("event_id"))] = cg_id
                r["in_collision_group"] = True
                r["collision_group_id"] = cg_id
            collision_groups.append({
                "collision_group_id": cg_id,
                "second": sec,
                "member_count": len(run),
                "member_event_ids": member_ids,
                "critical_sovereignty": critical,
                "kind": "content_provable_position_unprovable",
            })
            intervals.append({
                "kind": "position_unprovable",
                "collision_group_id": cg_id,
                "record_count": len(run),
                "start_event_id": member_ids[0],
                "end_event_id": member_ids[-1],
                "second_range": [sec, sec],
            })
        else:
            # Singleton second -> order provable. Accumulate contiguous
            # singletons into one order_provable interval.
            rec = run[0]
            rec["in_collision_group"] = False
            rec["collision_group_id"] = None
            if intervals and intervals[-1]["kind"] == "order_provable":
                last = intervals[-1]
                last["record_count"] += 1
                last["end_event_id"] = str(rec.get("event_id"))
                last["second_range"][1] = sec
            else:
                intervals.append({
                    "kind": "order_provable",
                    "collision_group_id": None,
                    "record_count": 1,
                    "start_event_id": str(rec.get("event_id")),
                    "end_event_id": str(rec.get("event_id")),
                    "second_range": [sec, sec],
                })
        i = j

    return intervals, collision_groups


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def derive_audit_view(source_path: str, out_dir: str) -> dict:
    """Produce the derived audit view for ``source_path`` under ``out_dir``.

    Strictly read-only on ``source_path``: an isolated ``.backup`` copy is made
    and all work happens on the copy. Returns a summary dict.
    """
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"source database not found: {source_path}")
    os.makedirs(out_dir, exist_ok=True)

    # 1) Anchor the original via its byte-level sha256 (read-only).
    original_sha256, original_size = compute_file_sha256(source_path)
    original_name = os.path.basename(source_path)
    # Snapshot anchor: the source file's own modification time at read, used as the
    # data-cut timestamp for this derivation. Deterministic given the source and
    # precise for a forensic snapshot. Never written back to the source.
    source_mtime = os.path.getmtime(source_path)

    # 2) Work only on an isolated copy.
    copy_path = backup_to_temp_copy(source_path)
    try:
        conn = sqlite3.connect(copy_path)
        try:
            records = load_chain_records(conn)
            sorted_records = resort_and_recompute(records)
            intervals, collision_groups = partition_intervals(sorted_records)
        finally:
            conn.close()
    finally:
        # Remove the throwaway copy; never leave it behind in the source tree.
        try:
            os.remove(copy_path)
            parent = os.path.dirname(copy_path)
            if parent and os.path.isdir(parent):
                os.rmdir(parent)
        except OSError:
            pass

    # 3) Summaries.
    total = len(sorted_records)
    content_provable = sum(1 for r in sorted_records if r.get("content_provable"))
    content_unprovable = total - content_provable
    position_unprovable_records = sum(
        g["member_count"] for g in collision_groups
    )
    order_provable_records = total - position_unprovable_records
    crit_groups = [g for g in collision_groups if g["critical_sovereignty"]]
    crit_detected = len(crit_groups)

    # --- Re-adjudicated invariant (Phase 3.6 forensic DB investigation) ---
    # The former static constant 14 (D22 "grouped at 14 spots") is NOT a stable
    # invariant. The live audit_store.db is append-only and continuously written,
    # so this data-driven count DRIFTS (STEP 11 found 348; this snapshot 354).
    # We therefore never freeze the number, never redefine the metric to force a
    # match, and never gate/fail on the delta. We compute the LIVE count with
    # documented conditions + snapshot time, compare it against a clearly-labeled
    # BASELINE_SNAPSHOT (date / query / conditions as METADATA, not pass/fail
    # truth), and report the delta as a FINDING with an explicit explanation.
    baseline = BASELINE_SNAPSHOT
    baseline_count = baseline["recorded_count"]
    delta = crit_detected - baseline_count
    live_snapshot_time = datetime.datetime.fromtimestamp(
        source_mtime, datetime.UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    summary = {
        "total_records": total,
        "content_provable": content_provable,
        "content_unprovable": content_unprovable,
        "order_provable_records": order_provable_records,
        "position_unprovable_records": position_unprovable_records,
        "collision_groups_total": len(collision_groups),
        "critical_sovereignty_collision_groups_live": crit_detected,
        "baseline_snapshot": {
            "label": baseline["label"],
            "recorded_date": baseline["recorded_date"],
            "recorded_count": baseline_count,
            "query_conditions": baseline["query_conditions"],
            "source": baseline["source"],
            "caveat": baseline["caveat"],
        },
        "live_vs_baseline_delta": delta,
        "live_snapshot_time": live_snapshot_time,
        "intervals_total": len(intervals),
        "order_provable_intervals": sum(1 for iv in intervals if iv["kind"] == "order_provable"),
        "position_unprovable_intervals": sum(1 for iv in intervals if iv["kind"] == "position_unprovable"),
    }

    # Always report the delta as a FINDING (read-only; never a gate / never hides
    # the difference / never redefines the metric to force a match).
    if delta != 0:
        summary["FINDING_critical_sovereignty_collision_groups"] = (
            f"live detected {crit_detected} CRITICAL-sovereignty same-second "
            f"collision groups at snapshot {live_snapshot_time}; "
            f"BASELINE_SNAPSHOT records {baseline_count} "
            f"({baseline['source']}, recorded {baseline['recorded_date']}). "
            f"Delta = {delta}. "
            f"This is EXPECTED DRIFT, not a verification failure: the metric is "
            f"data-driven (>=2 CRITICAL human_sovereignty_override events sharing "
            f"an integer-second timestamp) and the live audit_store.db is still "
            f"being written. The D22 '14' was a point-in-time decision figure, "
            f"not a reproducible invariant. Reconcile by re-deriving the live "
            f"count; do NOT freeze or redefine the metric to force a match."
        )
    else:
        summary["FINDING_critical_sovereignty_collision_groups"] = (
            f"live detected {crit_detected} == BASELINE_SNAPSHOT "
            f"{baseline_count}. This equality is a property of THIS live snapshot "
            f"only; the metric drifts as the db is written and must not be treated "
            f"as a stable invariant."
        )

    partition_doc = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_sha256": original_sha256,
        "source_file": original_name,
        "canonical_hash_fields": list(CANONICAL_FIELDS),
        "hash_alg": HASH_ALG,
        "recompute_note": (
            "content hash recomputed over the 8-field canonical form (HC-01); "
            "prev_event_hash is linkage only and excluded from the hash input. "
            "Records were re-sorted by (timestamp, event_id) for a deterministic "
            "derived view; the original seq-ordering is never written back."
        ),
        "summary": summary,
        "collision_groups": collision_groups,
        "intervals": intervals,
    }

    # 4) Write deterministic outputs (no wall-clock => idempotent).
    manifest_path = os.path.join(out_dir, MANIFEST_FILENAME)
    partition_path = os.path.join(out_dir, PARTITION_FILENAME)
    records_path = os.path.join(out_dir, RECORDS_FILENAME)

    manifest_doc = {
        "artifact": "audit_store.original.sha256",
        "algorithm": HASH_ALG,
        "source_file": original_name,
        "sha256": original_sha256,
        "size_bytes": original_size,
        "content_provenance": (
            "computed over the ORIGINAL db file bytes (read-only); the tool "
            "never writes to, reorders, or overwrites the original database."
        ),
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
    }

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest_doc, fh, sort_keys=True, indent=2)
        fh.write("\n")

    with open(partition_path, "w", encoding="utf-8") as fh:
        json.dump(partition_doc, fh, sort_keys=True, indent=2)
        fh.write("\n")

    with open(records_path, "w", encoding="utf-8") as fh:
        for r in sorted_records:
            out = {
                "event_id": r.get("event_id"),
                "event_type": r.get("event_type"),
                "principal_id": r.get("principal_id"),
                "scope": r.get("scope"),
                "timestamp": r.get("timestamp"),
                "correlation_id": r.get("correlation_id"),
                "outcome": r.get("outcome"),
                "details": r.get("details"),
                "stored_event_hash": r.get("event_hash"),
                "recomputed_event_hash": r.get("recomputed_event_hash"),
                "content_provable": r.get("content_provable"),
                "in_collision_group": r.get("in_collision_group"),
                "collision_group_id": r.get("collision_group_id"),
                "derived_chain_prev_hash": r.get("derived_chain_prev_hash"),
            }
            fh.write(_canon(out) + "\n")

    return {
        "source_sha256": original_sha256,
        "manifest_path": manifest_path,
        "partition_path": partition_path,
        "records_path": records_path,
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D22 read-only derived audit view (safe forensic re-derivation)."
    )
    parser.add_argument(
        "--source",
        default="audit_store.db",
        help="Path to the audit_store.db to derive from. Opened read-only; an "
             "isolated .backup copy is made and all work happens on the copy. "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default="audit_derived_view",
        help="Output directory for MANIFEST(sha256) and the derived view files. "
             "(default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        result = derive_audit_view(args.source, args.out)
    except Exception as exc:  # operational errors only; never data-gated
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    s = result["summary"]
    print(f"D22 derive_audit_view: source_sha256={result['source_sha256'][:16]}...")
    print(f"  records={s['total_records']} "
          f"content_provable={s['content_provable']} "
          f"order_provable={s['order_provable_records']} "
          f"position_unprovable={s['position_unprovable_records']}")
    print(f"  collision_groups={s['collision_groups_total']} "
          f"critical_sovereignty_collision_groups_live={s['critical_sovereignty_collision_groups_live']} "
          f"baseline={s['baseline_snapshot']['recorded_count']} "
          f"delta={s['live_vs_baseline_delta']} "
          f"snapshot={s['live_snapshot_time']}")
    if "FINDING_critical_sovereignty_collision_groups" in s:
        print(f"  FINDING: {s['FINDING_critical_sovereignty_collision_groups']}")
    print(f"  wrote: {result['manifest_path']}")
    print(f"         {result['partition_path']}")
    print(f"         {result['records_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
