"""D22 — tests for the read-only derived audit view.

Builds a SYNTHETIC in-memory SQLite database (never the real deployed
``audit_store.db``) and asserts:

  * the re-sort / recompute / partition logic flags exactly the collision groups
    we planted (14 same-second CRITICAL sovereignty groups), marking them
    "content_provable_position_unprovable";
  * the tool is idempotent: running it twice on the same source produces
    byte-identical output;
  * the tool never writes back to (or reorders) the original — it only ever works
    on an isolated ``.backup`` copy.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "derive_audit_view.py"

_spec = importlib.util.spec_from_file_location("derive_audit_view_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # required before exec_module so decorators resolve
_spec.loader.exec_module(mod)

N_COLLISION_GROUPS = 14
N_SINGLETONS = 11


def _build_schema_and_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE audit_events (
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
            created_at REAL NOT NULL,
            seq INTEGER NOT NULL,
            hash_alg TEXT NOT NULL DEFAULT 'sha256'
        )"""
    )

    rows = []

    # 14 same-second collision groups, each with 2 CRITICAL sovereignty events.
    for g in range(N_COLLISION_GROUPS):
        sec = 1000 + g
        for k in range(2):
            rec = {
                "event_id": f"sov-{g}-{k}",
                "event_type": "human_sovereignty_override",
                "principal_id": f"p-{g}-{k}",
                "scope": "L0",
                "timestamp": float(sec) + 0.1 * k,  # same integer second
                "correlation_id": f"corr-{g}-{k}",
                "outcome": "ok",
                "details": json.dumps({"risk_levels": {"capability.retire": "CRITICAL"}}),
                "prev_event_hash": None,
                "created_at": float(sec),
                "seq": 1000 + g * 2 + k,
                "hash_alg": "sha256",
            }
            rec["event_hash"] = mod.canonical_content_hash(rec)
            rows.append(rec)

    # 11 non-colliding singleton events at distinct seconds (order-provable).
    for s in range(2000, 2000 + N_SINGLETONS):
        rec = {
            "event_id": f"state-{s}",
            "event_type": "state_change",
            "principal_id": "sys",
            "scope": "L1",
            "timestamp": float(s) + 0.5,
            "correlation_id": f"corr-state-{s}",
            "outcome": "ok",
            "details": None,
            "prev_event_hash": None,
            "created_at": float(s),
            "seq": 2000 + s,
            "hash_alg": "sha256",
        }
        rec["event_hash"] = mod.canonical_content_hash(rec)
        rows.append(rec)

    conn.executemany(
        "INSERT INTO audit_events "
        "(event_id,event_type,principal_id,scope,timestamp,correlation_id,"
        "outcome,details,event_hash,prev_event_hash,created_at,seq,hash_alg) "
        "VALUES (:event_id,:event_type,:principal_id,:scope,:timestamp,"
        ":correlation_id,:outcome,:details,:event_hash,:prev_event_hash,"
        ":created_at,:seq,:hash_alg)",
        rows,
    )
    conn.commit()


def _make_synthetic_source(tmp_path: Path) -> Path:
    """Build the synthetic db IN MEMORY, then back it up to a temp file so the
    test exercises the same ``.backup`` copy path the real tool uses."""
    mem = sqlite3.connect(":memory:")
    _build_schema_and_rows(mem)
    src_file = tmp_path / "synthetic_audit_store.db"
    fc = sqlite3.connect(str(src_file))
    mem.backup(fc)
    fc.close()
    mem.close()
    return src_file


