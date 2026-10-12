"""Cost Optimizer for Model Gateway.

Cost optimization features:
- Token usage tracking per model, user, project
- Cost calculation (prompt + completion tokens)
- Cost projection for tasks
- Budget tracking per project/user
- Cost-saving recommendations
- Prompt caching mechanism
- Batch request optimization
- Cost anomaly detection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


class BudgetPeriod(str, Enum):
    """Budget tracking periods."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class CostAnomalySeverity(str, Enum):
    """Severity levels for cost anomalies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TokenUsageRecord:
    """Record of token usage for a request.

    Attributes:
        record_id: Unique record identifier
        model_id: Model used
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cost: Total cost in dollars
        user_id: User who made the request
        project_id: Project associated with the request
        timestamp: When the request was made
        task_type: Type of task performed
        cache_hit: Whether the request hit cache
        metadata: Additional metadata
    """

    record_id: str = field(default_factory=lambda: str(uuid4()))
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_type: str = ""
    cache_hit: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens


@dataclass
class Budget:
    """Budget configuration for a project or user.

    Attributes:
        budget_id: Unique budget identifier
        entity_type: Type of entity (user, project, organization)
        entity_id: Entity identifier
        period: Budget period
        limit: Budget limit in dollars
        spent: Amount spent in current period
        threshold: Alert threshold (fraction of limit, e.g., 0.8 for 80%)
        alerts_enabled: Whether budget alerts are enabled
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    budget_id: str = field(default_factory=lambda: str(uuid4()))
    entity_type: str = "project"
    entity_id: str = ""
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    limit: float = 100.0
    spent: float = 0.0
    threshold: float = 0.8
    alerts_enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def usage_ratio(self) -> float:
        """Fraction of budget used."""
        if self.limit <= 0:
            return 0.0
        return self.spent / self.limit

    @property
    def remaining(self) -> float:
        """Remaining budget."""
        return max(0.0, self.limit - self.spent)

    @property
    def is_over_budget(self) -> bool:
        """Whether the budget has been exceeded."""
        return self.spent > self.limit

    @property
    def is_near_limit(self) -> bool:
        """Whether usage is near the budget limit."""
        return self.usage_ratio >= self.threshold


@dataclass
class CostSavingRecommendation:
    """A cost-saving recommendation.

    Attributes:
        recommendation_id: Unique identifier
        category: Recommendation category
        title: Short title
        description: Detailed description
        estimated_savings: Estimated monthly savings in dollars
        confidence: Confidence in the recommendation (0-1)
        effort: Implementation effort (low, medium, high)
        actionable: Whether the recommendation is actionable
        metadata: Additional data
    """

    recommendation_id: str = field(default_factory=lambda: str(uuid4()))
    category: str = ""
    title: str = ""
    description: str = ""
    estimated_savings: float = 0.0
    confidence: float = 0.5
    effort: str = "medium"
    actionable: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CostAnomaly:
    """Detected cost anomaly.

    Attributes:
        anomaly_id: Unique identifier
        severity: Anomaly severity
        description: Description of the anomaly
        expected_cost: Expected cost for the period
        actual_cost: Actual cost
        deviation_ratio: Ratio of actual to expected
        detected_at: Detection timestamp
        entity_type: Affected entity type
        entity_id: Affected entity ID
        metadata: Additional data
    """

    anomaly_id: str = field(default_factory=lambda: str(uuid4()))
    severity: CostAnomalySeverity = CostAnomalySeverity.MEDIUM
    description: str = ""
    expected_cost: float = 0.0
    actual_cost: float = 0.0
    deviation_ratio: float = 1.0
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entity_type: str = ""
    entity_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchRequestPlan:
    """Plan for batching requests.

    Attributes:
        plan_id: Unique identifier
        original_requests: Number of original requests
        batched_requests: Number of batched requests
        estimated_savings: Estimated cost savings
        batch_size: Size of each batch
        model_id: Target model ID
    """

    plan_id: str = field(default_factory=lambda: str(uuid4()))
    original_requests: int = 0
    batched_requests: int = 0
    estimated_savings: float = 0.0
    batch_size: int = 10
    model_id: str = ""


