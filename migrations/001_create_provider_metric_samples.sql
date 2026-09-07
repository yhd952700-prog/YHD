-- Migration 001: provider metric samples
CREATE TABLE IF NOT EXISTS provider_metric_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    success_rate REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_provider_metric_samples_provider_model
    ON provider_metric_samples (provider, model);

CREATE INDEX IF NOT EXISTS idx_provider_metric_samples_timestamp
    ON provider_metric_samples (timestamp);
