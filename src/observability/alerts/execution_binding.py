"""Wire execution-failure events to the real alert subsystem (closed loop).

Previously ``AlertManager.evaluate_alerts`` was never driven by any loop and
execution events never reached the alert store, so alerts never actually
fired — the alert subsystem was a stub even though the plumbing existed.

This module subscribes to the EventBus for real execution failures
(``action_failed``, ``task_failed``, and ``execution_completed`` with failed
tasks) at the widest scope (L7) and emits *real* alerts to the store + console.

The handler is fail-safe: it never raises, so a synchronous EventBus dispatch
(handler exceptions are swallowed into DeadLetter by the bus) can never break
the bus or drop other subscribers.
"""

from __future__ import annotations

import logging
import uuid
from typing import List

from src.kernels.event import (
    EventScope,
    Event,
    subscribe_event,
)
from src.observability.alerts.models import (
    Alert,
    AlertRule,
    AlertSeverity,
    AlertState,
    AlertThreshold,
    AlertType,
)
from src.observability.alerts.store import emit_alert

logger = logging.getLogger(__name__)

# Default alert rule ids registered for autonomous-execution failures.
RULE_TASK_FAILED = "exec_task_failed"
RULE_ACTION_FAILED = "exec_action_failed"
RULE_EXEC_FAILED = "exec_plan_failed"

# Subscribed event types — real execution-failure signals from the kernel.
_SUBSCRIBED_EVENTS = ("action_failed", "task_failed", "execution_completed")

# Track installation so install() is idempotent (safe to call repeatedly).
_installed_sub_ids: List[str] = []


def _emit_exec_alert(
    name: str,
    severity: AlertSeverity,
    message: str,
    source: str,
    tags: dict,
) -> None:
    """Build, emit (store + console) and log a real alert. Never raises."""
    alert = Alert(
        id=str(uuid.uuid4()),
        alert_type=AlertType.CUSTOM,
        name=name,
        severity=severity,
        message=message,
        source=source,
        state=AlertState.FIRING,
        tags=tags,
    )
    # Real dispatch: write to the alerts store (AlertStore.emit_alert). That
    # call now persists the alert AND delivers it through the configured sink(s)
    # (console log + webhook when LIUHAO_ALERT_WEBHOOK is set). No stub — the
    # store path is what actually persists and pages. The console log is emitted
    # by ConsoleLogSink so every alert (not just execution ones) is surfaced.
    emit_alert(alert)


def _on_execution_event(event: Event) -> None:
    """Fail-safe handler: map a real execution failure event to a real alert.

    Never raises. Context:
      * The bus dispatches handlers synchronously and swallows handler
        exceptions into DeadLetter (see src/kernels/event/__init__.py:150), so
        a raising handler would silently lose the event. We never raise.
      * Even if it did raise, the bus would catch it — but we still guard with
        try/except so we control the failure mode (log, not crash).
    """
    try:
        etype = event.type
        data = event.data or {}
        cid = event.correlation_id

        if etype == "task_failed":
            # Default rule: any task_failed -> HIGH alert.
            _emit_exec_alert(
                name="Autonomous Task Failed",
                severity=AlertSeverity.HIGH,
                message=(
                    f"task {data.get('task_id')} (goal {data.get('goal_id')}) "
                    f"failed: {data.get('error')}"
                ),
                source="execution_kernel",
                tags={
                    "event": etype,
                    "correlation_id": cid,
                    "task_id": str(data.get("task_id")),
                },
            )
            return

        if etype == "action_failed":
            # Default rule: any action_failed with CRITICAL priority -> CRITICAL
            # alert; otherwise HIGH (the kernel publishes action_failed at HIGH).
            if getattr(event.priority, "name", "") == "CRITICAL":
                severity = AlertSeverity.CRITICAL
            else:
                severity = AlertSeverity.HIGH
            _emit_exec_alert(
                name="Autonomous Action Failed",
                severity=severity,
                message=(
                    f"action {data.get('action_id')} "
                    f"(task {data.get('task_id')}) failed: {data.get('error')}"
                ),
                source="execution_kernel",
                tags={
                    "event": etype,
                    "correlation_id": cid,
                    "action_id": str(data.get("action_id")),
                },
            )
            return

        if etype == "execution_completed":
            failed = data.get("failed") or 0
            try:
                failed_n = int(failed)
            except (TypeError, ValueError):
                failed_n = 0
            if failed_n > 0:
                # Default rule: completed plan with failed tasks -> HIGH alert.
                _emit_exec_alert(
                    name="Execution Plan Completed With Failures",
                    severity=AlertSeverity.HIGH,
                    message=(
                        f"goal {data.get('goal_id')} plan {data.get('plan_id')} "
                        f"completed with {failed_n} failed task(s)"
                    ),
                    source="execution_kernel",
                    tags={
                        "event": etype,
                        "correlation_id": cid,
                        "goal_id": str(data.get("goal_id")),
                    },
                )
            return
    except Exception as exc:  # noqa: BLE001 - fail-safe: never break the bus
        logger.error(
            "execution alert binding handler failed for %s: %s",
            getattr(event, "type", "?"),
            exc,
            exc_info=True,
        )


