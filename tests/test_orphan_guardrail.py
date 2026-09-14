"""Negative controls for ``scripts/verify_no_undocumented_orphans.py``.

``tests/test_guardrail_scripts.py`` proves the orphan detector is *wired* and
*structurally able* to fail. That is necessary but not sufficient: a guardrail
that never actually fires protects nothing. This module proves the detector
**really fails** on the exact situation it exists to catch, and — just as
importantly — that it **does not fire** on the situations it must tolerate.

Each case builds a throwaway repository under ``tmp_path`` and invokes the
script's ``main`` directly, asserting on the documented exit-code contract:

    0  every orphan acknowledged (or there are none)
    1  at least one unacknowledged orphan
    2  the registry itself is malformed
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_no_undocumented_orphans.py"


def _load_module():
    """Import the guardrail script as a module without importing ``scripts``."""
    spec = importlib.util.spec_from_file_location("_orphan_guardrail", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guardrail():
    return _load_module()


def _mk_repo(root: pathlib.Path, *, src_files: dict, corpus_files: dict | None = None):
    """Create a minimal repository layout the detector can scan."""
    for rel, text in src_files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel, text in (corpus_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")


def _run(guardrail, root: pathlib.Path, registry: pathlib.Path | None = None) -> int:
    argv = ["--root", str(root)]
    if registry is not None:
        argv += ["--registry", str(registry)]
    return guardrail.main(argv)


# --------------------------------------------------------------------------- #
# 1. The detector must FIRE on a genuine orphan.
# --------------------------------------------------------------------------- #

def test_unreferenced_module_fails_the_gate(guardrail, tmp_path):
    """The whole point: a src/ module with zero references is a missing decision."""
    _mk_repo(tmp_path, src_files={
        "src/lonely.py": "def nothing_calls_me():\n    return 1\n",
    })
    assert _run(guardrail, tmp_path) == 1


def test_module_referenced_from_a_call_site_passes(guardrail, tmp_path):
    """A module that IS used must not trip the gate.

    The call site deliberately lives in ``scripts/`` rather than ``src/``: a
    second module under ``src/`` would itself be unreferenced and turn this into
    a test of a different thing.
    """
    _mk_repo(
        tmp_path,
        src_files={"src/used.py": "VALUE = 1\n"},
        corpus_files={"scripts/use_it.py": "from src.used import VALUE\n\nprint(VALUE)\n"},
    )
    assert _run(guardrail, tmp_path) == 0


def test_module_referenced_only_by_tests_is_not_gated(guardrail, tmp_path):
    """'test-only reachable' is a signal, not a verdict -- documented behaviour."""
    _mk_repo(
        tmp_path,
        src_files={"src/maybe.py": "def helper():\n    return 1\n"},
        corpus_files={"tests/test_maybe.py": "from src.maybe import helper\n"},
    )
    assert _run(guardrail, tmp_path) == 0


def test_quoted_dotted_path_counts_as_a_reference(guardrail, tmp_path):
    """Dynamic loading by string must not be misread as an orphan."""
    _mk_repo(
        tmp_path,
        src_files={"src/dyn.py": "def go():\n    return 1\n"},
        corpus_files={
            "scripts/loader.py": "import importlib\n\nm = importlib.import_module('src.dyn')\n",
        },
    )
    assert _run(guardrail, tmp_path) == 0


# --------------------------------------------------------------------------- #
# 2. An acknowledgement must be a real decision.
# --------------------------------------------------------------------------- #

def test_acknowledged_orphan_passes(guardrail, tmp_path):
    _mk_repo(tmp_path, src_files={"src/lonely.py": "VALUE = 1\n"})
    registry = tmp_path / "orphan-registry.yaml"
    registry.write_text(
        "acknowledged:\n"
        "  - module: src.lonely\n"
        "    classification: library-staged\n"
        "    reason: staged helper, no consumer yet\n",
        encoding="utf-8",
    )
    assert _run(guardrail, tmp_path, registry) == 0


def test_acknowledgement_without_a_reason_is_malformed(guardrail, tmp_path):
    """An acknowledgement without a reason is not a decision -- exit 2."""
    _mk_repo(tmp_path, src_files={"src/lonely.py": "VALUE = 1\n"})
    registry = tmp_path / "orphan-registry.yaml"
    registry.write_text(
        "acknowledged:\n  - module: src.lonely\n    classification: library-staged\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        _run(guardrail, tmp_path, registry)
    assert str(excinfo.value) == "ERROR: src.lonely has no 'reason'. An acknowledgement without a reason is not a decision."


def test_acknowledging_a_module_that_is_not_an_orphan_still_passes(guardrail, tmp_path):
    """A stale entry is warned about, not fatal -- the registry self-heals visibly."""
    _mk_repo(
        tmp_path,
        src_files={"src/used.py": "VALUE = 1\n"},
        corpus_files={"scripts/use_it.py": "from src.used import VALUE\n"},
    )
    registry = tmp_path / "orphan-registry.yaml"
    registry.write_text(
        "acknowledged:\n"
        "  - module: src.used\n"
        "    classification: library-staged\n"
        "    reason: no longer an orphan -- should be removed\n",
        encoding="utf-8",
    )
    assert _run(guardrail, tmp_path, registry) == 0


# --------------------------------------------------------------------------- #
# 3. The real repository must be in the acknowledged steady state.
# --------------------------------------------------------------------------- #

def test_the_real_repository_has_no_unacknowledged_orphans(guardrail):
    """Runs against this checkout: the committed registry must cover every orphan."""
    assert _run(guardrail, REPO_ROOT) == 0


def test_real_registry_covers_at_least_one_superseded_duplicate(guardrail):
    """Guard against the registry being emptied of its actual findings.

    Note the deliberate absence of literal dotted module paths in this file: the
    detector scans ``tests/`` for references, so a test that *names* a module
    would make it look referenced and silently blind the very audit it is
    checking. Assertions therefore go through the registry, never through
    hardcoded module names.
    """
    import yaml  # noqa: PLC0415 - CI installs pyyaml

    registry = REPO_ROOT / "orphan-registry.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    entries = data.get("acknowledged") or []

    by_classification: dict[str, list[dict]] = {}
    for entry in entries:
        by_classification.setdefault(entry.get("classification", ""), []).append(entry)

    duplicates = by_classification.get("superseded-duplicate", [])
    assert len(duplicates) >= 3, (
        "expected at least three verified dead duplicates of live "
        "implementations; found %s" % sorted(by_classification)
    )
    # Every duplicate reason must point at a live counterpart, not restate itself.
    for entry in duplicates:
        assert "live" in entry["reason"].lower() or "counterpart" in entry["reason"].lower(), entry
