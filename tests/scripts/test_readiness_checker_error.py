"""A checker that could not RUN must not read as an honest NOT VERIFIED.

#383: ``scripts/verify_readiness.py``'s C3 (``--timeout=60``) and C6
(``--timeout=120``) delegate to ``pytest`` via ``sys.executable``. If the
launching interpreter lacks ``pytest-timeout``, pytest rejects the flag
(``error: unrecognized arguments: --timeout``) and exits non-zero -- which the
old code folded into a plain ``NOT_VERIFIED``, i.e. an *unrunnable* check
masqueraded as a recorded evidence gap. These tests pin the distinct ``ERROR``
status that separates "never ran" from "ran, no verdict".

The real end-to-end (missing plugin -> rc=4 -> ERROR -> exit 2) is environment
dependent and is exercised as run evidence, not here; these tests drive the
classification deterministically with a stubbed ``_run``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import tomllib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_readiness.py"


def _load():
    assert _SCRIPT.is_file(), "missing %s" % _SCRIPT
    spec = importlib.util.spec_from_file_location(
        "verify_readiness_under_test", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
PASS = MODULE.PASS
ERROR = MODULE.ERROR
NOT_VERIFIED = MODULE.NOT_VERIFIED

#: Verbatim shape pytest prints when it rejects a flag it does not know
#: (e.g. ``--timeout`` with pytest-timeout absent).
_PYTEST_ARG_ERROR = (
    "ERROR: usage: __main__.py [options] [file_or_dir] [file_or_dir] [...]\n"
    "__main__.py: error: unrecognized arguments: --timeout=120\n"
)


def test_error_status_is_distinct_from_not_verified() -> None:
    assert ERROR == "ERROR"
    assert ERROR != NOT_VERIFIED


def test_tool_failure_detects_a_rejected_timeout_flag() -> None:
    assert MODULE._tool_failure(_PYTEST_ARG_ERROR) is not None


def test_tool_failure_is_none_for_a_real_test_failure() -> None:
    """A suite that RAN and failed is a verdict, not a tool accident."""
    assert MODULE._tool_failure("5 failed, 267 passed, 0 errors in 41.2s\n") is None


def test_tool_failure_ignores_incidental_wording_far_down() -> None:
    """Only the runner's own startup diagnostics count, not a test's prose."""
    out = ("." * 1200) + "some test printed: unrecognized arguments:\n"
    assert MODULE._tool_failure(out) is None


def test_reliability_checker_reports_error_when_plugin_missing(monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "_run", lambda *a, **k: (4, _PYTEST_ARG_ERROR))
    status, evidence = MODULE.check_reliability()
    assert status == ERROR, (status, evidence)
    assert "could not run" in evidence


def test_full_test_checker_reports_error_when_plugin_missing(monkeypatch) -> None:
    def fake_run(cmd, *a, **k):
        if "--co" in cmd:
            return 0, "3424 tests collected in 12.3s\n"
        return 4, _PYTEST_ARG_ERROR

    monkeypatch.setattr(MODULE, "_run", fake_run)
    status, evidence = MODULE.check_tests(full=True)
    assert status == ERROR, (status, evidence)


def test_full_test_checker_stays_not_verified_on_real_failures(monkeypatch) -> None:
    """Guard against over-reach: a suite that ran and failed is NOT an ERROR."""

    def fake_run(cmd, *a, **k):
        if "--co" in cmd:
            return 0, "3424 tests collected in 12.3s\n"
        return 1, "5 failed, 267 passed in 41.2s\n"

    monkeypatch.setattr(MODULE, "_run", fake_run)
    status, _ = MODULE.check_tests(full=True)
    assert status == NOT_VERIFIED


def test_an_error_condition_exits_nonzero_and_blocks_release(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    """Verdict layer: ERROR counts separately, blocks RELEASE READY, exits 2."""
    monkeypatch.setattr(MODULE, "SCRIPTS", tmp_path)
    monkeypatch.setattr(
        MODULE,
        "CHECKS",
        [("C6", "Crash / recovery / failover", lambda: (ERROR, "forced: never ran"))],
    )
    with contextlib.redirect_stdout(io.StringIO()):
        with pytest.raises(SystemExit) as exc:
            MODULE.main([])
    assert exc.value.code == 2
    report = json.loads(
        (tmp_path / "readiness_report.json").read_text(encoding="utf-8")
    )
    assert report["counts"].get("ERROR") == 1
    assert report["release_ready"] is False


def test_pyproject_declares_pytest_timeout() -> None:
    """The declaration the runner needs, pinned so it cannot silently vanish."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    names = [d.split(">")[0].split("=")[0].strip() for d in extras.get("tests", [])]
    assert "pytest-timeout" in names, extras.get("tests")


def test_contract_documents_the_error_status() -> None:
    """The status vocabulary is documented, not just in code."""
    text = (_REPO_ROOT / "RELEASE-READINESS-CONTRACT.md").read_text(encoding="utf-8")
    assert "ERROR" in text
