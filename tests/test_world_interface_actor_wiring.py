"""Regression guard: every production ``WorldInterface(`` must set ``actor=``.

U1/U5 hardening is opt-in at construction: only an interface built with
``actor="autonomous"`` gets the default-deny + shell-block posture. A non-human
module that constructs ``WorldInterface()`` with the default (human) silently
bypasses the gate. This test fails CI if any production (non-test) module
constructs ``WorldInterface`` without an explicit ``actor=``, so the autonomous
surface stays default-deny end-to-end.

This complements (not replaces) the behavioural proof in
``tests/test_world_interface_autonomous.py`` — that file proves an
``actor="autonomous"`` interface actually denies by default; this file proves no
production site forgets to set ``actor=`` in the first place.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

_WORLD_CALL = re.compile(r"WorldInterface\s*\(")
_ACTOR_KW = re.compile(r"actor\s*=")


def _production_py_files() -> list[Path]:
    files: list[Path] = []
    for p in sorted(SRC.rglob("*.py")):
        # Skip test code: tests legitimately omit actor= to set up scenarios.
        if "test" in p.parts:
            continue
        files.append(p)
    return files


def _call_has_actor(path: Path, lineno: int) -> bool:
    """Within the call spanning from ``lineno``, is ``actor=`` present?"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    window = "\n".join(lines[lineno : lineno + 10])
    return bool(_ACTOR_KW.search(window))


@pytest.mark.parametrize("path", _production_py_files(), ids=lambda p: str(p))
def test_world_interface_calls_carry_actor(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    offenders: list[int] = []
    for i, line in enumerate(lines):
        # Ignore pure-comment lines that merely mention WorldInterface.
        if line.lstrip().startswith("#"):
            continue
        if _WORLD_CALL.search(line) and not _call_has_actor(path, i):
            offenders.append(i + 1)
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} constructs WorldInterface() without an "
        f"explicit actor= on line(s) {offenders}. Autonomous paths must be built "
        f"with actor='autonomous' so the default-deny gate actually applies."
    )


def test_known_autonomous_sites_set_actor_autonomous() -> None:
    """Spot-check the known non-human sites wire actor='autonomous'."""
    for rel in (
        "src/ai/tools.py",
        "src/ai/e2e_demo.py",
        "src/ai/vhl_benchmark.py",
        "src/ai/hardening.py",
    ):
        p = REPO_ROOT / rel
        assert p.exists(), f"expected {rel} to exist"
        text = p.read_text(encoding="utf-8", errors="replace")
        m = _WORLD_CALL.search(text)
        assert m is not None, f"{rel} should construct WorldInterface"
        start = text[: m.start()].count("\n")
        window = "\n".join(text.splitlines()[start : start + 10])
        assert 'actor="autonomous"' in window, (
            f"{rel} WorldInterface must set actor='autonomous'"
        )
