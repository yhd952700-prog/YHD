"""Cost tracking system for LiuHao AI OS.

Provides comprehensive cost management including:
- Cost categorisation (API calls, compute, storage, network, third-party)
- Time-based aggregation (daily / weekly / monthly)
- Budget management with spending vs. budget tracking
- Threshold-based cost alerts (50%, 80%, 100% of budget)
- Cost optimization suggestions (waste detection, cheaper alternatives)
- Simple cost forecasting based on historical data
- Export to CSV and JSON formats
"""
from __future__ import annotations

import csv
import io
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# Enums & Data Classes
# =====================================================================


class CostCategory(str, Enum):
    """Categories of costs tracked by the system."""

    API_CALLS = "api_calls"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    THIRD_PARTY = "third_party"


class AlertLevel(str, Enum):
    """Cost alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AggregationPeriod(str, Enum):
    """Periods for cost aggregation."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class CostRecord:
    """A single cost record entry.

    Attributes:
        record_id: Unique identifier for this record.
        timestamp: Unix timestamp of when the cost was incurred.
        category: Cost category.
        amount: Cost amount (in the configured currency, typically USD).
        provider: Service provider (e.g. "openai", "aws", "gcp").
        resource_id: Identifier of the resource that generated the cost.
        description: Human-readable description.
        metadata: Arbitrary additional key-value data.
    """

    category: CostCategory
    amount: float
    provider: str
    timestamp: float = field(default_factory=lambda: time.time())
    resource_id: Optional[str] = None
    description: str = ""
    record_id: str = field(default_factory=lambda: f"cost_{uuid.uuid4().hex[:12]}")
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError(f"Cost amount cannot be negative: {self.amount}")

    @property
    def datetime(self) -> datetime:
        """Return the timestamp as a timezone-naive datetime."""
        return datetime.fromtimestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "category": self.category.value,
            "amount": self.amount,
            "provider": self.provider,
            "resource_id": self.resource_id,
            "description": self.description,
            "metadata": self.metadata,
        }


@dataclass
class Budget:
    """A budget for a specific scope.

    Attributes:
        name: Budget name / scope identifier.
        limit: Maximum allowed spend.
        period: Budget period.
        category: Optional category this budget applies to (None = all).
        current_spend: Amount spent so far in the current period.
        alerts_fired: Set of alert thresholds already fired this period.
    """

    name: str
    limit: float
    period: AggregationPeriod = AggregationPeriod.MONTHLY
    category: Optional[CostCategory] = None
    current_spend: float = 0.0
    alerts_fired: set = field(default_factory=set)

    @property
    def remaining(self) -> float:
        """Remaining budget."""
        return self.limit - self.current_spend

    @property
    def utilization(self) -> float:
        """Ratio of spent to limit (0.0 - 1.0+)."""
        if self.limit <= 0:
            return 0.0
        return self.current_spend / self.limit

    @property
    def is_over_budget(self) -> bool:
        """True if current spend exceeds the limit."""
        return self.current_spend > self.limit

    def reset(self) -> None:
        """Reset spend and alerts for a new period."""
        self.current_spend = 0.0
        self.alerts_fired = set()


@dataclass
class CostAlert:
    """A cost alert triggered by a threshold crossing."""

    budget_name: str
    level: AlertLevel
    threshold_pct: float
    current_spend: float
    budget_limit: float
    message: str
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "budget_name": self.budget_name,
            "level": self.level.value,
            "threshold_pct": self.threshold_pct,
            "current_spend": self.current_spend,
            "budget_limit": self.budget_limit,
            "message": self.message,
            "timestamp": self.timestamp,
        }


@dataclass
class OptimizationSuggestion:
    """A cost optimization suggestion."""

    title: str
    description: str
    estimated_savings: float
    category: CostCategory
    priority: str = "medium"  # "low", "medium", "high"
    action_items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "title": self.title,
            "description": self.description,
            "estimated_savings": self.estimated_savings,
            "category": self.category.value,
            "priority": self.priority,
            "action_items": self.action_items,
        }


