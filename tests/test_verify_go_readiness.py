"""Tests for scripts/verify_go_readiness.py (UU-01 -- GO-readiness gate).

These prove:
  * the gate FAILS when HC-01 status is UNVERIFIED, and
  * the gate would PASS when HC-01 is COMPLIANT (and the deployment arms CRITICAL),
  * the deployment-truth check independently blocks GO.

Status is injected via monkeypatch -- the tests NEVER touch the live
audit_store.db and NEVER change the matrix's real classification. The current
REAL status of HC-01 is UNVERIFIED (forked persisted chain), so a live run of
the gate correctly FAILS; that honest result is documented in the guarded
live test below.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_go_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "verify_go_readiness_ut", str(SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(mod):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()


def test_gate_fails_when_hc01_unverified(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "determine_hc01_status", lambda: "UNVERIFIED")
    monkeypatch.setattr(mod, "deployment_arming_ok", lambda: (True, []))
    rc, out = _capture(mod)
    assert rc != 0
    assert "GO_STATUS: UNVERIFIED" in out
    assert "HC-01: UNVERIFIED" in out


def test_gate_passes_when_hc01_compliant_and_deploy_ok(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "determine_hc01_status", lambda: "COMPLIANT")
    monkeypatch.setattr(mod, "deployment_arming_ok", lambda: (True, []))
    rc, out = _capture(mod)
    assert rc == 0
    assert "GO_STATUS: COMPLIANT" in out


def test_gate_fails_when_deployment_not_critical(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "determine_hc01_status", lambda: "COMPLIANT")
    monkeypatch.setattr(
        mod,
        "deployment_arming_ok",
        lambda: (False, ["scripts/build_cloud_bundle.py arms HIGH,CRITICAL"]),
    )
    rc, out = _capture(mod)
    assert rc != 0
    assert "GO_STATUS: FAILED" in out
    assert "DEPLOYMENT TRUTH: FAIL" in out


def test_live_gate_currently_fails_honestly():
    """The real HC-01 status is UNVERIFIED, so the live gate must fail.

    Skipped (not failed) if this environment cannot import src / read the DB,
    so the monkeypatch-based tests above remain authoritative.
    """
    mod = _load_module()
    try:
        status = mod.determine_hc01_status()
    except Exception:  # pragma: no cover - environment dependent
        pytest.skip("HC-01 determination unavailable in this environment")
    # Whatever the honest status, if it is not COMPLIANT the gate must fail.
    if status != "COMPLIANT":
        rc, out = _capture(mod)
        assert rc != 0
        assert "GO_STATUS:" in out
        assert "HC-01:" in out
