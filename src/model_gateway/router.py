"""Smart Model Router for Model Gateway.

Enhanced routing engine with multiple strategies:
- Task-type-based model selection
- Cost-aware routing (cheapest model meeting requirements)
- Latency-aware routing (fastest model for time-sensitive tasks)
- Quality-tier routing (best model for complex tasks)
- Fallback routing with retry logic
- Load balancing across model instances
- Configurable rules engine
- Context window matching
- Capability-based routing (vision, code, function calling, etc.)
"""

from __future__ import annotations

import hashlib
import heapq
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .model_registry_extended import (
    Capability,
    ModelTier,
    ModelStatus,
    RegisteredModelExtended,
    TaskType,
)


class RoutingStrategy(str, Enum):
    """Available routing strategies."""

    COST_OPTIMIZED = "cost_optimized"
    LATENCY_OPTIMIZED = "latency_optimized"
    QUALITY_OPTIMIZED = "quality_optimized"
    BALANCED = "balanced"
    ROUND_ROBIN = "round_robin"
    LEAST_LOADED = "least_loaded"


@dataclass
class RoutingRequest:
    """Request parameters for model routing.

    Attributes:
        task_type: Type of task to perform
        required_capabilities: Set of capabilities the model must have
        estimated_input_tokens: Estimated input token count
        estimated_output_tokens: Estimated output token count
        max_latency_ms: Maximum acceptable latency in milliseconds
        max_cost: Maximum acceptable cost for the request
        min_quality_score: Minimum quality score required
        preferred_tier: Preferred model tier (economy/standard/premium)
        context_size: Total context size needed (input + output)
        user_id: User identifier for per-user routing preferences
        project_id: Project identifier for budget tracking
        metadata: Additional request metadata
    """

    task_type: TaskType = TaskType.TEXT_GENERATION
    required_capabilities: Set[Capability] = field(default_factory=set)
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    max_latency_ms: Optional[float] = None
    max_cost: Optional[float] = None
    min_quality_score: Optional[float] = None
    preferred_tier: Optional[ModelTier] = None
    context_size: Optional[int] = None
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """Result of a routing decision.

    Attributes:
        model: The selected model
        strategy: Strategy used for routing
        reason: Human-readable explanation
        estimated_cost: Estimated cost for the request
        estimated_latency_ms: Estimated latency
        fallback_models: List of fallback model IDs
        confidence: Confidence score (0-1) for the decision
        metadata: Additional decision metadata
    """

    model: RegisteredModelExtended
    strategy: RoutingStrategy
    reason: str
    estimated_cost: float = 0.0
    estimated_latency_ms: float = 0.0
    fallback_models: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingRule:
    """A configurable routing rule.

    Attributes:
        name: Rule name
        condition: Callable that takes RoutingRequest and returns bool
        action: Action to take when condition matches (strategy override, model pin, etc.)
        priority: Higher priority rules evaluated first
        enabled: Whether the rule is active
        description: Human-readable description
    """

    name: str
    condition: Callable[[RoutingRequest], bool]
    action: Dict[str, Any]
    priority: int = 0
    enabled: bool = True
    description: str = ""


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


