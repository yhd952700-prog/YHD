"""Tests for src.reliability.metrics (stdlib-only metric primitives)."""
from __future__ import annotations

import pytest

from src.reliability import metrics
from src.reliability.metrics import Counter, Gauge, Histogram, MetricRegistry


# --------------------------------------------------------------------------- #
# Metric name validation
# --------------------------------------------------------------------------- #
def test_invalid_metric_name_raises():
    with pytest.raises(ValueError):
        Counter("has-dash")
    with pytest.raises(ValueError):
        Gauge("")
    # valid: underscore + alnum
    Gauge("valid_name_2")


# --------------------------------------------------------------------------- #
# Counter
# --------------------------------------------------------------------------- #
def test_counter_inc_and_sample():
    c = Counter("c1", "demo")
    assert c.sample() == 0.0
    c.inc()
    assert c.sample() == 1.0
    c.inc(2.5)
    assert c.sample() == 3.5


def test_counter_cannot_decrease():
    c = Counter("c2")
    with pytest.raises(ValueError):
        c.inc(-1.0)
    with pytest.raises(ValueError):
        c.set(-5.0)


def test_counter_prom_lines():
    c = Counter("c3", "a counter")
    c.inc(4)
    lines = c._prom_lines()
    assert "# HELP c3 a counter" in lines
    assert "# TYPE c3 counter" in lines
    assert "c3 4.0" in lines


# --------------------------------------------------------------------------- #
# Gauge
# --------------------------------------------------------------------------- #
def test_gauge_set_inc_dec():
    g = Gauge("g1")
    g.set(10)
    assert g.sample() == 10.0
    g.inc(5)
    assert g.sample() == 15.0
    g.dec(3)
    assert g.sample() == 12.0


# --------------------------------------------------------------------------- #
# Histogram
# --------------------------------------------------------------------------- #
def test_histogram_buckets_cumulative_and_sum_count():
    h = Histogram("h1", buckets=(0.1, 0.5, 1.0))
    for v in (0.05, 0.2, 0.2, 2.0):
        h.observe(v)
    lines = {ln.split(" ", 1)[0]: ln for ln in h._prom_lines()}
    # cumulative bucket counts: 0.05->le=0.1, 0.2,0.2->le=0.5, 2.0->+Inf
    assert 'h1_bucket{le="0.1"} 1' in lines["h1_bucket{le=\"0.1\"}"]
    assert 'h1_bucket{le="0.5"} 3' in lines["h1_bucket{le=\"0.5\"}"]
    assert 'h1_bucket{le="1.0"} 3' in lines["h1_bucket{le=\"1.0\"}"]
    assert 'h1_bucket{le="+Inf"} 4' in lines["h1_bucket{le=\"+Inf\"}"]
    assert "h1_sum 2.45" in lines["h1_sum"]
    assert "h1_count 4" in lines["h1_count"]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_register_dedup_returns_same():
    reg = MetricRegistry()
    g1 = reg.register(Gauge("rg1"))
    g2 = reg.register(Gauge("rg1"))
    assert g1 is g2


def test_registry_type_conflict_raises():
    reg = MetricRegistry()
    reg.register(Counter("rc1"))
    with pytest.raises(TypeError):
        reg.register(Gauge("rc1"))


def test_registry_collect_text_sorted_and_separated():
    reg = MetricRegistry()
    reg.register(Gauge("z_last"))
    reg.register(Counter("a_first"))
    text = reg.collect_text()
    assert text.index("# TYPE a_first") < text.index("# TYPE z_last")
    # empty line between metrics
    assert "\n\n" in text


# --------------------------------------------------------------------------- #
# Global get-or-create helpers
# --------------------------------------------------------------------------- #
def test_global_get_or_create_dedup():
    a = metrics.counter("global_c")
    b = metrics.counter("global_c")
    assert a is b
    assert metrics.get_metric_registry().get("global_c") is a


def test_global_get_or_create_type_conflict_raises():
    metrics.counter("gc1")
    with pytest.raises(TypeError):
        metrics.gauge("gc1")


def test_global_prometheus_text_exposition():
    metrics.gauge("global_g").set(42)
    text = metrics.collect_prometheus_text()
    assert "global_g 42.0" in text
    assert "# TYPE global_g gauge" in text
