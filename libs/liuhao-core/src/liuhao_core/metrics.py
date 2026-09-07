"""Prometheus metrics for liuhao AI OS"""
from prometheus_client import Counter, Gauge, Histogram, start_http_server
import threading
import time

# Counters
api_requests_total = Counter(
    "liuhao_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"]
)

api_errors_total = Counter(
    "liuhao_api_errors_total",
    "Total API errors",
    ["method", "endpoint"]
)

# Gauges
active_connections = Gauge(
    "liuhao_active_connections",
    "Active connections"
)

# Histograms
request_duration = Histogram(
    "liuhao_request_duration_seconds",
    "Request duration in seconds",
    ["endpoint"]
)

# Daily cost tracker
daily_cost_total = Gauge(
    "liuhao_daily_cost_total",
    "Total daily cost in USD",
    ["user_id"]
)

def start_metrics_server(port: int = 8000):
    """Start Prometheus metrics HTTP server."""
    start_http_server(port)
    logger.info(f"Metrics server started on port {port}")
