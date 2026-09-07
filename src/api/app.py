from __future__ import annotations

import os
from typing import Any

from .providers_metrics import ProviderMetric, ProviderMetricsRepository


class MetricsApplication:
    """Minimal application object that exposes the main runtime surface."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./verify_metrics.db")
        self.repository = ProviderMetricsRepository(self.database_url)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "database_url": self.database_url,
            "provider_samples": self.repository.count(),
        }

    def ready(self) -> dict[str, Any]:
        count = self.repository.count()
        return {
            "status": "ready" if count >= 0 else "not_ready",
            "database_url": self.database_url,
            "provider_samples": count,
        }

    def record_metric(
        self,
        provider: str,
        model: str,
        latency_ms: float,
        success_rate: float,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        metric = ProviderMetric(
            provider=provider,
            model=model,
            timestamp=timestamp or "2026-08-25T00:00:00Z",
            latency_ms=float(latency_ms),
            success_rate=float(success_rate),
        )
        self.repository.record(metric)
        return {
            "status": "recorded",
            "metric": {
                "provider": metric.provider,
                "model": metric.model,
                "timestamp": metric.timestamp,
                "latency_ms": metric.latency_ms,
                "success_rate": metric.success_rate,
            },
        }

    def list_metrics(self, limit: int = 10) -> dict[str, Any]:
        rows = self.repository.list_recent(limit=limit)
        return {"count": len(rows), "items": rows}


app = MetricsApplication()
