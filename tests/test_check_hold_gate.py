"""Tests for scripts/check_hold_gate.py (C1 -- machine-verifiable HOLD gate).

These prove the gate is wired-semantics-correct:
  (1) LIUHAO_HOLD=1            -> exit non-zero (release blocked)
  (2) LIUHAO_HOLD=0 (or unset) -> exit 0        (normal)
  (3) default / unset          -> exit 0 AND an auditable message on stdout

Run with the managed venv python:
  python -m pytest tests/test_check_hold_gate.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_hold_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_hold_gate_ut", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_with(env_value):
    """Run the script with LIUHAO_HOLD set to env_value (or unset when None)."""
    env = dict(os.environ)
    if env_value is None:
        env.pop("LIUHAO_HOLD", None)
    else:
        env["LIUHAO_HOLD"] = env_value
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_hold_active_blocks_release():
    # (1) LIUHAO_HOLD=1 -> exit non-zero (blocked)
    r = _run_with("1")
    assert r.returncode != 0, r.stdout
    assert "HOLD active" in r.stdout
    assert "release blocked" in r.stdout


def test_hold_explicit_false_allows():
    # (2) LIUHAO_HOLD=0 -> exit 0 (normal)
    r = _run_with("0")
    assert r.returncode == 0, r.stdout
    assert "HOLD inactive" in r.stdout


def test_hold_unset_default_allows_and_is_auditable():
    # (3) default / unset -> exit 0 AND an auditable message was printed.
    r = _run_with(None)
    assert r.returncode == 0, r.stdout
    assert "HOLD inactive" in r.stdout
    # Auditable: it must state it was the *default* decision, not silent.
    assert "default" in r.stdout.lower()


def test_hold_falsy_variants_allow():
    for v in ("false", "off", ""):
        r = _run_with(v)
        assert r.returncode == 0, (v, r.stdout)
        assert "HOLD inactive" in r.stdout, v


def test_hold_other_truthy_blocks():
    for v in ("true", "yes", "on", "anything"):
        r = _run_with(v)
        assert r.returncode != 0, (v, r.stdout)
        assert "HOLD active" in r.stdout, v


def test_logic_importable_and_pure():
    # The decision logic is unit-testable without spawning a subprocess.
    mod = _load_module()
    active, msg = mod.evaluate("1")
    assert active is True and "release blocked" in msg
    active, msg = mod.evaluate(None)
    assert active is False and "default" in msg.lower()
    active, msg = mod.evaluate("0")
    assert active is False
    active, msg = mod.evaluate("false")
    assert active is False
    active, msg = mod.evaluate("HIGH,CRITICAL")
    assert active is True and "release blocked" in msg
