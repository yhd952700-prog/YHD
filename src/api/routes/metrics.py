from __future__ import annotations

from typing import Any

from ..app import MetricsApplication


class MetricsRoutes:
    def __init__(self, application: MetricsApplication) -> None:
        self.application = application

    def health(self) -> dict[str, Any]:
        return self.application.health()

    def ready(self) -> dict[str, Any]:
        return self.application.ready()

    def latest(self, limit: int = 10) -> dict[str, Any]:
        return self.application.list_metrics(limit=limit)

    def add_metric(self, provider: str, model: str, latency_ms: float, success_rate: float) -> dict[str, Any]:
        return self.application.record_metric(provider, model, latency_ms, success_rate)


def register_routes(application: MetricsApplication) -> MetricsRoutes:
    return MetricsRoutes(application)
