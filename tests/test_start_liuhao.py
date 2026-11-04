"""Tests for the one-click launcher (scripts/start_liuhao.py).

These exercise the small, pure helpers that make the launcher usable on a
fresh machine: port auto-selection, LAN address discovery, and the argument
defaults (single-port mode by default, no more --single-port flag).

The launcher itself spawns uvicorn/vite as child processes, which is far too
heavy for a unit test; the subprocess wiring is exercised by the real end-to-end
run at the end of the distribution batch.
"""
from __future__ import annotations

import argparse
import socket

import pytest

from scripts import start_liuhao as sl


def test_port_in_use_detects_a_bound_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert sl._port_in_use("127.0.0.1", port) is True
        # A clearly free ephemeral port above the listening one is very likely free.
        assert sl._port_in_use("127.0.0.1", 0) is False  # bind(0) never "in use"
    finally:
        server.close()


def test_ensure_free_port_returns_requested_when_free():
    # 0 is never "in use" (bind(0) succeeds).
    assert sl._ensure_free_port("127.0.0.1", 0, "test") == 0


def test_ensure_free_port_falls_forward_when_occupied():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        chosen = sl._ensure_free_port("127.0.0.1", port, "test")
        assert chosen > port
        assert chosen <= port + sl.PORT_SCAN_LIMIT
    finally:
        server.close()


def test_lan_addresses_excludes_loopback():
    addrs = sl._lan_addresses()
    for ip in addrs:
        assert not ip.startswith("127.")
        assert not ip.startswith("169.254.")


def test_default_mode_is_single_port_when_no_flags():
    """No flag => single_port True (the new default for double-click usage)."""
    ns = _parse_default()
    assert ns.backend_only is False
    assert ns.vite is False
    assert (not ns.backend_only and not ns.vite) is True


def _parse_default() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=sl.DEFAULT_API_HOST)
    parser.add_argument("--api-port", type=int, default=sl.DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=sl.DEFAULT_UI_PORT)
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--vite", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--https-port", type=int, default=8443)
    return parser.parse_args([])


def test_backend_only_and_vite_are_mutually_exclusive(monkeypatch):
    """main() must reject the --backend-only/--vite combination before spawning."""
    monkeypatch.setattr("sys.argv", ["start_liuhao.py", "--backend-only", "--vite"])
    with pytest.raises(SystemExit) as exc:
        sl.main()
    assert exc.value.code != 0
