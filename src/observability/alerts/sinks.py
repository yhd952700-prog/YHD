"""Real notification sinks for the alert subsystem (p36-obs-delivery).

The original ``AlertStore.emit_alert`` only persisted a JSON file and relied on
the *caller* to log to console. There was no real notification sink, so an
operator was never paged when an execution failure fired an alert. This module
adds a sink abstraction so ``emit_alert`` actually *delivers*:

  * :class:`ConsoleLogSink`  — the surviving "console" half of the original
    file/console behaviour (the file half stays in ``AlertStore``).
  * :class:`WebhookSink`     — POSTs the alert JSON to ``LIUHAO_ALERT_WEBHOOK``.

``emit_alert`` dispatches to the configured sink(s). The design is **fail-closed**:

  * If no webhook is configured, the console sink still runs (no crash).
  * If the webhook POST fails, the error is logged and swallowed — it never
    propagates into the alert path. The alert is already persisted to the store
    *before* dispatch, so a sink failure cannot lose the alert record.

No fake metric emitters are invented here. Metric-threshold rules that have no
emitter are disabled in ``src/observability/alerts/__init__.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, List, Optional

from .models import Alert

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "LIUHAO_ALERT_WEBHOOK"
WEBHOOK_TIMEOUT_ENV = "LIUHAO_ALERT_WEBHOOK_TIMEOUT"
_DEFAULT_TIMEOUT = 5.0


class AlertSink:
    """A delivery target for alerts."""

    def send(self, alert: Alert) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleLogSink(AlertSink):
    """Logs the alert to the console (the surviving "console" half of the
    original file/console behaviour). File persistence is still handled by the
    AlertStore; this sink only surfaces the alert for operators watching logs.
    """

    def send(self, alert: Alert) -> None:
        logger.warning(
            "[ALERT][%s] %s :: %s",
            alert.severity.name,
            alert.name,
            alert.message,
        )


class WebhookSink(AlertSink):
    """Delivers the alert as JSON to a webhook URL via HTTP POST.

    Fail-closed: any network/serialization error is logged and swallowed, never
    raised into the alert path. ``poster`` is injectable for tests (defaults to
    ``requests.post``).
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        poster: Optional[Callable] = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self._poster = poster

    def send(self, alert: Alert) -> None:
        payload = alert.to_dict()
        try:
            import requests

            poster = self._poster or requests.post
            resp = poster(self.url, json=payload, timeout=self.timeout)
            # Tolerate non-2xx without raising — the alert is already persisted.
            status = getattr(resp, "status_code", None)
            if status is not None and not (200 <= status < 300):
                logger.error(
                    "alert webhook POST %s returned HTTP %s (alert still persisted)",
                    self.url,
                    status,
                )
        except Exception as exc:  # noqa: BLE001 - fail-closed: never break alert path
            logger.error(
                "alert webhook POST %s failed (alert NOT delivered, still persisted): %s",
                self.url,
                exc,
            )


def build_sinks(*, webhook_url: Optional[str] = None) -> List[AlertSink]:
    """Build the active sink list from configuration.

    The console sink is always present. A webhook sink is appended when a URL is
    configured (``webhook_url`` arg or ``LIUHAO_ALERT_WEBHOOK`` env).
    """
    sinks: List[AlertSink] = [ConsoleLogSink()]
    url = webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_ENV)
    if url:
        timeout = float(os.environ.get(WEBHOOK_TIMEOUT_ENV, _DEFAULT_TIMEOUT))
        sinks.append(WebhookSink(url, timeout=timeout))
    return sinks


def dispatch_alert(
    alert: Alert,
    *,
    sinks: Optional[List[AlertSink]] = None,
    webhook_url: Optional[str] = None,
) -> None:
    """Dispatch an alert to the configured sink(s). Fail-closed.

    Each sink is invoked independently; a failure in one sink never prevents
    the others (or the already-persisted alert record) from completing.
    """
    if sinks is None:
        sinks = build_sinks(webhook_url=webhook_url)
    for sink in sinks:
        try:
            sink.send(alert)
        except Exception as exc:  # noqa: BLE001 - sink must never break alert path
            logger.error(
                "alert sink %s failed (alert already persisted): %s",
                sink.__class__.__name__,
                exc,
            )


__all__ = [
    "WEBHOOK_ENV",
    "AlertSink",
    "ConsoleLogSink",
    "WebhookSink",
    "build_sinks",
    "dispatch_alert",
]
