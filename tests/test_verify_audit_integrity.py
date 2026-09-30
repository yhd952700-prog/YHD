"""Lightweight logic tests for scripts/verify_audit_integrity.py.

These do NOT re-run the six heavy gates; they prove the orchestration logic
(bootstrap, pass/fail aggregation, non-zero exit, skip/note detection) by
monkeypatching GATES with tiny fake gate scripts. The real gates are exercised in
CI by the script itself.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_audit_integrity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("vai_under_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fake(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fake_gate.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_script_bootstraps_sys_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "sys.path.insert" in source or "sys.path.append" in source


def test_aggregate_all_pass_exits_zero(tmp_path, capsys) -> None:
    vai = _load_module()
    vai.GATES = [
        ("T1", "fake pass 1", _write_fake(tmp_path, "import sys\nprint('ok1')\nsys.exit(0)\n")),
        ("T2", "fake pass 2", _write_fake(tmp_path, "import sys\nprint('ok2')\nsys.exit(0)\n")),
    ]
    with pytest.raises(SystemExit) as exc:
        vai.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "OVERALL=PASS" in out
    assert "record_hash=" in out


def test_aggregate_one_fail_exits_nonzero(tmp_path, capsys) -> None:
    vai = _load_module()
    vai.GATES = [
        ("T1", "fake fail", _write_fake(tmp_path, "import sys\nprint('boom')\nsys.exit(1)\n")),
    ]
    with pytest.raises(SystemExit) as exc:
        vai.main()
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "OVERALL=FAIL" in out


def test_skip_note_detection(tmp_path, capsys) -> None:
    vai = _load_module()
    vai.GATES = [
        ("T1", "fake self-attested", _write_fake(tmp_path, "import sys\nprint('SELF-ATTESTED by reference log')\nsys.exit(0)\n")),
    ]
    with pytest.raises(SystemExit) as exc:
        vai.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "PASS (NOTE)" in out
    assert "OVERALL=PASS" in out
