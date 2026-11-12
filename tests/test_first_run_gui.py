"""图形化首启向导的行为测试（不创建真实窗口）。

覆盖现场最容易复发的四条路径：
1. 没有可交互终端 / 图形界面起不来（计划任务、pythonw、会话 0）；
2. 用户中途关闭窗口（不能留下半个账号，更不能拉起控制台）；
3. 密码不合规（必须给中文原因，且不写登记表）；
4. 端口被占用（必须先拦住，不能注册成功后再起不来）。
"""
from __future__ import annotations

import gc
import hashlib
import os
import queue
import socket
import time
from pathlib import Path

import pytest

# 本文件会创建真实 Tk 窗口（test_check_page_buttons_exist_and_are_clickable）。
# 与 tests/desktop/conftest.py 共用同一套护栏实现：autouse fixture 在全部
# fixture teardown 之后、pytest 主线程里检查 Tk 是否已回收，避免「Tk 落进引用环、
# 由后台线程的循环 GC 回收」触发 Tcl_AsyncDelete -> int3 的进程级 abort。
from _tk_thread_guard import tk_lifecycle_guard  # noqa: F401 - autouse fixture

from scripts import first_run_gui as gui
from scripts import first_run_wizard as frw
from src.ai.model_config import get_model_config_store, reset_model_config_store
from src.kernels.identity._persistence import HUMAN_IDENTITIES_INTEGRITY_KEY_ENV


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """与 test_first_run_wizard 同款隔离：身份/凭据/模型配置全部落临时目录。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(gui, "DATA_DIR_MARKER", tmp_path / "data-dir.txt")
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(data_dir / "config" / "human_identities.json"))
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(data_dir / "config" / "auth_secrets.json"))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(data_dir / "data" / "model_registry.sqlite3"))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY_FILE", str(data_dir / "data" / "model_config.key"))
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, "test-integrity-key-0123456789")
    reset_model_config_store()
    snapshot = dict(os.environ)
    yield data_dir
    # commit() 会直接写 os.environ（进程内共享），测试结束后整体还原，
    # 避免把路径泄漏给同一 pytest 会话里的其它用例。
    os.environ.clear()
    os.environ.update(snapshot)
    reset_model_config_store()


def _values(data_dir: Path, **overrides):
    values = {
        "data_dir": str(data_dir),
        "principal": "liuhao",
        "display": "鎏灏管理员",
        "password": "liuhao2026",
        "confirm": "liuhao2026",
        "model": "skip",
        "model_name": "",
        "base_url": "",
        "api_key": "",
    }
    values.update(overrides)
    return values


class _FakeUI:
    """最小 UI 替身：只模拟"用户做了什么"，不渲染任何控件。"""

    def __init__(self, *, cancel: bool = False, values=None):
        self.canceled = cancel
        self.accepted = False
        self.error_message = ""
        self.values = values

    def wait(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. 无交互：窗口起不来时必须给中文原因 + 日志路径，不能 EOFError
# ---------------------------------------------------------------------------


def test_no_interactive_display_reports_chinese_error_and_log(isolated_env, capsys):
    def broken_factory(default_dir, on_submit, log_path, port=8080):
        import tkinter

        raise tkinter.TclError("no display name and no $DISPLAY environment variable")

    code = gui.run_wizard(ui_factory=broken_factory)

    assert code == gui.EXIT_UNAVAILABLE
    output = capsys.readouterr().out
    assert "无法打开设置窗口" in output
    assert "日志：" in output
    assert "first_run.log" in output
    # 没有账号，也没有启动控制台。
    assert not (Path(os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"])).exists()
    assert not (Path(os.environ["LIUHAO_DATA_DIR"]) / "config" / "current_url.txt").exists()


def test_no_interactive_stdio_still_does_not_touch_stdin(monkeypatch):
    """向导入口不得因为 stdin 为空而崩溃（双击/计划任务核心诉求）。"""
    monkeypatch.setattr(gui, "run_wizard", lambda **_: gui.EXIT_OK)
    # sys.stdin 为 None 时 argparse/异常处理也必须正常返回。
    monkeypatch.setattr(gui.sys, "stdin", None)
    assert gui.main([]) == gui.EXIT_OK


def test_full_flow_without_stdin_creates_account(isolated_env, monkeypatch):
    """没有 stdin 时，"填写 → 提交"整条链路仍然可用（不退回 input()）。"""
    monkeypatch.setattr(gui.sys, "stdin", None)

    def factory(default_dir, on_submit, log_path, port=8080):
        ui = _FakeUI()
        ok, message = on_submit(_values(isolated_env))
        ui.accepted, ui.error_message = ok, message
        return ui

    assert gui.run_wizard(ui_factory=factory, port=0) == gui.EXIT_OK
    assert frw._has_loginable_human() is True


# ---------------------------------------------------------------------------
# 2. 中途取消
# ---------------------------------------------------------------------------


def test_cancel_leaves_no_account_and_no_console(isolated_env, capsys):
    code = gui.run_wizard(ui_factory=lambda *_: _FakeUI(cancel=True))

    assert code == gui.EXIT_CANCELED
    assert "已取消首次设置" in capsys.readouterr().out
    assert frw._has_loginable_human() is False
    assert not (Path(os.environ["LIUHAO_DATA_DIR"]) / "config" / "current_url.txt").exists()


def test_launcher_does_not_expose_console_when_wizard_cancelled(isolated_env, monkeypatch):
    """首启取消时启动器必须停下：不拉起网关、不写地址、不打印驾驶舱地址。"""
    from scripts import launch_installed as li

    monkeypatch.setattr(gui, "needs_setup", lambda: True)
    monkeypatch.setattr(gui, "run_wizard", lambda **_: gui.EXIT_CANCELED)
    monkeypatch.setattr(li, "LAUNCHER_LOG", isolated_env / "logs" / "launcher.log")

    started = {"gateway": False}

    from scripts import start_liuhao as sl

    monkeypatch.setattr(sl, "main", lambda: started.__setitem__("gateway", True) or 0)

    code = li._run(isolated_env, isolated_env / "config" / "logging.yaml", [])

    assert code == gui.EXIT_CANCELED
    assert started["gateway"] is False
    assert not (isolated_env / "config" / "current_url.txt").exists()


# ---------------------------------------------------------------------------
# 3. 密码不合规
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "hint"),
    [
        ("", "不能为空"),
        ("abc123", "太短"),
        ("abcdefgh", "数字"),
        ("12345678", "字母"),
    ],
)
def test_validate_password_rejects_weak_values(password, hint):
    message = gui.validate_password(password)
    assert message is not None
    assert hint in message


def test_validate_password_accepts_compliant_value():
    assert gui.validate_password("liuhao2026") is None


def test_commit_refuses_noncompliant_password(isolated_env):
    data_dir = isolated_env
    ok, message = gui.commit(_values(data_dir, password="12345678"), port=0)

    assert ok is False
    assert "密码" in message
    assert "日志：" in message
    assert frw._has_loginable_human() is False
    # 密码不合规时连目录初始化都不该做（不能留下半成品）。
    assert not (data_dir / "config" / "human_identities.json").exists()


# ---------------------------------------------------------------------------
# 4. 端口被占用
# ---------------------------------------------------------------------------


def test_commit_refuses_when_port_is_busy(isolated_env):
    data_dir = isolated_env
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        busy_port = probe.getsockname()[1]

        ok, message = gui.commit(_values(data_dir), port=busy_port)

    assert ok is False
    assert "占用" in message
    assert str(busy_port) in message
    assert "日志：" in message
    assert frw._has_loginable_human() is False


# ---------------------------------------------------------------------------
# 成功路径：账号 + 模型 + 数据目录标记真的落盘
# ---------------------------------------------------------------------------


def test_commit_creates_loginable_admin_and_marks_data_dir(isolated_env):
    data_dir = isolated_env
    ok, message = gui.commit(
        _values(data_dir, model="ollama", model_name="qwen2.5:7b"),
        port=0,
    )

    assert ok is True, message
    assert frw._has_loginable_human() is True
    models = get_model_config_store().list_models()
    assert [m["model"] for m in models] == ["qwen2.5:7b"]
    # 首启完成前不允许留下旧的控制台地址；成功提交同样不能替网关写地址。
    assert not (data_dir / "config" / "current_url.txt").exists()
    assert (data_dir / "logs" / "first_run.log").is_file()


def test_first_run_log_masks_principal_and_keeps_fingerprint(isolated_env):
    """日志会随诊断包转给外人：用户名只留两位 + 指纹，不留全文。"""
    ok, message = gui.commit(_values(isolated_env), port=0)
    assert ok is True, message

    log = isolated_env / "logs" / "first_run.log"
    done = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if "first-run done:" in line
    ]
    assert len(done) == 1
    assert "principal=li***" in done[0]
    assert "principal_hash=" in done[0]
    assert "liuhao" not in done[0]
    # 指纹必须是可复算的 sha256 前 8 位，否则"同一台机器上对账"无从核起。
    expected = hashlib.sha256(b"liuhao").hexdigest()[:8]
    assert f"principal_hash={expected}" in done[0]


@pytest.mark.parametrize(
    ("principal", "expected"),
    [("liuhao", "li***"), ("ab", "***"), ("a", "***"), ("", "***")],
)
def test_mask_principal_never_reveals_short_names(principal, expected):
    """不足 3 个字符时全掩：留两位就等于把整个名字给出去了。"""
    assert gui.mask_principal(principal) == expected


def test_changing_data_dir_updates_marker_for_next_launch(isolated_env, tmp_path):
    """用户在向导里换目录：必须写 marker，否则下次双击又回到旧目录。"""
    target = tmp_path / "chosen-data"
    ok, message = gui.commit(_values(target), port=0)

    assert ok is True, message
    assert gui.DATA_DIR_MARKER.is_file()
    assert str(target) in gui.DATA_DIR_MARKER.read_text(encoding="utf-8-sig")
    assert (target / "config" / "human_identities.json").is_file()


def test_commit_uses_port_typed_in_wizard(isolated_env):
    """界面里填的端口优先于启动器默认值：被占时必须在提交前拦住。"""
    data_dir = isolated_env
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        busy_port = probe.getsockname()[1]

        ok, message = gui.commit(_values(data_dir, api_port=str(busy_port)))

    assert ok is False
    assert "占用" in message
    assert str(busy_port) in message
    assert frw._has_loginable_human() is False


def test_installed_app_env_is_production_and_upgrades_staging(tmp_path):
    """安装版不能再写 APP_ENV=staging（那是开发用的调试姿态）。"""
    from scripts import launch_installed as li

    fresh = tmp_path / "fresh.env"
    li._ensure_app_env(fresh)
    assert "APP_ENV=production" in fresh.read_text(encoding="utf-8")
    assert "APP_ENV=staging" not in fresh.read_text(encoding="utf-8")

    legacy = tmp_path / "legacy.env"
    legacy.write_text(
        "APP_ENV=staging\nLOG_LEVEL=info\nLIUHAO_JWT_SECRET=keep-me\n",
        encoding="utf-8",
    )
    li._ensure_app_env(legacy)
    upgraded = legacy.read_text(encoding="utf-8")
    assert "APP_ENV=production" in upgraded
    assert "LIUHAO_JWT_SECRET=keep-me" in upgraded


# ---------------------------------------------------------------------------
# 5. 完成页自检：四项真实检查 + 失败时的可点击下一步
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _busy_port() -> int:
    """占住一个端口（不 listen，稳定落在 occupied 分支）。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    return probe, int(probe.getsockname()[1])


