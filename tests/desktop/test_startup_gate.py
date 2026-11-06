"""启动门测试：连接失败按 starting、degraded 也进门、超时有终点。

三类证据：

1. 纯逻辑（假探测）：退避 250→500ms、404 退回 /v1/health、超时文案含日志与诊断命令；
2. 真 TCP / 真 uvicorn（无桌面也能跑）：慢启动（端口未监听）、degraded、永远不 ready；
3. 桌面壳决策：只有 ready/degraded 才 ``webbrowser.open``，degraded 必须显示真实原因。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from src.desktop import app as desktop_app
from src.desktop import startup
from src.desktop.probe import pick_port
from src.desktop.service import GRACEFUL_TIMEOUT, stop_process
from tests.desktop.startup_apps import DELAY_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _start_runner(app_path: str, port: int, log_path: Path) -> tuple[subprocess.Popen[bytes], Any]:
    handle = log_path.open("wb")
    env = dict(os.environ)
    # 每个 runner 用独立实例目录：它们是"多个网关"的测试替身，不能互相抢锁。
    env["LIUHAO_INSTANCE_ROOT"] = str(log_path.parent / f"instance-{port}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "src.desktop._runner",
            "--app",
            app_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return proc, handle


def _stop_runner(proc: subprocess.Popen[bytes], handle: Any, log_path: Path) -> None:
    stop_process(proc, timeout=GRACEFUL_TIMEOUT, log_path=log_path, log_offset=0)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
    handle.close()


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class _Label:
    def __init__(self) -> None:
        self.text = ""

    def config(self, *, text: str) -> None:
        self.text = text


class _Tray:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str, bool]] = []
        self.tooltips: list[str] = []
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_tooltip(self, text: str) -> None:
        self.tooltips.append(text)

    def notify(self, title: str, message: str, *, timeout: float = 8.0, warning: bool = False) -> bool:
        self.notices.append((title, message, warning))
        return True


def _app_with_stubs() -> tuple[desktop_app.DesktopApp, _Tray]:
    app = desktop_app.DesktopApp(open_browser=True)
    app._labels.update({"service": _Label(), "startup": _Label(), "health": _Label()})
    tray = _Tray()
    app._tray = tray
    app.service.port = 8080
    app._stop_event.set()  # 不启动健康轮询循环
    return app, tray


def _fake_probe(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(startup, "probe_startup", lambda base_url, timeout=2.0: dict(responses.pop(0)))


def _gate_result(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    _fake_probe(monkeypatch, [{"reachable": True, "state": body["state"], "body": body, "error": None, "legacy": False}])
    return startup.wait_until_ready("http://127.0.0.1:8080", timeout=timeout, sleep=lambda _s: None)


# --------------------------------------------------------------------------- #
# 纯逻辑
# --------------------------------------------------------------------------- #
def test_gate_treats_connection_failure_as_starting_then_enters(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {"reachable": False, "state": "starting", "body": None, "error": "ConnectionRefusedError", "legacy": False},
        {"reachable": False, "state": "starting", "body": None, "error": "ConnectionRefusedError", "legacy": False},
        {"reachable": True, "state": "ready", "body": {"state": "ready"}, "error": None, "legacy": False},
    ]
    _fake_probe(monkeypatch, responses)

    slept: list[float] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    result = startup.wait_until_ready(
        "http://127.0.0.1:9", timeout=5.0, sleep=sleep, clock=lambda: now[0]
    )
    assert result["state"] == "ready"
    assert result["console_ok"] is True
    assert result["polls"] == 3
    assert "已就绪" in result["detail"]
    # 退避从 250ms 起、翻倍到 500ms 封顶。
    assert slept == [0.25, 0.5]


def test_gate_enters_on_degraded_and_keeps_real_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "state": "degraded",
        "stages": [{"name": "核心", "ms": 80}],
        "background": {
            "status": "failed",
            "errors": [{"step": "情报", "error": "SMTP 未配置：缺少 smtp_host"}],
        },
    }
    result = _gate_result(monkeypatch, body)
    assert result["state"] == "degraded"
    assert result["console_ok"] is True
    assert result["errors"][0]["step"] == "情报"
    assert "核心可用" in result["detail"]
    assert "SMTP 未配置" in result["detail"]


def test_gate_timeout_ends_with_human_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_probe(
        monkeypatch,
        [
            {
                "reachable": False,
                "state": "starting",
                "body": {"state": "starting", "stages": [{"name": "导入路由"}]},
                "error": None,
                "legacy": False,
            }
        ]
        * 10,
    )
    slept: list[float] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    result = startup.wait_until_ready(
        "http://127.0.0.1:9", timeout=1.0, log_dir=tmp_path, sleep=sleep, clock=lambda: now[0]
    )
    assert result["state"] == "timeout"
    assert result["console_ok"] is False
    assert "diagnose" in result["detail"]
    assert str(tmp_path) in result["detail"]
    assert "导入路由" in result["detail"]
    assert slept[0] == 0.25


def test_gate_reports_exited_when_process_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe(
        monkeypatch,
        [{"reachable": False, "state": "starting", "body": None, "error": None, "legacy": False}],
    )
    result = startup.wait_until_ready(
        "http://127.0.0.1:9", timeout=5.0, is_alive=lambda: False, sleep=lambda _s: None
    )
    assert result["state"] == "exited"
    assert result["console_ok"] is False
    assert "退出" in result["detail"]


def test_gate_falls_back_to_health_on_old_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_http(url: str, timeout: float = 2.0) -> dict[str, Any]:
        if url.endswith(startup.STARTUP_PATH):
            return {"ok": False, "status": 404, "body": None, "error": "HTTP 404"}
        return {"ok": True, "status": 200, "body": {"status": "ok"}, "error": None}

    monkeypatch.setattr(startup, "http_json", fake_http)
    probe = startup.probe_startup("http://127.0.0.1:8080")
    assert probe["state"] == "ready"
    assert probe["legacy"] is True


# --------------------------------------------------------------------------- #
# 真 TCP / 真 uvicorn：三种状态与真实耗时
# --------------------------------------------------------------------------- #
def test_gate_waits_for_real_slow_listen(tmp_path: Path) -> None:
    port = pick_port(18740)
    log_path = tmp_path / "runner.log"
    proc, handle = _start_runner("tests.desktop.startup_apps:slow_ready_app", port, log_path)
    try:
        url = f"http://127.0.0.1:{port}"
        began = time.monotonic()
        result = startup.wait_until_ready(url, timeout=15.0, log_dir=tmp_path)
        wall = time.monotonic() - began

        assert result["state"] == "ready"
        assert result["console_ok"] is True
        # lifespan 睡 DELAY_SECONDS 期间端口还没监听：这段时间的探测都是 starting
        # （Windows 上连接未监听端口可能一直挂到探测超时，所以只断言"至少探测过
        # 一次 starting"，墙钟时间才是"确实等到了端口监听"的证据）。
        assert wall >= DELAY_SECONDS - 0.2
        assert result["polls"] >= 2
        assert result["elapsed"] >= DELAY_SECONDS - 0.2
        assert _wait_for_port(port, 1.0)
    finally:
        _stop_runner(proc, handle, log_path)


def test_gate_enters_console_when_real_gateway_is_degraded(tmp_path: Path) -> None:
    port = pick_port(18750)
    log_path = tmp_path / "runner.log"
    proc, handle = _start_runner("tests.desktop.startup_apps:degraded_app", port, log_path)
    try:
        url = f"http://127.0.0.1:{port}"
        assert _wait_for_port(port, 20.0)
        result = startup.wait_until_ready(url, timeout=10.0, log_dir=tmp_path)
        assert result["state"] == "degraded"
        assert result["console_ok"] is True
        assert result["errors"][0]["step"] == "情报"
        assert "SMTP 未配置" in result["detail"]
        assert result["elapsed"] < 5.0
    finally:
        _stop_runner(proc, handle, log_path)


def test_gate_times_out_on_real_stuck_gateway(tmp_path: Path) -> None:
    port = pick_port(18760)
    log_path = tmp_path / "runner.log"
    proc, handle = _start_runner("tests.desktop.startup_apps:stuck_app", port, log_path)
    try:
        url = f"http://127.0.0.1:{port}"
        assert _wait_for_port(port, 20.0)
        result = startup.wait_until_ready(url, timeout=2.0, log_dir=tmp_path)
        assert result["state"] == "timeout"
        assert result["console_ok"] is False
        assert 1.9 <= result["elapsed"] <= 4.0
        assert "diagnose" in result["detail"]
    finally:
        _stop_runner(proc, handle, log_path)


def test_desktop_boot_pipeline_reports_gate_through_event_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``DesktopApp._boot`` → 启动门 → 事件队列 → 界面/浏览器 的整条链路。"""
    port = pick_port(18770)
    log_path = tmp_path / "boot_runner.log"
    proc, handle = _start_runner("tests.desktop.startup_apps:degraded_app", port, log_path)
    try:
        assert _wait_for_port(port, 20.0)
        opened: list[str] = []
        monkeypatch.setattr(desktop_app.webbrowser, "open", lambda url: opened.append(url))
        app, tray = _app_with_stubs()
        app.service.port = port
        monkeypatch.setattr(app.service, "start", lambda timeout=120.0: True)
        monkeypatch.setattr(app.service, "is_running", lambda: proc.poll() is None)

        app._boot()

        payload: dict[str, Any] = {}
        while True:
            kind, item = app.events.get_nowait()
            if kind == "boot":
                payload = item
                break
        assert payload["started"] is True
        assert payload["gate"]["state"] == "degraded"
        app._apply_boot_result(payload)
        assert opened == [f"http://127.0.0.1:{port}/"]
        assert tray.notices[-1][0] == "鎏灏已启动（部分组件未就绪）"
        assert "SMTP 未配置" in tray.notices[-1][1]
    finally:
        _stop_runner(proc, handle, log_path)


