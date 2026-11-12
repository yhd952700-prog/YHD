"""网关停止归因日志：谁让网关停的，必须能事后从日志里查出来。"""

from __future__ import annotations

import logging

from src.desktop.app import DesktopApp
from src.desktop.stop_log import append_stop_attribution, describe_stop_attribution


def test_attribution_line_has_source_pid_time_and_flags():
    line = describe_stop_attribution("tray", caller_pid=4321, graceful=True, forced=False, at=0)

    assert line.startswith("evt=gateway.stop ")
    assert "source=tray" in line
    assert "caller_pid=4321" in line
    assert "at=" in line
    assert "graceful=True" in line
    assert "forced=False" in line


def test_attribution_without_pid_says_why():
    line = describe_stop_attribution("external")

    assert "caller_pid=unknown(caller_pid_unavailable)" in line
    assert "graceful=unknown" in line
    assert "forced=unknown" in line


def test_apply_stop_result_logs_stop_source(caplog):
    app = DesktopApp.__new__(DesktopApp)
    app._stop_source = "tray"
    app._stop_logged = False
    app._stop_result = None
    app._labels = {}
    app._tray = None
    app._closing = False
    app._restart_after_stop = False
    app._stop_state_poller = lambda: None
    app._clear_token = lambda: None
    app._refresh_status_text = lambda running: None

    with caplog.at_level(logging.INFO, logger="src.desktop.app"):
        app._apply_stop_result(
            {"stopped": True, "graceful": True, "forced": False, "elapsed": 0.8}
        )

    messages = [record.getMessage() for record in caplog.records]
    attribution = [m for m in messages if m.startswith("evt=gateway.stop ")]
    assert attribution, f"缺少停止归因日志：{messages}"
    assert "source=tray" in attribution[0]
    assert "graceful=True" in attribution[0]
    assert "forced=False" in attribution[0]
    # 归因只写来源/PID/时间/结果，不能带出任何口令或令牌。
    assert all("secret" not in m.lower() and "token" not in m.lower() for m in messages)


def test_apply_stop_result_logs_once_per_stop(caplog):
    app = DesktopApp.__new__(DesktopApp)
    app._stop_source = "tray"
    app._stop_logged = False
    app._stop_result = None
    app._labels = {}
    app._tray = None
    app._closing = False
    app._restart_after_stop = False
    app._stop_state_poller = lambda: None
    app._clear_token = lambda: None
    app._refresh_status_text = lambda running: None

    with caplog.at_level(logging.INFO, logger="src.desktop.app"):
        app._apply_stop_result({"stopped": True, "graceful": True, "forced": False})
        app._apply_stop_result({"stopped": True, "graceful": True, "forced": False})

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("evt=gateway.stop ")]
    assert len(lines) == 1


def test_external_stop_appends_line_to_desktop_log(tmp_path):
    log_path = tmp_path / "logs" / "desktop.log"

    assert append_stop_attribution(log_path, "external", caller_pid=7788) is True
    content = log_path.read_text(encoding="utf-8")

    assert "src.desktop.stop:" in content
    assert "evt=gateway.stop source=external caller_pid=7788" in content
