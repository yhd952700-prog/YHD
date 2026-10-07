"""SA-1 gate tests: the dependency declaration surface checker.

These tests are about the *checker*, not about the current state of the
repository. A gate that is itself wrong is worse than no gate, so the cases
below pin the three ways this checker was observed to be wrong while it was
being built, each of which would have produced a **false red** and trained
people to ignore the output:

  1. ``yaml`` is shipped by the distribution ``PyYAML`` -- comparing the
     *import name* against the declared list flags a correctly declared
     dependency as missing.
  2. ``error_types`` is a first-party module at the repository root, imported
     by bare name from ``src/ai/providers.py``. Resolving first-party-ness
     through ``importlib`` fails here because the gate runs as
     ``python scripts/<name>.py``, so ``sys.path[0]`` is ``scripts/`` and the
     repo root is not importable.
  3. ``from sqlalchemy import BigInteger`` names an **attribute**, not a
     module. Probing ``sqlalchemy.BigInteger`` with ``find_spec`` reports a
     perfectly good installation as broken.

Plus the two ways it could have been *too weak*: a lazy (function-body)
import is a real dependency, and a job that installs ``requirements.txt`` is
not equivalent to a job that installs the ``[tests]`` extra.

Runtime cost -- read this before concluding the file is hung
------------------------------------------------------------
This file is **slow but it finishes**. Collection is fast (~1.7 s, 11 tests),
but two of the cases do real work against the whole ``src/`` tree instead of
asserting on a fixture:

  * ``test_gate_reports_no_undeclared_dependency_in_src`` probes the
    importability of every collected ``src/`` import -- ~97 s here;
  * ``test_gate_main_runs_and_reports_a_verdict`` runs the gate end to end
    -- ~104 s here.

Whole file, measured: **``11 passed in ~214 s``**. A run killed at 30-90 s
prints *nothing* for the slow cases and is indistinguishable from a hang --
it is not one. Give it a >300 s budget; to run a single slow case, leave a
generous timeout on the command, e.g.::

    python -m pytest tests/test_dependency_declaration_surface.py \\
        -k test_gate_reports_no_undeclared_dependency_in_src

(use the project venv interpreter; ``--timeout=300`` is available via
pytest-timeout if you want a hard stop). Do not buy speed by weakening the
assertions: the cost is the price of probing the real repository.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify_dependency_declaration_surface.py"


def _load_gate():
    """Import the gate as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("sa1_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# ───────────────────────── the three false reds ─────────────────────────

def test_import_name_is_mapped_to_distribution_name():
    """``import yaml`` is provided by the distribution ``PyYAML``."""
    dists = gate.distribution_names_for("yaml")
    assert "pyyaml" in dists, (
        "the import name must resolve to the distribution that ships it; "
        f"got {dists}"
    )
    # And that distribution is what pyproject declares.
    assert "pyyaml" in gate.declared_packages()


def test_repo_root_module_is_first_party_not_a_missing_dependency():
    """``error_types`` lives in this repo, so it is not a dependency."""
    first_party = gate.first_party_modules()
    assert "error-types" in first_party or "error_types" in first_party, (
        "a first-party module at the repo root must not be reported as an "
        f"undeclared third-party import; got {sorted(first_party)[:20]}"
    )
    assert (REPO / "error_types.py").is_file(), "fixture assumption: error_types.py exists"


def test_from_import_of_an_attribute_is_not_reported_unimportable():
    """``from sqlalchemy import BigInteger`` must not probe a submodule path."""
    assert gate.module_is_importable("sqlalchemy")
    # The attribute is not a module; the gate must never probe it, and the
    # real module path it does probe must succeed.
    assert gate.module_is_importable("sqlalchemy.orm")
    assert not gate.module_is_importable("sqlalchemy.this_submodule_does_not_exist")


# ───────────────────────── the two "too weak" cases ─────────────────────────

def test_lazy_imports_are_collected_as_dependencies():
    """A function-body import is a hard dependency of the path that runs."""
    top, lazy_only, _ = gate.collect_imports()
    # requests is imported inside functions in src/ai/providers.py and
    # src/observability/alerts/sinks.py, never at module top level.
    assert "requests" not in top, "requests is a lazy import; if this changes, update the test"
    assert "requests" in lazy_only, "lazy imports must be surfaced, not ignored"


def test_lazy_import_site_is_reported_with_its_file():
    _, lazy_only, _ = gate.collect_imports()
    sites = lazy_only.get("requests", set())
    assert any("sinks.py" in s for s in sites), (
        f"the alert webhook delivery sink must be named as a requests user; got {sorted(sites)}"
    )


# ───────────────────────── install-surface logic ─────────────────────────

def test_requirements_without_the_extra_does_not_satisfy_the_test_surface():
    ok, why = gate.requirements_provides_test_extra()
    # This is the real state of the repository and the whole point of the
    # gate: requirements.txt is a default-dependencies compile.
    assert ok is False
    assert "fakeredis" in why or "lupa" in why


def test_any_install_command_may_satisfy_the_test_surface():
    """A job may install the lock AND then the extra; that is satisfied."""
    ok, _ = gate.job_installs_test_surface(
        ["pip install -r requirements.txt", "pip install -e .[tests,etcd]"]
    )
    assert ok is True, "judging on the first command alone is a false red"

    ok, _ = gate.job_installs_test_surface(["pip install -r requirements.txt"])
    assert ok is False, "the lock alone does not provide the test surface"


def test_scope_without_gated_tests_is_not_asserted():
    """``tests/integration`` has no fakeredis/lupa test, so do not demand it."""
    needs, why = gate.pytest_scope_needs_extra(["python -m pytest tests/integration/ -v"])
    assert needs is False
    assert "no test under" in why

    needs, why = gate.pytest_scope_needs_extra(["python -m pytest tests/ -v"])
    assert needs is True
    assert "gated test file" in why


def test_workflow_parser_finds_install_and_pytest_per_job():
    jobs = gate.parse_jobs(REPO / ".github" / "workflows" / "ci.yml")
    assert "test" in jobs, f"expected a 'test' job; got {sorted(jobs)}"
    assert jobs["test"]["installs"], "the test job must record its install commands"
    assert jobs["test"]["pytests"], "the test job must record its pytest command"
    assert jobs["test"]["setup_python"] is True


# ───────────────────────── the gate on the real repository ─────────────────────────

def test_gate_reports_no_undeclared_dependency_in_src():
    """After the fix, every importable src/ import is declared.

    The unimportable case is deliberately excluded: ``google.generativeai``
    is a genuine broken code path in ``src/ai/providers.py`` owned by the AI
    domain, and it is a *code* defect rather than a missing declaration -- it
    cannot be made green by editing pyproject.toml.
    """
    top, lazy_only, full = gate.collect_imports()
    declared = gate.declared_packages()
    undeclared = []
    for store in (top, lazy_only):
        for root in store:
            if gate.distribution_names_for(root) & declared:
                continue
            submodules = sorted(full.get(root, {root}))
            if all(gate.module_is_importable(m) for m in submodules):
                undeclared.append(root)
    assert not undeclared, f"src/ imports that work but are not declared: {sorted(undeclared)}"


def test_gate_main_runs_and_reports_a_verdict():
    """The gate must produce a verdict, not a traceback."""
    code = gate.main()
    assert code in (0, 1, 2), f"gate must exit 0/1/2, got {code}"