class CostOptimizer:
    """Cost optimizer for model gateway.

    Tracks usage, manages budgets, detects anomalies, and provides
    cost-saving recommendations.

    Usage:
        optimizer = CostOptimizer()
        optimizer.track_usage(TokenUsageRecord(model_id="gpt-4", ...))
        savings = optimizer.get_recommendations(project_id="my-project")
    """

    def __init__(self) -> None:
        """Initialize the cost optimizer."""
        self._usage_history: List[TokenUsageRecord] = []
        self._budgets: Dict[str, Budget] = {}
        self._anomalies: List[CostAnomaly] = []
        self._cost_per_model: Dict[str, Dict[str, float]] = {}
        self._model_cost_per_1k: Dict[str, Tuple[float, float]] = {}

        # Cache statistics
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_savings: float = 0.0

        # Batch statistics
        self._batches_processed: int = 0
        self._batch_savings: float = 0.0

    # ------------------------------------------------------------------
    # Model cost configuration
    # ------------------------------------------------------------------

    def set_model_cost(
        self, model_id: str, cost_per_1k_input: float, cost_per_1k_output: float
    ) -> None:
        """Set the cost configuration for a model.

        Args:
            model_id: Model identifier
            cost_per_1k_input: Cost per 1K input tokens
            cost_per_1k_output: Cost per 1K output tokens
        """
        self._model_cost_per_1k[model_id] = (cost_per_1k_input, cost_per_1k_output)

    def get_model_cost(
        self, model_id: str
    ) -> Tuple[float, float]:
        """Get the cost configuration for a model.

        Args:
            model_id: Model identifier

        Returns:
            Tuple of (cost_per_1k_input, cost_per_1k_output)
        """
        return self._model_cost_per_1k.get(model_id, (0.0, 0.0))

    def calculate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate the cost for a request.

        Args:
            model_id: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Total cost in dollars
        """
        input_cost, output_cost = self._model_cost_per_1k.get(
            model_id, (0.0, 0.0)
        )
        return (
            input_tokens / 1000.0 * input_cost
            + output_tokens / 1000.0 * output_cost
        )

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def track_usage(self, record: TokenUsageRecord) -> None:
        """Track a token usage record.

        Args:
            record: Usage record to track
        """
        if record.cost == 0.0 and record.model_id in self._model_cost_per_1k:
            record.cost = self.calculate_cost(
                record.model_id, record.input_tokens, record.output_tokens
            )

        self._usage_history.append(record)

        # Update per-model stats
        if record.model_id not in self._cost_per_model:
            self._cost_per_model[record.model_id] = {
                "total_cost": 0.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "request_count": 0,
            }

        stats = self._cost_per_model[record.model_id]
        stats["total_cost"] += record.cost
        stats["total_input_tokens"] += record.input_tokens
        stats["total_output_tokens"] += record.output_tokens
        stats["request_count"] += 1

        # Update budgets
        self._update_budgets(record)

        # Update cache stats
        if record.cache_hit:
            self._cache_hits += 1
            self._cache_savings += record.cost
        else:
            self._cache_misses += 1

    def get_usage(
        self,
        *,
        model_id: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get aggregated usage statistics.

        Args:
            model_id: Filter by model ID
            user_id: Filter by user ID
            project_id: Filter by project ID
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Aggregated usage statistics
        """
        records = self._usage_history

        if model_id:
            records = [r for r in records if r.model_id == model_id]
        if user_id:
            records = [r for r in records if r.user_id == user_id]
        if project_id:
            records = [r for r in records if r.project_id == project_id]
        if start_time:
            start_str = start_time.isoformat()
            records = [r for r in records if r.timestamp >= start_str]
        if end_time:
            end_str = end_time.isoformat()
            records = [r for r in records if r.timestamp <= end_str]

        total_cost = sum(r.cost for r in records)
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)

        return {
            "request_count": len(records),
            "total_cost": total_cost,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "avg_cost_per_request": total_cost / len(records) if records else 0.0,
            "cache_hit_count": sum(1 for r in records if r.cache_hit),
        }

    # ------------------------------------------------------------------
    # Budget management
    # ------------------------------------------------------------------

    def set_budget(
        self,
        entity_id: str,
        limit: float,
        *,
        entity_type: str = "project",
        period: BudgetPeriod = BudgetPeriod.MONTHLY,
        threshold: float = 0.8,
    ) -> Budget:
        """Set a budget for an entity.

        Args:
            entity_id: Entity identifier
            limit: Budget limit in dollars
            entity_type: Type of entity
            period: Budget period
            threshold: Alert threshold fraction

        Returns:
            Created or updated budget
        """
        key = f"{entity_type}:{entity_id}"
        if key in self._budgets:
            budget = self._budgets[key]
            budget.limit = limit
            budget.period = period
            budget.threshold = threshold
            budget.updated_at = datetime.now(timezone.utc).isoformat()
        else:
            budget = Budget(
                entity_type=entity_type,
                entity_id=entity_id,
                limit=limit,
                period=period,
                threshold=threshold,
            )
            self._budgets[key] = budget
        return budget

    def get_budget(
        self, entity_id: str, *, entity_type: str = "project"
    ) -> Optional[Budget]:
        """Get the budget for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Type of entity

        Returns:
            Budget if found, None otherwise
        """
        key = f"{entity_type}:{entity_id}"
        return self._budgets.get(key)

    def _update_budgets(self, record: TokenUsageRecord) -> None:
        """Update budget spending from a usage record."""
        for key, budget in self._budgets.items():
            entity_type, entity_id = key.split(":", 1)
            if entity_type == "project" and record.project_id == entity_id:
                budget.spent += record.cost
            elif entity_type == "user" and record.user_id == entity_id:
                budget.spent += record.cost

    def check_budget(
        self,
        entity_id: str,
        *,
        entity_type: str = "project",
        request_cost: float = 0.0,
    ) -> Dict[str, Any]:
        """Check if a request would exceed the budget.

        Args:
            entity_id: Entity identifier
            entity_type: Type of entity
            request_cost: Cost of the proposed request

        Returns:
            Budget check result with status details
        """
        budget = self.get_budget(entity_id, entity_type=entity_type)
        if not budget:
            return {"allowed": True, "reason": "no_budget_set"}

        projected = budget.spent + request_cost

        if projected > budget.limit:
            return {
                "allowed": False,
                "reason": "over_budget",
                "budget": budget.limit,
                "spent": budget.spent,
                "projected": projected,
                "deficit": projected - budget.limit,
            }
        elif projected >= budget.limit * budget.threshold:
            return {
                "allowed": True,
                "reason": "near_limit",
                "budget": budget.limit,
                "spent": budget.spent,
                "projected": projected,
                "remaining": budget.limit - projected,
            }
        else:
            return {
                "allowed": True,
                "reason": "within_budget",
                "budget": budget.limit,
                "spent": budget.spent,
                "remaining": budget.remaining,
            }

    # ------------------------------------------------------------------
    # Cost projection
    # ------------------------------------------------------------------

    def project_cost(
        self,
        model_id: str,
        num_requests: int,
        avg_input_tokens: int,
        avg_output_tokens: int,
    ) -> Dict[str, Any]:
        """Project the cost for a given workload.

        Args:
            model_id: Model identifier
            num_requests: Number of requests
            avg_input_tokens: Average input tokens per request
            avg_output_tokens: Average output tokens per request

        Returns:
            Cost projection details
        """
        input_cost, output_cost = self._model_cost_per_1k.get(
            model_id, (0.0, 0.0)
        )

        total_input = num_requests * avg_input_tokens
        total_output = num_requests * avg_output_tokens
        total_cost = (
            total_input / 1000.0 * input_cost
            + total_output / 1000.0 * output_cost
        )

        return {
            "model_id": model_id,
            "num_requests": num_requests,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost": total_cost,
            "avg_cost_per_request": total_cost / num_requests
            if num_requests
            else 0.0,
        }

    # ------------------------------------------------------------------
    # Cost-saving recommendations
    # ------------------------------------------------------------------

    def get_recommendations(
        self,
        *,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[CostSavingRecommendation]:
        """Generate cost-saving recommendations.

        Args:
            project_id: Project to analyze
            user_id: User to analyze

        Returns:
            List of recommendations sorted by estimated savings
        """
        recommendations: List[CostSavingRecommendation] = []

        # Get relevant usage data
        usage = self.get_usage(project_id=project_id, user_id=user_id)

        # Recommendation: Use smaller model where possible
        if usage["request_count"] > 10:
            recommendations.append(
                CostSavingRecommendation(
                    category="model_selection",
                    title="Consider using economy-tier model for simple tasks",
                    description=(
                        "Based on your usage pattern, many requests could be "
                        "handled by a cheaper economy-tier model, resulting in "
                        "significant cost savings for simple text generation tasks."
                    ),
                    estimated_savings=usage["total_cost"] * 0.3,
                    confidence=0.6,
                    effort="medium",
                )
            )

        # Recommendation: Implement caching
        if usage["request_count"] > 50 and self._cache_hits == 0:
            recommendations.append(
                CostSavingRecommendation(
                    category="caching",
                    title="Enable prompt caching for repeated queries",
                    description=(
                        "No cache hits detected. Implementing semantic caching "
                        "for repeated or similar queries could reduce costs "
                        "by serving cached responses."
                    ),
                    estimated_savings=usage["total_cost"] * 0.15,
                    confidence=0.5,
                    effort="low",
                )
            )

        # Recommendation: Batch similar requests
        if usage["request_count"] > 100:
            recommendations.append(
                CostSavingRecommendation(
                    category="batching",
                    title="Batch independent requests to reduce overhead",
                    description=(
                        "Your high request volume could benefit from batch "
                        "processing. Combining similar independent requests "
                        "reduces per-request overhead and improves throughput."
                    ),
                    estimated_savings=usage["total_cost"] * 0.1,
                    confidence=0.7,
                    effort="high",
                )
            )

        # Recommendation: Reduce output tokens
        if usage["total_output_tokens"] > usage["total_input_tokens"] * 0.5:
            recommendations.append(
                CostSavingRecommendation(
                    category="output_optimization",
                    title="Optimize prompts to reduce output token usage",
                    description=(
                        "Output tokens account for a large portion of your cost. "
                        "Consider using more specific prompts to generate shorter "
                        "responses, or set lower max_tokens limits."
                    ),
                    estimated_savings=usage["total_cost"] * 0.2,
                    confidence=0.65,
                    effort="low",
                )
            )

        # Sort by estimated savings descending
        recommendations.sort(key=lambda r: r.estimated_savings, reverse=True)
        return recommendations

    # ------------------------------------------------------------------
    # Batch optimization
    # ------------------------------------------------------------------

    def plan_batch(
        self,
        model_id: str,
        num_requests: int,
        avg_input_tokens: int,
        avg_output_tokens: int,
        batch_size: int = 10,
    ) -> BatchRequestPlan:
        """Create a batch optimization plan.

        Args:
            model_id: Model identifier
            num_requests: Number of requests to batch
            avg_input_tokens: Average input tokens per request
            avg_output_tokens: Average output tokens per request
            batch_size: Desired batch size

        Returns:
            Batch request plan with savings estimate
        """
        import math

        batched_requests = math.ceil(num_requests / batch_size)
        overhead_factor = 0.9  # 10% savings from batching

        projection = self.project_cost(
            model_id, num_requests, avg_input_tokens, avg_output_tokens
        )
        baseline_cost = projection["total_cost"]
        batched_cost = baseline_cost * overhead_factor

        self._batches_processed += batched_requests
        self._batch_savings += baseline_cost - batched_cost

        return BatchRequestPlan(
            original_requests=num_requests,
            batched_requests=batched_requests,
            estimated_savings=baseline_cost - batched_cost,
            batch_size=batch_size,
            model_id=model_id,
        )

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def detect_anomalies(
        self,
        *,
        window_days: int = 7,
        threshold_ratio: float = 2.0,
    ) -> List[CostAnomaly]:
        """Detect cost anomalies.

        Args:
            window_days: Number of days to use as baseline
            threshold_ratio: Ratio above baseline to flag as anomaly

        Returns:
            List of detected anomalies
        """
        anomalies: List[CostAnomaly] = []
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=window_days)
        window_start_str = window_start.isoformat()

        # Calculate daily average for the window
        window_records = [
            r for r in self._usage_history if r.timestamp >= window_start_str
        ]

        if len(window_records) < 3:
            return anomalies

        # Group by day
        daily_costs: Dict[str, float] = {}
        for record in window_records:
            day = record.timestamp[:10]  # YYYY-MM-DD
            daily_costs[day] = daily_costs.get(day, 0.0) + record.cost

        if not daily_costs:
            return anomalies

        avg_daily = sum(daily_costs.values()) / len(daily_costs)
        max_daily = max(daily_costs.values()) if daily_costs else 0.0

        if avg_daily > 0 and max_daily > avg_daily * threshold_ratio:
            severity = CostAnomalySeverity.MEDIUM
            ratio = max_daily / avg_daily
            if ratio > 5.0:
                severity = CostAnomalySeverity.CRITICAL
            elif ratio > 3.0:
                severity = CostAnomalySeverity.HIGH

            anomaly = CostAnomaly(
                severity=severity,
                description=(
                    f"Daily cost spike detected: peak of ${max_daily:.2f} "
                    f"vs average of ${avg_daily:.2f} "
                    f"({ratio:.1f}x above baseline)"
                ),
                expected_cost=avg_daily,
                actual_cost=max_daily,
                deviation_ratio=ratio,
                entity_type="global",
                entity_id="all",
            )
            anomalies.append(anomaly)
            self._anomalies.append(anomaly)

        return anomalies

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get cost optimizer statistics.

        Returns:
            Dictionary of statistics
        """
        return {
            "total_usage_records": len(self._usage_history),
            "total_cost": sum(r.cost for r in self._usage_history),
            "models_tracked": len(self._cost_per_model),
            "budgets_configured": len(self._budgets),
            "anomalies_detected": len(self._anomalies),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_rate": (
                self._cache_hits / (self._cache_hits + self._cache_misses)
                if (self._cache_hits + self._cache_misses) > 0
                else 0.0
            ),
            "cache_savings": self._cache_savings,
            "batches_processed": self._batches_processed,
            "batch_savings": self._batch_savings,
            "cost_per_model": dict(self._cost_per_model),
        }

    def __repr__(self) -> str:
        total_cost = sum(r.cost for r in self._usage_history)
        return (
            f"CostOptimizer(records={len(self._usage_history)}, "
            f"total_cost=${total_cost:.2f})"
        )
