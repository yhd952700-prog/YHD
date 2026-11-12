"""C3/C12 must read the RUNNER'S verdict, not a bare ``rc``.

Two failure modes this pins down, both about a green run being misjudged:

1. **Safe-delete shim.** The WorkBuddy host injects ``sitecustomize.py`` which
   raises ``SystemExit(1)`` once a turn's cumulative deletes cross a threshold.
   A pytest that ran with every test green can therefore exit ``rc=1``. Read as
   a plain non-zero exit, that is a *false red*.

2. **Killed run.** A truncated/killed pytest never reaches
   ``pytest_sessionfinish``, so its JUnit XML is absent or partial. Read by a
   regex that happens to match "0 failed", a killed run could be a *false
   green*. The fix: a PASS requires a structurally self-consistent JUnit
   document; anything less can never be read as zero failures.

The classification helpers are driven directly with synthetic JUnit documents
and outputs, so the contract is testable instead of being a claim.
"""

from __future__ import annotations

import importlib.util
import pathlib

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

#: JUnit shape pytest actually emits: totals live on the <testsuite>, the
#: <testsuites> root carries only a name. (Validated against a real run.)
_CLEAN_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="0" failures="0" skipped="1" tests="3" '
    'time="1.0">'
    '<testcase classname="t" name="a" time="0.1" />'
    '<testcase classname="t" name="b" time="0.1" />'
    '<testcase classname="t" name="c" time="0.1">'
    '<skipped type="pytest.skip" message="m">m</skipped></testcase>'
    "</testsuite></testsuites>"
)
_FAILED_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2" '
    'time="1.0">'
    '<testcase classname="t" name="a" time="0.1" />'
    '<testcase classname="t" name="b" time="0.1">'
    '<failure message="boom">tb</failure></testcase>'
    "</testsuite></testsuites>"
)
#: A partially written document: the suite CLAIMS 5 tests but only 2 are present.
_TRUNCATED_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="0" failures="0" tests="5" time="1.0">'
    '<testcase classname="t" name="a" time="0.1" />'
    '<testcase classname="t" name="b" time="0.1" />'
    "</testsuite></testsuites>"
)
_SHIM_OUT = (
    "[safe-delete]SAFE_DELETE_BULK_CONFIRM_REQUIRED: bulk delete of 51 paths "
    "from a non-interactive turn was aborted\n"
)
_UNRECOGNIZED_ARG = "error: unrecognized arguments: --timeout=60\n"


