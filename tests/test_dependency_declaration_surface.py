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

# 本文件里有两个用例要真实扫描/探测整个 src/ 树，单次 200s 上下；
# 用 slow marker 让它只在发版门禁显式开启（见 pyproject addopts 与
# docs/best-practices/test-subset-mapping.md）。
pytestmark = pytest.mark.slow


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

def _scan_alert_sink():
    """Scan just ``sinks.py`` so the assertion does not depend on whether
    ``requests`` happens to be a top-level import elsewhere in the tree."""
    stdlib = set(sys.stdlib_module_names)
    first_party = gate.first_party_modules()
    top: dict[str, set[str]] = {}
    inside: dict[str, set[str]] = {}
    full: dict[str, set[str]] = {}
    gate._collect_one_file(
        REPO / "src" / "observability" / "alerts" / "sinks.py",
        REPO / "src",
        "src",
        top,
        inside,
        full,
        stdlib,
        first_party,
    )
    return top, inside


def test_lazy_imports_are_collected_as_dependencies():
    """A function-body import is a hard dependency of the path that runs."""
    top, inside = _scan_alert_sink()
    # ``import requests`` sits inside ``WebhookSink.send``, never at module
    # top level. (仓库别处已改成顶层导入 requests，所以只能按文件断言。)
    assert "requests" not in top
    assert "requests" in inside, "lazy imports must be surfaced, not ignored"


def test_lazy_import_site_is_reported_with_its_file():
    _, inside = _scan_alert_sink()
    sites = inside.get("requests", set())
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
        ["pip install -r requirements.txt", "pip install -e .[tests]"]
    )
    assert ok is True, "judging on the first command alone is a false red"

    ok, _ = gate.job_installs_test_surface(["pip install -r requirements.txt"])
    assert ok is False, "the lock alone does not provide the test surface"


def test_scope_without_gated_tests_is_not_asserted():
    """``tests/integration`` has no fakeredis/lupa test, so do not demand it."""
    needs, verified, why = gate.pytest_scope_needs_extra(["python -m pytest tests/integration/ -v"])
    assert needs is False
    assert verified is True
    assert "no test under" in why

    needs, verified, why = gate.pytest_scope_needs_extra(["python -m pytest tests/ -v"])
    assert needs is True
    assert verified is True
    assert "gated test file" in why


def test_unresolvable_pytest_target_is_not_verified():
    """A target the gate cannot resolve was NOT measured: never report a pass."""
    needs, verified, why = gate.pytest_scope_needs_extra(
        ["python -m pytest tests/no_such_dir/ -v"]
    )
    assert verified is False, "unresolvable scope must not be treated as verified"
    assert needs is True
    assert "NOT VERIFIED" in why


def test_workflow_parser_finds_install_and_pytest_per_job():
    jobs = gate.parse_jobs(REPO / ".github" / "workflows" / "ci.yml")
    assert "test" in jobs, f"expected a 'test' job; got {sorted(jobs)}"
    assert jobs["test"]["installs"], "the test job must record its install commands"
    assert jobs["test"]["pytests"], "the test job must record its pytest command"
    assert jobs["test"]["setup_python"] is True


# ───────────────────────── the gate on the real repository ─────────────────────────

def test_gate_reports_no_undeclared_dependency_in_src():
    """After the fix, every importable src/ import is declared.

    The unimportable case is deliberately excluded: a lazily imported module
    that cannot be imported *in this interpreter* is an environment /
    provisioning question, not a missing declaration -- it cannot be made
    green by editing pyproject.toml.
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
    assert code in (0, 1, 3), f"gate must exit 0 (pass) / 1 (fail) / 3 (not verified), got {code}"


def test_bom_source_file_makes_the_gate_fail(tmp_path, monkeypatch, capsys):
    """护栏：无法 ``ast.parse`` 的文件必须让门禁变红，不能静默跳过。

    历史缺陷：``src/desktop/repair.py`` 的 UTF-8 BOM 让 ``ast.parse`` 抛
    ``U+FEFF``，门禁只打印 ``[BLOCKED]`` 就继续，最后仍报 PASS —— 整个文件的
    import 面从没被检查过。这里用一个真实带 BOM 的临时文件复现并锁死行为。
    """
    bom = tmp_path / "bom_source.py"
    bom.write_bytes(b"\xef\xbb\xbfimport requests\n")

    gate.PARSE_FAILURES.clear()
    top, inside, full = {}, {}, {}
    gate._collect_one_file(
        bom, tmp_path, "src", top, inside, full,
        set(sys.stdlib_module_names), gate.first_party_modules(),
    )

    failures = gate.parse_failures()
    assert any("bom_source.py" in f for f in failures), failures
    assert "cannot parse" in capsys.readouterr().out
    assert "requests" not in top and "requests" not in inside, "解析失败的文件不能产出假阴性"

    # check A 必须把解析失败算成 problem（即门禁 verdict 为红）。
    monkeypatch.setattr(gate, "collect_imports", lambda: ({}, {}, {}))
    gate.PARSE_FAILURES[:] = failures
    problems = gate.check_a()
    assert any("cannot parse" in p for p in problems), problems
