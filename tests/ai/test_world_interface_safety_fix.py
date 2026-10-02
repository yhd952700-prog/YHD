"""World Interface safety fix — GENUINE enforcement, HONEST documentation.

Proves (p36-wi-safety):
  1. autonomous + filesystem(root=None)  -> read/list/write DENIED (fail-closed)
  2. autonomous + filesystem(bounded root) -> read/list/write ALLOWED + scoped
  3. human + filesystem(root=None)        -> read ALLOWED (legacy preserved)
  4. autonomous shell WITHOUT the host-command gate armed -> DENIED (proves the
     broker/enablement gate actually gates it, not a direct subprocess)
  5. shell WITH the gate armed + an allowing policy -> ALLOWED
  6. the denial comes from the policy gate (authorize / broker), NOT the
     executor fence wrapper (which is an identity fence with capabilities=()).

These tests never touch production persistence: AUDIT_DB_PATH / LIUHAO_WORKSPACE_ROOT
are redirected to temp (conftest redirects AUDIT_DB_PATH; we redirect the workspace
root too). No destructive or network commands are run.
"""
from __future__ import annotations

import os

import pytest

from src.ai.world_interface import (
    WorldInterface,
    WorldRequest,
    FilesystemAdapter,
    ShellAdapter,
)
from src.ai.host_command.enablement import reload as _hc_reload


@pytest.fixture(autouse=True)
def _isolate_persistence(tmp_path, monkeypatch):
    """Never touch production audit/workspace; redirect to temp."""
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit_store.db"))
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    # The host-command gate must be explicit per-test; ensure it starts OFF.
    monkeypatch.delenv("LIUHAO_HOST_COMMAND_ENABLED", raising=False)
    _hc_reload()
    yield


def _arm_gate(monkeypatch):
    monkeypatch.setenv("LIUHAO_HOST_COMMAND_ENABLED", "1")
    _hc_reload()


def _disarm_gate(monkeypatch):
    monkeypatch.delenv("LIUHAO_HOST_COMMAND_ENABLED", raising=False)
    _hc_reload()


# --------------------------------------------------------------------------- #
# 1) autonomous + unbounded filesystem -> fail-closed deny
# --------------------------------------------------------------------------- #
def test_autonomous_unbounded_fs_is_denied(tmp_path):
    wi = WorldInterface(actor="autonomous", adapters=[FilesystemAdapter()])

    target = tmp_path / "secret.txt"
    # write
    res = wi.execute(WorldRequest(
        adapter="filesystem", action="write",
        params={"path": str(target), "content": "x"},
    ))
    assert res.success is False
    assert "denied" in (res.error or "").lower()
    assert not target.exists()

    # read + list (observe path)
    target.write_text("leak", encoding="utf-8")  # pretend it exists on disk
    obs_read = wi.observe(WorldRequest(
        adapter="filesystem", action="read", params={"path": str(target)},
    ))
    assert obs_read["status"] == "denied"
    obs_list = wi.observe(WorldRequest(
        adapter="filesystem", action="list", params={"path": str(tmp_path)},
    ))
    assert obs_list["status"] == "denied"


