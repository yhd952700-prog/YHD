"""D22 — tests for the read-only derived audit view.

Builds a SYNTHETIC in-memory SQLite database (never the real deployed
``audit_store.db``) and asserts:

  * the re-sort / recompute / partition logic flags exactly the collision groups
    we planted (14 same-second CRITICAL sovereignty groups), marking them
    "content_provable_position_unprovable";
  * the tool is idempotent: running it twice on the same source produces
    byte-identical output;
  * the tool never writes back to (or reorders) the original — it only ever works
    on an isolated ``.backup`` copy;
  * the ``--allow-stale`` fail-closed contract (team-lead ruling A): a
    pre-existing ``-wal``/``-shm`` sidecar makes the tool refuse by default,
    proceeds only with the explicit flag, marks STALE in EVERY output document
    in that mode, and leaves the ordinary (no-sidecar) path unperturbed.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

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
    # Functional assertion on CONTROLLED synthetic data: the tool must partition
    # exactly the 14 same-second groups we planted (it never sees the live db).
    assert s["collision_groups_total"] == N_COLLISION_GROUPS
    # On this controlled data every planted group has >=2 CRITICAL sovereignty
    # members, so the LIVE critical-sovereignty count equals the planted count.
    assert s["critical_sovereignty_collision_groups_live"] == N_COLLISION_GROUPS
    # Re-adjudicated (D22): the tool must NOT gate on a frozen "known" figure or
    # report a pass/fail "match". It reports the count as a read-only FINDING with
    # the live-vs-baseline delta, never as a pass/fail truth.
    assert "critical_sovereignty_collision_groups_match" not in s
    assert "critical_sovereignty_collision_groups_known" not in s
    assert "FINDING_critical_sovereignty_collision_groups" in s

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


# ---------------------------------------------------------------------------
# `--allow-stale` fail-closed contract (team-lead ruling A)
#
# The source is opened with immutable=1, which SKIPS the WAL. A pre-existing
# -wal/-shm sidecar therefore means the view may silently MISS committed-but-
# not-yet-checkpointed transactions -- a view that LOOKS complete but is not.
# The tool must (i) refuse by default, (ii) proceed only with the explicit
# --allow-stale opt-in AND mark STALE in EVERY output document, and (iii) leave
# the ordinary (no-sidecar) path byte-for-byte unchanged.
# ---------------------------------------------------------------------------

def _make_pre_existing_sidecar(src: Path, suffix: str = "-wal") -> Path:
    """Create an empty ``<db>-wal`` beside the source.

    ``detect_pre_existing_sidecars`` is a pure directory listing, so the mere
    PRESENCE of the file (whatever its bytes) is the signal under test.
    """
    side = src.with_name(src.name + suffix)
    side.write_bytes(b"")
    return side


def test_no_sidecar_is_complete_and_carries_no_stale_marker(tmp_path: Path) -> None:
    """(iii) No sidecar -> COMPLETE view; ordinary output is not perturbed.

    Without -wal/-shm the derivation must behave as before: the summary says
    COMPLETE, and NO stale marker leaks into any output document (so a normal
    run stays byte-identical to the pre-change tool).
    """
    src = _make_synthetic_source(tmp_path)
    out = tmp_path / "out"
    rc = mod.main(["--source", str(src), "--out", str(out)])
    assert rc == 0

    summary = json.loads(
        (out / mod.PARTITION_FILENAME).read_text(encoding="utf-8")
    )["summary"]
    assert summary["view_status"] == "COMPLETE"
    assert summary["stale_reason"] is None
    assert summary["pre_existing_sidecars"] == []

    manifest = json.loads((out / mod.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert "view_status" not in manifest
    assert "STALE_WARNING" not in manifest
    for line in (out / mod.RECORDS_FILENAME).read_text(encoding="utf-8").splitlines():
        assert "view_status" not in json.loads(line)


def test_pre_existing_sidecar_fails_closed_without_flag(tmp_path: Path) -> None:
    """(i) Sidecar + NO flag -> fail closed (non-zero exit; no view written)."""
    src = _make_synthetic_source(tmp_path)
    _make_pre_existing_sidecar(src, "-wal")
    out = tmp_path / "out"

    # Function-level: the explicit fail-closed error is raised.
    try:
        mod.derive_audit_view(str(src), str(out))
    except mod.StaleSourceError:
        pass
    else:  # pragma: no cover - only on regression
        raise AssertionError("expected StaleSourceError (fail-closed) by default")

    # CLI-level: main() reports the non-zero exit code.
    rc = mod.main(["--source", str(src), "--out", str(out)])
    assert rc != 0


def test_allow_stale_flag_marks_every_output_document(tmp_path: Path) -> None:
    """(ii) Sidecar + --allow-stale -> exit 0 AND the STALE marker is REAL.

    The marker must be present in EVERY output document (MANIFEST(sha256),
    audit_view_partition.json, audit_view_records.jsonl), not merely claimed in
    a docstring.
    """
    src = _make_synthetic_source(tmp_path)
    _make_pre_existing_sidecar(src, "-wal")
    out = tmp_path / "out"

    rc = mod.main(["--source", str(src), "--out", str(out), "--allow-stale"])
    assert rc == 0

    manifest = json.loads((out / mod.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["view_status"] == "STALE_INCOMPLETE"
    assert manifest["stale"] is True
    assert manifest["stale_reason"]
    assert manifest["STALE_WARNING"]

    partition = json.loads(
        (out / mod.PARTITION_FILENAME).read_text(encoding="utf-8")
    )
    assert partition["view_status"] == "STALE_INCOMPLETE"
    assert partition["summary"]["view_status"] == "STALE_INCOMPLETE"
    assert partition["summary"]["stale_reason"]

    lines = (out / mod.RECORDS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert lines, "records stream must not be empty"
    for line in lines:
        assert json.loads(line)["view_status"] == "STALE_INCOMPLETE"