def _write(tmp_path: pathlib.Path, text: str) -> str:
    p = tmp_path / "report.xml"
    p.write_text(text, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# shim marker
# --------------------------------------------------------------------------- #
def test_shim_marker_is_detected() -> None:
    assert MODULE._shim_marker(_SHIM_OUT) is not None


def test_shim_marker_absent_is_none() -> None:
    assert MODULE._shim_marker("3 passed, 0 failed in 4.2s\n") is None
    assert MODULE._shim_marker("") is None


# --------------------------------------------------------------------------- #
# _pytest_verdict: structural self-consistency
# --------------------------------------------------------------------------- #
def test_clean_junit_parses(tmp_path: pathlib.Path) -> None:
    assert MODULE._pytest_verdict(_write(tmp_path, _CLEAN_JUNIT)) == (3, 0, 0, 1)


def test_failed_junit_parses(tmp_path: pathlib.Path) -> None:
    assert MODULE._pytest_verdict(_write(tmp_path, _FAILED_JUNIT)) == (2, 1, 0, 0)


def test_truncated_junit_is_rejected(tmp_path: pathlib.Path) -> None:
    """Structural mismatch (claims 5, holds 2) must not be read as a verdict."""
    assert MODULE._pytest_verdict(_write(tmp_path, _TRUNCATED_JUNIT)) is None


def test_missing_and_malformed_junit_is_none(tmp_path: pathlib.Path) -> None:
    assert MODULE._pytest_verdict(None) is None
    assert MODULE._pytest_verdict(str(tmp_path / "nope.xml")) is None
    assert MODULE._pytest_verdict(_write(tmp_path, "<testsuites>")) is None


# --------------------------------------------------------------------------- #
# _verdict_from_run
# --------------------------------------------------------------------------- #
def test_green_run_aborted_by_shim_is_still_pass(tmp_path: pathlib.Path) -> None:
    """The exact false-red this exists for: rc=1, but the runner says green."""
    status, evidence = MODULE._verdict_from_run(
        1, _SHIM_OUT, _write(tmp_path, _CLEAN_JUNIT),
        subject="full suite", pass_detail="full run: 3 collected, 0 failed",
    )
    assert status == PASS, (status, evidence)
    assert "shim" in evidence.lower(), evidence


def test_green_run_rc0_is_pass(tmp_path: pathlib.Path) -> None:
    status, evidence = MODULE._verdict_from_run(
        0, "3 passed in 4.2s\n", _write(tmp_path, _CLEAN_JUNIT),
        subject="full suite", pass_detail="full run",
    )
    assert status == PASS, (status, evidence)


def test_failed_run_with_junit_is_not_verified(tmp_path: pathlib.Path) -> None:
    status, _ = MODULE._verdict_from_run(
        1, "1 failed, 1 passed in 4.2s\n", _write(tmp_path, _FAILED_JUNIT),
        subject="full suite", pass_detail="full run",
    )
    assert status == NOT_VERIFIED


def test_failed_run_without_junit_is_not_verified() -> None:
    """A suite that ran and failed is a verdict, not a tool accident."""
    status, evidence = MODULE._verdict_from_run(
        1, "5 failed, 267 passed in 41.2s\n", None,
        subject="full suite", pass_detail="full run",
    )
    assert status == NOT_VERIFIED, (status, evidence)


def test_killed_run_with_no_verdict_is_error() -> None:
    """rc=1, no JUnit, no completion summary -> did not finish -> ERROR."""
    status, evidence = MODULE._verdict_from_run(
        1, "collected 10 items\n(test output truncated mid-run", None,
        subject="full suite", pass_detail="full run",
    )
    assert status == ERROR, (status, evidence)
    assert "did not complete" in evidence


def test_rc0_without_verdict_is_not_verified() -> None:
    status, _ = MODULE._verdict_from_run(
        0, "no junit here\n", None, subject="full suite", pass_detail="full run",
    )
    assert status == NOT_VERIFIED


def test_tool_that_could_not_start_is_error() -> None:
    status, evidence = MODULE._verdict_from_run(
        4, _UNRECOGNIZED_ARG, None, subject="full suite", pass_detail="full run",
    )
    assert status == ERROR, (status, evidence)


# --------------------------------------------------------------------------- #
# check_tests (C3)
# --------------------------------------------------------------------------- #
def _stub_collection(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE, "_run", lambda *a, **k: (0, "4300 tests collected in 10.0s\n")
    )


def test_check_tests_full_green_passes(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    _stub_collection(monkeypatch)
    xml = _write(tmp_path, _CLEAN_JUNIT)
    monkeypatch.setattr(
        MODULE, "_execute_full_suite",
        lambda: {"rc": 0, "out": "3 passed\n", "xml": xml,
                 "tests": 3, "failures": 0, "errors": 0, "skipped": 1,
                 "command": "pytest tests/"},
    )
    status, evidence = MODULE.check_tests(full=True)
    assert status == PASS, (status, evidence)
    # And the run is stashed for C12 to reuse instead of re-executing.
    assert MODULE._LAST_FULL_RUN is not None


def test_check_tests_green_but_shim_aborted_is_pass(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    """rc=1 from the shim + green JUnit -> PASS, not a false red."""
    _stub_collection(monkeypatch)
    xml = _write(tmp_path, _CLEAN_JUNIT)
    monkeypatch.setattr(
        MODULE, "_execute_full_suite",
        lambda: {"rc": 1, "out": _SHIM_OUT, "xml": xml,
                 "tests": 3, "failures": 0, "errors": 0, "skipped": 1,
                 "command": "pytest tests/"},
    )
    status, evidence = MODULE.check_tests(full=True)
    assert status == PASS, (status, evidence)


def test_check_tests_full_failure_is_not_verified(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    monkeypatch.setattr(
        MODULE, "_execute_full_suite",
        lambda: {"rc": 1, "out": "1 failed, 4299 passed\n", "xml": None,
                 "tests": 4300, "failures": 1, "errors": 0, "skipped": 24,
                 "command": "pytest tests/"},
    )
    status, _ = MODULE.check_tests(full=True)
    assert status == NOT_VERIFIED


def test_check_tests_full_killed_is_error(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    monkeypatch.setattr(
        MODULE, "_execute_full_suite",
        lambda: {"rc": 1, "out": "collected 4300 items\n(truncated", "xml": None,
                 "tests": None, "failures": None, "errors": None, "skipped": None,
                 "command": "pytest tests/"},
    )
    status, _ = MODULE.check_tests(full=True)
    assert status == ERROR


# --------------------------------------------------------------------------- #
# _execute_full_suite wiring
# --------------------------------------------------------------------------- #
def test_execute_full_suite_requests_junit(monkeypatch) -> None:
    """The full run MUST ask pytest for JUnit XML, or the verdict is unreachable."""
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return 0, "1 passed\n"

    monkeypatch.setattr(MODULE, "_run", fake_run)
    MODULE._execute_full_suite()
    cmd = captured["cmd"]
    assert any(str(c).startswith("--junitxml=") for c in cmd), cmd
    assert "tests/" in cmd


# --------------------------------------------------------------------------- #
# check_no_major_failures (C12)
# --------------------------------------------------------------------------- #
def test_check_c12_non_full_is_not_verified(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    status, _ = MODULE.check_no_major_failures(full=False)
    assert status == NOT_VERIFIED


def test_check_c12_full_clean_passes(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    monkeypatch.setattr(
        MODULE, "_LAST_FULL_RUN",
        {"rc": 0, "out": "", "xml": None, "tests": 4300, "failures": 0,
         "errors": 0, "skipped": 24, "command": "pytest tests/"},
    )
    status, evidence = MODULE.check_no_major_failures(full=True)
    assert status == PASS, (status, evidence)


def test_check_c12_full_failure_is_not_verified(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    monkeypatch.setattr(
        MODULE, "_LAST_FULL_RUN",
        {"rc": 1, "out": "", "xml": None, "tests": 4300, "failures": 2,
         "errors": 0, "skipped": 24, "command": "pytest tests/"},
    )
    status, _ = MODULE.check_no_major_failures(full=True)
    assert status == NOT_VERIFIED


def test_check_c12_full_killed_is_not_verified(monkeypatch) -> None:
    _stub_collection(monkeypatch)
    monkeypatch.setattr(
        MODULE, "_LAST_FULL_RUN",
        {"rc": 1, "out": "", "xml": None, "tests": None, "failures": None,
         "errors": None, "skipped": None, "command": "pytest tests/"},
    )
    status, _ = MODULE.check_no_major_failures(full=True)
    assert status == NOT_VERIFIED


def test_c12_accepts_the_full_keyword() -> None:
    """main() routes --full to C12; the callable must accept the kwarg."""
    import inspect

    assert "full" in inspect.signature(MODULE.check_no_major_failures).parameters


@pytest.mark.parametrize("cid", ["C3", "C12"])
def test_main_passes_full_to_both_full_aware_conditioners(
    cid: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Pin the main() routing so C12 cannot silently stop honouring --full."""
    seen = {}

    def probe(full=False):
        seen[cid] = full
        return PASS, "probe"

    import contextlib
    import io

    monkeypatch.setattr(MODULE, "SCRIPTS", tmp_path)  # keep the report out of tree
    original = MODULE.CHECKS
    try:
        MODULE.CHECKS = [(cid, "probe", probe)]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                MODULE.main(["--full", "--only", cid])
            except SystemExit:
                pass
    finally:
        MODULE.CHECKS = original
    assert seen.get(cid) is True
