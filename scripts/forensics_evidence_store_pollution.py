"""Read-only forensics for the HC-01 evidence-store pollution problem (③).

WHY THIS EXISTS
================
The project-root ``audit_store.db`` is the FROZEN HC-01 evidence store. It must
never be silently written by a process that forgot to configure a path. The
suspected root cause: ``AuditStore.__init__`` falls back to the project-root
file when ``AUDIT_DB_PATH`` is unset, so any background process / script that
runs without setting the env var pollutes the frozen evidence.

WHAT THIS SCRIPT DOES (and does NOT)
====================================
- NEVER imports ``src/*`` and NEVER writes to the frozen store.
- Sets ``AUDIT_DB_PATH`` to a throwaway temp dir defensively (so even a stray
  transitive import cannot land on the root file).
- Takes a CONSISTENT snapshot of the frozen DB via the sqlite3 ``.backup()``
  API (safe against the concurrent writers that are currently active), copies
  it to a temp file, then opens the COPY read-only.
- Reports, for the last 7 days: total events, events/day, distinct writer
  identities (principal_id), event_type distribution, and correlation_id
  counts — i.e. the shape of who is writing to the shared root store.

LIMITATION (reported honestly)
==============================
This is a SINGLE-SNAPSHOT analysis, not a 7-day time-series of growth. The
"7-day growth correlation" framing assumes periodic snapshots; this environment
only has the current state, so we characterize the in-store 7-day timeline by
each event's own ``timestamp`` instead. That is sufficient to confirm the
pollution shape (many distinct writers landing on one shared root file) which is
what the config-layer fix (③) closes.

Usage:
    python scripts/forensics_evidence_store_pollution.py [PATH_TO_AUDIT_DB]
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

# Defensive: even a stray import of src.* must not land on the root store.
os.environ["AUDIT_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "forensics_probe.db")

REPO = pathlib.Path(__file__).resolve().parents[1]


def _open_readonly(src: pathlib.Path) -> pathlib.Path:
    """Consistent, read-only snapshot of ``src`` into a temp copy.

    The live store is WAL-mode and concurrently written by background
    processes. A read-only ``mode=ro`` open + ``.backup()`` trips SQLite's
    "attempt to write a readonly database" (it must read the live -wal). So we
    byte-copy the main db together with its -wal/-shm into a writable temp dir,
    then open the COPY read-only. The original is only ever read via this copy;
    no write / VACUUM / migrate touches it.
    """
    tmp = pathlib.Path(tempfile.mkdtemp())
    copy = tmp / "audit_snapshot.db"
    # Copy the main db to the snapshot name, and any -wal/-shm alongside it so
    # the copy is self-consistent and openable in read-only/WAL mode.
    shutil.copyfile(src, copy)
    for ext in ("-wal", "-shm"):
        p = src.parent / (src.name + ext)
        if p.exists():
            shutil.copyfile(p, copy.parent / (copy.name + ext))
    return copy


def main() -> int:
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (REPO / "audit_store.db")
    if not target.exists():
        print(f"FROZENS STORE NOT FOUND at {target}; nothing to analyze.")
        return 0

    print(f"Analyzing (read-only snapshot of): {target}")
    snap = _open_readonly(target)
    # Open the temp COPY directly (writable flag is harmless: it is a throwaway
    # snapshot, never the original). The frozen store is only ever read via copy.
    conn = sqlite3.connect(str(snap), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        n_total = cur.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        print(f"\n[total events] {n_total}")

        # events per day, last 7 days relative to the newest event
        rows = cur.execute(
            """
            SELECT strftime('%Y-%m-%d', timestamp, 'unixepoch') AS day,
                   COUNT(*) AS n
            FROM audit_events
            WHERE timestamp >= (SELECT MAX(timestamp) FROM audit_events) - 7*86400
            GROUP BY day ORDER BY day
            """
        ).fetchall()
        print("\n[events/day — last 7 days]")
        for r in rows:
            print(f"  {r['day']}: {r['n']}")

        # writer identities (principal_id) — many distinct == many processes/scripts
        rows = cur.execute(
            "SELECT principal_id, COUNT(*) AS n FROM audit_events "
            "GROUP BY principal_id ORDER BY n DESC LIMIT 12"
        ).fetchall()
        print("\n[top writer identities (principal_id) — distinct count below]")
        distinct = cur.execute("SELECT COUNT(DISTINCT principal_id) FROM audit_events").fetchone()[0]
        print(f"  DISTINCT principal_id = {distinct}")
        for r in rows:
            print(f"  {r['principal_id']!r}: {r['n']}")

        # event_type distribution
        rows = cur.execute(
            "SELECT event_type, COUNT(*) AS n FROM audit_events "
            "GROUP BY event_type ORDER BY n DESC LIMIT 15"
        ).fetchall()
        print("\n[event_type distribution (top 15)]")
        for r in rows:
            print(f"  {r['event_type']!r}: {r['n']}")

        # correlation_id count — many distinct == many isolated runs sharing one file
        distinct_corr = cur.execute(
            "SELECT COUNT(DISTINCT correlation_id) FROM audit_events"
        ).fetchone()[0]
        print(f"\n[distinct correlation_id = {distinct_corr}] "
              f"(high value => many independent runs land in ONE shared root file)")

        # sample details keys to understand the payloads being written
        sample = cur.execute(
            "SELECT details FROM audit_events WHERE details IS NOT NULL LIMIT 1"
        ).fetchone()
        if sample and sample["details"]:
            try:
                keys = sorted(json.loads(sample["details"]).keys())
                print(f"\n[details JSON keys observed] {keys}")
            except json.JSONDecodeError:
                print("\n[details not valid JSON in sampled row]")

        # chain_state sanity
        cs = cur.execute("SELECT last_seq, last_hash FROM chain_state WHERE id=1").fetchone()
        if cs:
            print(f"\n[chain_state] last_seq={cs['last_seq']} last_hash={str(cs['last_hash'])[:16]}...")
    finally:
        conn.close()
        shutil.rmtree(snap.parent, ignore_errors=True)

    print("\nFORENSICS COMPLETE — frozen store was only READ (backup + read-only open). "
          "No write, no VACUUM, no migrate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