# --------------------------------------------------------------------------- #
# 桌面壳决策：只有 ready/degraded 才叫浏览器
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state", ["ready", "degraded", "timeout", "failed"])
def test_desktop_opens_console_only_when_gate_allows(
    state: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop_app.webbrowser, "open", lambda url: opened.append(url))
    app, tray = _app_with_stubs()

    if state == "ready":
        gate = _gate_result(monkeypatch, {"state": "ready"})
        expected_notice = "鎏灏已就绪"
    elif state == "degraded":
        gate = _gate_result(
            monkeypatch,
            {
                "state": "degraded",
                "background": {"errors": [{"step": "情报", "error": "SMTP 未配置"}]},
            },
        )
        expected_notice = "鎏灏已启动（部分组件未就绪）"
    elif state == "timeout":
        _fake_probe(
            monkeypatch,
            [{"reachable": True, "state": "starting", "body": {"state": "starting"}, "error": None, "legacy": False}],
        )
        gate = startup.wait_until_ready(
            "http://127.0.0.1:8080", timeout=0.0, log_dir=tmp_path, sleep=lambda _s: None
        )
        expected_notice = "鎏灏启动超时"
    else:
        gate = startup.unavailable_gate("网关在 120s 内未通过 /v1/health", log_dir=tmp_path)
        expected_notice = "鎏灏启动失败"

    app._apply_boot_result({"started": state != "failed", "gate": gate})

    if state in {"ready", "degraded"}:
        assert opened == ["http://127.0.0.1:8080/"]
    else:
        assert opened == []
    assert tray.notices, "托盘必须给出可见结论"
    assert tray.notices[-1][0] == expected_notice
    assert tray.notices[-1][2] == (state != "ready")  # 非 ready 一律警告态
    startup_text = app._labels["startup"].text
    if state == "degraded":
        assert "核心可用" in startup_text
        assert "SMTP 未配置" in startup_text
    if state == "timeout":
        assert "diagnose" in startup_text