def test_check_port_passes_when_bindable():
    result = gui.check_port_ready("127.0.0.1", _free_port())

    assert result.ok is True
    assert result.code == "free"
    assert result.action == ""
    assert "可用" in result.detail


def test_check_port_fails_with_action_when_occupied():
    probe, port = _busy_port()
    try:
        result = gui.check_port_ready("127.0.0.1", port)
    finally:
        probe.close()

    assert result.ok is False
    assert result.code == "occupied"
    assert result.action == "换一个端口"
    assert result.blocking is False
    assert "可以稍后再弄" in result.advice
    assert str(port) in result.detail


def test_check_data_dir_passes_and_shows_actual_path(isolated_env):
    result = gui.check_data_dir_writable(isolated_env)

    assert result.ok is True
    # 界面必须给实际路径，不能是 <本地路径> 之类的占位符。
    assert str(isolated_env) in result.detail
    assert "<本地路径>" not in result.detail


def test_check_data_dir_fails_with_open_dir_action(isolated_env, monkeypatch):
    from src.observability import boot_report

    monkeypatch.setattr(
        boot_report, "probe_writable", lambda _path: (False, PermissionError("denied"))
    )
    result = gui.check_data_dir_writable(isolated_env)

    assert result.ok is False
    assert result.action == "打开数据目录"
    assert result.blocking is True
    assert "必须先处理" in result.advice
    assert str(isolated_env) in result.detail


