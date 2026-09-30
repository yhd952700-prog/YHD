"""Fail-closed controls for the AI-layer audit-coverage gate.

The gate (scripts/verify_ai_layer_audit.py) used to be green-by-default: it
returned 0 unconditionally once the control probe passed, so 20/21 modules
losing audit coverage stayed green (silent-success audit finding F1). It is now
fail-closed. These tests prove:

  * positive: the gate passes (rc 0) against the current, legitimate state
    (P17 Economy is the only no-audit module and is intentionally allowlisted);
  * negative (regression): if a currently-audited module (P9b ToolRegistry)
    stops writing audit events, the gate must FAIL (rc != 0);
  * negative (allowlist): if the no-audit allowlist is emptied, the gate must
    FAIL (P17 becomes an unaccounted no-audit module).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "scripts" / "verify_ai_layer_audit.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("verify_ai_layer_audit_t", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_passes_on_current_code():
    """Against the current legitimate state the gate must exit 0."""
    proc = subprocess.run(
        [sys.executable, str(GATE)], capture_output=True, text=True)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)
    assert "AI-LAYER AUDIT GATE PASS" in proc.stdout


def test_gate_fails_when_a_known_audited_module_loses_audit(monkeypatch):
    """If P9b (ToolRegistry.register) stops writing audit events, the module
    becomes no-audit and -- not being in ALLOW_NO_AUDIT -- must turn the gate red.
    This proves the gate actually detects a coverage regression, not just the
    control-probe path.
    """
    import src.ai.tool_registry as tr

    class _NoAuditToolRegistry(tr.ToolRegistry):
        def __init__(self, *a, **k):
            pass

        def register(self, *a, **k):
            return None

    monkeypatch.setattr(tr, "ToolRegistry", _NoAuditToolRegistry)
    gate = _load_gate()
    rc = gate.main()
    assert rc != 0, "gate must fail when an audited module loses its audit coverage"


def test_gate_fails_when_allowlist_is_emptied(monkeypatch):
    """If the intentional no-audit allowlist is emptied, P17 becomes an
    unaccounted no-audit module and the gate must fail."""
    gate = _load_gate()
    monkeypatch.setattr(gate, "ALLOW_NO_AUDIT", frozenset())
    rc = gate.main()
    assert rc != 0, "gate must fail when the no-audit allowlist is empty"
