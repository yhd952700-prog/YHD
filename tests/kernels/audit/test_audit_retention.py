"""Audit retention: bounded storage that stays verifiable.

Covers the long-run defects measured in the 2-hour soak:
- the audit DB grew 21.5 MB/h with zero reclaim;
- /v1/audit/verify re-derived the whole chain, so p50 grew with history.

The policy deletes the OLDEST prefix by age / rows / bytes. Because the hash
chain links every event to its predecessor, deletion alone would make seq P+1
look like a broken chain. ``retention_state`` records the floor so verification
can anchor at P+1 and report the honest claim: the RETAINED suffix is intact,
while ``rooted_at_genesis`` is False.
"""

from __future__ import annotations

from src.kernels.audit import AuditEventType, AuditScope, AuditStore
from src.kernels.audit import verification as _verification


def _store(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_ENABLED", "1")
    # Small, deterministic policy so a test can reach the floor quickly.
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_DAYS", "0")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_ROWS", "50")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_BYTES", "0")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MIN_KEEP", "10")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_DELETE", "50")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return AuditStore(str(tmp_path / "audit.db"))


def _log(store: AuditStore, count: int, start: int = 0) -> None:
    for i in range(start, start + count):
        store.log_event(
            AuditEventType.POLICY_EVAL, "user-1", AuditScope.L0, "allow",
            {"i": i},
        )


def test_prefix_prune_keeps_retained_suffix_verifiable(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    _log(store, 150)

    # force bounded passes until the row ceiling is reached
    for _ in range(4):
        store._prune_once()

    floor, floor_hash, floor_link = store._retention_state(store._conn)
    assert floor > 0
    assert floor_hash and floor_link
    rows = store._conn.execute(
        "SELECT COUNT(*), MIN(seq), MAX(seq) FROM audit_events").fetchone()
    assert rows[0] == 50
    assert rows[1] == floor + 1
    assert rows[2] == 150

    ok, checked = store.verify_integrity()
    assert ok is True
    assert checked == 50

    inc = store.verify_incremental()
    assert inc["segment_verified"] is True
    assert inc["anchor_source"] == "retention"
    assert inc["from_seq"] == floor + 1
    assert inc["retention_floor_seq"] == floor
    # Honest split: the pruned prefix is not claimed as genesis-rooted.
    assert inc["rooted_at_genesis"] is False
    assert inc["rooted_at_retention_floor"] is True

    assert store.audit_checkpoints() == (True, [])

    coverage = store.verification_coverage()
    assert coverage["retention_floor_seq"] == floor
    assert coverage["covered_through"] == 150
    assert coverage["rooted_at_genesis"] is False
    assert coverage["rooted_at_retention_floor"] is True

    # Appending after a prune must keep verifying from the new floor, never
    # from a fabricated seq 1.
    _log(store, 20, start=150)
    ok, _ = store.verify_integrity()
    assert ok is True
    assert store.verify_incremental()["segment_verified"] is True


def test_prune_requires_cut_row_anchor_and_respects_min_keep(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, LIUHAO_AUDIT_RETENTION_MIN_KEEP="30")
    _log(store, 80)
    store._prune_once()
    floor, _, _ = store._retention_state(store._conn)
    # 80 retained rows, ceiling 50, but the newest 30 must survive.
    assert floor <= 80 - 30
    tail = store._conn.execute(
        "SELECT MAX(seq) FROM audit_events").fetchone()[0]
    assert tail - floor >= 30


def test_tampering_with_retained_event_is_detected(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    _log(store, 120)
    store._prune_once()
    floor, _, _ = store._retention_state(store._conn)
    target = floor + 1
    store._conn.execute(
        "UPDATE audit_events SET details = ? WHERE seq = ?",
        ('{"i": 999}', target))
    store._conn.commit()
    ok, _ = store.verify_integrity()
    assert ok is False


def test_default_policy_does_not_prune_small_history(tmp_path, monkeypatch):
    for name in (
        "LIUHAO_AUDIT_RETENTION_ENABLED",
        "LIUHAO_AUDIT_RETENTION_DAYS",
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS",
        "LIUHAO_AUDIT_RETENTION_MAX_BYTES",
        "LIUHAO_AUDIT_RETENTION_MIN_KEEP",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE",
    ):
        monkeypatch.delenv(name, raising=False)
    store = AuditStore(str(tmp_path / "audit.db"))
    _log(store, 40)
    store._maybe_prune()
    floor, _, _ = store._retention_state(store._conn)
    assert floor == 0
    ok, checked = store.verify_integrity()
    assert ok is True
    assert checked == 40


def test_verify_segment_still_checks_link_chain_after_prune(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    _log(store, 100)
    store._prune_once()
    floor, _, floor_link = store._retention_state(store._conn)
    result = _verification.verify_segment(
        store._conn, floor + 1, 100, floor_link,
        anchor_source=_verification.ANCHOR_RETENTION)
    assert result.verified is True
