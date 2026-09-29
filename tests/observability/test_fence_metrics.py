"""Prove the UBX-005 executor-fence telemetry is actually emitted (not just
defined) and wired into the readiness + Prometheus endpoints.

Every assertion reads the real Prometheus sample value from the shared
``REGISTRY`` (or the live HTTP endpoints), so a green run means the numbers are
genuinely produced. Run: tests/observability/test_fence_metrics.py
"""

from __future__ import annotations

from src.observability.metrics import REGISTRY
from src.kernels.execution.fence import (
    ExecutorFence,
    ExecutorFenceDenied,
    ExecutorLease,
    ExecutorLeaseView,
    FenceContext,
    StaleExecutorError,
)
from src.distribution.coordination import ExecutorFenceBackendError


def _val(name: str, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


# --------------------------------------------------------------------------- #
# Stub leases that exercise specific failure modes with the REAL exception types
# --------------------------------------------------------------------------- #
class _BackendDownLease(ExecutorLease):
    """Every read/write raises the fail-closed backend error."""

    def current(self, executor_id):
        raise ExecutorFenceBackendError("backend down")

    def validate(self, executor_id, token, capabilities=None):
        raise ExecutorFenceBackendError("backend down")

    def acquire(self, *a, **k):
        raise ExecutorFenceBackendError("backend down")

    def acquire_within(self, *a, **k):
        raise ExecutorFenceBackendError("backend down")

    def is_stale(self, executor_id, token):
        return False

    def heartbeat(self, executor_id, token):
        raise ExecutorFenceBackendError("backend down")

    def renew(self, *a, **k):
        raise ExecutorFenceBackendError("backend down")

    def release(self, *a, **k):
        return True

    def consume_token(self, *a, **k):
        return None

    def force_new_era(self):
        return 1

    def count_active(self, now=None):
        return 0

    def known_executor(self, executor_id):
        return True


class _LivenessLostLease(ExecutorLease):
    """heartbeat() reports liveness lost; everything else is benign."""

    def current(self, executor_id):
        return ExecutorLeaseView(
            executor_id, 1, 0, executor_id, 0.0, 1e9, 1e9, 0, (), None, "held"
        )

    def validate(self, executor_id, token, capabilities=None):
        return True

    def acquire(self, *a, **k):
        return 1

    def acquire_within(self, *a, **k):
        return 1

    def is_stale(self, executor_id, token):
        return False

    def heartbeat(self, executor_id, token):
        raise StaleExecutorError("simulated liveness loss")

    def renew(self, *a, **k):
        return 1

    def release(self, *a, **k):
        return True

    def consume_token(self, *a, **k):
        return None

    def force_new_era(self):
        return 1

    def count_active(self, now=None):
        return 1

    def known_executor(self, executor_id):
        return True


# --------------------------------------------------------------------------- #
# 1. metrics are registered on the shared REGISTRY
# --------------------------------------------------------------------------- #
def test_fence_metrics_registered():
    for name in (
        "executor_fence_denials_total",
        "executor_fence_enforce_seconds",
        "executor_fence_active_leases",
        "executor_fence_epoch",
        "executor_fence_heartbeat_failures_total",
        "executor_fence_backend_errors_total",
        "executor_fence_renew_failures_total",
        "coordinator_admission_contention_total",
    ):
        assert REGISTRY._names_to_collectors.get(name) is not None, name


# --------------------------------------------------------------------------- #
# 2. a denial is recorded with the correct reason label
# --------------------------------------------------------------------------- #
def test_denial_records_reason():
    fence = ExecutorFence(_LivenessLostLease())
    before = _val("executor_fence_denials_total", {"reason": "ExecutorUnknownError"})
    try:
        fence.enforce(None, "act")  # anonymous action -> ExecutorUnknownError
        assert False, "must deny anonymous action"
    except ExecutorFenceDenied:
        pass
    after = _val("executor_fence_denials_total", {"reason": "ExecutorUnknownError"})
    assert after - before == 1.0


# --------------------------------------------------------------------------- #
# 3. enforce() latency is observed (histogram count increments)
# --------------------------------------------------------------------------- #
def test_enforce_latency_observed():
    fence = ExecutorFence(_LivenessLostLease())
    before = _val("executor_fence_enforce_seconds_count")
    try:
        fence.enforce(None, "act")
    except ExecutorFenceDenied:
        pass
    after = _val("executor_fence_enforce_seconds_count")
    assert after - before == 1.0


# --------------------------------------------------------------------------- #
# 4. a successful enforce refreshes the active-leases + epoch gauges
# --------------------------------------------------------------------------- #
def test_success_refreshes_gauges():
    from src.kernels.execution.fence import InMemoryExecutorLease

    fence = ExecutorFence(InMemoryExecutorLease())
    ctx = fence.acquire_for("e1", "owner", ["cap.a"], ttl_sec=30.0)
    fence.enforce(ctx, "act", correlation_id="C1")
    assert _val("executor_fence_active_leases") >= 1.0
    assert _val("executor_fence_epoch") == ctx.epoch


# --------------------------------------------------------------------------- #
# 5. heartbeat failure is recorded
# --------------------------------------------------------------------------- #
def test_heartbeat_failure_recorded():
    fence = ExecutorFence(_LivenessLostLease())
    ctx = FenceContext(executor_id="e1", token=1, epoch=0, granted_capabilities=())
    before = _val("executor_fence_heartbeat_failures_total")
    try:
        fence.heartbeat(ctx)
        assert False, "heartbeat must fail"
    except StaleExecutorError:
        pass
    after = _val("executor_fence_heartbeat_failures_total")
    assert after - before == 1.0


# --------------------------------------------------------------------------- #
# 6. a coordination-backend failure is recorded as both a denial and a backend error
# --------------------------------------------------------------------------- #
def test_backend_error_recorded():
    fence = ExecutorFence(_BackendDownLease())
    ctx = FenceContext(executor_id="e1", token=1, epoch=0, granted_capabilities=())
    deny_before = _val("executor_fence_denials_total", {"reason": "ExecutorFenceBackendError"})
    be_before = _val("executor_fence_backend_errors_total")
    try:
        fence.enforce(ctx, "act")
        assert False, "backend-down must deny"
    except ExecutorFenceBackendError:
        pass
    deny_after = _val("executor_fence_denials_total", {"reason": "ExecutorFenceBackendError"})
    be_after = _val("executor_fence_backend_errors_total")
    assert deny_after - deny_before == 1.0
    assert be_after - be_before == 1.0


# --------------------------------------------------------------------------- #
# 6b. coordinator admission contention is recorded under max_executors pressure
# --------------------------------------------------------------------------- #
def test_coordinator_admission_contention_recorded(tmp_path):
    from src.distribution.coordination import (
        DistributedExecutorLease,
        FileLockLease,
    )

    class _ContendingAdmissionLease(ExecutorLease):
        """Admission lease that refuses the first ``fails`` acquires."""

        def __init__(self, fails):
            self._fails = fails

        def acquire(self, owner, ttl_sec=30.0, granted_capabilities=None, era=None):
            if self._fails > 0:
                self._fails -= 1
                return False, None
            return True, 1

        def acquire_within(self, *a, **k):
            return True, 1

        def validate(self, *a, **k):
            return True

        def is_stale(self, *a, **k):
            return False

        def heartbeat(self, *a, **k):
            return None

        def renew(self, *a, **k):
            return 1

        def release(self, *a, **k):
            return True

        def consume_token(self, *a, **k):
            return None

        def force_new_era(self):
            return 1

        def current(self, *a, **k):
            return None

        def count_active(self, now=None):
            return 0

        def known_executor(self, executor_id):
            return True

    dir_ = str(tmp_path)

    def _factory(name):
        if name == DistributedExecutorLease.ADMISSION_NAME:
            return _ContendingAdmissionLease(fails=3)  # 3 contention events
        return FileLockLease(name, directory=dir_)

    before = _val("coordinator_admission_contention_total")
    dist = DistributedExecutorLease(
        lease_factory=_factory, max_executors=1, admission_ttl=10.0
    )
    token = dist.acquire("e1", "owner", 30.0, ["cap.a"])
    after = _val("coordinator_admission_contention_total")
    assert after - before == 3.0
    assert token is not None


# --------------------------------------------------------------------------- #
# 7. /v1/ready surfaces the executor_fence check
# --------------------------------------------------------------------------- #
def test_ready_exposes_executor_fence():
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        body = client.get("/v1/ready").json()
    assert "executor_fence" in body["checks"], "executor_fence not in /v1/ready"
    ef = body["checks"]["executor_fence"]
    assert ef["status"] == "healthy"
    assert "armed" in ef and "backend" in ef and "denials_total" in ef


# --------------------------------------------------------------------------- #
# 8. /v1/metrics/prometheus exports the executor_fence metrics
# --------------------------------------------------------------------------- #
def test_prometheus_exports_fence_metrics():
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        resp = client.get("/v1/metrics/prometheus")
    assert resp.status_code == 200, resp.text
    text = resp.text
    for name in (
        "executor_fence_denials_total",
        "executor_fence_enforce_seconds",
        "executor_fence_active_leases",
    ):
        assert name in text, f"{name} missing from Prometheus export"
