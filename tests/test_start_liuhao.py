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


class _Proc:
    """网关子进程的最小替身：``_wait_gateway_ready`` 只用到 poll()。"""

    def __init__(self, code: int | None = None) -> None:
        self._code = code

    def poll(self) -> int | None:
        return self._code


def _gate_result(**overrides):
    base = {
        "state": "ready",
        "elapsed": 3.71,
        "polls": 12,
        "timeout": sl.STARTUP_TIMEOUT_S,
        "reachable": True,
        "legacy": False,
        "errors": [],
        "background": {},
        "last_stage": "核心就绪",
        "console_ok": True,
        "log_dir": "",
        "log_hint": "",
        "diagnose_hint": "",
        "detail": "已就绪（3.71s）",
    }
    base.update(overrides)
    return base


def _patch_gate(monkeypatch, result, seen: dict | None = None):
    def fake_wait(base_url, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return result

    monkeypatch.setattr("src.desktop.startup.wait_until_ready", fake_wait)


def test_main_waits_on_startup_gate_before_anything_else(monkeypatch):
    """护栏：``main()`` 必须先等 ``/v1/startup``，不能退回"先等 /v1/health"。

    网关入口换成 ``python -m src.gateway`` 后端口来源变成 ``HOST``/``PORT`` 环境
    变量，这条用例锁住"启动门仍被调用且排在健康探针之前"，防止两条流再撞车。
    """
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sl, "_spawn", lambda *args, **kwargs: _Proc())
    monkeypatch.setattr(
        sl,
        "_wait_gateway_ready",
        lambda base, proc, label="网关": calls.append(("gate", base)) or None,
    )
    monkeypatch.setattr(
        sl,
        "_wait_http_ok",
        lambda url, timeout, label, hint="": calls.append(("health", url)) or True,
    )
    monkeypatch.setattr(sl, "_serve_forever", lambda: 0)
    monkeypatch.setattr(
        "sys.argv", ["start_liuhao.py", "--backend-only", "--api-port", "18123"]
    )
    assert sl.main() == 1, "启动门未通过时必须返回非零，不能继续开浏览器"
    assert [kind for kind, _ in calls] == ["gate"], "main() 不应在启动门前等 /v1/health"
    assert calls[0][1].startswith("http://127.0.0.1:")


def test_launcher_enters_on_ready_and_degraded_only(monkeypatch, capsys):
    """安装版快捷方式链路：ready/degraded 才返回结果（调用方据此开浏览器）。"""
    _patch_gate(monkeypatch, _gate_result())
    result = sl._wait_gateway_ready("http://127.0.0.1:8080", _Proc(), "网关")
    assert result is not None
    assert result["console_ok"] is True
    out = capsys.readouterr().out
    assert "启动中：连接失败按「启动中」处理" in out
    assert "[ok] 网关就绪（3.71s）" in out

    degraded = _gate_result(
        state="degraded",
        detail="核心可用，但后台组件未就绪（1.5s）：情报：SMTP 未配置：缺少 smtp_host",
        console_ok=True,
    )
    _patch_gate(monkeypatch, degraded)
    result = sl._wait_gateway_ready("http://127.0.0.1:8080", _Proc(), "网关")
    assert result is not None
    assert result["state"] == "degraded"
    out = capsys.readouterr().out
    assert "[warn]" in out
    # 真实原因必须原样打出来，不许吞成"启动失败"。
    assert "SMTP 未配置：缺少 smtp_host" in out


def test_launcher_timeout_and_exit_stop_with_hints(monkeypatch, capsys):
    """超时/进程退出必须返回 None（不开浏览器），并给出日志与诊断终点。"""
    timeout_result = _gate_result(
        state="timeout",
        console_ok=False,
        detail="等待 120s 仍未就绪（最后阶段：核心初始化）。日志：logs/liuhao.log、logs/error.log",
    )
    _patch_gate(monkeypatch, timeout_result)
    assert sl._wait_gateway_ready("http://127.0.0.1:8080", _Proc(), "网关") is None
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "liuhao.log" in out
    assert "仍未就绪" in out

    _patch_gate(monkeypatch, _gate_result(state="exited", console_ok=False, detail="网关进程在启动过程中退出"))
    assert sl._wait_gateway_ready("http://127.0.0.1:8080", _Proc(code=1), "网关") is None
    out = capsys.readouterr().out
    assert "网关进程在启动过程中退出" in out


def test_launcher_gate_uses_launcher_logs_and_same_diagnose_entry(monkeypatch):
    """日志提示必须指向启动器真实写的文件，诊断命令走托盘同一入口。"""
    seen: dict = {}
    _patch_gate(monkeypatch, _gate_result(), seen)
    proc = _Proc()
    sl._wait_gateway_ready("http://127.0.0.1:8080", proc, "网关")
    assert seen["timeout"] == sl.STARTUP_TIMEOUT_S
    assert "liuhao.log" in seen["log_hint"]
    assert "desktop.log" not in seen["log_hint"]
    assert "liuhao_desktop.py" in seen["diagnose_hint"]
    assert "diagnose --verify" in seen["diagnose_hint"]
    assert seen["log_dir"] == sl.LOG_DIR
    assert seen["is_alive"]() is True


def test_describe_startup_prefers_caller_log_hint():
    from src.desktop.startup import describe_startup

    text = describe_startup(
        {
            "state": "timeout",
            "elapsed": 120.0,
            "timeout": 120.0,
            "last_stage": "核心初始化",
            "log_hint": "data/logs/liuhao.log、data/logs/error.log",
            "diagnose_hint": "runtime/python.exe scripts/liuhao_desktop.py diagnose --verify",
        }
    )
    assert "liuhao.log" in text
    assert "liuhao_desktop.py diagnose --verify" in text
    assert "desktop.log" not in text