@dataclass
class CircuitBreaker:
    """Circuit breaker for a model endpoint.

    Attributes:
        model_id: Model identifier
        state: Current circuit breaker state
        failure_count: Consecutive failure count
        failure_threshold: Threshold to open circuit
        success_threshold: Successes needed to close circuit (half-open)
        reset_timeout: Time in seconds before trying again
        last_failure_time: Timestamp of last failure
        last_success_time: Timestamp of last success
    """

    model_id: str
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    failure_threshold: int = 5
    success_count: int = 0
    success_threshold: int = 3
    reset_timeout: float = 30.0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0

    def record_failure(self) -> None:
        """Record a failure event."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN

    def record_success(self) -> None:
        """Record a success event."""
        self.last_success_time = time.time()
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self.success_count = 0
        elif self.state == CircuitBreakerState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def can_send_request(self) -> bool:
        """Check if a request can be sent to this model."""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                return True
            return False
        # HALF_OPEN - allow limited requests
        return self.success_count < self.success_threshold


class SmartRouter:
    """Enhanced smart model router with multiple strategies.

    Supports:
    - Multiple routing strategies (cost, latency, quality, balanced)
    - Capability and context window matching
    - Fallback chains
    - Configurable rules engine
    - Circuit breakers per model
    - Load awareness

    Usage:
        router = SmartRouter(registry)
        decision = router.route(RoutingRequest(task_type=TaskType.CODE_GENERATION))
    """

    def __init__(
        self,
        models: Optional[List[RegisteredModelExtended]] = None,
        default_strategy: RoutingStrategy = RoutingStrategy.BALANCED,
    ) -> None:
        """Initialize the smart router.

        Args:
            models: List of available models
            default_strategy: Default routing strategy
        """
        self._models: Dict[str, RegisteredModelExtended] = {}
        self._default_strategy = default_strategy
        self._rules: List[RoutingRule] = []
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._round_robin_counters: Dict[str, int] = {}
        self._load_counters: Dict[str, int] = {}
        self._routing_history: List[RoutingDecision] = []

        if models:
            for model in models:
                self.add_model(model)

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def add_model(self, model: RegisteredModelExtended) -> None:
        """Register a model with the router.

        Args:
            model: Model to register
        """
        self._models[model.model_id] = model
        if model.model_id not in self._circuit_breakers:
            self._circuit_breakers[model.model_id] = CircuitBreaker(
                model_id=model.model_id
            )
        if model.model_id not in self._load_counters:
            self._load_counters[model.model_id] = 0

    def remove_model(self, model_id: str) -> None:
        """Remove a model from the router.

        Args:
            model_id: ID of model to remove
        """
        self._models.pop(model_id, None)
        self._circuit_breakers.pop(model_id, None)
        self._load_counters.pop(model_id, None)

    def get_model(self, model_id: str) -> Optional[RegisteredModelExtended]:
        """Get a model by ID.

        Args:
            model_id: Model identifier

        Returns:
            The model if found, None otherwise
        """
        return self._models.get(model_id)

    def list_models(self) -> List[RegisteredModelExtended]:
        """List all registered models.

        Returns:
            List of all registered models
        """
        return list(self._models.values())

    # ------------------------------------------------------------------
    # Rules engine
    # ------------------------------------------------------------------

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule.

        Args:
            rule: Routing rule to add
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, rule_name: str) -> None:
        """Remove a routing rule by name.

        Args:
            rule_name: Name of rule to remove
        """
        self._rules = [r for r in self._rules if r.name != rule_name]

    def _evaluate_rules(
        self, request: RoutingRequest
    ) -> Dict[str, Any]:
        """Evaluate routing rules and return overrides.

        Args:
            request: The routing request

        Returns:
            Dictionary of routing overrides
        """
        overrides: Dict[str, Any] = {}
        for rule in self._rules:
            if not rule.enabled:
                continue
            try:
                if rule.condition(request):
                    overrides.update(rule.action)
                    if overrides.get("stop_processing"):
                        break
            except Exception:
                continue
        return overrides

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_eligible_models(
        self, request: RoutingRequest
    ) -> List[RegisteredModelExtended]:
        """Filter models based on hard requirements.

        Args:
            request: Routing request with requirements

        Returns:
            List of eligible models
        """
        eligible: List[RegisteredModelExtended] = []
        for model in self._models.values():
            # Status check
            if model.status != ModelStatus.ACTIVE:
                continue

            # Circuit breaker check
            cb = self._circuit_breakers.get(model.model_id)
            if cb and not cb.can_send_request():
                continue

            # Capability check
            if request.required_capabilities:
                if not request.required_capabilities.issubset(model.capabilities):
                    continue

            # Context window check
            needed_context = request.context_size or (
                request.estimated_input_tokens + request.estimated_output_tokens
            )
            if needed_context > 0 and needed_context > model.context_window:
                continue

            # Cost check (hard constraint)
            if request.max_cost is not None:
                est_cost = self._estimate_cost(model, request)
                if est_cost > request.max_cost:
                    continue

            # Latency check (hard constraint)
            if request.max_latency_ms is not None:
                est_latency = self._estimate_latency(model, request)
                if est_latency > request.max_latency_ms:
                    continue

            # Quality check (hard constraint)
            if request.min_quality_score is not None:
                task_bench = model.benchmarks.get(request.task_type.value, 0.0)
                if task_bench < request.min_quality_score:
                    continue

            # Preferred tier
            if request.preferred_tier is not None:
                if model.tier.value != request.preferred_tier.value:
                    # Still include but lower priority
                    pass

            eligible.append(model)

        return eligible

    # ------------------------------------------------------------------
    # Cost / latency estimation
    # ------------------------------------------------------------------

    def _estimate_cost(
        self, model: RegisteredModelExtended, request: RoutingRequest
    ) -> float:
        """Estimate the cost of a request for a given model.

        Args:
            model: Target model
            request: Routing request

        Returns:
            Estimated cost in dollars
        """
        input_tokens = max(request.estimated_input_tokens, 1)
        output_tokens = max(request.estimated_output_tokens, 1)
        return (
            input_tokens / 1000.0 * model.cost_per_1k_input
            + output_tokens / 1000.0 * model.cost_per_1k_output
        )

    def _estimate_latency(
        self, model: RegisteredModelExtended, request: RoutingRequest
    ) -> float:
        """Estimate latency for a request.

        Args:
            model: Target model
            request: Routing request

        Returns:
            Estimated latency in milliseconds
        """
        base_latency = model.avg_latency_ms
        token_factor = (
            request.estimated_input_tokens + request.estimated_output_tokens
        ) / 1000.0
        return base_latency * (1.0 + token_factor * 0.1)

    # ------------------------------------------------------------------
    # Scoring and ranking
    # ------------------------------------------------------------------

    def _score_model(
        self,
        model: RegisteredModelExtended,
        request: RoutingRequest,
        strategy: RoutingStrategy,
    ) -> float:
        """Score a model based on the routing strategy.

        Higher score = better match.

        Args:
            model: Model to score
            request: Routing request
            strategy: Routing strategy

        Returns:
            Score (higher is better)
        """
        cost = self._estimate_cost(model, request)
        latency = self._estimate_latency(model, request)
        quality = model.benchmarks.get(request.task_type.value, 0.5)
        tier_quality = {
            ModelTier.ECONOMY: 0.3,
            ModelTier.STANDARD: 0.6,
            ModelTier.PREMIUM: 0.9,
        }.get(model.tier, 0.5)

        # Normalize cost using logarithmic scale for better sensitivity
        if cost <= 0:
            cost_score = 1.0
        else:
            # Log-based scoring: lower cost = higher score
            # $0.0001/request -> ~0.95, $0.01/request -> ~0.5, $1/request -> ~0.05
            cost_score = max(0.0, 1.0 - math.log10(cost * 100 + 1) / 3.0)

        # Normalize latency
        latency_score = max(0.0, 1.0 - latency / 5000.0) if latency > 0 else 1.0

        # Quality score
        quality_score = max(0.0, min(1.0, max(quality, tier_quality)))

        if strategy == RoutingStrategy.COST_OPTIMIZED:
            return cost_score * 0.90 + quality_score * 0.03 + latency_score * 0.07
        elif strategy == RoutingStrategy.LATENCY_OPTIMIZED:
            return latency_score * 0.60 + quality_score * 0.20 + cost_score * 0.20
        elif strategy == RoutingStrategy.QUALITY_OPTIMIZED:
            return quality_score * 0.60 + latency_score * 0.20 + cost_score * 0.20
        elif strategy == RoutingStrategy.BALANCED:
            return (
                cost_score * 0.34
                + latency_score * 0.33
                + quality_score * 0.33
            )
        else:
            return quality_score

    # ------------------------------------------------------------------
    # Main routing methods
    # ------------------------------------------------------------------

    def route(self, request: RoutingRequest) -> RoutingDecision:
        """Route a request to the best model.

        Args:
            request: Routing request with requirements and preferences

        Returns:
            Routing decision with selected model and metadata

        Raises:
            ValueError: If no eligible models are available
        """
        # Evaluate rules for overrides
        overrides = self._evaluate_rules(request)
        strategy = RoutingStrategy(
            overrides.get("strategy", self._default_strategy.value)
        )
        pinned_model = overrides.get("pinned_model")

        # If a specific model is pinned, use it (if eligible)
        if pinned_model and pinned_model in self._models:
            model = self._models[pinned_model]
            eligible = self._filter_eligible_models(request)
            if model in eligible:
                return RoutingDecision(
                    model=model,
                    strategy=strategy,
                    reason=f"Pinned by rule: {overrides.get('rule_name', 'unknown')}",
                    estimated_cost=self._estimate_cost(model, request),
                    estimated_latency_ms=self._estimate_latency(model, request),
                    fallback_models=self._get_fallback_models(model, request),
                    confidence=1.0,
                    metadata={"pinned_by": overrides.get("rule_name")},
                )

        # Get eligible models
        eligible = self._filter_eligible_models(request)
        if not eligible:
            raise ValueError(
                f"No eligible models found for request: "
                f"task={request.task_type.value}, "
                f"capabilities={[c.value for c in request.required_capabilities]}"
            )

        if strategy == RoutingStrategy.ROUND_ROBIN:
            return self._route_round_robin(eligible, request, strategy)
        elif strategy == RoutingStrategy.LEAST_LOADED:
            return self._route_least_loaded(eligible, request, strategy)
        else:
            return self._route_scored(eligible, request, strategy)

    def _route_scored(
        self,
        eligible: List[RegisteredModelExtended],
        request: RoutingRequest,
        strategy: RoutingStrategy,
    ) -> RoutingDecision:
        """Route using scoring-based strategy.

        Args:
            eligible: List of eligible models
            request: Routing request
            strategy: Routing strategy

        Returns:
            Routing decision
        """
        scored = [
            (self._score_model(m, request, strategy), m) for m in eligible
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_model = scored[0]
        fallback = [m.model_id for _, m in scored[1:5]]  # Top 5 as fallbacks

        return RoutingDecision(
            model=best_model,
            strategy=strategy,
            reason=f"Selected by {strategy.value} strategy with score {best_score:.3f}",
            estimated_cost=self._estimate_cost(best_model, request),
            estimated_latency_ms=self._estimate_latency(best_model, request),
            fallback_models=fallback,
            confidence=min(1.0, best_score),
            metadata={"score": best_score, "candidates": len(scored)},
        )

    def _route_round_robin(
        self,
        eligible: List[RegisteredModelExtended],
        request: RoutingRequest,
        strategy: RoutingStrategy,
    ) -> RoutingDecision:
        """Route using round-robin strategy.

        Args:
            eligible: List of eligible models
            request: Routing request
            strategy: Routing strategy

        Returns:
            Routing decision
        """
        key = request.task_type.value
        if key not in self._round_robin_counters:
            self._round_robin_counters[key] = 0

        idx = self._round_robin_counters[key] % len(eligible)
        self._round_robin_counters[key] += 1
        selected = eligible[idx]
        fallback = [
            eligible[(idx + i + 1) % len(eligible)].model_id
            for i in range(min(4, len(eligible) - 1))
        ]

        return RoutingDecision(
            model=selected,
            strategy=strategy,
            reason=f"Round-robin selection (index {idx})",
            estimated_cost=self._estimate_cost(selected, request),
            estimated_latency_ms=self._estimate_latency(selected, request),
            fallback_models=fallback,
            confidence=1.0,
            metadata={"round_robin_index": idx},
        )

    def _route_least_loaded(
        self,
        eligible: List[RegisteredModelExtended],
        request: RoutingRequest,
        strategy: RoutingStrategy,
    ) -> RoutingDecision:
        """Route using least-loaded strategy.

        Args:
            eligible: List of eligible models
            request: Routing request
            strategy: Routing strategy

        Returns:
            Routing decision
        """
        selected = min(
            eligible,
            key=lambda m: self._load_counters.get(m.model_id, 0),
        )
        self._load_counters[selected.model_id] = (
            self._load_counters.get(selected.model_id, 0) + 1
        )

        others = sorted(
            eligible,
            key=lambda m: self._load_counters.get(m.model_id, 0),
        )
        fallback = [m.model_id for m in others[1:5]]

        return RoutingDecision(
            model=selected,
            strategy=strategy,
            reason=(
                f"Least-loaded selection "
                f"(load={self._load_counters[selected.model_id]})"
            ),
            estimated_cost=self._estimate_cost(selected, request),
            estimated_latency_ms=self._estimate_latency(selected, request),
            fallback_models=fallback,
            confidence=1.0,
            metadata={
                "load": self._load_counters[selected.model_id],
                "total_models": len(eligible),
            },
        )

    def _get_fallback_models(
        self,
        primary: RegisteredModelExtended,
        request: RoutingRequest,
    ) -> List[str]:
        """Get fallback model IDs for a given primary model.

        Args:
            primary: Primary selected model
            request: Routing request

        Returns:
            List of fallback model IDs
        """
        eligible = self._filter_eligible_models(request)
        eligible = [m for m in eligible if m.model_id != primary.model_id]
        # Sort by quality for fallback
        eligible.sort(
            key=lambda m: m.benchmarks.get(request.task_type.value, 0.0),
            reverse=True,
        )
        return [m.model_id for m in eligible[:4]]

    # ------------------------------------------------------------------
    # Fallback support
    # ------------------------------------------------------------------

    def get_fallback(
        self,
        failed_model_id: str,
        request: RoutingRequest,
    ) -> Optional[RoutingDecision]:
        """Get a fallback model when the primary fails.

        Args:
            failed_model_id: ID of the model that failed
            request: Original routing request

        Returns:
            Routing decision for fallback, or None if no fallback available
        """
        # Record failure in circuit breaker
        cb = self._circuit_breakers.get(failed_model_id)
        if cb:
            cb.record_failure()

        eligible = self._filter_eligible_models(request)
        eligible = [m for m in eligible if m.model_id != failed_model_id]

        if not eligible:
            return None

        # Use same strategy for fallback selection
        overrides = self._evaluate_rules(request)
        strategy = RoutingStrategy(
            overrides.get("strategy", self._default_strategy.value)
        )

        if strategy in (RoutingStrategy.ROUND_ROBIN, RoutingStrategy.LEAST_LOADED):
            decision = self._route_scored(
                eligible, request, RoutingStrategy.QUALITY_OPTIMIZED
            )
        else:
            decision = self._route_scored(eligible, request, strategy)

        decision.reason = f"Fallback: {decision.reason}"
        decision.metadata["is_fallback"] = True
        decision.metadata["failed_model"] = failed_model_id
        return decision

    def record_success(self, model_id: str) -> None:
        """Record a successful request for a model.

        Args:
            model_id: Model identifier
        """
        cb = self._circuit_breakers.get(model_id)
        if cb:
            cb.record_success()
        if model_id in self._load_counters:
            self._load_counters[model_id] = max(
                0, self._load_counters[model_id] - 1
            )

    def record_failure(self, model_id: str) -> None:
        """Record a failed request for a model.

        Args:
            model_id: Model identifier
        """
        cb = self._circuit_breakers.get(model_id)
        if cb:
            cb.record_failure()
        if model_id in self._load_counters:
            self._load_counters[model_id] = max(
                0, self._load_counters[model_id] - 1
            )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get router statistics.

        Returns:
            Dictionary of router statistics
        """
        circuit_breaker_stats = {
            mid: {
                "state": cb.state.name,
                "failure_count": cb.failure_count,
                "success_count": cb.success_count,
            }
            for mid, cb in self._circuit_breakers.items()
        }

        return {
            "total_models": len(self._models),
            "active_models": sum(
                1
                for m in self._models.values()
                if m.status == ModelStatus.ACTIVE
            ),
            "rules_count": len(self._rules),
            "default_strategy": self._default_strategy.value,
            "circuit_breakers": circuit_breaker_stats,
            "load_counters": dict(self._load_counters),
        }

    def __repr__(self) -> str:
        return (
            f"SmartRouter(models={len(self._models)}, "
            f"strategy={self._default_strategy.value})"
        )
