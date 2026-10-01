"""#109 regression — the ``_acquire_within_pid`` None-branch behaviour.

This closes the gap g6-reliability-scan flagged (finding #2) and mirrors the
scope lease-epoch confirmed against ADR-audit-writer-lease-identity §4.4b:

  (a) liveness-unknown => refuse (StaleWriterError). When ``_is_process_alive``
      cannot determine liveness it returns ``None``, and ``_acquire_within_pid``
      MUST refuse a takeover -- "cannot PROVE staleness -> refuse" is the ADR §4
      deliberate fail-closed availability cost, NOT a bug.
  (b) refusal is BOUNDED by the lease TTL. A takeover is refused ONLY while the
      competing lease is still live; once it expires, the writer takes over
      regardless of owner parseability. So a ``None`` liveness probe (or an
      unparseable owner) is never a permanent deadlock.
  (c) DB invariant: the owner string the store writes is always ``None`` or
      ``_owner_pid``-parseable to a positive int (``audit-store:<pid>``). uuid
      mode does not change the owner column, so this invariant holds in both
      modes. A regression that wrote any other format would fall into the
      bounded-expiry path rather than being silently accepted.

The tests drive ``SqliteWriterLease`` directly on a throwaway sqlite file -- no
AuditStore, no live audit DB, no transaction machinery.
"""
import os
import sqlite3
import time

import pytest

from src.kernels.audit import fencing


def _make_lease(tmp_path):
    """A standalone SqliteWriterLease on a throwaway DB (no AuditStore)."""
    conn = sqlite3.connect(str(tmp_path / "lease.db"))
    return fencing.SqliteWriterLease(conn), conn


def _plant_row(conn, owner, expires_at, token=1):
    conn.execute(
        "INSERT INTO writer_lease (id, token, owner, acquired_at, expires_at) "
        "VALUES (1, ?, ?, ?, ?)",
        (token, owner, time.time(), expires_at),
    )
    conn.commit()


def test_liveness_unknown_refuses_takeover(monkeypatch, tmp_path):
    """(a) ``_is_process_alive`` -> None MUST raise StaleWriterError (ADR §4).

    This pins the fail-closed verdict so a future "fix" cannot downgrade an
    unknown-liveness refusal into a silent takeover -- which would defeat the
    fence entirely.
    """
    lease, conn = _make_lease(tmp_path)
    _plant_row(conn, "audit-store:999999", time.time() + 30.0)
    monkeypatch.setattr(fencing, "_is_process_alive", lambda pid: None)
    with pytest.raises(fencing.StaleWriterError):
        lease._acquire_within_pid("audit-store:1", ttl_sec=30.0)
    conn.close()


def test_refusal_is_bounded_by_ttl(monkeypatch, tmp_path):
    """(b) expired + unparseable owner is taken over -- refusal != deadlock.

    The competing lease is already expired, so the writer may take over even
    though the owner is malformed and liveness is unknown. This is the property
    that prevents the broad ``if alive is not False: raise`` from becoming a
    permanent evidence-channel lockout.
    """
    lease, conn = _make_lease(tmp_path)
    _plant_row(conn, "garbage-owner", time.time() - 1.0)  # expired, unparseable
    monkeypatch.setattr(fencing, "_is_process_alive", lambda pid: None)
    tok = lease._acquire_within_pid("audit-store:1", ttl_sec=30.0)
    assert isinstance(tok, int) and tok >= 1  # takeover succeeded, no raise
    conn.close()


def test_owner_format_invariant_is_parseable():
    """(c) the store-written owner format round-trips through ``_owner_pid``.

    The store's only owner source is ``AuditStore._lease_owner`` ->
    ``audit-store:<pid>``. Asserting that round-trips to a positive int pins the
    invariant; and that malformed formats parse to ``None`` (so a future
    regression that wrote one would be refused/bounded, never silently allowed).
    """
    owner = "audit-store:%d" % os.getpid()
    assert fencing._owner_pid(owner) == os.getpid()
    # malformed owners must parse to None -- the bounded-fallback, not a live one
    assert fencing._owner_pid("writer_id:boot_gen:epoch") is None
    assert fencing._owner_pid("no-colon") is None
    assert fencing._owner_pid("") is None
