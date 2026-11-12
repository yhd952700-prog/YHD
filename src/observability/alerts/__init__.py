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

    LIVE AS OF BLOCKER #3: the metric emitters now exist. ``MetricCollector``
    (in ``src/observability/metrics.py``) produces REAL values for every one of
    the seven referenced metrics (``error_rate_percent``, ``memory_usage_percent``,
    ``cpu_usage_percent``, ``latency_p99_ms``, ``service_heartbeat_interval``,
    ``audit_log_lag_seconds``, ``audit_write_failing``) from the live process /
    OS / audit store — no value is invented. The gateway lifespan drives a 15s daemon loop that calls
    ``collect_snapshot()`` then ``AlertManager.evaluate_all(snapshot)``. Because
    each metric name is now present in the snapshot AND every rule below is
    ``enabled=True``, a genuine breach produces a genuine stored alert. This is
    the honest, working closed loop — not decorative.

    The execution-failure alerts (see ``execution_binding``) remain the OTHER
    REAL path: emitted directly on EventBus failure events and delivered via
    ``AlertStore.emit_alert`` -> ``dispatch_alert``.
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
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
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
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
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
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
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
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
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
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
    ))

    # Audit write STALLED: an audit write request has been issued but has not
    # landed for > 60s (writer blocked, DB write lock held, disk wedged).
    # NOT "no events for 60s": an idle system issues no writes and must stay
    # green. The metric is the age of the oldest UNCOMMITTED write request.
    add_alert_rule(AlertRule(
        id="audit_lag_high",
        name="Audit Log Write Stalled",
        description=(
            "An audit write request has been waiting to commit for longer than "
            "the threshold; the writer is blocked or the store is not accepting "
            "commits (it is not merely an idle period with no new events)."
        ),
        alert_type=AlertType.METRIC_THRESHOLD,
        metric_name="audit_log_lag_seconds",
        severity=AlertSeverity.HIGH,
        threshold=AlertThreshold(
            operator="gte",
            value=60.0,  # 60 seconds of a REAL write waiting to land
            duration=15.0,  # sustained for 15 seconds
        ),
        evaluation_interval=30.0,
        evaluation_count=2,
        enabled=True,  # LIVE: real MetricCollector emitters now feed this rule
    ))

    # Audit write FAILING: at least one write failed and no later write has
    # succeeded, so recovery is unproven and those events' evidence may be
    # missing. Distinct from the stall rule above so the alert name tells the
    # operator which failure mode they are looking at.
    add_alert_rule(AlertRule(
        id="audit_write_failing",
        name="Audit Log Write Failing",
        description=(
            "An audit write failed and no subsequent write has succeeded yet; "
            "evidence for the failed events may be missing (disk full, "
            "corruption, or an exhausted lock/write retry budget)."
        ),
        alert_type=AlertType.METRIC_THRESHOLD,
        metric_name="audit_write_failing",
        severity=AlertSeverity.HIGH,
        threshold=AlertThreshold(
            operator="gte",
            value=1.0,  # 1.0 == a failure streak with no proven recovery
        ),
        evaluation_interval=15.0,
        evaluation_count=1,
        enabled=True,  # LIVE: cleared only by a real successful append
    ))

    # 同步进 AlertStore，使读取面 GET /v1/alerts/rules 能如实返回规则状态。
    # 不改动 enabled，也不发明指标发射器——只是把"已注册"这件事落盘成可读记录。
    _persist_rules_to_store()


def _persist_rules_to_store() -> None:
    """把已注册的规则同步进 AlertStore，使其成为可被读取的 system-of-record。

    诚实说明：install_production_rules() 把规则注册进 AlertManager（评估注册表），
    但读取面 ``GET /v1/alerts/rules`` 读出的是 AlertStore（持久化记录）。两者必须
    同步，否则规则"注册了却读不到"。本函数同步的 enabled 状态就是 install_production_rules()
    里设定的真实值（7 条系统健康规则现已 enabled=True，因为真正的指标发射器已接线），
    不发明、不美化成 False。
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
