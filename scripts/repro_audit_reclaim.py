"""Reproduce the audit-DB "delete rows but never return disk" defect and prove
the fix reclaims space.

Defect (measured): two rounds showed ``PRAGMA auto_vacuum == 0``; after deleting
12,115 rows the file kept 12,011 rows' worth of pages plus the 109 freed pages
in the freelist, the WAL was never truncated, and the DB grew 2.10 -> 9.26 MB in
22 minutes (+21.5 MB/h). The default retention thresholds (30 days / 500k rows /
256 MB) do not fire in a short window, so an installed desktop build grows
forever.

Root cause found while fixing: ``PRAGMA journal_mode=WAL`` was executed BEFORE
``PRAGMA auto_vacuum=INCREMENTAL``. WAL materialises the DB header with
auto_vacuum=NONE, so the later pragma was a no-op -- even on a brand-new file.

This script asserts, with real byte counts:
  1. a FRESH store reports auto_vacuum=INCREMENTAL (2);
  2. after pruning ~10k rows the file actually SHRINKS and the WAL is 0 bytes;
  3. a LEGACY store (created with auto_vacuum=NONE) is migrated by one VACUUM
     on the maintenance path, then reclaims space on prune.

Run:
    .venv\\Scripts\\python.exe scripts\\repro_audit_reclaim.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.kernels.audit import (  # noqa: E402
    AuditEvent,
    AuditEventType,
    AuditScope,
    AuditStore,
)

TOTAL_ROWS = 12_000
PRUNE_TARGET = 2_000


def _configure_retention() -> None:
    os.environ["LIUHAO_AUDIT_RETENTION_ENABLED"] = "1"
    os.environ["LIUHAO_AUDIT_RETENTION_DAYS"] = "0"  # disable age-based cut
    os.environ["LIUHAO_AUDIT_RETENTION_MAX_ROWS"] = str(PRUNE_TARGET)
    os.environ["LIUHAO_AUDIT_RETENTION_MAX_BYTES"] = "0"
    os.environ["LIUHAO_AUDIT_RETENTION_MIN_KEEP"] = "1"
    os.environ["LIUHAO_AUDIT_RETENTION_MAX_DELETE"] = "5000"
    # Large interval keeps the append path from shrinking the store mid-fill,
    # so the measured "before prune" really holds all appended rows.
    os.environ["LIUHAO_AUDIT_RETENTION_INTERVAL_SEC"] = "3600"


_OPEN_STORES: list = []


def _sizes(db_path: str) -> dict:
    result = {}
    for suffix in ("", "-wal", "-shm"):
        try:
            result[suffix or "db"] = os.path.getsize(db_path + suffix)
        except OSError:
            result[suffix or "db"] = 0
    return result


def _append(store: AuditStore, count: int, start: int = 0) -> None:
    """Append ``count`` events in batches with a ~220-byte payload each."""
    pad = "x" * 200
    batch = []
    for i in range(start, start + count):
        batch.append(AuditEvent(
            event_id=uuid.uuid4().hex,
            event_type=AuditEventType.POLICY_EVAL,
            principal_id="reclaim-probe",
            scope=AuditScope.L0,
            timestamp=time.time(),
            correlation_id="",
            outcome="allow",
            details={"i": i, "pad": pad},
        ))
        if len(batch) == 500:
            store.log_event_batch(batch)
            batch = []
    if batch:
        store.log_event_batch(batch)


def _prune_to_target(store: AuditStore, max_passes: int = 6) -> int:
    """Run bounded prune passes until the row ceiling is reached."""
    passes = 0
    for _ in range(max_passes):
        rows = int(store._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        if rows <= PRUNE_TARGET:
            break
        store._prune_once()
        passes += 1
    return passes


def _rows(store: AuditStore) -> int:
    return int(store._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])


def _auto_vacuum(store: AuditStore) -> int:
    return int(store._conn.execute("PRAGMA auto_vacuum").fetchone()[0])


def _freelist(store: AuditStore) -> int:
    return int(store._conn.execute("PRAGMA freelist_count").fetchone()[0])


def fresh_store_check(tmp: str) -> bool:
    db_path = os.path.join(tmp, "fresh.db")
    store = AuditStore(db_path)
    _OPEN_STORES.append(store)
    mode = _auto_vacuum(store)
    print(f"[audit] fresh DB auto_vacuum={mode} (expect 2/INCREMENTAL)")
    if mode != 2:
        print("[audit] FAIL: fresh DB is not incremental auto-vacuum")
        return False

    _append(store, TOTAL_ROWS)
    before = _sizes(db_path)
    before_rows = _rows(store)
    before_free = _freelist(store)
    print(f"[audit] before prune: rows={before_rows} "
          f"db={before['db']} wal={before['-wal']} total={sum(before.values())} "
          f"freelist={before_free}")

    passes = _prune_to_target(store)
    after = _sizes(db_path)
    after_rows = _rows(store)
    print(f"[audit] prune passes={passes}: rows {before_rows} -> {after_rows}, "
          f"db {before['db']} -> {after['db']}, "
          f"wal {before['-wal']} -> {after['-wal']}, "
          f"total {sum(before.values())} -> {sum(after.values())}, "
          f"freelist={_freelist(store)}")

    if before_rows - after_rows < 10_000:
        print(f"[audit] FAIL: pruned only {before_rows - after_rows} rows")
        return False
    if after["-wal"] != 0:
        print("[audit] FAIL: WAL was not truncated to 0 bytes")
        return False
    if after["db"] >= before["db"]:
        print("[audit] FAIL: database file did not shrink")
        return False
    if _freelist(store) != 0:
        print("[audit] FAIL: freed pages remain in the freelist")
        return False
    print("[audit] PASS: fresh store reclaimed disk and truncated the WAL")
    return True


def legacy_store_check(tmp: str) -> bool:
    db_path = os.path.join(tmp, "legacy.db")
    # Simulate a database produced by the old "WAL first" open order: header
    # frozen at auto_vacuum=NONE, rows already present.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE legacy_probe (x INTEGER)")
    conn.execute("INSERT INTO legacy_probe VALUES (1)")
    conn.commit()
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    conn.close()

    store = AuditStore(db_path)
    _OPEN_STORES.append(store)
    print(f"[audit] legacy DB on open: auto_vacuum={_auto_vacuum(store)} "
          f"migration_pending={store._auto_vacuum_pending}")
    if not store._auto_vacuum_pending:
        print("[audit] FAIL: legacy DB was not detected as needing migration")
        return False

    _append(store, TOTAL_ROWS)
    before = _sizes(db_path)
    before_rows = _rows(store)
    print(f"[audit] legacy before prune: rows={before_rows} "
          f"db={before['db']} wal={before['-wal']} total={sum(before.values())}")

    # The one-time VACUUM runs from the prune path (where pages are actually
    # freed), not from the open path or a plain append.
    passes = _prune_to_target(store)
    after = _sizes(db_path)
    after_rows = _rows(store)
    migrated = _auto_vacuum(store)
    print(f"[audit] legacy after prune: auto_vacuum={migrated} "
          f"migration_pending={store._auto_vacuum_pending}, passes={passes}, "
          f"rows {before_rows} -> {after_rows}, "
          f"db {before['db']} -> {after['db']}, "
          f"wal {before['-wal']} -> {after['-wal']}, "
          f"total {sum(before.values())} -> {sum(after.values())}, "
          f"freelist={_freelist(store)}")
    if migrated != 2 or store._auto_vacuum_pending:
        print("[audit] FAIL: legacy VACUUM migration did not take effect")
        return False

    if after["-wal"] != 0:
        print("[audit] FAIL: WAL was not truncated left after legacy migration")
        return False
    if after["db"] >= before["db"]:
        print("[audit] FAIL: legacy DB did not shrink after migration + prune")
        return False
    print("[audit] PASS: legacy store migrated to incremental and reclaimed disk")
    return True


def main() -> int:
    _configure_retention()
    ok_fresh = ok_legacy = False
    with tempfile.TemporaryDirectory(prefix="liuhao-audit-reclaim-") as tmp:
        try:
            ok_fresh = fresh_store_check(tmp)
            ok_legacy = legacy_store_check(tmp)
        finally:
            # Windows keeps the sqlite files locked while the store is open;
            # close every handle before the temporary directory is removed.
            for store in _OPEN_STORES:
                try:
                    store._conn.close()
                except Exception:
                    pass
    if ok_fresh and ok_legacy:
        print("[audit] PASS: auto_vacuum=INCREMENTAL + incremental_vacuum + "
              "WAL checkpoint(TRUNCATE) reclaim both new and legacy stores")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
