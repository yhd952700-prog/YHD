"""Tests for src.reliability.structured_log (JSON-line logging)."""
from __future__ import annotations

import json
import logging

from src.reliability.structured_log import (
    StructuredLogger,
    _JsonFormatter,
    get_struct_logger,
)


def _capture(logger: StructuredLogger):
    """Replace handlers with a single JSON-formatting capturing handler."""
    logger._logger.handlers.clear()
    handler = _ListHandler()
    handler.setFormatter(_JsonFormatter())
    logger._logger.addHandler(handler)
    logger._logger.setLevel(logging.DEBUG)
    return handler


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


def test_struct_logger_emits_json_line():
    sl = StructuredLogger("test.rel")
    handler = _capture(sl)
    sl.info("hello", scope="audit", count=3)

    assert len(handler.records) == 1
    payload = json.loads(handler.records[0])
    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.rel"
    assert payload["scope"] == "audit"
    assert payload["count"] == 3
    assert "ts" in payload


def test_struct_logger_skips_none_fields():
    sl = StructuredLogger("test.rel2")
    handler = _capture(sl)
    sl.warning("msg", keep="yes", drop=None)
    payload = json.loads(handler.records[0])
    assert payload["keep"] == "yes"
    assert "drop" not in payload


def test_get_struct_logger_caches_by_name():
    a = get_struct_logger("cache.name")
    b = get_struct_logger("cache.name")
    assert a is b
    c = get_struct_logger("other.name")
    assert a is not c


def test_struct_logger_error_and_critical():
    sl = StructuredLogger("test.rel3")
    handler = _capture(sl)
    sl.error("boom")
    sl.critical("kaboom")
    assert len(handler.records) == 2
    levels = [json.loads(r)["level"] for r in handler.records]
    assert "ERROR" in levels
    assert "CRITICAL" in levels


def test_json_formatter_includes_exc_info():
    import sys

    fmt = _JsonFormatter()
    try:
        raise RuntimeError("demo")
    except RuntimeError:
        record = logging.LogRecord(
            "x", logging.ERROR, "f", 1, "msg", None, sys.exc_info()
        )
    line = fmt.format(record)
    payload = json.loads(line)
    assert "exc" in payload
    assert "demo" in payload["exc"]
