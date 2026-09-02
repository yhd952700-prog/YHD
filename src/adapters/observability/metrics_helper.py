"""Structured metrics helper for LiuHao AI OS.

Provides Prometheus-format metrics export for production observability.
"""

import time
import threading
from collections import defaultdictfrom typing import Dict, Any

# Metrics counters and gauges
_request_count = defaultdict(int)
_request_latency = defaultdict(list)
_active_requests = threading.Lock()

# Custom metrics can be registered here


def increment_counter(name: str, labels: Dict[str, str] = None) -> None:
    """Increment a metrics counter."""
    if labels:
        key = f"{name}:{':'.join(labels.get(k, '') for k in sorted(labels))}"
    else:
        key = name
    _request_count[key] += 1


def observe_latency(name: str, duration: float, labels: Dict[str, str] = None) -> None:
    """Observe a latency measurement."""
    if labels:
        key = f"{name}:{':'.join(labels.get(k, '') for k in sorted(labels))}"
    else:
        key = name
    _request_latency[key].append(duration)


def get_metrics() -> str:
    """Export all metrics in Prometheus format."""
    lines = []
    
    # Export counters
    for name, count in _request_count.items():
        lines.append(f'# HELP {name} Request counter')
        lines.append(f'# TYPE {name} counter')
        lines.append(f'{name} {count}')
    
    # Export latency summaries
    for name, latencies in _request_latency.items():
        if latencies:
            avg = sum(latencies) / len(latencies)
            min_lat = min(latencies)
            max_lat = max(latencies)
            lines.append(f'# HELP {name} Latency summary')
            lines.append(f'# TYPE {name} summary')
            lines.append(f'{name}_count {len(latencies)}')
            lines.append(f'{name}_sum {sum(latencies)}')
            lines.append(f'{name}_avg {avg}')
            lines.append(f'{name}_max {max_lat}')
            lines.append(f'{name}_min {min_lat}')
    
    return '
'.join(lines)


# Singleton access
_metrics_instance = None

def get_metrics_collector() -> 'MetricsCollector':
    """Get the global metrics collector instance."""
    global _metrics_instance
    if _metrics_instance is None:
        from .metrics_helper import MetricsCollector
        _metrics_instance = MetricsCollector()
    return _metrics_instance


class MetricsCollector:
    """Production-ready metrics collector."""
    
    def __init__(self):
        self.counters = defaultdict(int)
        self.latencies = defaultdict(list)
        self._lock = threading.Lock()
    
    def increment(self, name: str, labels: Dict[str, str] = None) -> None:
        with self._lock:
            if labels:
                key = f"{name}:{':'.join(labels.get(k, '') for k in sorted(labels))}"
            else:
                key = name
            self.counters[key] += 1
    
    def observe(self, name: str, duration: float, labels: Dict[str, str] = None) -> None:
        with self._lock:
            if labels:
                key = f"{name}:{':'.join(labels.get(k, '') for k in sorted(labels))}"
            else:
                key = name
            self.latencies[key].append(duration)
    
    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        
        for name, count in self.counters.items():
            lines.append(f'# HELP {name} Request counter')
            lines.append(f'# TYPE {name} counter')
            lines.append(f'{name} {count}')
        
        for name, latencies in self.latencies.items():
            if latencies:
                avg = sum(latencies) / len(latencies)
                min_lat = min(latencies)
                max_lat = max(latencies)
                lines.append(f'# HELP {name} Latency summary')
                lines.append(f'# TYPE {name} summary')
                lines.append(f'{name}_count {len(latencies)}')
                lines.append(f'{name}_sum {sum(latencies)}')
                lines.append(f'{name}_avg {avg}')
                lines.append(f'{name}_max {max_lat}')
                lines.append(f'{name}_min {min_lat}')
        
        return '
'.join(lines)
