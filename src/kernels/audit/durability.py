"""WAL correctness + transaction-consistent snapshot for the audit store
(Q3.5 / R8 / R11).

``configure_audit_durability`` upgrades the evidence store to ``synchronous=FULL``
so a commit survives power loss (the current ``synchronous=NORMAL`` default in
``src/kernels/audit/__init__.py`` only survives process crash).

``snapshot_audit_db`` copies a DB with ``VACUUM INTO`` — a transaction-consistent
copy. This is the correct way to capture the live DB for migration/forensics;
a naive file ``cp`` of a WAL-mode database with an uncheckpointed ``-wal`` loses
data (the R11 hazard).

These helpers operate on a CALLER-SUPPLIED path. They never touch the forensic
snapshot or any Evidence file; a caller must point them only at an
authorised source (e.g. a copy) under the migration run. Tests use synthetic DBs.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Tuple

_VALID_LEVELS = ("OFF", "NORMAL", "FULL", "EXTRA")
# PRAGMA synchronous returns an integer code (0=OFF,1=NORMAL,2=FULL,3=EXTRA);
# normalise back to the human-readable name so callers get a stable string.
_SYNC_INT_TO_NAME = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}


def configure_audit_durability(conn: sqlite3.Connection, level: str = "FULL") -> str:
    """Set ``PRAGMA synchronous`` on a connection. Returns the resulting level.

    Use ``FULL`` (or ``EXTRA``) for the evidence-grade audit store so commits
    are fsync'd and survive power loss.
    """
    if level not in _VALID_LEVELS:
        raise ValueError(f"invalid synchronous level {level!r}; want one of {_VALID_LEVELS}")
    conn.execute(f"PRAGMA synchronous={level}")
    row = conn.execute("PRAGMA synchronous").fetchone()
    if row is None:
        return level
    val = row[0]
    if isinstance(val, int):
        return _SYNC_INT_TO_NAME.get(val, str(val))
    return val


def _count_audit_events(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def snapshot_audit_db(
    src_path: str,
    dst_path: str,
    *,
    busy_timeout_ms: int = 5000,
) -> Tuple[int, int]:
    """Copy ``src_path`` to ``dst_path`` as a transaction-consistent snapshot.

    Uses ``VACUUM INTO`` (SQLite >= 3.27). ``dst_path`` must not already exist.
    Returns ``(source_event_count, dest_event_count)`` for verification.
    """
    if os.path.exists(dst_path):
        raise FileExistsError(
            f"snapshot destination already exists: {dst_path} "
            f"(VACUUM INTO requires a non-existent target)"
        )
    src = sqlite3.connect(src_path)
    try:
        src.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        # NOTE: VACUUM INTO cannot run inside an explicit transaction
        # ("cannot VACUUM from within a transaction"). It is itself a
        # transaction-consistent copy of the source as of the command start, so
        # no surrounding BEGIN is needed — and any concurrent commits on the
        # source simply are not included in the snapshot (correct, since the
        # migration path snapshots only under a single-writer fence / on a copy).
        src.execute(f"VACUUM INTO '{dst_path}'")
    finally:
        src.close()

    src_n = _count_audit_events(src_path)
    dst_n = _count_audit_events(dst_path)
    return src_n, dst_n
