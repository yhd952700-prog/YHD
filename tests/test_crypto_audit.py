"""D19 retention / high-water-mark tests for the in-memory audit loggers.

These prove the ring-buffer eviction accounting and the monotonic HWM counter
(telemetry only — NOT audit evidence) for both ``CryptoAuditLogger`` (HC-09)
and ``AuditKernel`` (HC-10). The HWM file holds ONLY a counter and must never
influence a trust decision.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.security.audit_logger import CryptoAuditLogger, CryptoOperation  # noqa: E402
from src.security.audit_policy import AuditKernel, AuditEventType  # noqa: E402


# ---------------------------------------------------------------------------
# CryptoAuditLogger (HC-09) — D19
# ---------------------------------------------------------------------------
def test_ring_eviction_counts_dropped_and_evicts_oldest() -> None:
    """D19: >10000 events evicts the oldest and increments dropped_count."""
    logger = CryptoAuditLogger(component_name="p08-d19", hwm_path=None)
    op = CryptoOperation.ENCRYPT
    first = logger.log(op, key_name="oldest", success=True)
    for _ in range(10000):  # 10001 total -> exactly one eviction
        logger.log(op, success=True)
    assert logger.dropped_count == 1
    # the oldest event is no longer in the buffer
    ids = [e.event_id for e in logger.get_events()]
    assert first.event_id not in ids
    assert len(logger._events) == 10000


def test_high_water_mark_survives_restart_and_is_monotonic(tmp_path) -> None:
    """D19: HWM persists across instances (same file) and is monotonic."""
    hwm = str(tmp_path / "audit_hwm.json")
    l1 = CryptoAuditLogger(component_name="p08-d19", hwm_path=hwm)
    for _ in range(37):
        l1.log(CryptoOperation.ENCRYPT, success=True)
    assert l1.high_water_mark == 37
    assert os.path.isfile(hwm)
    raw = json.loads(open(hwm, encoding="utf-8").read())
    assert raw == {"entries_ever_written": 37}

    # A new instance loading the same HWM file continues from 37 (monotonic).
    l2 = CryptoAuditLogger(component_name="p08-d19", hwm_path=hwm)
    assert l2.high_water_mark == 37
    for _ in range(3):
        l2.log(CryptoOperation.ENCRYPT, success=True)
    assert l2.high_water_mark == 40
    # the first instance is untouched
    assert l1.high_water_mark == 37


def test_clear_does_not_reset_high_water_mark() -> None:
    """D19: clear() wipes events but the HWM counter stays monotonic."""
    logger = CryptoAuditLogger(component_name="p08-d19", hwm_path=None)
    for _ in range(5):
        logger.log(CryptoOperation.ENCRYPT, success=True)
    assert logger.high_water_mark == 5
    logger.clear()
    assert len(logger._events) == 0
    assert logger.high_water_mark == 5  # NOT reset
    assert logger.dropped_count == 0   # NOT incremented by clear
    # logging after clear continues the counter
    logger.log(CryptoOperation.ENCRYPT, success=True)
    assert logger.high_water_mark == 6


def test_hwm_file_is_counter_only_and_fail_open(tmp_path) -> None:
    """D19: the HWM file holds ONLY a counter; a malformed file fails open (0)."""
    hwm = str(tmp_path / "audit_hwm.json")
    logger = CryptoAuditLogger(component_name="p08-d19", hwm_path=hwm)
    logger.log(CryptoOperation.ENCRYPT, success=True)
    content = open(hwm, encoding="utf-8").read()
    assert content == json.dumps({"entries_ever_written": 1})

    # Corrupt the file -> next instance must NOT crash and must start at 0.
    with open(hwm, "w", encoding="utf-8") as fh:
        fh.write("not-json{")
    broken = CryptoAuditLogger(component_name="p08-d19", hwm_path=hwm)
    assert broken.high_water_mark == 0


# ---------------------------------------------------------------------------
# AuditKernel (HC-10) — D19 (same pattern)
# ---------------------------------------------------------------------------
def test_audit_kernel_ring_eviction_counts_dropped() -> None:
    kernel = AuditKernel(hwm_path=None)
    etype = next(iter(AuditEventType))
    for _ in range(10001):
        kernel.log(etype, "p", result="ok")
    assert kernel.dropped_count == 1
    assert len(kernel._entries) == 10000


def test_audit_kernel_high_water_mark_survives_restart(tmp_path) -> None:
    hwm = str(tmp_path / "ak_hwm.json")
    k1 = AuditKernel(hwm_path=hwm)
    etype = next(iter(AuditEventType))
    for _ in range(12):
        k1.log(etype, "p", result="ok")
    assert k1.high_water_mark == 12
    k2 = AuditKernel(hwm_path=hwm)
    assert k2.high_water_mark == 12
    k2.log(etype, "p", result="ok")
    assert k2.high_water_mark == 13