@dataclass
class CostForecast:
    """A simple cost forecast."""

    period: AggregationPeriod
    projected_cost: float
    historical_avg: float
    trend: str  # "increasing", "decreasing", "stable"
    confidence: float  # 0.0 - 1.0
    basis_points: int  # number of historical periods used

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a serialisable dictionary."""
        return {
            "period": self.period.value,
            "projected_cost": self.projected_cost,
            "historical_avg": self.historical_avg,
            "trend": self.trend,
            "confidence": self.confidence,
            "basis_points": self.basis_points,
        }


# =====================================================================
# CostTracker
# =====================================================================


@dataclass
class CostTracker:
    """Central cost tracking and management service.

    Records cost entries, aggregates them by period, manages budgets,
    fires alerts, generates optimization suggestions, forecasts future
    costs, and can export data to CSV / JSON.
    """

    records: List[CostRecord] = field(default_factory=list)
    budgets: Dict[str, Budget] = field(default_factory=dict)
    alerts: List[CostAlert] = field(default_factory=list)
    alert_thresholds: List[float] = field(
        default_factory=lambda: [0.5, 0.8, 1.0]
    )
    currency: str = "USD"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_cost(
        self,
        category: CostCategory,
        amount: float,
        provider: str,
        resource_id: Optional[str] = None,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> CostRecord:
        """Record a cost entry and update relevant budgets.

        Args:
            category: Cost category.
            amount: Cost amount (must be non-negative).
            provider: Service provider name.
            resource_id: Optional resource identifier.
            description: Optional description.
            metadata: Optional key-value metadata.
            timestamp: Optional timestamp (defaults to now).

        Returns:
            The newly created ``CostRecord``.
        """
        record = CostRecord(
            category=category,
            amount=amount,
            provider=provider,
            resource_id=resource_id,
            description=description,
            metadata=metadata or {},
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        self.records.append(record)
        self._update_budgets(record)
        return record

    def _update_budgets(self, record: CostRecord) -> None:
        """Update all applicable budgets and fire alerts if needed."""
        for budget in self.budgets.values():
            if budget.category is not None and budget.category != record.category:
                continue
            budget.current_spend += record.amount
            self._check_budget_alerts(budget)

    # ------------------------------------------------------------------
    # Budget management
    # ------------------------------------------------------------------

    def set_budget(
        self,
        name: str,
        limit: float,
        period: AggregationPeriod = AggregationPeriod.MONTHLY,
        category: Optional[CostCategory] = None,
    ) -> Budget:
        """Create or update a budget.

        Args:
            name: Unique budget name.
            limit: Maximum allowed spend.
            period: Budget period.
            category: Optional category restriction.

        Returns:
            The ``Budget`` object.
        """
        budget = Budget(
            name=name,
            limit=limit,
            period=period,
            category=category,
        )
        self.budgets[name] = budget
        return budget

    def get_budget(self, name: str) -> Optional[Budget]:
        """Get a budget by name."""
        return self.budgets.get(name)

    def reset_budget(self, name: str) -> bool:
        """Reset a budget's spend and alerts.  Returns False if not found."""
        budget = self.budgets.get(name)
        if budget is None:
            return False
        budget.reset()
        return True

    def reset_all_budgets(self) -> None:
        """Reset all budgets."""
        for budget in self.budgets.values():
            budget.reset()

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _check_budget_alerts(self, budget: Budget) -> None:
        """Check a budget against alert thresholds and fire alerts."""
        if budget.limit <= 0:
            return

        utilization = budget.current_spend / budget.limit

        for threshold in sorted(self.alert_thresholds):
            if utilization >= threshold and threshold not in budget.alerts_fired:
                budget.alerts_fired.add(threshold)

                if threshold < 0.75:
                    level = AlertLevel.INFO
                elif threshold < 1.0:
                    level = AlertLevel.WARNING
                else:
                    level = AlertLevel.CRITICAL

                pct = threshold * 100
                alert = CostAlert(
                    budget_name=budget.name,
                    level=level,
                    threshold_pct=pct,
                    current_spend=budget.current_spend,
                    budget_limit=budget.limit,
                    message=(
                        f"Budget '{budget.name}' has reached {pct:.0f}% of "
                        f"its {budget.period.value} limit "
                        f"(${budget.current_spend:.2f} / ${budget.limit:.2f})."
                    ),
                )
                self.alerts.append(alert)

    def recent_alerts(
        self,
        level: Optional[AlertLevel] = None,
        limit: int = 10,
    ) -> List[CostAlert]:
        """Return recent alerts, optionally filtered by level.

        Args:
            level: Optional alert level filter.
            limit: Maximum number of alerts to return (newest first).

        Returns:
            List of ``CostAlert`` objects, newest first.
        """
        result = self.alerts
        if level is not None:
            result = [a for a in result if a.level == level]
        return sorted(result, key=lambda a: a.timestamp, reverse=True)[:limit]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _period_key(self, ts: float, period: AggregationPeriod) -> str:
        """Generate a string key for a timestamp & period."""
        dt = datetime.fromtimestamp(ts)
        if period == AggregationPeriod.DAILY:
            return dt.strftime("%Y-%m-%d")
        elif period == AggregationPeriod.WEEKLY:
            # ISO week
            return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        else:  # monthly
            return dt.strftime("%Y-%m")

    def aggregate(
        self,
        period: AggregationPeriod = AggregationPeriod.DAILY,
        category: Optional[CostCategory] = None,
    ) -> Dict[str, float]:
        """Aggregate costs by time period.

        Args:
            period: Aggregation granularity.
            category: Optional category filter.

        Returns:
            Dict mapping period key string to total cost.
        """
        result: Dict[str, float] = defaultdict(float)
        for record in self.records:
            if category is not None and record.category != category:
                continue
            key = self._period_key(record.timestamp, period)
            result[key] += record.amount
        return dict(sorted(result.items()))

    def total_cost(
        self,
        category: Optional[CostCategory] = None,
        provider: Optional[str] = None,
    ) -> float:
        """Calculate total cost, optionally filtered.

        Args:
            category: Optional category filter.
            provider: Optional provider filter.

        Returns:
            Total cost amount.
        """
        total = 0.0
        for record in self.records:
            if category is not None and record.category != category:
                continue
            if provider is not None and record.provider != provider:
                continue
            total += record.amount
        return total

    def by_category(self) -> Dict[CostCategory, float]:
        """Return total cost broken down by category."""
        result: Dict[CostCategory, float] = defaultdict(float)
        for record in self.records:
            result[record.category] += record.amount
        return dict(result)

    def by_provider(self) -> Dict[str, float]:
        """Return total cost broken down by provider."""
        result: Dict[str, float] = defaultdict(float)
        for record in self.records:
            result[record.provider] += record.amount
        return dict(result)

    # ------------------------------------------------------------------
    # Optimization suggestions
    # ------------------------------------------------------------------

    def get_optimization_suggestions(self) -> List[OptimizationSuggestion]:
        """Generate cost optimization suggestions based on recorded data.

        Uses heuristics to detect potential waste areas.  In production,
        this would be backed by ML models and detailed usage analysis.

        Returns:
            List of ``OptimizationSuggestion`` objects.
        """
        suggestions: List[OptimizationSuggestion] = []

        category_totals = self.by_category()

        # API cost optimization
        api_cost = category_totals.get(CostCategory.API_CALLS, 0.0)
        if api_cost > 0:
            suggestions.append(OptimizationSuggestion(
                title="Optimize API call batching",
                description=(
                    "Consider batching API calls and using request "
                    "caching to reduce the number of API calls."
                ),
                estimated_savings=api_cost * 0.15,
                category=CostCategory.API_CALLS,
                priority="medium",
                action_items=[
                    "Implement request-level caching",
                    "Batch multiple small requests together",
                    "Review unused API endpoints",
                ],
            ))

        # Compute cost optimization
        compute_cost = category_totals.get(CostCategory.COMPUTE, 0.0)
        if compute_cost > 0:
            suggestions.append(OptimizationSuggestion(
                title="Rightsize compute resources",
                description=(
                    "Review compute instance sizes and consider "
                    "downsizing underutilized resources."
                ),
                estimated_savings=compute_cost * 0.20,
                category=CostCategory.COMPUTE,
                priority="high",
                action_items=[
                    "Audit CPU/memory utilization",
                    "Consider spot instances for non-critical workloads",
                    "Schedule idle resources to shut down",
                ],
            ))

        # Storage cost optimization
        storage_cost = category_totals.get(CostCategory.STORAGE, 0.0)
        if storage_cost > 0:
            suggestions.append(OptimizationSuggestion(
                title="Storage lifecycle optimization",
                description=(
                    "Move infrequently accessed data to cheaper storage "
                    "tiers and clean up orphaned resources."
                ),
                estimated_savings=storage_cost * 0.25,
                category=CostCategory.STORAGE,
                priority="low",
                action_items=[
                    "Enable lifecycle policies for old data",
                    "Identify and remove duplicate data",
                    "Compress historical data",
                ],
            ))

        # Budget-based suggestions
        for budget in self.budgets.values():
            if budget.is_over_budget:
                suggestions.append(OptimizationSuggestion(
                    title=f"Budget exceeded: {budget.name}",
                    description=(
                        f"Budget '{budget.name}' is over its "
                        f"{budget.period.value} limit by "
                        f"${budget.current_spend - budget.limit:.2f}."
                    ),
                    estimated_savings=budget.current_spend - budget.limit,
                    category=budget.category or CostCategory.API_CALLS,
                    priority="high",
                    action_items=[
                        "Review recent cost spikes",
                        "Implement rate limiting",
                        "Consider cheaper alternatives",
                    ],
                ))

        return suggestions

    # ------------------------------------------------------------------
    # Forecasting
    # ------------------------------------------------------------------

    def forecast_cost(
        self,
        period: AggregationPeriod = AggregationPeriod.MONTHLY,
    ) -> CostForecast:
        """Forecast future costs based on historical data.

        Uses a simple moving-average approach with trend detection.
        For production use, replace with a proper time-series model.

        Args:
            period: Period to forecast for.

        Returns:
            A ``CostForecast`` with the projection.
        """
        aggregated = self.aggregate(period)
        values = list(aggregated.values())

        if not values:
            return CostForecast(
                period=period,
                projected_cost=0.0,
                historical_avg=0.0,
                trend="stable",
                confidence=0.0,
                basis_points=0,
            )

        avg = sum(values) / len(values)

        # Detect trend by comparing first half to second half
        if len(values) >= 2:
            mid = len(values) // 2
            first_half_avg = sum(values[:mid]) / mid
            second_half_avg = sum(values[mid:]) / (len(values) - mid)

            if second_half_avg > first_half_avg * 1.1:
                trend = "increasing"
            elif second_half_avg < first_half_avg * 0.9:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Project next period
        if trend == "increasing":
            projected = avg * 1.1
        elif trend == "decreasing":
            projected = avg * 0.9
        else:
            projected = avg

        # Confidence based on number of data points
        confidence = min(len(values) / 10.0, 1.0)

        return CostForecast(
            period=period,
            projected_cost=round(projected, 2),
            historical_avg=round(avg, 2),
            trend=trend,
            confidence=round(confidence, 2),
            basis_points=len(values),
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_csv(self) -> str:
        """Export all cost records as CSV.

        Returns:
            CSV string with all cost records.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "record_id", "timestamp", "category", "amount",
            "provider", "resource_id", "description",
        ])
        for record in self.records:
            writer.writerow([
                record.record_id,
                record.timestamp,
                record.category.value,
                record.amount,
                record.provider,
                record.resource_id or "",
                record.description,
            ])
        return output.getvalue()

    def to_json(self, indent: int = 2) -> str:
        """Export all cost data as JSON.

        Includes records, budgets, and alerts.

        Args:
            indent: JSON indentation level.

        Returns:
            JSON string.
        """
        data = {
            "currency": self.currency,
            "total_records": len(self.records),
            "total_cost": self.total_cost(),
            "records": [r.to_dict() for r in self.records],
            "budgets": {
                name: {
                    "limit": b.limit,
                    "period": b.period.value,
                    "category": b.category.value if b.category else None,
                    "current_spend": b.current_spend,
                    "remaining": b.remaining,
                    "utilization": b.utilization,
                    "is_over_budget": b.is_over_budget,
                }
                for name, b in self.budgets.items()
            },
            "alerts": [a.to_dict() for a in self.alerts],
            "by_category": {
                cat.value: amt for cat, amt in self.by_category().items()
            },
            "by_provider": self.by_provider(),
        }
        return json.dumps(data, indent=indent)

    def export_to_file(self, filepath: Path, fmt: str = "json") -> Path:
        """Export cost data to a file.

        Args:
            filepath: Path to the output file.
            fmt: Export format ("json" or "csv").

        Returns:
            The path to the written file.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        if fmt.lower() == "csv":
            content = self.to_csv()
        else:
            content = self.to_json()

        filepath.write_text(content, encoding="utf-8")
        return filepath
