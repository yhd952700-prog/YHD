"""Negative + positive controls for the single-writer fence CI gate.

The gate (scripts/verify_fence_single_writer.py) drives the REAL AuditStore and
asserts that (a) normal writes succeed and (b) a fenced/intruder writer is
refused. Two risks must be ruled out:

  * the gate is a no-op that never fails -> prove it PASSES on a clean DB;
  * the gate would still pass if the fence were removed -> prove it FAILS when
    both fence layers on the live append path are neutralised.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[3] / "scripts" / "verify_fence_single_writer.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("verify_fence_single_writer", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_passes_on_clean_db():
    """The gate must exit 0 against a hermetic, fence-intact store."""
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr)
    assert "FENCE GATE PASS" in proc.stdout


def test_gate_fails_when_fence_neutered(monkeypatch):
    """If the two fence layers on the live append path are removed, the intruder
    write must succeed and the gate's assertion must fail (the gate is then
    non-zero). This proves the gate actually depends on the fence."""
    import src.kernels.audit as audit_mod
    from src.kernels.audit.fencing import SqliteWriterLease

    # Neutralise the live append path's fence so an intruder writer is NOT
    # refused: acquire_within returns the row's current token (a valid token,
    # so require_writer_lease also passes) and require_writer_lease is a no-op.
    def _noop_acquire_within(self, *a, **k):
        row = self._conn.execute(
            "SELECT token FROM writer_lease WHERE id=1").fetchone()
        return (row[0] if row else 1)

    monkeypatch.setattr(SqliteWriterLease, "acquire_within", _noop_acquire_within)
    monkeypatch.setattr(audit_mod, "require_writer_lease", lambda lease, token: None)

    gate = _load_gate()
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "audit.db")
        # The gate's assertion ("FENCE NOT ENFORCED") must fire because the
        # intruder write no longer raises -> run_fence_check raises.
        with pytest.raises(AssertionError):
            gate.run_fence_check(db)