def test_canonical_content_hash_matches_stored() -> None:
    """The canonical hash the tool recomputes must equal what a writer stores.

    The authoritative write path parses ``details`` (a JSON TEXT column) back
    into a dict before canonicalization; the tool must do the same.
    """
    rec = {
        "event_id": "x1",
        "event_type": "state_change",
        "principal_id": "p",
        "scope": "L0",
        "timestamp": 1234.5,
        "correlation_id": "c",
        "outcome": "ok",
        "details": '{"a":1}',
    }
    expected_payload = {k: rec[k] for k in mod.CANONICAL_FIELDS}
    expected_payload["details"] = json.loads(expected_payload["details"])
    assert mod.canonical_content_hash(rec) == mod.sha256_hex(
        json.dumps(
            expected_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def test_fourteen_collision_groups_flagged(tmp_path: Path) -> None:
    src = _make_synthetic_source(tmp_path)
    out = tmp_path / "out"
    result = mod.derive_audit_view(str(src), str(out))

    s = result["summary"]
    # 14 planted collision groups, all of them CRITICAL sovereignty.
    assert s["collision_groups_total"] == N_COLLISION_GROUPS
    assert s["critical_sovereignty_collision_groups_detected"] == N_COLLISION_GROUPS
    # The synthetic data matches the LOCKED decision's known figure of 14.
    assert s["critical_sovereignty_collision_groups_known"] == N_COLLISION_GROUPS
    assert s["critical_sovereignty_collision_groups_match"] is True

    # Position-unprovable records = 14 groups * 2 members.
    assert s["position_unprovable_records"] == N_COLLISION_GROUPS * 2
    # Order-provable records = 11 singletons.
    assert s["order_provable_records"] == N_SINGLETONS

    # Every collision group is marked content-provable but position-unprovable.
    partition_path = out / mod.PARTITION_FILENAME
    doc = json.loads(partition_path.read_text(encoding="utf-8"))
    assert len(doc["collision_groups"]) == N_COLLISION_GROUPS
    for g in doc["collision_groups"]:
        assert g["kind"] == "content_provable_position_unprovable"
        assert g["critical_sovereignty"] is True
        assert g["member_count"] == 2
    # And the matching intervals are all position_unprovable.
    pos_unprov = [iv for iv in doc["intervals"] if iv["kind"] == "position_unprovable"]
    assert len(pos_unprov) == N_COLLISION_GROUPS


def test_idempotent_output(tmp_path: Path) -> None:
    """Re-running on the same source yields byte-identical output files."""
    src = _make_synthetic_source(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    mod.derive_audit_view(str(src), str(out1))
    mod.derive_audit_view(str(src), str(out2))

    for name in (mod.MANIFEST_FILENAME, mod.PARTITION_FILENAME, mod.RECORDS_FILENAME):
        b1 = (out1 / name).read_bytes()
        b2 = (out2 / name).read_bytes()
        assert b1 == b2, f"output not idempotent: {name}"


def test_original_never_written(tmp_path: Path) -> None:
    """The source file is read-only for this tool: its bytes are unchanged and
    no write-back / VACUUM ever touches it."""
    import hashlib

    src = _make_synthetic_source(tmp_path)
    before = hashlib.sha256(src.read_bytes()).hexdigest()

    out = tmp_path / "out"
    result = mod.derive_audit_view(str(src), str(out))

    after = hashlib.sha256(src.read_bytes()).hexdigest()
    assert before == after, "source database bytes changed — must never happen"
    # The derived sha256 in the manifest must equal the original's sha256.
    manifest_path = out / mod.MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["sha256"] == before == result["source_sha256"]


def test_collision_group_sorting_is_deterministic(tmp_path: Path) -> None:
    """Same-second ties are broken deterministically by event_id, so re-sorts
    are reproducible regardless of physical row order."""
    src = _make_synthetic_source(tmp_path)
    out = tmp_path / "out"
    mod.derive_audit_view(str(src), str(out))
    doc = json.loads((out / mod.PARTITION_FILENAME).read_text(encoding="utf-8"))
    # Every position_unprovable interval's members are sorted by event_id.
    for g in doc["collision_groups"]:
        ids = g["member_event_ids"]
        assert ids == sorted(ids), "collision group members not deterministically ordered"
