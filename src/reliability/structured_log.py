"""Structured (JSON-line) logging helpers for LIUHAO reliability signals.

NON-INTRUSIVE: creates its own logger namespace ``liuhao.reliability`` and does
NOT replace or reconfigure the application's root logging. Each record is emitted
as a single-line JSON object so downstream log shippers / Loki / ES can parse
fields directly.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

_DEFAULT_LOGGER = "liuhao.reliability"


class _JsonFormatter(logging.Formatter):
    """Render a LogRecord (with ``structured_fields``) as one JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "structured_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class StructuredLogger:
    """Thin wrapper over stdlib ``logging`` that emits structured JSON lines."""

    def __init__(self, name: str = _DEFAULT_LOGGER, level: int = logging.INFO) -> None:
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(_JsonFormatter())
            self._logger.addHandler(handler)
            self._logger.setLevel(level)
            self._logger.propagate = False

    def _emit(self, level: int, msg: str, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if v is not None}
        self._logger.log(level, msg, extra={"structured_fields": clean})

    def debug(self, msg: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, msg, **fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, **fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, **fields)

    def critical(self, msg: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, msg, **fields)


_LOGGER_CACHE: Dict[str, StructuredLogger] = {}


def get_struct_logger(name: str = _DEFAULT_LOGGER) -> StructuredLogger:
    cached = _LOGGER_CACHE.get(name)
    if cached is None:
        cached = StructuredLogger(name)
        _LOGGER_CACHE[name] = cached
    return cached
