from __future__ import annotations

from .providers_metrics import ProviderMetric, ProviderMetricsRepository


def persist_sample(database_url: str | None = None, *, provider: str = "openai", model: str = "gpt-4o-mini") -> list[dict[str, float | str]]:
    repository = ProviderMetricsRepository(database_url)
    sample = ProviderMetric(
        provider=provider,
        model=model,
        timestamp="2026-08-25T00:00:00Z",
        latency_ms=220.5,
        success_rate=0.99,
    )
    repository.record(sample)
    return repository.list_recent(limit=5)
