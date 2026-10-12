"""Load Balancer for Model Gateway.

Load balancing features:
- Round-robin load balancing
- Least-connections load balancing
- Weighted load balancing
- Health checking of model endpoints
- Automatic failover
- Connection pooling
- Circuit breaker per model endpoint
- Latency-aware routing
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4


class LoadBalancingStrategy(str, Enum):
    """Load balancing strategies."""

    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    WEIGHTED = "weighted"
    LEAST_LATENCY = "least_latency"
    RANDOM = "random"
    IP_HASH = "ip_hash"


class EndpointHealth(str, Enum):
    """Endpoint health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CircuitBreakerState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ModelEndpoint:
    """Represents a model endpoint instance.

    Attributes:
        endpoint_id: Unique endpoint identifier
        model_id: Model identifier this endpoint serves
        url: Endpoint URL
        provider: Provider name
        region: Deployment region
        weight: Weight for weighted load balancing
        max_connections: Maximum concurrent connections
        current_connections: Current active connections
        total_requests: Total requests served
        total_failures: Total failed requests
        avg_latency_ms: Rolling average latency
        health: Current health status
        last_health_check: Timestamp of last health check
        enabled: Whether the endpoint is enabled
        metadata: Additional endpoint metadata
    """

    endpoint_id: str = field(default_factory=lambda: str(uuid4()))
    model_id: str = ""
    url: str = ""
    provider: str = ""
    region: str = "default"
    weight: int = 100
    max_connections: int = 100
    current_connections: int = 0
    total_requests: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    health: EndpointHealth = EndpointHealth.UNKNOWN
    last_health_check: float = 0.0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        """Failure rate (0-1)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests

    @property
    def utilization(self) -> float:
        """Connection utilization (0-1)."""
        if self.max_connections == 0:
            return 0.0
        return self.current_connections / self.max_connections

    @property
    def is_available(self) -> bool:
        """Whether the endpoint is available to serve requests."""
        return (
            self.enabled
            and self.health
            in (EndpointHealth.HEALTHY, EndpointHealth.DEGRADED)
            and self.current_connections < self.max_connections
        )


@dataclass
class ConnectionPool:
    """Connection pool for a model endpoint.

    Attributes:
        endpoint_id: Endpoint identifier
        pool_size: Maximum pool size
        active_connections: Number of active connections
        idle_connections: Number of idle connections
        total_created: Total connections ever created
        total_reused: Total connections reused
        last_used: Timestamp of last use
    """

    endpoint_id: str
    pool_size: int = 20
    active_connections: int = 0
    idle_connections: int = 0
    total_created: int = 0
    total_reused: int = 0
    last_used: float = 0.0

    @property
    def available(self) -> int:
        """Number of available connections."""
        return self.idle_connections + (
            self.pool_size - self.active_connections - self.idle_connections
        )

    @property
    def utilization_rate(self) -> float:
        """Pool utilization rate (0-1)."""
        if self.pool_size == 0:
            return 0.0
        return self.active_connections / self.pool_size

    def acquire(self) -> bool:
        """Acquire a connection from the pool.

        Returns:
            True if connection was acquired, False if pool is full
        """
        if self.active_connections >= self.pool_size:
            return False

        if self.idle_connections > 0:
            self.idle_connections -= 1
            self.total_reused += 1
        else:
            self.total_created += 1

        self.active_connections += 1
        self.last_used = time.time()
        return True

    def release(self) -> None:
        """Release a connection back to the pool."""
        if self.active_connections > 0:
            self.active_connections -= 1
            self.idle_connections = min(
                self.idle_connections + 1, self.pool_size
            )


@dataclass
class HealthCheckConfig:
    """Configuration for health checking.

    Attributes:
        enabled: Whether health checking is enabled
        interval_seconds: Time between health checks
        timeout_seconds: Health check timeout
        failure_threshold: Consecutive failures to mark unhealthy
        success_threshold: Consecutive successes to mark healthy
        check_fn: Custom health check function
    """

    enabled: bool = True
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    failure_threshold: int = 3
    success_threshold: int = 2
    check_fn: Optional[Callable[[ModelEndpoint], bool]] = None


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration.

    Attributes:
        enabled: Whether circuit breaker is enabled
        failure_threshold: Number of failures to open circuit
        success_threshold: Successes to close from half-open
        reset_timeout_seconds: Time before trying again
        half_open_limit: Max requests in half-open state
    """

    enabled: bool = True
    failure_threshold: int = 5
    success_threshold: int = 3
    reset_timeout_seconds: float = 30.0
    half_open_limit: int = 3


