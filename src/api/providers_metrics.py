from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderMetric:
    provider: str
    model: str
    timestamp: str
    latency_ms: float
    success_rate: float


class ProviderMetricsRepository:
    """Small SQLite-backed repository for provider metrics."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./verify_metrics.db")
        self._initialize()

    @staticmethod
    def _resolve_sqlite_path(database_url: str) -> Path:
        parsed = urlparse(database_url)
        if parsed.scheme != "sqlite":
            raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

        if parsed.path in ("", ":memory:"):
            return Path(":memory:")

        path_value = parsed.path
        if path_value.startswith("/") and not path_value.startswith("//"):
            path_value = path_value.lstrip("/")

        database_file = Path(path_value)
        if not database_file.is_absolute():
            database_file = (Path.cwd() / database_file).resolve()
        return database_file

    def _connect(self) -> sqlite3.Connection:
        parsed = urlparse(self.database_url)
        if parsed.scheme == "sqlite":
            database_path = self._resolve_sqlite_path(self.database_url)
            if database_path != Path(":memory:"):
                database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(database_path))
            connection.row_factory = sqlite3.Row
            return connection

        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_metric_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    success_rate REAL NOT NULL
                )
                """
            )
            connection.commit()

    def record(self, metric: ProviderMetric) -> ProviderMetric:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_metric_samples (provider, model, timestamp, latency_ms, success_rate)
                VALUES (?, ?, ?, ?, ?)
                """,
                (metric.provider, metric.model, metric.timestamp, metric.latency_ms, metric.success_rate),
            )
            connection.commit()
        return metric

    def list_recent(self, limit: int = 10) -> List[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider, model, timestamp, latency_ms, success_rate
                FROM provider_metric_samples
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM provider_metric_samples").fetchone()
        return int(row[0]) if row is not None else 0
