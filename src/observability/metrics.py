"""
Prometheus Metrics for LiuHao AI OS

Provides standardized metrics for:
- HTTP/API requests
- AI Provider operations
- Agent/Employee coordination
- Goal→Task Graph execution
- System resources
"""

from prometheus_client import Counter, Histogram, Gauge, Summary, CollectorRegistry
from typing import Optional
import time
from functools import wraps


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