class EndpointCircuitBreaker:
    """Circuit breaker for a model endpoint.

    Implements the circuit breaker pattern to prevent cascading failures.
    """

    def __init__(
        self,
        endpoint_id: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            endpoint_id: Endpoint identifier
            config: Circuit breaker configuration
        """
        self.endpoint_id = endpoint_id
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0
        self.half_open_requests = 0

    def allow_request(self) -> bool:
        """Check if a request is allowed through.

        Returns:
            True if request is allowed, False if circuit is open
        """
        if not self.config.enabled:
            return True

        if self.state == CircuitBreakerState.CLOSED:
            return True

        if self.state == CircuitBreakerState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.config.reset_timeout_seconds:
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                self.half_open_requests = 0
                return True
            return False

        # HALF_OPEN
        if self.half_open_requests < self.config.half_open_limit:
            self.half_open_requests += 1
            return True
        return False

    def record_success(self) -> None:
        """Record a successful request."""
        self.last_success_time = time.time()

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self.half_open_requests = 0
        elif self.state == CircuitBreakerState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            self.half_open_requests = 0
        elif self.failure_count >= self.config.failure_threshold:
            self.state = CircuitBreakerState.OPEN

    def get_state(self) -> Dict[str, Any]:
        """Get circuit breaker state information.

        Returns:
            State dictionary
        """
        return {
            "endpoint_id": self.endpoint_id,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class ModelLoadBalancer:
    """Load balancer for model endpoints.

    Supports multiple load balancing strategies, health checking,
    connection pooling, and circuit breakers.

    Usage:
        lb = ModelLoadBalancer(strategy=LoadBalancingStrategy.LEAST_CONNECTIONS)
        lb.add_endpoint(ModelEndpoint(model_id="gpt-4", url="..."))
        endpoint = lb.select_endpoint("gpt-4")
    """

    def __init__(
        self,
        strategy: LoadBalancingStrategy = LoadBalancingStrategy.ROUND_ROBIN,
        health_check_config: Optional[HealthCheckConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        """Initialize the load balancer.

        Args:
            strategy: Default load balancing strategy
            health_check_config: Health check configuration
            circuit_breaker_config: Circuit breaker configuration
        """
        self._default_strategy = strategy
        self._endpoints: Dict[str, ModelEndpoint] = {}
        self._model_endpoints: Dict[str, List[str]] = {}
        self._pools: Dict[str, ConnectionPool] = {}
        self._circuit_breakers: Dict[str, EndpointCircuitBreaker] = {}
        self._health_config = health_check_config or HealthCheckConfig()
        self._cb_config = circuit_breaker_config or CircuitBreakerConfig()
        self._round_robin_counters: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._latency_history: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # Endpoint management
    # ------------------------------------------------------------------

    def add_endpoint(self, endpoint: ModelEndpoint) -> None:
        """Add a model endpoint to the load balancer.

        Args:
            endpoint: Endpoint to add
        """
        with self._lock:
            self._endpoints[endpoint.endpoint_id] = endpoint

            if endpoint.model_id not in self._model_endpoints:
                self._model_endpoints[endpoint.model_id] = []
            if endpoint.endpoint_id not in self._model_endpoints[endpoint.model_id]:
                self._model_endpoints[endpoint.model_id].append(endpoint.endpoint_id)

            if endpoint.endpoint_id not in self._pools:
                self._pools[endpoint.endpoint_id] = ConnectionPool(
                    endpoint_id=endpoint.endpoint_id,
                    pool_size=endpoint.max_connections,
                )

            if endpoint.endpoint_id not in self._circuit_breakers:
                self._circuit_breakers[endpoint.endpoint_id] = (
                    EndpointCircuitBreaker(
                        endpoint_id=endpoint.endpoint_id,
                        config=self._cb_config,
                    )
                )

            if endpoint.model_id not in self._round_robin_counters:
                self._round_robin_counters[endpoint.model_id] = 0

    def remove_endpoint(self, endpoint_id: str) -> None:
        """Remove a model endpoint.

        Args:
            endpoint_id: ID of endpoint to remove
        """
        with self._lock:
            endpoint = self._endpoints.pop(endpoint_id, None)
            if endpoint:
                model_id = endpoint.model_id
                if model_id in self._model_endpoints:
                    self._model_endpoints[model_id] = [
                        eid for eid in self._model_endpoints[model_id]
                        if eid != endpoint_id
                    ]
            self._pools.pop(endpoint_id, None)
            self._circuit_breakers.pop(endpoint_id, None)

    def get_endpoint(self, endpoint_id: str) -> Optional[ModelEndpoint]:
        """Get an endpoint by ID.

        Args:
            endpoint_id: Endpoint identifier

        Returns:
            Endpoint if found, None otherwise
        """
        return self._endpoints.get(endpoint_id)

    def list_endpoints(
        self, model_id: Optional[str] = None
    ) -> List[ModelEndpoint]:
        """List all endpoints, optionally filtered by model.

        Args:
            model_id: Filter by model ID

        Returns:
            List of endpoints
        """
        if model_id:
            return [
                self._endpoints[eid]
                for eid in self._model_endpoints.get(model_id, [])
                if eid in self._endpoints
            ]
        return list(self._endpoints.values())

    # ------------------------------------------------------------------
    # Endpoint selection
    # ------------------------------------------------------------------

    def select_endpoint(
        self,
        model_id: str,
        *,
        strategy: Optional[LoadBalancingStrategy] = None,
        request_id: Optional[str] = None,
    ) -> Optional[ModelEndpoint]:
        """Select an endpoint for a model using the configured strategy.

        Args:
            model_id: Model identifier
            strategy: Override default strategy
            request_id: Request ID for IP hash strategy

        Returns:
            Selected endpoint, or None if no available endpoints
        """
        effective_strategy = strategy or self._default_strategy
        available = self._get_available_endpoints(model_id)

        if not available:
            return None

        if effective_strategy == LoadBalancingStrategy.ROUND_ROBIN:
            return self._select_round_robin(model_id, available)
        elif effective_strategy == LoadBalancingStrategy.LEAST_CONNECTIONS:
            return self._select_least_connections(available)
        elif effective_strategy == LoadBalancingStrategy.WEIGHTED:
            return self._select_weighted(available)
        elif effective_strategy == LoadBalancingStrategy.LEAST_LATENCY:
            return self._select_least_latency(available)
        elif effective_strategy == LoadBalancingStrategy.RANDOM:
            return self._select_random(available)
        elif effective_strategy == LoadBalancingStrategy.IP_HASH:
            return self._select_ip_hash(model_id, available, request_id or "")
        else:
            return self._select_round_robin(model_id, available)

    def _get_available_endpoints(
        self, model_id: str
    ) -> List[ModelEndpoint]:
        """Get list of available endpoints for a model."""
        endpoint_ids = self._model_endpoints.get(model_id, [])
        available: List[ModelEndpoint] = []

        for eid in endpoint_ids:
            endpoint = self._endpoints.get(eid)
            if not endpoint or not endpoint.is_available:
                continue
            cb = self._circuit_breakers.get(eid)
            if cb and not cb.allow_request():
                continue
            available.append(endpoint)

        return available

    def _select_round_robin(
        self, model_id: str, available: List[ModelEndpoint]
    ) -> ModelEndpoint:
        """Select using round-robin."""
        with self._lock:
            idx = self._round_robin_counters.get(model_id, 0) % len(available)
            self._round_robin_counters[model_id] = (
                self._round_robin_counters.get(model_id, 0) + 1
            )
        return available[idx]

    def _select_least_connections(
        self, available: List[ModelEndpoint]
    ) -> ModelEndpoint:
        """Select using least connections."""
        return min(available, key=lambda e: e.current_connections)

    def _select_weighted(
        self, available: List[ModelEndpoint]
    ) -> ModelEndpoint:
        """Select using weighted random."""
        import random

        total_weight = sum(e.weight for e in available)
        if total_weight == 0:
            return available[0]

        pick = random.uniform(0, total_weight)
        current = 0.0
        for endpoint in available:
            current += endpoint.weight
            if current >= pick:
                return endpoint
        return available[-1]

    def _select_least_latency(
        self, available: List[ModelEndpoint]
    ) -> ModelEndpoint:
        """Select using least average latency."""
        return min(
            available,
            key=lambda e: e.avg_latency_ms if e.avg_latency_ms > 0 else float("inf"),
        )

    def _select_random(
        self, available: List[ModelEndpoint]
    ) -> ModelEndpoint:
        """Select randomly."""
        import random

        return random.choice(available)

    def _select_ip_hash(
        self, model_id: str, available: List[ModelEndpoint], key: str
    ) -> ModelEndpoint:
        """Select using consistent hashing."""
        import hashlib

        hash_value = int(
            hashlib.md5(f"{model_id}:{key}".encode()).hexdigest(), 16
        )
        idx = hash_value % len(available)
        return available[idx]

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def begin_request(self, endpoint_id: str) -> bool:
        """Begin a request on an endpoint.

        Args:
            endpoint_id: Endpoint identifier

        Returns:
            True if request was accepted, False otherwise
        """
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return False

        pool = self._pools.get(endpoint_id)
        if pool and not pool.acquire():
            return False

        with self._lock:
            endpoint.current_connections += 1
            endpoint.total_requests += 1

        return True

    def end_request(
        self,
        endpoint_id: str,
        *,
        success: bool = True,
        latency_ms: float = 0.0,
    ) -> None:
        """End a request on an endpoint.

        Args:
            endpoint_id: Endpoint identifier
            success: Whether the request succeeded
            latency_ms: Request latency in milliseconds
        """
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return

        with self._lock:
            endpoint.current_connections = max(0, endpoint.current_connections - 1)

            if not success:
                endpoint.total_failures += 1

            # Update rolling average latency
            if latency_ms > 0:
                if endpoint.avg_latency_ms == 0:
                    endpoint.avg_latency_ms = latency_ms
                else:
                    alpha = 0.1
                    endpoint.avg_latency_ms = (
                        alpha * latency_ms
                        + (1 - alpha) * endpoint.avg_latency_ms
                    )

                # Track latency history
                if endpoint_id not in self._latency_history:
                    self._latency_history[endpoint_id] = []
                self._latency_history[endpoint_id].append(latency_ms)
                if len(self._latency_history[endpoint_id]) > 100:
                    self._latency_history[endpoint_id] = self._latency_history[
                        endpoint_id
                    ][-100:]

        # Update circuit breaker
        cb = self._circuit_breakers.get(endpoint_id)
        if cb:
            if success:
                cb.record_success()
            else:
                cb.record_failure()

        # Release connection
        pool = self._pools.get(endpoint_id)
        if pool:
            pool.release()

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    def run_health_check(self, endpoint_id: str) -> EndpointHealth:
        """Run a health check on an endpoint.

        Args:
            endpoint_id: Endpoint identifier

        Returns:
            Health status after check
        """
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return EndpointHealth.UNKNOWN

        endpoint.last_health_check = time.time()

        if self._health_config.check_fn:
            try:
                healthy = self._health_config.check_fn(endpoint)
                if healthy:
                    endpoint.health = EndpointHealth.HEALTHY
                else:
                    endpoint.health = EndpointHealth.UNHEALTHY
            except Exception:
                endpoint.health = EndpointHealth.UNHEALTHY
        else:
            # Default: based on failure rate
            if endpoint.failure_rate > 0.5:
                endpoint.health = EndpointHealth.UNHEALTHY
            elif endpoint.failure_rate > 0.1:
                endpoint.health = EndpointHealth.DEGRADED
            else:
                endpoint.health = EndpointHealth.HEALTHY

        return endpoint.health

    def run_all_health_checks(self) -> Dict[str, EndpointHealth]:
        """Run health checks on all endpoints.

        Returns:
            Dictionary of endpoint_id -> health status
        """
        results: Dict[str, EndpointHealth] = {}
        for endpoint_id in list(self._endpoints.keys()):
            results[endpoint_id] = self.run_health_check(endpoint_id)
        return results

    # ------------------------------------------------------------------
    # Failover
    # ------------------------------------------------------------------

    def get_failover_endpoint(
        self,
        failed_endpoint_id: str,
        model_id: str,
    ) -> Optional[ModelEndpoint]:
        """Get a failover endpoint when the primary fails.

        Args:
            failed_endpoint_id: ID of the failed endpoint
            model_id: Model identifier

        Returns:
            Failover endpoint, or None if none available
        """
        available = self._get_available_endpoints(model_id)
        available = [e for e in available if e.endpoint_id != failed_endpoint_id]

        if not available:
            return None

        # Prefer same region, then least latency
        failed = self._endpoints.get(failed_endpoint_id)
        if failed:
            same_region = [e for e in available if e.region == failed.region]
            if same_region:
                return min(same_region, key=lambda e: e.avg_latency_ms)

        return min(available, key=lambda e: e.avg_latency_ms)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get load balancer statistics.

        Returns:
            Dictionary of statistics
        """
        endpoint_stats: Dict[str, Dict[str, Any]] = {}
        for eid, endpoint in self._endpoints.items():
            pool = self._pools.get(eid)
            cb = self._circuit_breakers.get(eid)
            endpoint_stats[eid] = {
                "model_id": endpoint.model_id,
                "health": endpoint.health.value,
                "current_connections": endpoint.current_connections,
                "max_connections": endpoint.max_connections,
                "total_requests": endpoint.total_requests,
                "total_failures": endpoint.total_failures,
                "failure_rate": endpoint.failure_rate,
                "avg_latency_ms": endpoint.avg_latency_ms,
                "weight": endpoint.weight,
                "pool_active": pool.active_connections if pool else 0,
                "pool_idle": pool.idle_connections if pool else 0,
                "circuit_breaker_state": cb.state.value if cb else "unknown",
            }

        return {
            "default_strategy": self._default_strategy.value,
            "total_endpoints": len(self._endpoints),
            "healthy_endpoints": sum(
                1
                for e in self._endpoints.values()
                if e.health == EndpointHealth.HEALTHY
            ),
            "endpoints": endpoint_stats,
        }

    def __repr__(self) -> str:
        return (
            f"ModelLoadBalancer(endpoints={len(self._endpoints)}, "
            f"strategy={self._default_strategy.value})"
        )
