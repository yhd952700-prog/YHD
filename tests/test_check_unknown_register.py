"""Tests for scripts/check_unknown_register.py (register reconciliation gate, U58).

The gate must be GREEN on the real register (it was just reconciled) and RED on a
crafted counterexample proving it can actually fail. Counterexamples are written
to ``tmp_path`` -- the real register is never mutated.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_unknown_register.py"
REAL_REGISTER = REPO_ROOT / "docs" / "autonomous" / "UNKNOWN-TO-OWNER.md"


def _load():
    spec = importlib.util.spec_from_file_location("check_unknown_register", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, check=False)
    return proc.stdout.strip()


ROOT_COMMIT = (_git("rev-list", "--max-parents=0", "HEAD").split() or [""])[-1]
HEAD_COMMIT = _git("rev-parse", "HEAD")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _require_git():
    if not ROOT_COMMIT or not HEAD_COMMIT:
        pytest.skip("git history unavailable")


# --- GREEN: the real register must pass -------------------------------------

def test_real_register_has_no_stale_entries():
    assert cr.find_mismatches(REAL_REGISTER, REPO_ROOT, "HEAD") == []


# --- RED: counterexamples proving the gate can fail --------------------------

def test_status_region_citation_of_landed_fix_is_stale(tmp_path):
    p = _write(tmp_path, "status.md", f"""# t
## U99 - demo
- **Status:** DISCOVERED - the fix landed in `{ROOT_COMMIT}`.
""")
    assert [m[0] for m in cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT)] == ["U99"]


def test_unique_body_citation_of_landed_fix_is_stale(tmp_path):
    p = _write(tmp_path, "body.md", f"""# t
## U98 - demo
- **What:** depends on work committed at `{ROOT_COMMIT}`.
- **Status:** DISCOVERED (read-only).
""")
    assert [m[0] for m in cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT)] == ["U98"]


# --- controls: must NOT be flagged ------------------------------------------

def test_closed_entry_citing_ancestor_is_not_flagged(tmp_path):
    p = _write(tmp_path, "closed.md", f"""# t
## U97 - demo
- **What:** depends on `{ROOT_COMMIT}`.
- **Status:** REMEDIATED (commit `{ROOT_COMMIT}`).
""")
    assert cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT) == []


def test_open_entry_citing_arrow_movement_is_not_flagged(tmp_path):
    p = _write(tmp_path, "arrow.md", f"""# t
## U96 - demo
- **What:** HEAD moved `{ROOT_COMMIT}` \u2192 `{HEAD_COMMIT}`.
- **Status:** DISCOVERED (read-only).
""")
    assert cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT) == []


def test_shared_context_commit_is_not_flagged(tmp_path):
    # Two entries cite the same commit; the open one is the *shared-context*
    # second entry (same shape as real U45 <- U43 / U58 <- U48..U51).
    p = _write(tmp_path, "shared.md", f"""# t
## U95 - owner of the fix
- **Status:** REMEDIATED (commit `{ROOT_COMMIT}`).
## U94 - context citation of the same commit
- **What:** see the fix in `{ROOT_COMMIT}`.
- **Status:** DISCOVERED (read-only).
""")
    assert cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT) == []


def test_human_decision_entry_is_exempt(tmp_path):
    p = _write(tmp_path, "human.md", f"""# t
## U93 - demo
- **Status:** DISCOVERED - pending HUMAN DECISION; fix landed in `{ROOT_COMMIT}`.
""")
    assert cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT) == []


def test_fail_on_context_disables_shared_exclusion(tmp_path):
    p = _write(tmp_path, "shared.md", f"""# t
## U95 - owner of the fix
- **Status:** REMEDIATED (commit `{ROOT_COMMIT}`).
## U94 - context citation of the same commit
- **What:** see the fix in `{ROOT_COMMIT}`.
- **Status:** DISCOVERED (read-only).
""")
    got = [m[0] for m in cr.find_mismatches(p, REPO_ROOT, HEAD_COMMIT, fail_on_context=True)]
    assert got == ["U94"]
