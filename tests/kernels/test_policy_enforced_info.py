"""Every policy-related INFO line must state whether the gate is armed.

``policy_enforced=False`` means record-only (the verdict is written but the
action is not stopped); ``True`` means the enforcement cut-line is armed for
this action/tier. Without it an operator cannot tell the two modes apart when
diagnosing a denial that did -- or did not -- actually block anything.
"""

from __future__ import annotations

import logging

from src.kernels._crosscutting import kernel_action


def _kernel_action_info_lines(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "liuhao.kernel.crosscutting"
        and record.getMessage().startswith("kernel_action=")
    ]


def test_record_only_info_line_says_policy_not_enforced(caplog) -> None:
    caplog.set_level(logging.INFO, logger="liuhao.kernel.crosscutting")

    @kernel_action("memory.store")
    def store() -> int:
        return 1

    store()

    lines = _kernel_action_info_lines(caplog)
    assert lines, "kernel_action INFO line was not emitted"
    assert "policy_enforced=False" in lines[-1]


def test_armed_info_line_says_policy_enforced(monkeypatch, caplog) -> None:
    import src.kernels._crosscutting as crosscutting

    # An allow verdict keeps the armed path on the success/INFO branch while the
    # gate itself is genuinely armed (enforce=True on a HIGH action).
    monkeypatch.setattr(
        crosscutting,
        "_adjudicate",
        lambda action, risk: (
            "allow",
            "test_allow",
            {"type": "service", "principal": "test-service"},
        ),
    )
    caplog.set_level(logging.INFO, logger="liuhao.kernel.crosscutting")

    @kernel_action("identity.grant_permission", enforce=True)
    def privileged() -> str:
        return "ok"

    assert privileged() == "ok"

    lines = _kernel_action_info_lines(caplog)
    assert lines, "kernel_action INFO line was not emitted"
    assert "policy_enforced=True" in lines[-1]
