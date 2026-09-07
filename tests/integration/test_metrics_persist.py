from __future__ import annotations

from src.api.providers_metrics import ProviderMetricsRepository
from src.api.providers_metrics_persist import persist_sample


def test_persistence_round_trip(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'verify_metrics.db'}"

    rows = persist_sample(database_url)
    repository = ProviderMetricsRepository(database_url)

    assert len(rows) >= 1
    assert repository.count() >= 1
    assert rows[0]["provider"] == "openai"
    assert rows[0]["model"] == "gpt-4o-mini"
