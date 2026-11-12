"""Regression test for U39: Windows sandbox RLIMIT gap.

On non-Unix (``os.name == 'nt'``) SubprocessBackend cannot apply
``setrlimit`` / ``preexec_fn`` resource limits, so it must *honestly warn*
instead of silently pretending isolation is active. The warning branch is
forced via ``monkeypatch`` on ``os.name`` so this test is platform
independent (passes on both Windows and Linux CI).
"""
import logging
import sys

import pytest

from src.plugins.sandbox.backends import subprocess_backend
from src.plugins.sandbox.backends.subprocess_backend import (
    ResourceLimits,
    SubprocessBackend,
)

_LOG = "src.plugins.sandbox.backends.subprocess_backend"


def test_windows_limits_not_enforced_emits_warning(monkeypatch, caplog):
    monkeypatch.setattr(subprocess_backend.os, "name", "nt")
    backend = SubprocessBackend()
    limits = ResourceLimits(
        memory_limit=1024 * 1024,
        max_pids=4,
        max_output_size=4096,
    )
    with caplog.at_level(logging.WARNING, logger=_LOG):
        result = backend.execute(
            "u39-warn",
            [sys.executable, "-c", "print('hello')"],
            resource_limits=limits,
            timeout=10,
        )
    assert result.success is True
    assert any(
        "NOT enforced" in rec.message for rec in caplog.records
    ), "expected a warning that Windows resource limits are not enforced"


def test_windows_no_limits_requested_no_warning(monkeypatch, caplog):
    monkeypatch.setattr(subprocess_backend.os, "name", "nt")
    backend = SubprocessBackend()
    with caplog.at_level(logging.WARNING, logger=_LOG):
        backend.execute(
            "u39-no-warn",
            [sys.executable, "-c", "print('hi')"],
            timeout=10,
        )
    assert not any(
        "NOT enforced" in rec.message for rec in caplog.records
    ), "no warning expected when no RLIMIT-backed limits are requested"
