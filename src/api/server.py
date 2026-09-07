from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .events_ws import router as events_router

from .app import MetricsApplication

app = FastAPI(title="LiuHao AI OS Metrics API", version="0.1.0")

metrics_app = MetricsApplication()

# Integrate WebSocket event router (P1-7 真实性修复)
app.include_router(events_router)


class ProviderMetricIn(BaseModel):
    provider: str
    model: str
    latency_ms: float
    success_rate: float


@app.get("/health")
def health() -> dict:
    return metrics_app.health()


@app.get("/ready")
def ready() -> dict:
    return metrics_app.ready()


@app.get("/metrics")
def list_metrics(limit: int = 10) -> dict:
    return metrics_app.list_metrics(limit=limit)


@app.post("/metrics/provider")
def add_metric(payload: ProviderMetricIn) -> dict:
    return metrics_app.record_metric(
        provider=payload.provider,
        model=payload.model,
        latency_ms=payload.latency_ms,
        success_rate=payload.success_rate,
    )
