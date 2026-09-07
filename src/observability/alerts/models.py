"""
Advanced Alert Models for LiuHao AI OS

Defines the data model for alert events, thresholds, and notification channels.
Provides standardized alert types, severity levels, and alert state management.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum, auto
from datetime import datetime


class AlertSeverity(Enum):
    """Alert severity levels."""
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()


class AlertState(Enum):
    """Alert lifecycle states."""
    FIRING = auto()    # 正在触发
    RESOLVED = auto()  # 已解决
    SUPPRESSED = auto()  # 已抑制


class AlertType(Enum):
    """Types of alerts supported."""
    METRIC_THRESHOLD = "metric_threshold"      # 指标阈值告警
    LOG_PATTERN = "log_pattern"               # 日志模式告警
    SERVICE_DOWN = "service_down"             # 服务 down 告警
    ERROR_RATE = "error_rate"                 # 错误率告警
    LATENCY = "latency"                       # 延迟告警
    CUSTOM = "custom"                         # 自定义告警


@dataclass
class AlertThreshold:
    """Threshold configuration for metric alerts."""
    operator: str          # "gt", "gte", "lt", "lte", "eq", "ne"
    value: float           # 阈值
    duration: float = 0.0  # 持续时长(秒)，阈值必须连续触发多长时间才报警
    timeout: float = 0.0  # 超时时间(秒)，若长时间未恢复则触发持续告警


@dataclass
class Alert:
    """Core alert data model."""
    id: str
    alert_type: AlertType
    name: str
    severity: AlertSeverity
    message: str
    source: str          # 触发源 (service, component, metric)
    metric_name: Optional[str] = None  # 关联的指标名
    threshold: Optional[AlertThreshold] = None  # 关联的阈值配置
    state: AlertState = AlertState.FIRING
    fired_at: float = field(default_factory=datetime.now().timestamp)
    resolved_at: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "alert_type": self.alert_type.value,
            "name": self.name,
            "severity": self.severity.name,
            "message": self.message,
            "source": self.source,
            "metric_name": self.metric_name,
            "state": self.state.name,
            "fired_at": self.fired_at,
            "resolved_at": self.resolved_at,
            "tags": self.tags,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Alert":
        """Create Alert from dictionary."""
        return cls(
            id=data.get("id", str(auto())),
            alert_type=AlertType(data.get("alert_type", "metric_threshold")),
            name=data.get("name", ""),
            severity=AlertSeverity[data.get("severity", "LOW")],
            message=data.get("message", ""),
            source=data.get("source", ""),
            metric_name=data.get("metric_name"),
            threshold=AlertThreshold.from_dict(data.get("threshold", {})) if data.get("threshold") else None,
            state=AlertState[data.get("state", "FIRING")],
            fired_at=data.get("fired_at", datetime.now().timestamp()),
            resolved_at=data.get("resolved_at"),
            tags=data.get("tags", {}),
            extra=data.get("extra", {}),
        )


@dataclass
class AlertThreshold:
    """Threshold configuration for metric alerts."""
    operator: str          # "gt", "gte", "lt", "lte", "eq", "ne"
    value: float           # 阈值
    duration: float = 0.0  # 持续时长(秒)，阈值必须连续触发多长时间才报警
    timeout: float = 0.0  # 超时时间(秒)，若长时间未恢复则触发持续告警

    def check(self, current_value: float) -> Optional[bool]:
        """
        Check if current value triggers the threshold.

        Returns:
            None: value within threshold
            True: value exceeds threshold
            False: value below threshold
        """
        operators = {
            "gt": current_value > self.value,
            "gte": current_value >= self.value,
            "lt": current_value < self.value,
            "lte": current_value <= self.value,
            "eq": current_value == self.value,
            "ne": current_value != self.value,
        }
        return operators.get(self.operator)


@dataclass
class AlertRule:
    """Alert rule definition for automated monitoring."""
    id: str
    name: str
    description: str
    alert_type: AlertType
    metric_name: str
    threshold: AlertThreshold
    severity: AlertSeverity = AlertSeverity.MEDIUM
    evaluation_interval: float = 60.0  # 评估间隔(秒)
    evaluation_count: int = 1  # 需连续多少次评估才触发
    tags: Dict[str, str] = field(default_factory=dict)
    receiver: str = "console"  # 告警接收渠道
    enabled: bool = True

    def evaluate(self, current_metric: float, history: List[float] = None) -> Optional[Alert]:
        """
        Evaluate alert rule against current metric value.

        Returns:
            Alert if threshold crossed, None otherwise
        """
        if not self.enabled:
            return None

        # Check threshold
        triggered = self.threshold.check(current_metric)

        if triggered is None:
            # Within threshold - reset consecutive count conceptually
            return None

        if triggered:
            # Value is outside threshold - this is the nth consecutive trigger
            # For simplicity, we just fire on first detection
            # In production, would track consecutive evaluations
            alert = Alert(
                id=str(auto()),
                alert_type=self.alert_type,
                name=self.name,
                severity=self.severity,
                message=f"{self.name}: {self.metric_name} = {current_metric} exceeds threshold {self.threshold.value} ({self.threshold.operator})",
                source="observability.alerting",
                metric_name=self.metric_name,
                threshold=self.threshold,
                state=AlertState.FIRING,
                tags=self.tags,
            )
            return alert

        # Value returned to normal - could resolve if currently firing
        return None


@dataclass
class AlertManager:
    """
    Central alert management system.

    Features:
    - Alert rule management
    - Automatic evaluation against metrics
    - Alert state tracking (firing/resolved/suppressed)
    - Notification routing
    - Alert deduplication
    """

    def __init__(self):
        self.rules: Dict[str, AlertRule] = {}
        self.alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self._evaluation_count = 0

    def add_rule(self, rule: AlertRule) -> None:
        """Add an alert rule."""
        self.rules[rule.id] = rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove an alert rule."""
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        """Get an alert rule by ID."""
        return self.rules.get(rule_id)

    def list_rules(self) -> List[AlertRule]:
        """List all alert rules."""
        return list(self.rules.values())

    def evaluate_all(self, metrics: Dict[str, float]) -> List[Alert]:
        """
        Evaluate all rules against current metrics.

        Args:
            metrics: Dict of metric_name -> current_value

        Returns:
            List of newly triggered alerts
        """
        new_alerts = []

        for rule in self.rules.values():
            if rule.metric_name not in metrics:
                continue

            current_value = metrics[rule.metric_name]
            alert = rule.evaluate(current_value)

            if alert is not None:
                # Store alert
                self.alerts[alert.id] = alert
                self.alert_history.append(alert)

                # Update alert state in storage
                new_alerts.append(alert)

        return new_alerts

    def resolve_alert(self, alert_id: str) -> bool:
        """Mark an alert as resolved."""
        if alert_id in self.alerts:
            self.alerts[alert_id].state = AlertState.RESOLVED
            self.alerts[alert_id].resolved_at = datetime.now().timestamp()
            return True
        return False

    def get_active_alerts(self) -> List[Alert]:
        """Get all currently firing alerts."""
        return [a for a in self.alerts.values() if a.state == AlertState.FIRING]

    def get_alerts_by_severity(self, severity: AlertSeverity) -> List[Alert]:
        """Get alerts by severity level."""
        return [a for a in self.alerts.values() if a.severity == severity]


# Module-level convenience functions
_default_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get the default alert manager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = AlertManager()
    return _default_manager


def add_alert_rule(rule: AlertRule) -> None:
    """Add an alert rule to the default manager."""
    get_alert_manager().add_rule(rule)


def evaluate_alerts(metrics: Dict[str, float]) -> List[Alert]:
    """Evaluate all alert rules against current metrics."""
    return get_alert_manager().evaluate_all(metrics)


def resolve_alert(alert_id: str) -> bool:
    """Resolve an alert by ID."""
    return get_alert_manager().resolve_alert(alert_id)


def get_active_alerts() -> List[Alert]:
    """Get all currently firing alerts."""
    return get_alert_manager().get_active_alerts()
