"""Locks HC-01's evidence rule: a gitignored runtime artifact must never be
positive release evidence.

The P0-8 final matrix used to derive "continuity_ok = YES" from the mere
presence of a healthy ``audit_store.db`` in the repo root. That file is a
gitignored runtime artifact, so the same code would report a different status
on a host where the file is absent -- i.e. the matrix was grading the laptop,
not the product.

These tests pin the corrected rule:

  * healthy artifact  -> continuity is NOT demonstrated (N/A), HC-01 stays
                          UNVERIFIED;
  * forked artifact   -> "NO" only as a local forensics signal, HC-01 stays
                          UNVERIFIED (no red/green inversion).

They build their own databases inside ``tmp_path`` and point the matrix module's
``REPO_ROOT`` at it, so the real development database is never touched.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_p08_final_status_matrix.py"


def _load_matrix():
    spec = importlib.util.spec_from_file_location(
        "p08f_hc01_evidence_ut", str(SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_audit_db(path: Path) -> None:
    from src.kernels.audit import AuditStore, AuditEventType, AuditScope

    store = AuditStore(db_path=str(path))
    store.initialize()
    etype, scope = next(iter(AuditEventType)), next(iter(AuditScope))
    store.log_event(etype, "p-1", scope, "ok", details={"n": 1})
    store.log_event(etype, "p-2", scope, "ok", details={"n": 2})


def test_healthy_runtime_artifact_is_not_positive_evidence(tmp_path, monkeypatch):
    db = tmp_path / "audit_store.db"
    _build_audit_db(db)

    mod = _load_matrix()
    mod.FACTS.clear()
    monkeypatch.setattr(mod, "REPO_ROOT", str(tmp_path))
    mod.probe_hc01()

    facts = mod.FACTS["HC-01"]
    assert facts["continuity_ok"] == "N/A", (
        "a healthy repo-root runtime artifact must never become YES; "
        f"got {facts['continuity_ok']!r}"
    )
    assert "0 broken joins" in facts["runtime_artifact"]
    status, why = mod.classify("HC-01")
    assert status == "UNVERIFIED"
    assert "not demonstrated" in why


def test_forked_runtime_artifact_is_forensics_only(tmp_path, monkeypatch):
    db = tmp_path / "audit_store.db"
    _build_audit_db(db)

    with sqlite3.connect(db) as cx:
        last_seq = cx.execute(
            "SELECT seq FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0]
        cx.execute(
            "UPDATE audit_events SET prev_event_hash='CORRUPT' WHERE seq=?",
            (last_seq,),
        )

    mod = _load_matrix()
    mod.FACTS.clear()
    monkeypatch.setattr(mod, "REPO_ROOT", str(tmp_path))
    mod.probe_hc01()

    facts = mod.FACTS["HC-01"]
    assert facts["continuity_ok"] == "NO"
    status, why = mod.classify("HC-01")
    assert status == "UNVERIFIED", (
        "a broken artifact may add a forensics reason but must not change the "
        f"verdict direction; got {status!r}"
    )
    assert "broken" in why
