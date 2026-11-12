"""Tests for src.reliability.tracing (lightweight span hooks)."""
from __future__ import annotations

import time

from src.reliability import metrics
from src.reliability.tracing import Span, Tracer, get_tracer


def test_span_records_duration_on_normal_exit():
    tracer = Tracer()
    before = metrics.histogram("liuhao_span_duration_seconds").sample()
    with tracer.span("work", attributes={"k": "v"}):
        time.sleep(0.001)
    after = metrics.histogram("liuhao_span_duration_seconds").sample()
    # at least one observation recorded (sum increased)
    assert after > before


def test_span_does_not_suppress_exceptions():
    tracer = Tracer()
    try:
        with tracer.span("boom"):
            raise ValueError("kaboom")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("span should not suppress the exception")


def test_get_tracer_returns_shared_instance():
    assert get_tracer() is get_tracer()


def test_span_is_reusable_object():
    tracer = Tracer()
    span = tracer.span("named")
    assert isinstance(span, Span)
    assert span.name == "named"
    assert span.attributes == {}
