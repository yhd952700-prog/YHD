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
    """Register production alert rules with tuned thresholds (OB-04 gap).

    PENDING METRIC EMITTERS (honest status, do not whitewash):
    Every rule below references a metric (``error_rate_percent``,
    ``memory_usage_percent``, ``cpu_usage_percent``, ``latency_p99_ms``,
    ``service_heartbeat_interval``, ``audit_log_lag_seconds``) for which NO
    emitter exists anywhere in the codebase. ``AlertManager.evaluate_all`` only
    fires a rule when that metric name is present in the metrics dict passed to
    it, and nothing feeds these names today — so enabling them would make
    ``evaluate_all`` silently inert (decorative). They are therefore registered
    but ``enabled=False`` until real metric emitters are wired. We deliberately
    do NOT invent fake metric emitters to make them "pass".

    The execution-failure alerts (see ``execution_binding``) are the REAL,
    working path: they are emitted directly on EventBus failure events and reach
    the configured sink via ``AlertStore.emit_alert`` -> ``dispatch_alert``.
    """
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
        enabled=False,  # PENDING METRIC EMITTER: no service_heartbeat_interval source
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
        enabled=False,  # PENDING METRIC EMITTER: no error_rate_percent source
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
        enabled=False,  # PENDING METRIC EMITTER: no latency_p99_ms source
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
        enabled=False,  # PENDING METRIC EMITTER: no memory_usage_percent source
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
        enabled=False,  # PENDING METRIC EMITTER: no cpu_usage_percent source
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
        enabled=False,  # PENDING METRIC EMITTER: no audit_log_lag_seconds source
    ))

    # 同步进 AlertStore，使读取面 GET /v1/alerts/rules 能如实返回规则状态。
    # 不改动 enabled，也不发明指标发射器——只是把"已注册"这件事落盘成可读记录。
    _persist_rules_to_store()



def _persist_rules_to_store() -> None:
    """把已注册的规则同步进 AlertStore，使其成为可被读取的 system-of-record。

    诚实说明：install_production_rules() 把规则注册进 AlertManager（评估注册表），
    但读取面 ``GET /v1/alerts/rules`` 读出的是 AlertStore（持久化记录）。两者必须
    同步，否则规则"注册了却读不到"。本函数不修改任何 enabled 状态，也不发明指标发射器。
    """
    store = AlertStore()
    for _rule in get_alert_manager().list_rules():
        store.add_rule(_rule)


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
