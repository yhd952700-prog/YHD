"""
ADA Compute Intelligence for LiuHao AI OS (MASTER-SPEC 33-35).

§33 ADA: computational intelligence - Data / SQL / Python / Statistics / Anomaly ...
§34 Compute Engine pipeline: Question -> Data Discovery -> Permission ->
    Query/Code -> Sandbox -> Compute -> Verification -> Result.
§35 Code Execution MUST run inside the Compute Sandbox with CPU/Memory/Timeout limits.

This module reuses the existing sandbox (src.plugins.sandbox.backends) rather
than re-implementing isolation. run_python() executes real code via subprocess -
no simulation, no fake results (CODER-CONTRACT NO FAKE AI).

Capabilities that are not implemented (SQL engine, visualization, ...) are
returned honestly as NOT_IMPLEMENTED and never faked.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict, List, Optional

from src.plugins.sandbox.backends.base import ExecutionResult, ResourceLimits
from src.plugins.sandbox.backends.subprocess_backend import SubprocessBackend
from .observability import observe
from .audit import audited

# Default sandbox limits for ad-hoc python execution.
_DEFAULT_TIMEOUT = 30
_DEFAULT_MEMORY_LIMIT = 128 * 1024 * 1024  # 128 MB


class ComputeEngine:
    """§34 minimal honest Compute Engine.

    Delegates actual code execution to the shared SubprocessBackend sandbox so
    every run is real, isolated and resource-bounded.
    """

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

    def __init__(self, backend: Optional[SubprocessBackend] = None):
        self._backend: SubprocessBackend = backend or SubprocessBackend()

    @observe("ada.compute_engine.run_python")
    @audited("p11.ada.run_python", module="src.ai.ada")
    def run_python(
        self,
        code: str,
        timeout: Optional[int] = None,
        memory_limit: Optional[int] = None,
    ) -> ExecutionResult:
        """§35 Run Python code inside the Compute Sandbox (real subprocess).

        Args:
            code:         source passed to `python -c`
            timeout:      wall-clock seconds before the run is killed
            memory_limit: address-space cap in bytes (enforced on Unix; set
                          regardless so the limit is declared and traceable)
        """
        effective_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
        effective_memory = memory_limit if memory_limit is not None else _DEFAULT_MEMORY_LIMIT

        limits = ResourceLimits(
            execution_time_limit=effective_timeout,
            memory_limit=effective_memory,
            network_access=False,
        )

        return self._backend.execute(
            execution_id=f"ada-py-{uuid.uuid4().hex[:12]}",
            command=[sys.executable, "-c", code],
            resource_limits=limits,
            timeout=effective_timeout,
        )

    @observe("ada.compute_engine.analyze")
    def analyze(self, data: List[float]) -> Dict[str, float]:
        """Real descriptive statistics (pure Python, no third-party needed).

        Returns count/mean/median/std/min/max/sum. std is the sample standard
        deviation (n-1). Empty input returns a zeroed summary with count 0.
        """
        n = len(data)
        if n == 0:
            return {
                "count": 0.0,
                "mean": 0.0,
                "median": 0.0,
                "std": 0.0,
                "min": 0.0,
                "max": 0.0,
                "sum": 0.0,
            }

        total = sum(data)
        mean = total / n

        if n == 1:
            std = 0.0
        else:
            variance = sum((x - mean) ** 2 for x in data) / (n - 1)
            std = variance ** 0.5

        ordered = sorted(data)
        mid = n // 2
        median = (
            ordered[mid]
            if n % 2 == 1
            else (ordered[mid - 1] + ordered[mid]) / 2.0
        )

        return {
            "count": float(n),
            "mean": mean,
            "median": median,
            "std": std,
            "min": min(data),
            "max": max(data),
            "sum": total,
        }

    def detect_anomalies(self, data: List[float], threshold: float = 2.0) -> List[int]:
        """Real z-score anomaly detection.

        Returns the indices of points whose |z| exceeds `threshold`
        (default 2.0 == ~95% for normal data). Needs >= 2 points and a
        non-zero sample std; otherwise no anomalies can be defined.
        """
        n = len(data)
        if n < 2:
            return []

        mean = sum(data) / n
        variance = sum((x - mean) ** 2 for x in data) / (n - 1)
        std = variance ** 0.5
        if std == 0:
            return []

        outliers: List[int] = []
        for i, x in enumerate(data):
            z = abs((x - mean) / std)
            if z > threshold:
                outliers.append(i)
        return outliers

    @observe("ada.compute_engine.run_sql")
    def run_sql(self, query: str, *args: Any, **kwargs: Any) -> ExecutionResult:
        """SQL execution - NOT implemented. Honest failure, no fabrication."""
        return ExecutionResult(
            execution_id=f"ada-sql-{uuid.uuid4().hex[:12]}",
            success=False,
            exit_code=-1,
            error=f"{self.NOT_IMPLEMENTED}: SQL engine not available in this build",
            backend_info={"engine": "ada", "capability": "sql"},
        )

    def visualize(self, data: Any, *args: Any, **kwargs: Any) -> ExecutionResult:
        """Visualization - NOT implemented. Honest failure, no fabrication."""
        return ExecutionResult(
            execution_id=f"ada-viz-{uuid.uuid4().hex[:12]}",
            success=False,
            exit_code=-1,
            error=f"{self.NOT_IMPLEMENTED}: visualization not available in this build",
            backend_info={"engine": "ada", "capability": "visualization"},
        )
