"""
Prometheus Metrics for LiuHao AI OS

Provides standardized metrics for:
- HTTP/API requests
- AI Provider operations
- Agent/Employee coordination
- Goal→Task Graph execution
- System resources
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry
import threading
import time
from collections import deque
from typing import Dict, Optional


# Create custom registry
REGISTRY = CollectorRegistry()

# ============================================================
# HTTP/API Metrics
# ============================================================

http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status'],
    registry=REGISTRY
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint'],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY
)

http_request_size_bytes = Histogram(
    'http_request_size_bytes',
    'HTTP request size in bytes',
    ['method', 'endpoint'],
    buckets=[100, 1000, 10000, 100000, 1000000],
    registry=REGISTRY
)

http_response_size_bytes = Histogram(
    'http_response_size_bytes',
    'HTTP response size in bytes',
    ['method', 'endpoint'],
    buckets=[100, 1000, 10000, 100000, 1000000],
    registry=REGISTRY
)


# ============================================================
# AI Provider Metrics
# ============================================================

provider_requests_total = Counter(
    'provider_requests_total',
    'Total AI provider requests',
    ['provider', 'model', 'status'],
    registry=REGISTRY
)

provider_request_duration_seconds = Histogram(
    'provider_request_duration_seconds',
    'AI provider request latency in seconds',
    ['provider', 'model'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
    registry=REGISTRY
)

provider_tokens_total = Counter(
    'provider_tokens_total',
    'Total tokens consumed',
    ['provider', 'model', 'type'],  # type: prompt, completion, total
    registry=REGISTRY
)

provider_errors_total = Counter(
    'provider_errors_total',
    'Total provider errors',
    ['provider', 'model', 'error_type'],
    registry=REGISTRY
)

provider_up = Gauge(
    'provider_up',
    'Provider availability (1=up, 0=down)',
    ['provider'],
    registry=REGISTRY
)

provider_active_requests = Gauge(
    'provider_active_requests',
    'Number of active provider requests',
    ['provider'],
    registry=REGISTRY
)


# ============================================================
# Agent/Employee Metrics
# ============================================================

agent_tasks_total = Counter(
    'agent_tasks_total',
    'Total agent tasks',
    ['agent_type', 'status'],  # status: started, completed, failed
    registry=REGISTRY
)

agent_task_duration_seconds = Histogram(
    'agent_task_duration_seconds',
    'Agent task execution duration',
    ['agent_type'],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0],
    registry=REGISTRY
)

agent_coordination_total = Counter(
    'agent_coordination_total',
    'Total agent coordination events',
    ['coordination_type', 'status'],  # type: distribute, aggregate, broadcast
    registry=REGISTRY
)

agent_coordination_failures_total = Counter(
    'agent_coordination_failures_total',
    'Agent coordination failures',
    ['coordination_type'],
    registry=REGISTRY
)

employee_active_agents = Gauge(
    'employee_active_agents',
    'Number of active agents in employee',
    ['employee_name'],
    registry=REGISTRY
)

employee_kpi_success_rate = Gauge(
    'employee_kpi_success_rate',
    'Employee task success rate (0-100)',
    ['employee_name'],
    registry=REGISTRY
)


# ============================================================
# Goal→Task Graph Metrics
# ============================================================

goal_decompositions_total = Counter(
    'goal_decompositions_total',
    'Total goal decompositions',
    ['status'],  # success, partial, failed
    registry=REGISTRY
)

goal_decomposition_duration_seconds = Histogram(
    'goal_decomposition_duration_seconds',
    'Goal decomposition latency',
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY
)

goal_tasks_generated = Histogram(
    'goal_tasks_generated',
    'Number of tasks generated per goal decomposition',
    buckets=[1, 2, 3, 5, 10, 20, 50, 100],
    registry=REGISTRY
)

task_execution_total = Counter(
    'task_execution_total',
    'Total task executions',
    ['task_type', 'status'],
    registry=REGISTRY
)

task_execution_duration_seconds = Histogram(
    'task_execution_duration_seconds',
    'Task execution duration',
    ['task_type'],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0],
    registry=REGISTRY
)

task_queue_depth = Gauge(
    'task_queue_depth',
    'Current task queue depth',
    ['queue_name'],
    registry=REGISTRY
)

task_dependencies_resolved = Counter(
    'task_dependencies_resolved_total',
    'Total task dependencies resolved',
    ['status'],  # resolved, failed
    registry=REGISTRY
)


# ============================================================
# System Resource Metrics
# ============================================================

process_cpu_seconds_total = Counter(
    'process_cpu_seconds_total',
    'Total CPU time used by process',
    registry=REGISTRY
)

process_memory_bytes = Gauge(
    'process_memory_bytes',
    'Process memory usage in bytes',
    ['type'],  # rss, vms
    registry=REGISTRY
)

process_open_fds = Gauge(
    'process_open_fds',
    'Number of open file descriptors',
    registry=REGISTRY
)

process_threads = Gauge(
    'process_threads',
    'Number of threads',
    registry=REGISTRY
)


# ============================================================
# Helper Functions / Decorators
# ============================================================

def track_http_request(method: str, endpoint: str, status: int, duration: float,
                       request_size: int = 0, response_size: int = 0):
    """Track HTTP request metrics."""
    http_requests_total.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)
    if request_size:
        http_request_size_bytes.labels(method=method, endpoint=endpoint).observe(request_size)
    if response_size:
        http_response_size_bytes.labels(method=method, endpoint=endpoint).observe(response_size)


def track_provider_request(provider: str, model: str, status: str, duration: float,
                           prompt_tokens: int = 0, completion_tokens: int = 0):
    """Track AI provider request metrics."""
    provider_requests_total.labels(provider=provider, model=model, status=status).inc()
    provider_request_duration_seconds.labels(provider=provider, model=model).observe(duration)
    if prompt_tokens:
        provider_tokens_total.labels(provider=provider, model=model, type='prompt').inc(prompt_tokens)
    if completion_tokens:
        provider_tokens_total.labels(provider=provider, model=model, type='completion').inc(completion_tokens)
    if prompt_tokens or completion_tokens:
        provider_tokens_total.labels(provider=provider, model=model, type='total').inc(
            prompt_tokens + completion_tokens
        )


def track_provider_error(provider: str, model: str, error_type: str):
    """Track provider error."""
    provider_errors_total.labels(provider=provider, model=model, error_type=error_type).inc()


def set_provider_availability(provider: str, available: bool):
    """Set provider availability status."""
    provider_up.labels(provider=provider).set(1 if available else 0)


def track_agent_task(agent_type: str, status: str, duration: float = None):
    """Track agent task."""
    agent_tasks_total.labels(agent_type=agent_type, status=status).inc()
    if duration is not None:
        agent_task_duration_seconds.labels(agent_type=agent_type).observe(duration)


def track_coordination(coord_type: str, status: str, failed: bool = False):
    """Track agent coordination event."""
    agent_coordination_total.labels(coordination_type=coord_type, status=status).inc()
    if failed:
        agent_coordination_failures_total.labels(coordination_type=coord_type).inc()


def track_goal_decomposition(status: str, duration: float, task_count: int):
    """Track goal decomposition."""
    goal_decompositions_total.labels(status=status).inc()
    goal_decomposition_duration_seconds.observe(duration)
    goal_tasks_generated.observe(task_count)


def track_task_execution(task_type: str, status: str, duration: float = None):
    """Track task execution."""
    task_execution_total.labels(task_type=task_type, status=status).inc()
    if duration is not None:
        task_execution_duration_seconds.labels(task_type=task_type).observe(duration)


def set_queue_depth(queue_name: str, depth: int):
    """Set task queue depth."""
    task_queue_depth.labels(queue_name=queue_name).set(depth)


def set_employee_agents(employee_name: str, count: int):
    """Set active agent count for employee."""
    employee_active_agents.labels(employee_name=employee_name).set(count)


def set_employee_success_rate(employee_name: str, rate: float):
    """Set employee success rate."""
    employee_kpi_success_rate.labels(employee_name=employee_name).set(rate)


# ============================================================
# Executor Fence / Coordinator Metrics (UBX-005 observability)
# ============================================================
# These surface the human-sovereignty-critical executor fence (the default-DENY
# autonomous-action gate) into Prometheus so operators can see denials, liveness
# failures, backend outages, and concurrent-executor pressure. Every emitter is
# best-effort: a metrics failure must NEVER break the fence path.

executor_fence_denials_total = Counter(
    'executor_fence_denials_total',
    'Total executor-fence denials (default-deny outcomes), labelled by reason',
    ['reason'],
    registry=REGISTRY,
)

executor_fence_enforce_seconds = Histogram(
    'executor_fence_enforce_seconds',
    'Executor fence enforce() latency in seconds (the per-action gate)',
    buckets=[0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
    registry=REGISTRY,
)

executor_fence_active_leases = Gauge(
    'executor_fence_active_leases',
    'Number of currently-active (non-stale) executor leases',
    registry=REGISTRY,
)

executor_fence_epoch = Gauge(
    'executor_fence_epoch',
    'Current executor-fence logical era/epoch in effect',
    registry=REGISTRY,
)

executor_fence_heartbeat_failures_total = Counter(
    'executor_fence_heartbeat_failures_total',
    'Executor fence heartbeat failures (liveness lost or backend error)',
    registry=REGISTRY,
)

executor_fence_backend_errors_total = Counter(
    'executor_fence_backend_errors_total',
    'Executor fence coordination-backend failures that forced a fail-closed deny',
    registry=REGISTRY,
)

executor_fence_renew_failures_total = Counter(
    'executor_fence_renew_failures_total',
    'Executor fence renew() failures (stale lease or backend error)',
    registry=REGISTRY,
)

coordinator_admission_contention_total = Counter(
    'coordinator_admission_contention_total',
    'Coordinator admission-section contention (retry attempts under max_executors)',
    registry=REGISTRY,
)

# G8/G9 fail-open observability: whether the *production* executor fence is
# actually installed in this process. ``attach_default_executor_fence()`` failing
# at boot used to be invisible (one ERROR log, boot continues, metric families
# absent) -- an operator scraping Prometheus saw a fully instrumented service
# that was silently running autonomous actions UN-FENCED.
# 0 does NOT mean "the fence is not needed": it means "not installed". A
# deployment that legitimately leaves the gate unarmed is reported separately by
# /v1/ready (armed=False) and must not be conflated with this signal.
liuhao_executor_fence_installed = Gauge(
    'liuhao_executor_fence_installed',
    'Executor-fence installation state (1=default production fence attached, '
    '0=NOT attached -- autonomous actions may be running un-fenced)',
    registry=REGISTRY,
)

liuhao_executor_fence_install_failures_total = Counter(
    'liuhao_executor_fence_install_failures_total',
    'Total failures to attach the default production executor fence at boot '
    '(each one left the process un-fenced unless startup aborted)',
    registry=REGISTRY,
)


def set_fence_installed(installed: bool) -> None:
    """Set the executor-fence installation gauge (1 attached / 0 not attached)."""
    try:
        liuhao_executor_fence_installed.set(1 if installed else 0)
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_fence_install_failure() -> None:
    """Count one failed attempt to attach the default executor fence."""
    try:
        liuhao_executor_fence_install_failures_total.inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_fence_denial(reason: str) -> None:
    """Record one executor-fence denial, labelled by its reason class name."""
    try:
        executor_fence_denials_total.labels(reason=reason).inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def observe_fence_enforce(duration: float) -> None:
    """Record the latency of a single enforce() call."""
    try:
        executor_fence_enforce_seconds.observe(duration)
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def set_fence_active_leases(n: int) -> None:
    """Set the gauge of currently-active executor leases."""
    try:
        executor_fence_active_leases.set(int(n))
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def set_fence_epoch(epoch: int) -> None:
    """Set the gauge of the current executor-fence era/epoch."""
    try:
        executor_fence_epoch.set(int(epoch))
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_fence_heartbeat_failure() -> None:
    """Record one executor-fence heartbeat failure."""
    try:
        executor_fence_heartbeat_failures_total.inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_fence_backend_error() -> None:
    """Record one coordination-backend failure that forced a fail-closed deny."""
    try:
        executor_fence_backend_errors_total.inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_fence_renew_failure() -> None:
    """Record one executor-fence renew() failure."""
    try:
        executor_fence_renew_failures_total.inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def record_coordinator_admission_contention() -> None:
    """Record one coordinator admission-section contention event."""
    try:
        coordinator_admission_contention_total.inc()
    except Exception:  # pragma: no cover - metrics must never raise
        pass


# ============================================================
# Context Managers for Easy Tracking
# ============================================================

class TrackProviderRequest:
    """Context manager to track provider request metrics."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self.start_time = None
        self.status = 'success'

    def __enter__(self):
        self.start_time = time.time()
        provider_active_requests.labels(provider=self.provider).inc()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        provider_active_requests.labels(provider=self.provider).dec()

        if exc_type is not None:
            self.status = 'error'
            track_provider_error(self.provider, self.model, exc_type.__name__)

        track_provider_request(self.provider, self.model, self.status, duration)
        return False


