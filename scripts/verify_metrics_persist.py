#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./verify_metrics.db")
METRICS_PERSIST = os.environ.get("METRICS_PERSIST", "0")


def _persist_sqlite_sample(database_url: str) -> list[dict[str, object]]:
    try:
        from src.api.providers_metrics_persist import persist_sample

        return persist_sample(database_url)
    except Exception:
        parsed = urlparse(database_url)
        if parsed.scheme != "sqlite":
            raise

        sqlite_path = parsed.path
        if sqlite_path in ("", ":memory:"):
            db_path = ":memory:"
        else:
            sqlite_path = sqlite_path.lstrip("/")
            db_path = str((Path.cwd() / sqlite_path).resolve()) if not Path(sqlite_path).is_absolute() else sqlite_path

        connection = sqlite3.connect(db_path)
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
        connection.execute(
            """
            INSERT INTO provider_metric_samples (provider, model, timestamp, latency_ms, success_rate)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("openai", "gpt-4o-mini", "2026-08-25T00:00:00Z", 220.5, 0.99),
        )
        connection.commit()
        rows = connection.execute(
            """
            SELECT provider, model, timestamp, latency_ms, success_rate
            FROM provider_metric_samples
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        connection.close()
        return [dict(row) for row in rows]


def _is_postgres_url(database_url: str) -> bool:
    return database_url.startswith("postgresql://") or database_url.startswith("postgres://")


def _run_postgres_verification(database_url: str) -> list[dict[str, object]]:
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PostgreSQL verification requires sqlalchemy to be installed.") from exc

    sample = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latency_ms": 220.5,
        "success_rate": 0.99,
    }

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS provider_metric_samples (
                    id SERIAL PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latency_ms DOUBLE PRECISION NOT NULL,
                    success_rate DOUBLE PRECISION NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_metric_samples (provider, model, timestamp, latency_ms, success_rate)
                VALUES (:provider, :model, :timestamp, :latency_ms, :success_rate)
                """
            ),
            sample,
        )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT provider, model, timestamp, latency_ms, success_rate
                FROM provider_metric_samples
                ORDER BY id DESC
                LIMIT 5
                """
            )
        ).mappings().all()

    return [dict(row) for row in rows]


def main() -> int:
    if METRICS_PERSIST != "1":
        print("METRICS_PERSIST is not enabled; skipping persistence verification.")
        return 0

    if _is_postgres_url(DATABASE_URL):
        rows = _run_postgres_verification(DATABASE_URL)
    else:
        rows = _persist_sqlite_sample(DATABASE_URL)

    print(f"DATABASE_URL={DATABASE_URL}")
    print(f"METRICS_PERSIST={METRICS_PERSIST}")
    print(f"persisted_rows={len(rows)}")
    for row in rows:
        print(f"sample={row}")

    if not rows:
        raise RuntimeError("Persistence verification failed: no rows were written to the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
