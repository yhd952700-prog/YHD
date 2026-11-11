"""系统层验收回归：双开提示、退出残留清理、托盘菜单与窗口按钮一致性。

对应用户验收清单：
  * 第二次点图标 → 已运行窗口到前台 + 一句人话（托盘驻留 / 服务在跑 / 服务卡死）；
  * 退出后不留 ``run/instance.lock`` 与 ``run/instance.json``；
  * 托盘菜单每一项的文案与点击结果，与界面同名功能一致。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.start_liuhao as launcher  # noqa: E402
from src.common.instance_lock import InstanceLock  # noqa: E402
from src.desktop import app as desktop_app  # noqa: E402
from src.desktop import tray  # noqa: E402
from src.desktop.app import DesktopApp  # noqa: E402
from src.desktop.service import cleanup_instance_files  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows 系统层能力")


# --------------------------------------------------------------------------- #
# 托盘菜单 ↔ 窗口按钮
# --------------------------------------------------------------------------- #
def test_tray_menu_labels_are_the_shared_constants() -> None:
    labels = {action: text for _command, action, text in tray.MENU_ITEMS}
    assert labels[tray.ACTION_OPEN_CONSOLE] == tray.LABEL_OPEN_CONSOLE
    assert labels[tray.ACTION_SHOW_STATUS] == tray.LABEL_SHOW_STATUS
    assert labels[tray.ACTION_OPEN_LOGS] == tray.LABEL_OPEN_LOGS
    assert labels[tray.ACTION_STOP_SERVICE] == tray.LABEL_STOP_SERVICE
    assert labels[tray.ACTION_QUIT] == tray.LABEL_QUIT


def test_state_window_buttons_reuse_tray_labels() -> None:
    """界面按钮不得另写一份文案；改一处必须两边同时生效。"""
    source = Path(desktop_app.__file__).read_text(encoding="utf-8")
    for constant in (
        "LABEL_OPEN_CONSOLE",
        "LABEL_OPEN_LOGS",
        "LABEL_STOP_SERVICE",
        "LABEL_QUIT",
    ):
        assert f"text={constant}" in source, f"窗口按钮没有复用 {constant}"
    # 托盘有「打开日志目录」，界面必须同样给出入口。
    assert "command=self._open_logs" in source


def test_stop_service_action_is_shared_by_tray_and_window() -> None:
    instance = object.__new__(DesktopApp)
    calls: list[str] = []
    sources: list[str] = []

    def fake_stop_service(*, source: str = "button") -> None:
        sources.append(source)
        calls.append("stop")

    instance._stop_service = fake_stop_service  # type: ignore[method-assign]
    instance._show_window = lambda: calls.append("show")  # type: ignore[method-assign]
    DesktopApp._handle_action(instance, tray.ACTION_STOP_SERVICE)
    assert calls == ["stop", "show"]
    # 停止归因必须区分来源：托盘是 tray，窗口按钮是 window；桩扔掉 source 就归因不出来。
    assert sources == ["tray"]


# --------------------------------------------------------------------------- #
# 退出后的残留清理
# --------------------------------------------------------------------------- #
def _clear_instance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """去掉全局 conftest 注入的数据目录/实例目录重定向，按 tmp_path 断言。"""
    for name in ("LIUHAO_DATA_DIR", "LIUHAO_INSTANCE_ROOT", "LIUHAO_INSTANCE_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_cleanup_removes_stale_lock_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_instance_env(monkeypatch)
    run = tmp_path / "run"
    run.mkdir()
    (run / "instance.lock").write_bytes(b"0")
    (run / "instance.json").write_text('{"pid": 1234}', encoding="utf-8")
    (run / "local_scheduler.lock").write_bytes(b"0")

    outcome = cleanup_instance_files(tmp_path)

    assert outcome["cleaned"] is True
    assert not (run / "instance.lock").exists()
    assert not (run / "instance.json").exists()
    assert not (run / "local_scheduler.lock").exists()


@WINDOWS_ONLY
def test_cleanup_keeps_files_while_another_instance_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_instance_env(monkeypatch)
    lock = InstanceLock(tmp_path)
    lock.acquire()
    lock.publish(pid=os.getpid(), url="http://127.0.0.1:9/")
    try:
        outcome = cleanup_instance_files(tmp_path)
        assert outcome["cleaned"] is False
        assert (tmp_path / "run" / "instance.lock").exists()
        assert (tmp_path / "run" / "instance.json").exists()
    finally:
        lock.release()


# --------------------------------------------------------------------------- #
# 双开提示：三种前置状态
# --------------------------------------------------------------------------- #
def _handoff(
    monkeypatch: pytest.MonkeyPatch,
    *,
    shown: bool,
    healthy: bool,
    url: str,
    no_browser: bool = False,
):
    calls: dict[str, list[str]] = {"browser": [], "notice": []}
    monkeypatch.setattr("src.desktop.tray.request_show", lambda key, timeout=0.0: shown)
    monkeypatch.setattr(launcher, "_instance_url_healthy", lambda target: healthy)
    monkeypatch.setattr(
        launcher.webbrowser, "open", lambda target: calls["browser"].append(str(target))
    )
    monkeypatch.setattr(
        launcher,
        "_show_instance_notice",
        lambda text, warning: calls["notice"].append(("warning" if warning else "info") + text),
    )
    code = launcher._handoff_to_running_instance(
        {"pid": 4321, "url": url},
        no_browser=no_browser,
    )
    return code, calls


def test_handoff_when_tray_is_resident(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = _handoff(
        monkeypatch, shown=True, healthy=True, url="http://127.0.0.1:8080/"
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "状态窗口" in out
    # 提示语不能自相矛盾：这里已经接管/切换，不该再出现"本次启动已退出"。
    assert "本次启动已退出" not in out
    # 唤起成功时不再额外开浏览器，也不弹"无响应"警告。
    assert calls["browser"] == []
    assert calls["notice"] and calls["notice"][0].startswith("info")


def test_handoff_when_service_running_without_tray(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = _handoff(
        monkeypatch, shown=False, healthy=True, url="http://127.0.0.1:8080/"
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "已为你打开控制台" in out
    assert calls["browser"] == ["http://127.0.0.1:8080"]


def test_handoff_no_browser_does_not_claim_opened(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = _handoff(
        monkeypatch,
        shown=False,
        healthy=True,
        url="http://127.0.0.1:8080/",
        no_browser=True,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "控制台地址" in out
    assert "已为你打开控制台" not in out
    assert calls["browser"] == []


def test_handoff_when_service_is_stuck(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = _handoff(
        monkeypatch, shown=False, healthy=False, url="http://127.0.0.1:8080/"
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "暂时没有响应" in out
    assert "没有启动第二个服务" in out
    assert calls["browser"] == []
    assert calls["notice"] and calls["notice"][0].startswith("warning")


def test_tray_instance_key_follows_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    assert launcher._tray_instance_key() == str(tmp_path.resolve())


# --------------------------------------------------------------------------- #
# 桌面壳入口：命令行已起网关时不许再起第二份服务
# --------------------------------------------------------------------------- #
def _bare_app(tmp_path: Path) -> DesktopApp:
    from src.desktop.service import GatewayService

    instance = object.__new__(DesktopApp)
    instance.service = GatewayService()
    instance.hidden = True
    instance._requested_port = 0
    return instance


@WINDOWS_ONLY
def test_foreign_gateway_lock_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_instance_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_INSTANCE_ROOT", str(tmp_path))
    instance = _bare_app(tmp_path)

    assert instance._foreign_gateway_holds_data() is False

    lock = InstanceLock(tmp_path)
    lock.acquire()
    try:
        assert instance._foreign_gateway_holds_data() is True
    finally:
        lock.release()
    assert instance._foreign_gateway_holds_data() is False


def test_published_holder_matches_gateway_write_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_instance_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_INSTANCE_ROOT", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "instance.json").write_text(
        '{"pid": 4321, "url": "http://127.0.0.1:18087"}', encoding="utf-8"
    )

    instance = _bare_app(tmp_path)
    assert instance._published_holder() == {"pid": 4321, "url": "http://127.0.0.1:18087"}
    assert instance._published_console_url() == "http://127.0.0.1:18087/"
