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
from src.security.persistence_health import (  # noqa: E402
    PersistenceHealth,
    resolve_persistence_health,
    unaccounted_loss,
)


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


# ---------------------------------------------------------------------------
# D19-A+ persistence-health accounting — the half of A+ that was never wired
# ---------------------------------------------------------------------------
# A+ shipped the three counters (HWM / dropped / buffered) but nothing ever
# RECONCILED them, so "evidence was DESTROYED" and "evidence was evicted as
# expected" were indistinguishable at runtime. These pin the reconciliation for
# both chains. Every alert state has a healthy-path CONTROL beside it, because
# an accountability signal that cries wolf on a healthy chain is worse than none.


@pytest.mark.parametrize(
    "hwm,dropped,buffered,expected",
    [
        # Nothing was ever written -> idle.
        (0, 0, 0, PersistenceHealth.IDLE),
        # 120 written, 20 evicted, 100 buffered -> the ledger balances.
        (120, 20, 100, PersistenceHealth.EXPECTED_EVICTION),
        # No eviction happened yet -> still balanced, still healthy.
        (5, 0, 5, PersistenceHealth.EXPECTED_EVICTION),
        # Buffer empty but the durable counter says entries existed -> gone.
        (37, 0, 0, PersistenceHealth.DESTROYED),
        # 10 written, 0 evicted, 9 buffered -> one entry vanished silently.
        (10, 0, 9, PersistenceHealth.TRUNCATED),
        # A rolled-back/corrupt counter must never crash telemetry.
        (-1, -1, -1, PersistenceHealth.IDLE),
    ],
)
def test_verdict_is_a_pure_function_of_the_counters(
    hwm, dropped, buffered, expected
) -> None:
    assert resolve_persistence_health(hwm, dropped, buffered) is expected


def test_unaccounted_loss_is_never_negative() -> None:
    assert unaccounted_loss(3, 10, 5) == 0
    assert unaccounted_loss(10, 0, 9) == 1


class TestCryptoLoggerPersistenceHealth:
    """HC-09 (CryptoAuditLogger)."""

    def test_fresh_logger_is_idle(self) -> None:
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        assert logger.persistence_health() is PersistenceHealth.IDLE

    def test_healthy_buffer_is_not_an_alert(self) -> None:
        """CONTROL: a fully-accounted healthy chain must NOT raise an alert."""
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        for _ in range(5):
            logger.log(CryptoOperation.ENCRYPT, success=True)
        assert logger.persistence_health() is PersistenceHealth.EXPECTED_EVICTION
        assert logger.stats()["evidence_destroyed"] is False

    def test_ring_eviction_is_accounted_not_truncated(self) -> None:
        """CONTROL: documented eviction is expected loss, not corruption."""
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        for _ in range(10001):
            logger.log(CryptoOperation.ENCRYPT, success=True)
        assert logger.dropped_count == 1
        assert logger.persistence_health() is PersistenceHealth.EXPECTED_EVICTION
        assert logger.stats()["unaccounted_loss"] == 0

    def test_clear_is_reported_as_destroyed(self) -> None:
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        for _ in range(5):
            logger.log(CryptoOperation.ENCRYPT, success=True)
        logger.clear()
        # HWM stays 5 by design while the buffer is empty -> the loss is now
        # reported instead of silent.
        assert logger.persistence_health() is PersistenceHealth.DESTROYED

    def test_restart_is_reported_as_destroyed(self, tmp_path) -> None:
        """The A+ promise itself: restart loss becomes quantified, not silent."""
        hwm = str(tmp_path / "hwm.json")
        first = CryptoAuditLogger(component_name="p08-f1", hwm_path=hwm)
        for _ in range(9):
            first.log(CryptoOperation.ENCRYPT, success=True)

        restarted = CryptoAuditLogger(component_name="p08-f1", hwm_path=hwm)
        assert restarted.high_water_mark == 9
        assert len(restarted._events) == 0
        assert restarted.persistence_health() is PersistenceHealth.DESTROYED

    def test_silent_removal_is_truncated(self) -> None:
        """Entries that vanish WITHOUT an eviction count are unexplained."""
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        for _ in range(5):
            logger.log(CryptoOperation.ENCRYPT, success=True)
        assert logger.persistence_health() is PersistenceHealth.EXPECTED_EVICTION

        logger._events.popleft()  # behind the logger's back: no dropped bump
        assert logger.persistence_health() is PersistenceHealth.TRUNCATED
        assert logger.stats()["unaccounted_loss"] == 1

    def test_stats_exposes_the_verdict(self) -> None:
        logger = CryptoAuditLogger(component_name="p08-f1", hwm_path=None)
        logger.log(CryptoOperation.ENCRYPT, success=True)
        stats = logger.stats()
        assert stats["persistence_health"] == (
            PersistenceHealth.EXPECTED_EVICTION.value
        )
        assert stats["buffered_entries"] == 1
        assert stats["high_water_mark"] == 1
        assert stats["evidence_destroyed"] is False


class TestAuditKernelPersistenceHealth:
    """HC-10 (AuditKernel) — same contract, independently pinned."""

    def test_fresh_kernel_is_idle(self) -> None:
        assert AuditKernel(hwm_path=None).persistence_health() is (
            PersistenceHealth.IDLE
        )

    def test_healthy_kernel_is_not_an_alert(self) -> None:
        """CONTROL."""
        kernel = AuditKernel(hwm_path=None)
        etype = next(iter(AuditEventType))
        for _ in range(4):
            kernel.log(etype, "p", result="ok")
        assert kernel.persistence_health() is PersistenceHealth.EXPECTED_EVICTION

    def test_eviction_is_accounted(self) -> None:
        """CONTROL."""
        kernel = AuditKernel(hwm_path=None)
        etype = next(iter(AuditEventType))
        for _ in range(10001):
            kernel.log(etype, "p", result="ok")
        assert kernel.dropped_count == 1
        assert kernel.persistence_health() is PersistenceHealth.EXPECTED_EVICTION

    def test_silent_removal_is_truncated(self) -> None:
        kernel = AuditKernel(hwm_path=None)
        etype = next(iter(AuditEventType))
        for _ in range(5):
            kernel.log(etype, "p", result="ok")
        kernel._entries.popleft()
        assert kernel.persistence_health() is PersistenceHealth.TRUNCATED

    def test_stats_carries_the_verdict(self) -> None:
        kernel = AuditKernel(hwm_path=None)
        etype = next(iter(AuditEventType))
        kernel.log(etype, "p", result="ok")
        stats = kernel.stats()
        assert stats["persistence_health"] == (
            PersistenceHealth.EXPECTED_EVICTION.value
        )
        assert stats["unaccounted_loss"] == 0
        assert stats["evidence_destroyed"] is False