def install_execution_alert_rules() -> None:
    """Register default alert rules for autonomous-execution failures.

    These rules document the binding contract in the AlertManager and can be
    evaluated by ``AlertManager.evaluate_all`` if a metrics feed is added later.
    They are idempotent (keyed by id).
    """
    from src.observability.alerts.models import add_alert_rule

    rules = [
        AlertRule(
            id=RULE_TASK_FAILED,
            name="Autonomous Task Failed",
            description="Any task_failed execution event raises a HIGH alert.",
            alert_type=AlertType.CUSTOM,
            metric_name="execution.task_failed",
            severity=AlertSeverity.HIGH,
            threshold=AlertThreshold(operator="gte", value=1.0),
            receiver="console",
        ),
        AlertRule(
            id=RULE_ACTION_FAILED,
            name="Autonomous Action Failed (Critical)",
            description=(
                "Any action_failed with CRITICAL priority raises a CRITICAL alert."
            ),
            alert_type=AlertType.CUSTOM,
            metric_name="execution.action_failed",
            severity=AlertSeverity.CRITICAL,
            threshold=AlertThreshold(operator="gte", value=1.0),
            receiver="console",
        ),
        AlertRule(
            id=RULE_EXEC_FAILED,
            name="Execution Plan Failed",
            description=(
                "A completed execution with failed tasks raises a HIGH alert."
            ),
            alert_type=AlertType.CUSTOM,
            metric_name="execution.plan_failed",
            severity=AlertSeverity.HIGH,
            threshold=AlertThreshold(operator="gte", value=1.0),
            receiver="console",
        ),
    ]
    for rule in rules:
        add_alert_rule(rule)


def install_execution_alert_binding() -> List[str]:
    """Idempotently subscribe the execution-failure handler to the EventBus.

    Import-safe and callable from ``src/gateway/main.py`` at startup or
    standalone from tests. Has no effect beyond subscribing once. Returns the
    list of subscription ids.
    """
    if _installed_sub_ids:
        return list(_installed_sub_ids)

    install_execution_alert_rules()

    for etype in _SUBSCRIBED_EVENTS:
        sid = subscribe_event(etype, _on_execution_event, scope=EventScope.L7)
        _installed_sub_ids.append(sid)

    return list(_installed_sub_ids)


def uninstall_execution_alert_binding() -> None:
    """Remove the subscriptions (used by tests to keep the global bus clean)."""
    if not _installed_sub_ids:
        return
    try:
        from src.kernels.event import get_event_bus

        bus = get_event_bus()
        for sid in _installed_sub_ids:
            try:
                bus.unsubscribe(sid)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass
    _installed_sub_ids.clear()


__all__ = [
    "install_execution_alert_binding",
    "install_execution_alert_rules",
    "uninstall_execution_alert_binding",
    "RULE_TASK_FAILED",
    "RULE_ACTION_FAILED",
    "RULE_EXEC_FAILED",
]
