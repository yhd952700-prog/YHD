"""Shell safety gate tests — fail-closed policy for the ShellAdapter.

These tests are SAFE: they never execute destructive commands (no `rm`/`rm -rf`)
and never perform network egress. Where a real command is run it is a benign
`echo` probe used only to prove shell grammar is NOT interpreted.
"""

import pytest

from src.ai.world_interface import ShellAdapter, WorldRequest
from src.ai.world_interface_shell_gate import (
    build_shell_world_request,
    make_world_interface_with_shell,
    shell_parse_literal,
)


# --------------------------------------------------------------------------- #
# (a) an autonomous interface DENIES a shell run by default
# --------------------------------------------------------------------------- #
def test_autonomous_interface_denies_shell_by_default(monkeypatch):
    # Realistic autonomous policy: only allow non-shell, workspace actions.
    # It does NOT arm the shell adapter, so a shell run must be denied. Explicitly
    # disarm the global host-command gate so the test asserts the gate-off path
    # deterministically (the denial also holds when the gate is armed + policy denies).
    from src.ai.host_command.enablement import reload as _hc_reload

    monkeypatch.delenv("LIUHAO_HOST_COMMAND_ENABLED", raising=False)
    _hc_reload()

    def authorize(request):
        return request.adapter != "shell"

    wi = make_world_interface_with_shell(actor="autonomous", authorize=authorize)
    result = wi.execute(
        WorldRequest(adapter="shell", action="run", params={"command": "echo hi"})
    )
    assert result.success is False
    assert "deny" in (result.error or "").lower()


def test_autonomous_interface_requires_authorize_to_build():
    with pytest.raises(ValueError):
        make_world_interface_with_shell(actor="autonomous", authorize=None)


# --------------------------------------------------------------------------- #
# (b) shell=True blocked unless explicit human-arming authorize_fn AND logged
# --------------------------------------------------------------------------- #
def test_shell_true_requires_human_arm_token_at_build():
    with pytest.raises(ValueError):
        build_shell_world_request("echo hi", shell=True, human_arm_token=None)


def test_shell_true_with_token_but_unarmed_authorize_is_blocked(monkeypatch):
    from src.ai.host_command.enablement import reload as _hc_reload

    monkeypatch.delenv("LIUHAO_HOST_COMMAND_ENABLED", raising=False)
    _hc_reload()

    def authorize(request):
        return False  # policy does not arm shell

    wi = make_world_interface_with_shell(actor="autonomous", authorize=authorize)
    req = build_shell_world_request(
        "echo hi", shell=True, human_arm_token="tok-123"
    )
    assert req.metadata.get("human_arm_token") == "tok-123"
    result = wi.execute(req)
    assert result.success is False
    assert "deny" in (result.error or "").lower()


def test_shell_true_with_token_and_armed_authorize_is_allowed_and_logged(
    caplog, monkeypatch,
):
    import logging

    from src.ai.host_command.enablement import reload as _hc_reload

    # Under p36-wi-safety shell is gated by the global host-command enablement
    # switch; arm it so the armed+tokened policy can actually execute.
    monkeypatch.setenv("LIUHAO_HOST_COMMAND_ENABLED", "1")
    _hc_reload()

    def authorize(request):
        # Explicitly arms shell when a human token is present.
        return request.adapter == "shell" and bool(
            request.metadata.get("human_arm_token")
        )

    wi = make_world_interface_with_shell(actor="autonomous", authorize=authorize)
    with caplog.at_level(logging.WARNING):
        req = build_shell_world_request(
            "echo ARMED", shell=True, human_arm_token="tok-abc"
        )
    # The arm attempt must be logged (masked token, not raw).
    assert any("SHELL_GATE" in r.message for r in caplog.records)
    assert "tok-abc" not in "".join(r.message for r in caplog.records)

    result = wi.execute(req)
    assert result.success is True
    assert "ARMED" in (result.output or {}).get("stdout", "")


# --------------------------------------------------------------------------- #
# (c) default shlex path treats injection as literal; empty raises; no exec
# --------------------------------------------------------------------------- #
def test_shlex_path_raises_on_empty_command():
    adapter = ShellAdapter()
    with pytest.raises(ValueError):
        adapter.execute(
            WorldRequest(adapter="shell", action="run", params={"command": ""})
        )


def test_shlex_path_treats_semicolon_as_literal_not_executed():
    # Real shell would chain and run `echo B`; shlex must NOT. Safe `echo` probe.
    adapter = ShellAdapter()
    out = adapter.execute(
        WorldRequest(
            adapter="shell", action="run",
            params={"command": "echo A; echo B", "shell": False},
        )
    )
    # Single command executed; the `; echo B` is a literal argument, not a chain.
    assert out["returncode"] == 0
    assert "A; echo B" in out["stdout"]
    # No second line => shell grammar was NOT interpreted (no chained echo B).
    assert out["stdout"].count("\n") == 1


def test_shlex_parse_shows_injection_tokenized_literally():
    parsed = shell_parse_literal("; rm -rf /")
    # The injection string is tokenized; `rm` is never argv[0], so it is never
    # executed as a command. Injection surface is closed under shell=False.
    assert parsed["argv"][0] == ";"
    assert parsed["argv"] == [";", "rm", "-rf", "/"]


# --------------------------------------------------------------------------- #
# (d) gate is fail-closed (no authorize_fn + autonomous -> deny)
# --------------------------------------------------------------------------- #
def test_gate_fail_closed_no_authorize_autonomous():
    with pytest.raises(ValueError):
        make_world_interface_with_shell(actor="autonomous", authorize=None)


def test_gate_human_actor_builds_without_authorize():
    # The construction fail-closed rule only binds the autonomous actor; a human
    # interface may be built without an authorize policy (human sovereignty).
    wi = make_world_interface_with_shell(actor="human", authorize=None)
    # Human default-allow still reaches non-shell adapters via the policy gate.
    assert wi.authorize(WorldRequest(adapter="filesystem", action="read")) is True
    # But shell stays default-deny for BOTH actors unless a policy arms it.
    assert (
        wi.authorize(
            WorldRequest(adapter="shell", action="run", params={"command": "x"})
        )
        is False
    )