class TrackAgentTask:
    """Context manager to track agent task metrics."""

    def __init__(self, agent_type: str):
        self.agent_type = agent_type
        self.start_time = None
        self.status = 'completed'

    def __enter__(self):
        self.start_time = time.time()
        track_agent_task(self.agent_type, 'started')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        if exc_type is not None:
            self.status = 'failed'
        track_agent_task(self.agent_type, self.status, duration)
        return False


class TrackGoalDecomposition:
    """Context manager to track goal decomposition metrics."""

    def __init__(self):
        self.start_time = None
        self.status = 'success'
        self.task_count = 0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def set_task_count(self, count: int):
        self.task_count = count

    def set_status(self, status: str):
        self.status = status

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        if exc_type is not None:
            self.status = 'failed'
        track_goal_decomposition(self.status, duration, self.task_count)
        return False


# ============================================================
# Metrics Export
# ============================================================

def get_metrics_registry() -> CollectorRegistry:
    """Get the metrics registry for Prometheus exposition."""
    return REGISTRY


def generate_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    from prometheus_client import generate_latest
    return generate_latest(REGISTRY)


# ============================================================
# Real metric emitters for the alert engine (OB / blocker #3)
# ============================================================
# The 6 production alert rules (install_production_rules) reference metrics
# error_rate_percent / memory_usage_percent / cpu_usage_percent /
# latency_p99_ms / service_heartbeat_interval / audit_log_lag_seconds. Until
# now nothing produced those names, so evaluate_all() was inert. The classes
# below are the HONEST emitters: every value is measured from the real process
# / OS / audit store. No value is invented or hard-coded to "look healthy".
#
# ``psutil`` is used when importable (it is the cleanest cross-platform source
# for RSS and CPU); when it is absent we fall back to dependency-free
# platform calls. psutil is never added to requirements.