# --------------------------------------------------------------------------- #
# 2) autonomous + bounded root -> allowed AND actually scoped to that root
# --------------------------------------------------------------------------- #
def test_autonomous_bounded_fs_is_allowed_and_scoped(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    wi = WorldInterface(actor="autonomous", adapters=[FilesystemAdapter(root=str(root))])

    inside = root / "ok.txt"
    res = wi.execute(WorldRequest(
        adapter="filesystem", action="write",
        params={"path": "ok.txt", "content": "hello"},  # relative -> under root
    ))
    assert res.success is True
    assert inside.read_text(encoding="utf-8") == "hello"

    obs = wi.observe(WorldRequest(
        adapter="filesystem", action="read", params={"path": "ok.txt"},
    ))
    assert obs["status"] == "observed"
    assert obs["data"] == "hello"

    listing = wi.observe(WorldRequest(
        adapter="filesystem", action="list", params={"path": "."},
    ))
    assert listing["status"] == "observed"
    assert "ok.txt" in listing["data"]

    # escaping the bounded root is still refused (resolve_in_workspace fail-closed)
    escaped = tmp_path / "escaped.txt"
    bad = wi.execute(WorldRequest(
        adapter="filesystem", action="write",
        params={"path": str(escaped), "content": "x"},
    ))
    assert bad.success is False
    assert not escaped.exists()


# --------------------------------------------------------------------------- #
# 3) human + unbounded filesystem -> still allowed (legacy behaviour preserved)
# --------------------------------------------------------------------------- #
def test_human_unbounded_fs_still_allowed(tmp_path):
    wi = WorldInterface(adapters=[FilesystemAdapter()])  # default actor="human"

    target = tmp_path / "legacy.txt"
    res = wi.execute(WorldRequest(
        adapter="filesystem", action="write",
        params={"path": str(target), "content": "legacy"},
    ))
    assert res.success is True
    assert target.read_text(encoding="utf-8") == "legacy"

    obs = wi.observe(WorldRequest(
        adapter="filesystem", action="read", params={"path": str(target)},
    ))
    assert obs["status"] == "observed"
    assert obs["data"] == "legacy"


# --------------------------------------------------------------------------- #
# 4) autonomous shell WITHOUT the host-command gate -> DENIED (no direct subprocess)
# --------------------------------------------------------------------------- #
def test_autonomous_shell_without_gate_is_denied(tmp_path, monkeypatch):
    _disarm_gate(monkeypatch)
    # A permissive policy is injected, but the global gate is OFF => must deny.
    wi = WorldInterface(
        actor="autonomous", adapters=[ShellAdapter()],
        authorize=lambda r: True,
    )
    res = wi.execute(WorldRequest(
        adapter="shell", action="run",
        params={"command": f'"{_python()}" -c "print(42)"'},
    ))
    assert res.success is False
    assert "host-command" in (res.error or "").lower() or "deny" in (res.error or "").lower()


# --------------------------------------------------------------------------- #
# 5) shell WITH the gate armed + allowing policy -> ALLOWED
# --------------------------------------------------------------------------- #
def test_shell_with_gate_armed_and_policy_allows(tmp_path, monkeypatch):
    _arm_gate(monkeypatch)
    wi = WorldInterface(
        actor="autonomous", adapters=[ShellAdapter()],
        authorize=lambda r: True,
    )
    res = wi.execute(WorldRequest(
        adapter="shell", action="run",
        params={"command": f'"{_python()}" -c "print(\'ALLOWED\')"'},
    ))
    assert res.success is True
    assert "ALLOWED" in (res.output or {}).get("stdout", "")


# --------------------------------------------------------------------------- #
# 6) the denial is from the policy gate, NOT the executor fence wrapper
# --------------------------------------------------------------------------- #
def test_denial_comes_from_policy_gate_not_fence(tmp_path, monkeypatch):
    # The executor fence wrapper grants capabilities=() (a no-op); it provides only
    # executor-identity fencing. Prove the deny comes from authorize()/the broker,
    # i.e. it happens with the fence DISARMED (so the fence cannot be the cause).
    monkeypatch.delenv("LIUHAO_EXECUTOR_FENCE", raising=False)  # ensure disarmed
    _disarm_gate(monkeypatch)

    wi = WorldInterface(actor="autonomous", adapters=[FilesystemAdapter()])
    res = wi.execute(WorldRequest(
        adapter="filesystem", action="write",
        params={"path": "/etc/passwd", "content": "x"},
    ))
    assert res.success is False
    # denied by the policy gate, not by the fence
    assert "policy" in (res.error or "").lower()

    # shell with gate off is denied by the broker enablement gate, again with the
    # fence disarmed.
    wi_sh = WorldInterface(
        actor="autonomous", adapters=[ShellAdapter()], authorize=lambda r: True,
    )
    res_sh = wi_sh.execute(WorldRequest(
        adapter="shell", action="run",
        params={"command": f'"{_python()}" -c "print(1)"'},
    ))
    assert res_sh.success is False
    assert "host-command" in (res_sh.error or "").lower()


def _python() -> str:
    import sys

    return sys.executable
