"""Regression + security verification for the sandbox isolation assurance gate.

Every test asserts the UNTRUSTED-CODE posture (fail-closed when armed+HIGH/CRITICAL,
degraded-continue otherwise) -- never that code "exists". Uses a duck-typed fake
manager so the suite runs on any host without gVisor / Docker installed.
"""

from __future__ import annotations

import logging

from src.plugins.sandbox.assurance import (
    GENUINE_ISOLATION_BACKENDS,
    SandboxIsolationUnavailable,
    assure_sandbox_isolation,
    available_isolation_backends,
    genuine_isolation_available,
)
from src.plugins.sandbox.backends.base import SandboxBackendType


class _FakeBackend:
    def __init__(self, btype: SandboxBackendType, available: bool) -> None:
        self._btype = btype
        self._available = available

    @property
    def backend_type(self) -> SandboxBackendType:
        return self._btype

    def is_available(self) -> bool:
        return self._available


class _FakeManager:
    """Duck-typed stand-in: only needs ``_backends`` keyed by SandboxBackendType."""

    def __init__(self, available: set) -> None:
        self._backends = {}
        for btype in GENUINE_ISOLATION_BACKENDS:
            self._backends[btype] = _FakeBackend(btype, btype in available)
        # Always register a host subprocess backend (the unsafe fallback).
        self._backends[SandboxBackendType.SUBPROCESS] = _FakeBackend(
            SandboxBackendType.SUBPROCESS, True
        )


def test_genuine_available_true_when_any_real_backend():
    mgr = _FakeManager(available={SandboxBackendType.GVISOR})
    assert genuine_isolation_available(mgr) is True
    assert available_isolation_backends(mgr) == {"gvisor"}


def test_genuine_available_false_when_only_subprocess():
    mgr = _FakeManager(available=set())  # only host subprocess exists
    assert genuine_isolation_available(mgr) is False
    assert available_isolation_backends(mgr) == set()


def test_assure_allows_when_genuine_backend_present():
    mgr = _FakeManager(available={SandboxBackendType.RESTRICTED_PYTHON})
    # Must not raise, regardless of enforce / tier.
    assert assure_sandbox_isolation(mgr, risk_tier="CRITICAL", enforce=True) is mgr
    assert assure_sandbox_isolation(mgr, risk_tier="LOW", enforce=False) is mgr


def test_assure_degraded_continue_when_not_armed():
    mgr = _FakeManager(available=set())
    # Default posture: warn, do NOT raise, return manager (host subprocess).
    assert assure_sandbox_isolation(mgr, risk_tier="CRITICAL", enforce=False) is mgr


def test_assure_fail_closed_when_armed_high_critical():
    mgr = _FakeManager(available=set())
    for tier in ("HIGH", "CRITICAL"):
        try:
            assure_sandbox_isolation(mgr, risk_tier=tier, enforce=True)
            assert False, f"{tier} must be denied when armed + no isolation"
        except SandboxIsolationUnavailable:
            pass


def test_assure_low_medium_still_degraded_when_armed():
    mgr = _FakeManager(available=set())
    # LOW/MEDIUM keep degraded-continue even when armed (tier split).
    for tier in ("LOW", "MEDIUM"):
        assert (
            assure_sandbox_isolation(mgr, risk_tier=tier, enforce=True) is mgr
        )


def test_assure_skips_when_trusted_code():
    mgr = _FakeManager(available=set())
    # requires_untrusted_code=False -> no isolation check at all.
    assert (
        assure_sandbox_isolation(
            mgr, risk_tier="CRITICAL", enforce=True, requires_untrusted_code=False
        )
        is mgr
    )


def test_assure_env_arming(monkeypatch):
    mgr = _FakeManager(available=set())
    monkeypatch.setenv("LIUHAO_SANDBOX_ENFORCE", "on")
    try:
        assure_sandbox_isolation(mgr, risk_tier="HIGH")  # enforce from env
        assert False, "env-armed HIGH must deny"
    except SandboxIsolationUnavailable:
        pass


def test_assure_emits_warning_when_degraded(caplog):
    mgr = _FakeManager(available=set())
    with caplog.at_level(logging.WARNING, logger="src.plugins.sandbox.assurance"):
        assure_sandbox_isolation(mgr, risk_tier="CRITICAL", enforce=False)
    assert any(
        "DEGRADED-CONTINUE" in r.message for r in caplog.records
    ), "degraded-continue must be loudly logged"


# ---- Integration: the gate is wired into SandboxBackendManager.execute ------ #
def test_manager_execute_untrusted_fail_closed_when_armed(monkeypatch):
    from src.plugins.sandbox.backends.manager import SandboxBackendManager

    # Force the "no genuine isolation backend" condition deterministically.
    monkeypatch.setattr(
        "src.plugins.sandbox.assurance.genuine_isolation_available", lambda mgr: False
    )
    mgr = SandboxBackendManager()
    try:
        mgr.execute(
            execution_id="iso-fc-1",
            command=["echo", "should-not-run"],
            requires_untrusted_code=True,
            risk_tier="HIGH",
            enforce_isolation=True,
        )
        assert False, "armed untrusted HIGH must be fail-closed"
    except SandboxIsolationUnavailable:
        pass


def test_manager_execute_default_preserves_behaviour(monkeypatch):
    from src.plugins.sandbox.backends.manager import SandboxBackendManager

    monkeypatch.setattr(
        "src.plugins.sandbox.assurance.genuine_isolation_available", lambda mgr: False
    )
    mgr = SandboxBackendManager()
    # Default requires_untrusted_code=False -> the isolation check is skipped,
    # behaviour unchanged (runs on host subprocess).
    result = mgr.execute(
        execution_id="iso-default-1", command=["echo", "legacy_call"]
    )
    assert result.success is True