def test_check_tray_passes_when_instance_lock_held(isolated_env, monkeypatch):
    from src.common import instance_lock

    monkeypatch.setattr(
        instance_lock.InstanceLock,
        "probe",
        lambda self: {"url": "http://127.0.0.1:18080", "pid": 4321},
    )
    result = gui.check_tray_resident(isolated_env, tray_planned=False)

    assert result.ok is True
    assert result.code == "resident"
    assert "18080" in result.detail


def test_check_tray_fails_when_no_tray_and_not_planned(isolated_env, monkeypatch):
    from src.common import instance_lock

    monkeypatch.setattr(instance_lock.InstanceLock, "probe", lambda self: None)
    result = gui.check_tray_resident(isolated_env, tray_planned=False)

    assert result.ok is False
    assert result.code == "not_planned"
    assert result.action == "重新检测"
    assert result.blocking is False
    assert "可以稍后再弄" in result.advice


def test_check_tray_passes_as_planned_for_desktop_entry(isolated_env, monkeypatch):
    from src.common import instance_lock

    monkeypatch.setattr(instance_lock.InstanceLock, "probe", lambda self: None)
    result = gui.check_tray_resident(isolated_env, tray_planned=True)

    assert result.ok is True
    assert result.code == "planned"
    assert "托盘" in result.detail