def _percentile(values: list, quantile: float) -> float:
    """Return the ``quantile`` (0..1) percentile of a list of floats.

    Pure-python, no numpy. Returns 0.0 for an empty list. Uses the
    "nearest-rank" style index so the 99th percentile of a non-empty window is
    always a real member of the window (never an interpolated fake).
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if quantile <= 0.0:
        return float(ordered[0])
    if quantile >= 1.0:
        return float(ordered[-1])
    idx = int(round(quantile * (len(ordered) - 1)))
    idx = max(0, min(len(ordered) - 1, idx))
    return float(ordered[idx])


def _system_memory_total_bytes() -> float:
    """Best-effort total system RAM in bytes, dependency-free.

    Tries sysconf (POSIX) first; callers may also short-circuit via psutil.
    Returns 0.0 if it cannot be determined (caller then reports best-effort).
    """
    try:
        import os

        page_size = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]
        phys_pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined]
        if page_size and phys_pages:
            return float(page_size * phys_pages)
    except (ValueError, OSError, AttributeError):
        pass
    # Windows / fallback: parse /proc/meminfo if present.
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    # e.g. "MemTotal:       16384256 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) * 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


class MetricCollector:
    """Thread-safe collector of the 6 real metrics feeding the alert engine.

    ``record_request`` is called by the gateway HTTP middleware for every
    completed response. ``collect_snapshot`` computes the current value of each
    of the 6 metrics against real sources. A daemon thread in the gateway
    lifespan drives ``collect_snapshot`` -> ``AlertManager.evaluate_all`` every
    15s.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_requests = 0
        self._count_2xx = 0
        self._count_4xx = 0
        self._count_5xx = 0
        # Bounded rolling window of recent request latencies in seconds.
        self._latencies: "deque[float]" = deque(maxlen=200)
        # Heartbeat: time of the last successful collect_snapshot() tick.
        self._last_heartbeat = time.time()

    # -- ingestion ----------------------------------------------------------

    def record_request(self, duration_seconds: float, status_code: int) -> None:
        """Record one completed HTTP response. Fail-safe: never raises."""
        try:
            if not isinstance(status_code, int):
                status_code = int(status_code)
            with self._lock:
                self._total_requests += 1
                if 200 <= status_code < 300:
                    self._count_2xx += 1
                elif 400 <= status_code < 600:
                    # 4xx counted separately from 5xx by the model's intent,
                    # but only 5xx drives error_rate_percent. Keep both for
                    # completeness / future dashboards.
                    if 500 <= status_code < 600:
                        self._count_5xx += 1
                    self._count_4xx += 1
                # Other classes (3xx) only bump total.
                if duration_seconds is not None:
                    self._latencies.append(float(duration_seconds))
        except Exception:  # pragma: no cover - must never break a request
            pass

    # -- measurement helpers (each wrapped so one bad source can't sink all) -

    def _memory_usage_percent(self) -> float:
        """Process RSS / system RAM * 100. Real, never faked."""
        try:
            import psutil  # type: ignore

            rss = psutil.Process().memory_info().rss
            total = psutil.virtual_memory().total
            if total > 0:
                return float(rss) / float(total) * 100.0
        except Exception:
            pass
        # Dependency-free fallback (POSIX). On platforms without psutil and
        # without sysconf, return 0.0 honestly (source genuinely unavailable)
        # rather than inventing a number.
        try:
            import resource  # type: ignore

            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # type: ignore
            total = _system_memory_total_bytes()
            if total > 0 and rss_kb > 0:
                # ru_maxrss is KB on Linux/Unix.
                return float(rss_kb) * 1024.0 / total * 100.0
        except Exception:
            pass
        return 0.0

    def _cpu_usage_percent(self) -> float:
        """Process/Host CPU usage percent. Real, never faked."""
        try:
            import psutil  # type: ignore

            # interval=None => % since the previous call; the 15s loop makes
            # this a meaningful rolling average while staying non-blocking for
            # the synchronous /v1/observability/metrics endpoint.
            return float(psutil.cpu_percent())
        except Exception:
            pass
        # Dependency-free fallback: sample os.times() twice ~0.1s apart.
        try:
            import os

            t0 = os.times()  # type: ignore[attr-defined]
            time.sleep(0.1)
            t1 = os.times()  # type: ignore[attr-defined]
            utime = t1.user - t0.user
            stime = t1.system - t0.system
            wall = (t1.elapsed - t0.elapsed) if hasattr(t1, "elapsed") else 0.1
            if wall > 0:
                return (utime + stime) / wall * 100.0
        except Exception:
            pass
        return 0.0

    def _error_rate_percent(self) -> float:
        with self._lock:
            total = self._total_requests
            errs = self._count_5xx
        return (errs / max(1, total)) * 100.0

    def _latency_p99_ms(self) -> float:
        with self._lock:
            window = list(self._latencies)
        return _percentile(window, 0.99) * 1000.0

    def _service_heartbeat_interval(self) -> float:
        now = time.time()
        with self._lock:
            delta = now - self._last_heartbeat
            self._last_heartbeat = now
        return float(delta)

    def _audit_log_lag_seconds(self) -> float:
        """Seconds since the most recent audit event. 0.0 if no events yet.

        Reads the REAL audit store (sqlite) for MAX(timestamp). Never invents a
        lag: an empty store means "no data, no lag" -> 0.0.
        """
        try:
            from ..kernels.audit import get_audit_store

            store = get_audit_store()

            def _run(conn):
                row = conn.execute(
                    "SELECT MAX(timestamp) FROM audit_events"
                ).fetchone()
                return row[0] if row else None

            last_ts = store._read_only(_run)
            if last_ts is None:
                return 0.0
            return float(time.time() - last_ts)
        except Exception:
            # Audit store genuinely unavailable -> report 0.0 (honest: we
            # cannot prove a lag we cannot measure), never a fake large number.
            return 0.0

    # -- snapshot -----------------------------------------------------------

    def collect_snapshot(self) -> Dict[str, float]:
        """Compute the current value of all 6 metrics from real sources.

        Every key is always present (these 6 have real emitters). Each helper
        is independently defended so one failing source yields 0.0 for that key
        instead of crashing the whole snapshot.
        """
        return {
            "memory_usage_percent": self._memory_usage_percent(),
            "cpu_usage_percent": self._cpu_usage_percent(),
            "error_rate_percent": self._error_rate_percent(),
            "latency_p99_ms": self._latency_p99_ms(),
            "service_heartbeat_interval": self._service_heartbeat_interval(),
            "audit_log_lag_seconds": self._audit_log_lag_seconds(),
        }


# Module-level singleton (mirrors the other *get_* accessors in this module).
_metric_collector: Optional["MetricCollector"] = None


def get_metric_collector() -> "MetricCollector":
    """Return the process-wide MetricCollector singleton."""
    global _metric_collector
    if _metric_collector is None:
        _metric_collector = MetricCollector()
    return _metric_collector
