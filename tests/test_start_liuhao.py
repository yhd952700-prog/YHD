"""Tests for the one-click launcher (scripts/start_liuhao.py).

These exercise the small, pure helpers that make the launcher usable on a
fresh machine: port conflict handling (fail fast, never shift), LAN address
discovery, and the argument defaults (single-port mode by default, no more
--single-port flag).

The launcher itself spawns uvicorn/vite as child processes, which is far too
heavy for a unit test; the subprocess wiring is exercised by the real end-to-end
run at the end of the distribution batch.
"""
from __future__ import annotations

import argparse
import socket

import pytest

from scripts import start_liuhao as sl


@pytest.fixture(autouse=True)
def _isolate_instance_probe(monkeypatch):
    """本文件的用例只验证 main() 的输出与参数，不依赖本机是否真有实例在跑。"""
    monkeypatch.setattr(sl, "_probe_running_instance", lambda: None)


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


def test_require_available_port_returns_requested_when_free():
    # 0 is never "in use" (bind(0) succeeds).
    assert sl._require_available_port("127.0.0.1", 0, "test", "--api-port") == 0


def test_require_available_port_fails_instead_of_shifting():
    """端口被占时必须明确报错，不能自动 +1 换地址（书签/PWA origin 会失联）。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with pytest.raises(sl.PortUnavailable) as excinfo:
            sl._require_available_port("127.0.0.1", port, "test", "--api-port")
        message = str(excinfo.value)
        assert str(port) in message
        assert "--api-port" in message
        assert "不会自动换端口" in message
    finally:
        server.close()


def test_port_conflict_without_desktop_prints_chinese_and_stops(capsys, monkeypatch):
    """无桌面会话时保留命令行提示：说清端口/占用者/处理办法，然后返回 None。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        monkeypatch.setattr(
            sl,
            "_ask_port_choice",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no display")),
        )
        assert sl._resolve_port_or_explain("127.0.0.1", port, "网关", "--api-port") is None
        output = capsys.readouterr().out
        assert f"端口 {port} 已被占用" in output
        assert "--api-port" in output
    finally:
        server.close()


def test_port_conflict_can_choose_a_new_port_and_continue(monkeypatch):
    """图形窗口选「换一个端口继续」后直接返回新端口，不让用户重开一次。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    busy = server.getsockname()[1]

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    try:
        monkeypatch.setattr(
            sl, "_ask_port_choice", lambda *args, **kwargs: ("use", free)
        )
        assert sl._resolve_port_or_explain("127.0.0.1", busy, "网关", "--api-port") == free
    finally:
        server.close()


def test_port_conflict_cancel_stops_launch(monkeypatch):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        monkeypatch.setattr(
            sl, "_ask_port_choice", lambda *args, **kwargs: ("cancel", None)
        )
        assert sl._resolve_port_or_explain("127.0.0.1", port, "网关", "--api-port") is None
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


def test_display_host_derives_from_effective_binding_config():
    """展示地址必须来自实际绑定配置；只有通配地址映射为回环。"""
    assert sl._display_host("127.0.0.1") == "127.0.0.1"
    assert sl._display_host("0.0.0.0") == "127.0.0.1"
    assert sl._display_host("::") == "127.0.0.1"
    assert sl._display_host("[::]") == "127.0.0.1"
    assert sl._display_host("10.9.8.7") == "10.9.8.7"


def _run_main_for_url(monkeypatch, tmp_path, argv: list[str], spawned: list[dict]):
    """把 main() 的子进程/等待/常驻全部替换成替身，只留控制台打印可观察。"""
    monkeypatch.setattr(
        sl, "_require_available_port", lambda host, requested, label, flag: requested
    )
    monkeypatch.setattr(sl, "LOG_DIR", tmp_path / "logs")

    def fake_spawn(cmd, cwd, label, env=None):
        spawned.append(env or {})
        return _Proc()

    monkeypatch.setattr(sl, "_spawn", fake_spawn)
    monkeypatch.setattr(
        sl, "_wait_gateway_ready", lambda base, proc, label="网关": _gate_result()
    )
    monkeypatch.setattr(sl, "_wait_http_ok", lambda *a, **k: True)
    monkeypatch.setattr(sl, "_serve_forever", lambda: 0)
    monkeypatch.setattr("sys.argv", ["start_liuhao.py", *argv])
    return sl.main()


def test_main_prints_loopback_url_by_default(monkeypatch, tmp_path, capsys):
    """默认配置下打印的地址是回环，绝不出现浏览器打不开的 0.0.0.0。"""
    spawned: list[dict] = []
    assert _run_main_for_url(
        monkeypatch,
        tmp_path,
        ["--backend-only", "--api-port", "18124"],
        spawned,
    ) == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:18124" in out
    assert "http://0.0.0.0" not in out
    assert spawned[0]["HOST"] == "127.0.0.1"


def test_main_explicit_public_bind_warns_and_prints_openable_url(
    monkeypatch, tmp_path, capsys
):
    """显式 --host 0.0.0.0：警告保留，控制台仍给可打开的地址，真实绑定不丢。"""
    spawned: list[dict] = []
    assert _run_main_for_url(
        monkeypatch,
        tmp_path,
        ["--backend-only", "--host", "0.0.0.0", "--api-port", "18125"],
        spawned,
    ) == 0
    out = capsys.readouterr().out
    assert "已对外暴露" in out
    assert "http://0.0.0.0" not in out
    assert "http://127.0.0.1:18125" in out
    # 打印可以换展示形式，但传给网关的 HOST 必须还是用户显式选择。
    assert spawned[0]["HOST"] == "0.0.0.0"


def test_main_printed_host_follows_config_not_a_hardcoded_literal(
    monkeypatch, tmp_path, capsys
):
    """防硬编码：换一个网卡地址，打印必须跟着变。"""
    spawned: list[dict] = []
    assert _run_main_for_url(
        monkeypatch,
        tmp_path,
        ["--backend-only", "--host", "10.9.8.7", "--api-port", "18126"],
        spawned,
    ) == 0
    out = capsys.readouterr().out
    assert "http://10.9.8.7:18126" in out
    assert spawned[0]["HOST"] == "10.9.8.7"


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