def _fake_provider(monkeypatch, *, reply: str | None = "pong", error=None):
    from src.ai import providers

    class _Provider:
        def generate(self, *_args, **_kwargs):
            if error is not None:
                raise error
            return reply

    monkeypatch.setattr(providers, "provider_from_config", lambda _cfg: _Provider())


def test_check_model_passes_and_records_verification(isolated_env, monkeypatch):
    frw.configure_ollama("qwen2.5:7b")
    _fake_provider(monkeypatch)
    result = gui.check_default_model()

    assert result.ok is True
    assert result.code == "ok"
    model = get_model_config_store().list_models()[0]
    assert model["last_verified_at"]


def test_check_model_fails_with_model_page_action(isolated_env, monkeypatch):
    frw.configure_ollama("qwen2.5:7b")
    _fake_provider(monkeypatch, error=RuntimeError("connection refused"))
    result = gui.check_default_model()

    assert result.ok is False
    assert result.code == "failed"
    assert result.action == "去模型页测试连接"
    assert result.blocking is False
    assert "可以稍后再弄" in result.advice
    assert get_model_config_store().list_models()[0]["last_verified_at"] is None


def test_check_model_reports_skipped_when_no_model(isolated_env):
    result = gui.check_default_model()

    assert result.ok is False
    assert result.code == "skipped"
    assert result.action == "去模型页测试连接"
    assert "跳过" in result.detail


