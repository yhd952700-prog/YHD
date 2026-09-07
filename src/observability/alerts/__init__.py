"""Advanced alert module for LiuHao AI OS.

Provides alert data models (Alert, AlertRule, AlertThreshold), enums,
AlertStore persistence, and the AlertManager rule engine. Exposes convenience
functions plus a set of production alert rules with tuned thresholds (OB-04 gap).
"""

from .models import (
    Alert,
    AlertRule,
    AlertThreshold,
    AlertSeverity,
    AlertState,
    AlertType,
    AlertManager,
    add_alert_rule,
    get_alert_manager,
)
from .store import (
    AlertStore,
    get_alert_store,
    emit_alert,
    get_alert,
    list_alerts,
    list_by_severity,
    list_firing,
    resolve_alert,
)


def install_production_rules() -> None:
    """Register production alert rules with tuned thresholds (OB-04 gap)."""
    # Service down alert: fire if service heartbeat missing for 5 minutes
    add_alert_rule(AlertRule(
        id="svc_heartbeat_missing",
        name="Service Heartbeat Missing",
        description="Service heartbeat not received within expected interval",
        alert_type=AlertType.SERVICE_DOWN,
        metric_name="service_heartbeat_interval",
        severity=AlertSeverity.HIGH,
        threshold=AlertThreshold(
            operator="gte",
            value=300.0,  # 5 minutes
            duration=30.0,  # sustained for 30 seconds
        ),
        evaluation_interval=60.0,
        evaluation_count=3,
    ))

    # Error rate alert: fire if error rate > 5% over 5-min window
    add_alert_rule(AlertRule(
        id="err_rate_high",
        name="High Error Rate",
        description="Application error rate exceeds acceptable threshold",
        alert_type=AlertType.ERROR_RATE,
        metric_name="error_rate_percent",
        severity=AlertSeverity.CRITICAL,
        threshold=AlertThreshold(
            operator="gte",
            value=5.0,  # 5%
            duration=60.0,  # sustained for 1 minute
        ),
        evaluation_interval=30.0,
        evaluation_count=2,
    ))

    # Latency alert: fire if P99 latency > 2s
    add_alert_rule(AlertRule(
        id="latency_p99_high",
        name="High P99 Latency",
        description="99th percentile latency exceeds acceptable threshold",
        alert_type=AlertType.LATENCY,
        metric_name="latency_p99_ms",
        severity=AlertSeverity.MEDIUM,
        threshold=AlertThreshold(
            operator="gte",
            value=2000.0,  # 2 seconds
            duration=15.0,  # sustained for 15 seconds
        ),
        evaluation_interval=10.0,
        evaluation_count=3,
    ))

    # Memory usage alert: fire if memory > 85%
    add_alert_rule(AlertRule(
        id="mem_usage_high",
        name="High Memory Usage",
        description="Process memory usage exceeds 85%",
        alert_type=AlertType.METRIC_THRESHOLD,
        metric_name="memory_usage_percent",
        severity=AlertSeverity.HIGH,
        threshold=AlertThreshold(
            operator="gte",
            value=85.0,  # 85%
            duration=60.0,  # sustained for 1 minute
        ),
        evaluation_interval=30.0,
        evaluation_count=2,
    ))

    # CPU usage alert: fire if CPU > 80%
    add_alert_rule(AlertRule(
        id="cpu_usage_high",
        name="High CPU Usage",
        description="Process CPU usage exceeds 80%",
        alert_type=AlertType.METRIC_THRESHOLD,
        metric_name="cpu_usage_percent",
        severity=AlertSeverity.MEDIUM,
        threshold=AlertThreshold(
            operator="gte",
            value=80.0,  # 80%
            duration=30.0,  # sustained for 30 seconds
        ),
        evaluation_interval=15.0,
        evaluation_count=2,
    ))

    # Audit log gap alert: fire if audit log sync lag > 60 seconds
    add_alert_rule(AlertRule(
        id="audit_lag_high",
        name="Audit Log Sync Lag",
        description="Audit log writing lag exceeds acceptable threshold",
        alert_type=AlertType.METRIC_THRESHOLD,
        metric_name="audit_log_lag_seconds",
        severity=AlertSeverity.HIGH,
        threshold=AlertThreshold(
            operator="gte",
            value=60.0,  # 60 seconds
            duration=15.0,  # sustained for 15 seconds
        ),
        evaluation_interval=30.0,
        evaluation_count=2,
    ))


__all__ = [
    "Alert",
    "AlertRule",
    "AlertThreshold",
    "AlertSeverity",
    "AlertState",
    "AlertType",
    "AlertManager",
    "AlertStore",
    "get_alert_store",
    "get_alert_manager",
    "add_alert_rule",
    "emit_alert",
    "get_alert",
    "list_alerts",
    "list_by_severity",
    "list_firing",
    "resolve_alert",
    "install_production_rules",
]
