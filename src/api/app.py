from __future__ import annotations

import os
from typing import Any

from .providers_metrics import (
    ProviderMetric,
    ProviderMetricsRepository,
    default_metrics_database_url,
)
from ..ai.observability import observe


class MetricsApplication:
    """Minimal application object that exposes the main runtime surface."""

    def __init__(self, database_url: str | None = None) -> None:
        # 历史默认是相对 cwd 的 ``sqlite:///./verify_metrics.db``：从用户主目录
        # 启动会把指标库悄悄写到 C 盘。未显式配置时改由数据根解析（含旧库迁移）。
        self.database_url = (
            database_url
            or (os.environ.get("DATABASE_URL") or "").strip()
            or default_metrics_database_url()
        )
        self.repository = ProviderMetricsRepository(self.database_url)

    @observe("api.metrics_application.health")
    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "database_url": self.database_url,
            "provider_samples": self.repository.count(),
        }

    def ready(self) -> dict[str, Any]:
        """Readiness probe that can actually report ``not_ready``.

        Previously this returned ``"ready" if count >= 0 else "not_ready"`` --
        and SQL ``COUNT(*)`` is never negative, so the ``not_ready`` branch was
        unreachable and the probe reported healthy without checking anything.
        It now performs a real liveness probe (``repository.is_ready()``) and
        reports ``not_ready`` when the store is genuinely unreachable.
        """
        ready = self.repository.is_ready()
        count = self.repository.count() if ready else 0
        return {
            "status": "ready" if ready else "not_ready",
            "database_url": self.database_url,
            "provider_samples": count,
        }

    @observe("api.metrics_application.record_metric")
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

    @observe("api.metrics_application.list_metrics")
    def list_metrics(self, limit: int = 10) -> dict[str, Any]:
        rows = self.repository.list_recent(limit=limit)
        return {"count": len(rows), "items": rows}


app = MetricsApplication()