def test_self_check_log_matches_startup_check_fields(isolated_env, monkeypatch):
    frw.configure_ollama("qwen2.5:7b")
    _fake_provider(monkeypatch)
    port = _free_port()
    results = gui.run_self_check(
        data_dir=isolated_env, port=port, model_name="qwen2.5:7b", tray_planned=True
    )
    log_path = gui.write_self_check_log(isolated_env, results, port=port)

    assert log_path is not None
    line = log_path.read_text(encoding="utf-8")
    assert "evt=startup.check" in line
    assert f"data_dir={isolated_env}" in line
    assert "data_writable=1" in line
    assert "log_writable=1" in line
    assert f"port={port}" in line
    assert "port_bindable=" in line
    assert "port_state=" in line
    assert "provider=" in line
    assert "model=qwen2.5:7b" in line
    assert "key_fp=" in line
    assert "tray_resident=" in line
    assert "model_test=ok" in line
    assert "check_result=all_green" in line


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "真实 Tk 向导窗口（_WizardWindow）的控件回收依赖 Windows tcl86t 运行时："
        "POSIX 的 Tcl 在 destroy() 后仍有 C 层引用挂在 Tk 对象上，引用计数不归零，"
        "tk_lifecycle_guard 护栏按设计 fail-closed 拒绝（防 Tcl_AsyncDelete 跨线程"
        " panic 带走整个 pytest 进程）"
    ),
)
def test_check_page_buttons_exist_and_are_clickable(isolated_env, monkeypatch):
    """四项自检失败时，各自的按钮必须真的存在，且点了会触发对应动作。"""
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception:  # noqa: BLE001 - 无桌面会话时跳过 UI 断言
        pytest.skip("当前会话没有可用的 Tk 桌面")

    log_path = isolated_env / "logs" / "first_run.log"
    window = gui._WizardWindow(isolated_env, lambda _v: (True, "ok"), log_path, port=0)
    try:
        window.committed = True
        results = [
            gui.CheckResult("port", "网关端口", False, "占用", action="换一个端口",
                            advice="可以稍后再弄，软件已可用。", code="occupied"),
            gui.CheckResult("data_dir", "数据目录", False, "不可写", action="打开数据目录",
                            blocking=True, advice="必须先处理。", code="unwritable"),
            gui.CheckResult("tray", "托盘驻留（关窗后继续运行）", False, "没有托盘",
                            action="重新检测", advice="可以稍后再弄，软件已可用。",
                            code="not_planned"),
            gui.CheckResult("model", "默认模型", False, "连接失败",
                            action="去模型页测试连接",
                            advice="可以稍后再弄，软件已可用。", code="failed"),
        ]
        window._show_checks(results)

        for key, expected in {
            "port": "换一个端口",
            "data_dir": "打开数据目录",
            "tray": "重新检测",
            "model": "去模型页测试连接",
        }.items():
            button = window._check_widgets[key]["action"]
            assert button.winfo_manager() == "pack"
            assert button.cget("state") == "normal"
            assert button.cget("text") == expected

        calls: list[str] = []
        window._start_checks = lambda: calls.append("recheck")  # type: ignore[method-assign]
        window._open_data_dir = lambda: calls.append("open_dir")  # type: ignore[method-assign]
        new_port = _free_port()
        window._ask_new_port = lambda _suggestion: new_port  # type: ignore[method-assign]

        window._run_check_action("port")
        assert window.port == new_port
        assert calls == ["recheck"]

        window._run_check_action("data_dir")
        window._run_check_action("tray")
        assert calls == ["recheck", "open_dir", "recheck"]

        window._run_check_action("model")
        assert window._index == 2
    finally:
        # 收尾顺序是有意的（护栏在 teardown 时会检查 Tk 是否已回收）：
        #   1) 先停 DPI 观察者；
        #   2) 断开 window 对 root/控件/变量的引用 —— _WizardWindow 用
        #      ``dir_var.trace_add`` 注册了 Python 回调，Tcl 侧的 trace 会反向
        #      持有 window，而 tkapp 不被循环 GC 追踪，这种环 Python 永远收不掉
        #      （每跑一次泄漏一个 Tcl 解释器）；断开后 root 的引用计数才能归零；
        #   3) 在解释器仍存活时 destroy()，最后释放 root 让 tkapp 在主线程析构。
        try:
            window._stop_dpi_watcher()
            root = window.root
            window.__dict__.clear()
            root.destroy()
            del root
        except Exception:  # noqa: BLE001
            pass
        gc.collect()


# ---------------------------------------------------------------------------
# 「进入贾维斯」这一跳的兜底：不许静默丢弃点击，也不许让按钮永久停在禁用态。
# ---------------------------------------------------------------------------

