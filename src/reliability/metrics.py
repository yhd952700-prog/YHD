"""Stdlib-only reliability metric primitives for LIUHAO.

Why stdlib-only: the existing ``src.observability.metrics`` already exports
HTTP/provider/agent metrics to Prometheus via ``prometheus_client``. This module is a
deliberately dependency-free complement so reliability signals import and run in any
environment and can be scraped as Prometheus text without coupling to the OTel/Prom
stack's lifecycle. It also emits a Prometheus text-exposition format directly.

Ships with: tests (``tests/reliability/test_metrics.py``), a Prometheus exporter, and a
recovery note (a missing/crashed authoritative store is reported as
``audit_evidence_status = 0``, never silently swallowed).

Recovery consideration: metrics are OBSERVATIONAL only. They never mutate the audit
record of record; fork healing is owned by U3 (chain-fork recovery framework).
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

_DEFAULT_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Metric:
    """Base metric (thread-safe)."""

    _TYPE = "untyped"

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid metric name: {name!r}")
        self.name = name
        self.description = description
        self.unit = unit
        self._lock = threading.Lock()

    def sample(self) -> float:
        raise NotImplementedError

    def _prom_lines(self) -> List[str]:
        lines = []
        if self.description:
            lines.append(f"# HELP {self.name} {self.description}")
        lines.append(f"# TYPE {self.name} {self._TYPE}")
        return lines


class Counter(Metric):
    """Monotonic counter (may only increase)."""

    _TYPE = "counter"

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        super().__init__(name, description, unit)
        self._value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter cannot decrease")
        with self._lock:
            self._value += amount

    def set(self, value: float) -> None:
        if value < 0:
            raise ValueError("counter cannot be negative")
        with self._lock:
            self._value = value

    def sample(self) -> float:
        return self._value

    def _prom_lines(self) -> List[str]:
        lines = super()._prom_lines()
        lines.append(f"{self.name} {float(self._value)}")
        return lines


class Gauge(Metric):
    """Gauge (may increase or decrease)."""

    _TYPE = "gauge"

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        super().__init__(name, description, unit)
        self._value = 0.0

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value -= amount

    def sample(self) -> float:
        return self._value

    def _prom_lines(self) -> List[str]:
        lines = super()._prom_lines()
        lines.append(f"{self.name} {float(self._value)}")
        return lines


class Histogram(Metric):
    """Histogram with cumulative bucket counts (Prometheus-style)."""

    _TYPE = "histogram"

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "",
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> None:
        super().__init__(name, description, unit)
        self._buckets = tuple(buckets or _DEFAULT_BUCKETS)
        self._count = 0
        self._sum = 0.0
        self._bucket_counts = [0] * (len(self._buckets) + 1)

    def observe(self, value: float) -> None:
        with self._lock:
            self._count += 1
            self._sum += value
            for i, upper in enumerate(self._buckets):
                if value <= upper:
                    self._bucket_counts[i] += 1
                    break
            else:
                self._bucket_counts[-1] += 1

    def sample(self) -> float:
        return self._sum

    def _prom_lines(self) -> List[str]:
        lines = super()._prom_lines()
        cumulative = 0
        for i, upper in enumerate(self._buckets):
            cumulative += self._bucket_counts[i]
            lines.append(f'{self.name}_bucket{{le="{upper}"}} {cumulative}')
        cumulative += self._bucket_counts[-1]
        lines.append(f'{self.name}_bucket{{le="+Inf"}} {cumulative}')
        lines.append(f"{self.name}_sum {self._sum}")
        lines.append(f"{self.name}_count {self._count}")
        return lines


class MetricRegistry:
    """Thread-safe registry of named metrics."""

    def __init__(self) -> None:
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: Metric) -> Metric:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                if not isinstance(existing, type(metric)):
                    raise TypeError(
                        f"metric {metric.name!r} already registered as "
                        f"{type(existing).__name__}"
                    )
                return existing
            self._metrics[metric.name] = metric
            return metric

    def get(self, name: str) -> Optional[Metric]:
        with self._lock:
            return self._metrics.get(name)

    def collect_text(self) -> str:
        with self._lock:
            names = sorted(self._metrics)
        out: List[str] = []
        for name in names:
            out.extend(self._metrics[name]._prom_lines())
            out.append("")
        return "\n".join(out)


REGISTRY = MetricRegistry()


def get_metric_registry() -> MetricRegistry:
    return REGISTRY


def _get_or_create(metric_cls, name, description, unit, **extra):
    existing = REGISTRY.get(name)
    if existing is not None:
        if not isinstance(existing, metric_cls):
            raise TypeError(
                f"metric {name!r} already registered as {type(existing).__name__}"
            )
        return existing
    return REGISTRY.register(metric_cls(name, description, unit, **extra))


def counter(name: str, description: str = "", unit: str = "") -> Counter:
    return _get_or_create(Counter, name, description, unit)


def gauge(name: str, description: str = "", unit: str = "") -> Gauge:
    return _get_or_create(Gauge, name, description, unit)


def histogram(
    name: str,
    description: str = "",
    unit: str = "",
    buckets: Optional[Tuple[float, ...]] = None,
) -> Histogram:
    return _get_or_create(
        Histogram, name, description, unit, buckets=buckets or _DEFAULT_BUCKETS
    )


def collect_prometheus_text(registry: Optional[MetricRegistry] = None) -> str:
    return (registry or REGISTRY).collect_text()
