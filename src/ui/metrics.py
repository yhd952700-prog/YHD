"""Metric dashboard for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime


class MetricType(Enum):
    """Available metric types."""
    CPU = "cpu"
    MEMORY = "memory"
    TOKEN_USAGE = "token_usage"
    COST = "cost"
    ACTIVE_WORKERS = "active_workers"
    RESPONSE_TIME = "response_time"


@dataclass
class MetricPoint:
    """A single metric data point with timestamp."""

    timestamp: str
    value: float
    metric_type: MetricType
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class MetricDashboard:
    """Dashboard for monitoring system metrics."""

    metrics: Dict[str, List[MetricPoint]] = field(default_factory=dict)
    metric_labels: Dict[str, str] = field(default_factory=dict)

    def add_metric_point(self, metric_type: MetricType, value: float, labels: Dict[str, str] = None) -> None:
        """Add a metric data point."""
        key = metric_type.value
        if key not in self.metrics:
            self.metrics[key] = []
        self.metrics[key].append(MetricPoint(
            timestamp=datetime.now().isoformat(),
            value=value,
            metric_type=metric_type,
            labels=labels or {},
        ))

    def get_metric_history(self, metric_type: MetricType, limit: int = 100) -> List[MetricPoint]:
        """Get metric history, limited to most recent N points."""
        key = metric_type.value
        if key in self.metrics:
            return self.metrics[key][-limit:]
        return []

    def get_latest(self, metric_type: MetricType) -> Optional[float]:
        """Get the latest value for a metric type."""
        key = metric_type.value
        if key in self.metrics and self.metrics[key]:
            return self.metrics[key][-1].value
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metric dashboard to dictionary for UI rendering."""
        return {
            "metrics": {
                k: {
                    "label": self.metric_labels.get(k, k),
                    "latest": self.get_latest(MetricType(k)) if k in [m.value for m in MetricType] else None,
                    "history_count": len(self.metrics.get(k, [])),
                }
                for k in self.metrics
            },
            "total_metrics": sum(len(v) for v in self.metrics.values()),
        }