_WINDOW_SKIP = pytest.mark.skipif(
    os.name != "nt",
    reason="真实 Tk 窗口依赖 Windows tcl86t 运行时（同 test_check_page_buttons_exist_and_are_clickable）",
)


def _open_window(data_dir: Path, log_path: Path):
    window = gui._WizardWindow(data_dir, lambda _v: (True, "ok"), log_path, port=0)
    window.committed = True
    return window


def _close_window(window) -> None:
    """与 test_check_page_buttons_exist_and_are_clickable 相同的收尾顺序。"""
    try:
        window._stop_dpi_watcher()
        root = window.root
        window.__dict__.clear()
        root.destroy()
        del root
    except Exception:  # noqa: BLE001
        pass
    gc.collect()


def _skip_without_desktop() -> None:
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception:  # noqa: BLE001 - 无桌面会话时跳过 UI 断言
        pytest.skip("当前会话没有可用的 Tk 桌面")


def _all_green() -> list:
    return [
        gui.CheckResult("port", "网关端口", True, "可用", action="", advice="", code="free"),
        gui.CheckResult("data_dir", "数据目录", True, "可写", action="", advice="", code="writable"),
        gui.CheckResult(
            "tray", "托盘驻留（关窗后继续运行）", True, "可用", action="", advice="", code="ok"
        ),
        gui.CheckResult("model", "默认模型", True, "可用", action="", advice="", code="ok"),
    ]


@_WINDOW_SKIP
def test_finish_while_checking_is_queued_then_auto_enters(isolated_env):
    """自检未完成时点「进入贾维斯」：必须排队并在自检收尾后自动进入，而不是静默丢弃。"""
    _skip_without_desktop()
    log_path = isolated_env / "logs" / "first_run.log"
    window = _open_window(isolated_env, log_path)
    try:
        window._check_running = True
        window._finish()

        # 窗口不能关：点了但自检还没结束
        assert window.accepted is False
        assert window._finish_pending is True
        # 而且用户要看得见自己去哪了
        assert window._next.cget("state") == "disabled"
        assert window._next.cget("text") == "自检完成后自动进入…"

        # 自检收尾 → 自动进入，不需要用户再点一次
        window._check_running = False
        window._show_checks(_all_green())

        assert window.accepted is True
        assert window._finish_pending is False
        text = log_path.read_text(encoding="utf-8")
        assert "evt=wizard.finish_deferred" in text
        assert "evt=wizard.finish_resume auto=1" in text
        assert "evt=wizard.finish accepted=1" in text
    finally:
        _close_window(window)


@_WINDOW_SKIP
def test_deferred_finish_does_not_bypass_blocking_check(isolated_env):
    """自检真有阻塞项时，排队的那次点击不能被自动兑现——决定权交回界面。"""
    _skip_without_desktop()
    log_path = isolated_env / "logs" / "first_run.log"
    window = _open_window(isolated_env, log_path)
    try:
        window._check_running = True
        window._finish()
        assert window._finish_pending is True

        window._check_running = False
        window._show_checks(
            [
                gui.CheckResult(
                    "data_dir",
                    "数据目录",
                    False,
                    "不可写",
                    action="打开数据目录",
                    blocking=True,
                    advice="必须先处理。",
                    code="unwritable",
                )
            ]
        )

        assert window.accepted is False
        assert window._finish_pending is False
        assert window._next.cget("state") == "disabled"
        text = log_path.read_text(encoding="utf-8")
        assert "evt=wizard.finish_resume skipped=1" in text
        assert "blocking=1" in text
    finally:
        _close_window(window)


@_WINDOW_SKIP
def test_check_timeout_restores_enter_button_with_chinese_hint(isolated_env):
    """自检卡住到超时：按钮必须恢复可点，并给一行中文提示，不能只写日志。"""
    _skip_without_desktop()
    log_path = isolated_env / "logs" / "first_run.log"
    window = _open_window(isolated_env, log_path)
    try:
        window._check_running = True
        window._check_seq = 1
        window._check_started_at = time.monotonic() - (gui.CHECK_TIMEOUT_SECONDS + 5)

        window._poll_checks(queue.Queue())

        assert window._check_running is False
        assert window._next.cget("state") == "normal"
        assert window._next.cget("text") == "进入贾维斯"
        assert window._check_summary_label.cget("text") == gui.CHECK_TIMEOUT_HINT
        assert gui.CHECK_TIMEOUT_HINT == "自检超时，可以先进入，稍后在系统状态里看结果。"

        text = log_path.read_text(encoding="utf-8")
        assert "evt=startup.check.timeout" in text
        assert "reason=deadline" in text
        assert "next_state=normal" in text

        # 兜底之后仍然能正常进入
        window._finish()
        assert window.accepted is True
    finally:
        _close_window(window)


