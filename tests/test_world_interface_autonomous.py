"""U1/U5: autonomous capability dispatch is default-deny; human keeps default-allow.

Proves:
  * autonomous ``shell=True`` is denied unless an explicit human-arming policy
    allows it;
  * human ``shell=True`` stays usable (no regression);
  * autonomous non-shell is default-deny;
  * human non-shell stays allowed.

The ``actor`` flag is opt-in at construction: only an interface explicitly built
with ``actor="autonomous"`` gets the hardened posture. Human-built interfaces
keep the historical default-allow (human sovereignty).
"""
from __future__ import annotations

import os

from src.ai.world_interface import (
    WorldInterface,
    WorldRequest,
    ShellAdapter,
    FilesystemAdapter,
)


def _shell_request(cmd: str) -> WorldRequest:
    return WorldRequest(
        adapter="shell", action="run",
        params={"command": cmd, "shell": True},
    )


def test_autonomous_shell_default_denied():
    wi = WorldInterface(actor="autonomous", adapters=[ShellAdapter()])
    res = wi.execute(_shell_request("echo should-not-run"))
    assert res.success is False


def test_autonomous_shell_armed_allows(monkeypatch):
    # Under p36-wi-safety shell is gated by the global host-command enablement
    # switch (LIUHAO_HOST_COMMAND_ENABLED); arm it so the armed policy can proceed.
    from src.ai.host_command.enablement import reload as _hc_reload

    monkeypatch.setenv("LIUHAO_HOST_COMMAND_ENABLED", "1")
    _hc_reload()
    wi = WorldInterface(
        actor="autonomous", adapters=[ShellAdapter()],
        authorize=lambda r: True,  # explicit human arming
    )
    res = wi.execute(_shell_request("echo armed_ok"))
    assert res.success is True
    assert "armed_ok" in res.output["stdout"]


def test_human_shell_default_allowed(monkeypatch):
    # Human shell now also requires the global host-command gate to be armed.
    from src.ai.host_command.enablement import reload as _hc_reload

    monkeypatch.setenv("LIUHAO_HOST_COMMAND_ENABLED", "1")
    _hc_reload()
    wi = WorldInterface(adapters=[ShellAdapter()])  # default actor="human"
    res = wi.execute(_shell_request("echo human_ok"))
    assert res.success is True
    assert "human_ok" in res.output["stdout"]


def test_autonomous_non_shell_default_denied():
    wi = WorldInterface(actor="autonomous", adapters=[FilesystemAdapter()])
    res = wi.observe(WorldRequest(
        adapter="filesystem", action="list",
        params={"path": os.getcwd()},
    ))
    assert res["status"] == "denied"


def test_human_non_shell_default_allowed():
    wi = WorldInterface(adapters=[FilesystemAdapter()])
    res = wi.observe(WorldRequest(
        adapter="filesystem", action="list",
        params={"path": os.getcwd()},
    ))
    assert res["status"] == "observed"


def test_invalid_actor_rejected():
    import pytest
    with pytest.raises(ValueError):
        WorldInterface(actor="robot", adapters=[ShellAdapter()])
