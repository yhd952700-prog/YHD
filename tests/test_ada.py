"""
Tests for ADA Compute Engine (MASTER-SPEC 33-35).

Asserts REAL behaviour: code is genuinely executed in the sandbox subprocess
(no simulation), statistics are numerically correct, anomaly detection marks
true outliers, timeouts are enforced, and missing capabilities are honestly
reported as NOT_IMPLEMENTED.
"""

import pytest

from src.ai.ada import ComputeEngine


class TestRunPython:
    def test_executes_and_captures_stdout(self):
        result = ComputeEngine().run_python("print(2 + 2)")
        assert result.success is True
        assert result.exit_code == 0
        assert "4" in result.stdout

    def test_executes_real_computation(self):
        code = "total = sum(range(1, 11)); print('sum=' + str(total))"
        result = ComputeEngine().run_python(code)
        assert result.success is True
        assert "sum=55" in result.stdout

    def test_real_syntax_error_is_a_failed_execution(self):
        # a genuinely invalid program must fail - proving execution is real
        result = ComputeEngine().run_python("print(")
        assert result.success is False
        assert result.exit_code != 0

    def test_timeout_enforced_on_infinite_loop(self):
        # an unbounded loop must be killed by the sandbox timeout (success=False)
        result = ComputeEngine().run_python("while True:\n    pass\n", timeout=2)
        assert result.success is False
        # Killed by a signal -> negative exit code. The exact signal is
        # platform-specific (-9 SIGKILL on Linux CI, -1 elsewhere); the
        # contract under test is "terminated abnormally", not a magic number.
        assert result.exit_code < 0


class TestAnalyze:
    def test_descriptive_statistics_correct(self):
        data = [2, 4, 4, 4, 5, 5, 7, 9]
        stats = ComputeEngine().analyze(data)
        assert stats["count"] == 8.0
        assert stats["mean"] == pytest.approx(5.0)
        assert stats["median"] == pytest.approx(4.5)
        assert stats["min"] == pytest.approx(2.0)
        assert stats["max"] == pytest.approx(9.0)
        assert stats["sum"] == pytest.approx(40.0)
        # sample std: var = 32/7 -> std ~ 2.138
        assert stats["std"] == pytest.approx(2.138, abs=1e-2)

    def test_empty_input_returns_zeroed_summary(self):
        stats = ComputeEngine().analyze([])
        assert stats["count"] == 0.0
        assert stats["sum"] == 0.0


class TestDetectAnomalies:
    def test_marks_true_outlier(self):
        data = [1, 1, 1, 1, 1, 1, 1, 100]
        outliers = ComputeEngine().detect_anomalies(data)
        assert outliers == [7]

    def test_clean_series_has_no_outliers(self):
        data = [1, 2, 3, 4, 5]
        assert ComputeEngine().detect_anomalies(data) == []

    def test_threshold_is_respected(self):
        # with a tighter threshold the mild tail point is also flagged
        data = [10, 10, 10, 10, 10, 50]
        assert 5 in ComputeEngine().detect_anomalies(data, threshold=1.0)


class TestNotImplementedCapabilities:
    def test_sql_is_honestly_not_implemented(self):
        result = ComputeEngine().run_sql("SELECT 1")
        assert result.success is False
        assert "NOT_IMPLEMENTED" in (result.error or "")

    def test_visualize_is_honestly_not_implemented(self):
        result = ComputeEngine().visualize([1, 2, 3])
        assert result.success is False
        assert "NOT_IMPLEMENTED" in (result.error or "")
