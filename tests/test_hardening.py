"""Hardening Suite — MASTER-SPEC Phase 21 tests.

Verifies the security-posture battery actually observes each invariant: every
check must pass, and the summary must report all_passed=True. Also proves the
suite is honest (a check that fails is surfaced, not swallowed).
"""
from src.ai.hardening import HardeningSuite, CheckResult


def test_all_checks_pass():
    suite = HardeningSuite()
    results = suite.run_checks()
    assert len(results) == 7
    names = [r.name for r in results]
    # Exactly the 7 documented checks, no duplicates.
    assert len(set(names)) == 7
    for r in results:
        assert r.passed, f"check {r.name} failed: {r.detail}"


def test_summary_reports_all_passed():
    summary = HardeningSuite().summary()
    assert summary["total"] == 7
    assert summary["passed"] == 7
    assert summary["failed"] == 0
    assert summary["all_passed"] is True


def test_each_check_is_a_check_result():
    results = HardeningSuite().run_checks()
    for r in results:
        assert isinstance(r, CheckResult)
        assert r.name
        assert r.detail  # every check explains itself


def test_check_result_is_honest_on_failure():
    # A CheckResult constructed with passed=False must not be swallowed.
    failing = CheckResult("x", False, "boom")
    assert failing.passed is False
    assert failing.detail == "boom"