@_WINDOW_SKIP
def test_poll_after_failure_forces_finish_after_three_attempts(isolated_env, monkeypatch):
    """轮询重排连续失败 3 次必须强制收尾，不能把按钮永久留在「正在自检…」。"""
    _skip_without_desktop()
    log_path = isolated_env / "logs" / "first_run.log"
    window = _open_window(isolated_env, log_path)
    try:
        window._check_running = True
        window._check_seq = 1
        window._check_started_at = time.monotonic()  # 未到超时点，只能靠失败计数兜底

        def _boom(*_args, **_kwargs):
            raise RuntimeError("after() 失败")

        monkeypatch.setattr(window.root, "after", _boom)

        for _ in range(2):
            window._poll_checks(queue.Queue())
        assert window._check_running is True
        assert window._check_poll_failures == 2

        window._poll_checks(queue.Queue())
        assert window._check_poll_failures == 3
        assert window._check_running is False
        assert window._next.cget("state") == "normal"

        text = log_path.read_text(encoding="utf-8")
        assert "evt=startup.check.poll_error" in text
        assert "failures=3" in text
        assert "reason=poll_error" in text
    finally:
        # monkeypatch 的撤销表会反向持有 root 的绑定方法，必须先撤销再销毁窗口，
        # 否则 Tk 对象活过 teardown，护栏会按设计 fail-closed。
        monkeypatch.undo()
        _close_window(window)




# ---------------------------------------------------------------------------
# 工作台改名（2026-11-12 G 组）：向导里能起名字，存在数据目录，随数据走
# ---------------------------------------------------------------------------


def test_validate_workbench_name_rejects_too_long():
    assert gui.validate_workbench_name("x" * 41) is not None
    assert gui.validate_workbench_name("贾维斯", "y" * 81) is not None
    assert gui.validate_workbench_name("") is None
    assert gui.validate_workbench_name("贾维斯", "") is None
    assert gui.validate_workbench_name("阿强", "Ah Qiang") is None


def test_commit_saves_custom_workspace_name(isolated_env):
    """向导里起了新名字：commit 成功，名字落在数据目录的 workspace_settings.json。"""
    import json

    from src.brand import resolve_brand

    ok, _message = gui.commit(
        _values(
            isolated_env,
            workbench_name_zh="阿强",
            workbench_name_en="Ah Qiang",
        ),
        port=0,
    )
    assert ok is True
    settings_file = isolated_env / "workspace_settings.json"
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        "name_en": "Ah Qiang",
        "name_zh": "阿强",
    }
    assert resolve_brand()["lockup"] == "Ah Qiang · 阿强"
    assert resolve_brand()["customized"] is True


def test_commit_default_name_writes_no_settings_file(isolated_env):
    """名字留默认（贾维斯 / Jarvis）：commit 成功，但不写配置文件。"""
    from src.brand import resolve_brand

    ok, _message = gui.commit(
        _values(
            isolated_env,
            workbench_name_zh="贾维斯",
            workbench_name_en="Jarvis",
        ),
        port=0,
    )
    assert ok is True
    assert not (isolated_env / "workspace_settings.json").exists()
    assert resolve_brand()["name_zh"] == "贾维斯"
    assert resolve_brand()["customized"] is False


def test_commit_refuses_too_long_workspace_name_before_creating_admin(isolated_env):
    """名字超长：commit 在建账号之前就失败，不留半个账号。"""
    ok, message = gui.commit(
        _values(isolated_env, workbench_name_zh="x" * 41),
        port=0,
    )
    assert ok is False
    assert "工作台名字" in message
    assert frw._has_loginable_human() is False
