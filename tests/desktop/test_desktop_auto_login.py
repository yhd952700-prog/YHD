"""桌面壳第三块：本机免密、托盘状态位、暂停接活的单元测试。

覆盖三条硬要求的可验证部分：
* 首次保存前必须有过明确同意（默认否），且同意状态持久化；
* 口令/令牌不出现在日志与异常文本里；
* 「清除本机免密」只删文件即可完成，不需要解密。
"""

from __future__ import annotations

import logging
import os
import queue
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from src.desktop import auto_login, native_window, tray, tray_state
from src.desktop.app import WINDOW_NATIVE, DesktopApp
from src.desktop.auto_login import AutoLogin, session_from_login


# ---------------------------------------------------------------------- #
# 免密凭据
# ---------------------------------------------------------------------- #
def test_session_from_login_maps_frontend_shape() -> None:
    session = session_from_login(
        {
            "access_token": "tok",
            "principal": "alice",
            "display_name": "Alice",
            "scope": "admin",
            "permissions": ["goal.read", 7],
            "expires_at": "1700000000",
            "client": "web",
            "is_root": 1,
            "can_rotate_recovery": 1,
        }
    )
    assert session["token"] == "tok"
    assert session["displayName"] == "Alice"
    assert session["permissions"] == ["goal.read", "7"]
    assert session["expiresAt"] == 1700000000.0
    assert session["client"] == "desktop"
    assert session["isRoot"] is True
    assert session["canRotateRecovery"] is True


def test_save_requires_explicit_consent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="src.desktop.auto_login")
    login = AutoLogin(tmp_path)
    secret = "plain-text-secret-不会进日志"
    with pytest.raises(PermissionError) as excinfo:
        login.save("alice", secret)
    assert secret not in str(excinfo.value)
    assert secret not in caplog.text
    assert not login.credential_path.exists()

    login.record_consent(False)
    assert login.consent() == "denied"
    with pytest.raises(PermissionError):
        login.save("alice", secret)


def test_consent_is_persisted_across_instances(tmp_path: Path) -> None:
    first = AutoLogin(tmp_path)
    first.record_consent(True)
    assert AutoLogin(tmp_path).consent() == "granted"
    # 重复写不会变成"每次启动都问"。
    assert AutoLogin(tmp_path).consent() == "granted"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows 可用")
def test_dpapi_roundtrip_and_sid_binding(tmp_path: Path) -> None:
    login = AutoLogin(tmp_path)
    login.record_consent(True)
    assert login.save("alice", "s3cret-密码")
    payload = login.load()
    assert payload is not None
    assert payload["principal"] == "alice"
    assert payload["secret"] == "s3cret-密码"
    # 密文里不应出现明文口令。
    assert b"s3cret" not in login.credential_path.read_bytes()


def test_forget_only_deletes_file_without_decrypting(tmp_path: Path) -> None:
    login = AutoLogin(tmp_path)
    login.credential_path.parent.mkdir(parents=True, exist_ok=True)
    login.credential_path.write_bytes(b"not-a-dpapi-blob")  # 故意写坏，模拟换用户/损坏
    assert login.forget() is True
    assert not login.credential_path.exists()
    assert login.forget() is True  # 不存在也算成功，可重复调用


def test_login_refuses_non_loopback(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="src.desktop.auto_login")
    called = {"n": 0}

    def explode(*args, **kwargs):  # pragma: no cover - 不应被调用
        called["n"] += 1
        raise AssertionError("非回环地址不应发起请求")

    monkeypatch.setattr(auto_login, "urlopen", explode)
    login = AutoLogin(Path("."))
    assert login.login("http://192.168.1.5:8080", "alice", "secret") is None
    assert called["n"] == 0


def test_login_failure_never_logs_secret(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="src.desktop.auto_login")
    secret = "never-log-me-123"

    def fail(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(auto_login, "urlopen", fail)
    assert AutoLogin(Path(".")).login("http://127.0.0.1:9", "alice", secret) is None
    assert secret not in caplog.text

    def http_error(*args, **kwargs):
        raise HTTPError("http://127.0.0.1:9", 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(auto_login, "urlopen", http_error)
    assert AutoLogin(Path(".")).login("http://127.0.0.1:9", "alice", secret) is None
    assert secret not in caplog.text


# ---------------------------------------------------------------------- #
# 托盘状态
# ---------------------------------------------------------------------- #
def test_compute_state_precedence() -> None:
    assert (
        tray_state.compute_state(
            pending_approvals=3, running_goals=2, failed_agents=1
        )
        == tray.STATE_ERROR
    )
    assert (
        tray_state.compute_state(
            pending_approvals=3, running_goals=2, failed_agents=0
        )
        == tray.STATE_WAITING
    )
    assert (
        tray_state.compute_state(
            pending_approvals=0, running_goals=2, failed_agents=0
        )
        == tray.STATE_WORKING
    )
    assert (
        tray_state.compute_state(
            pending_approvals=None, running_goals=0, failed_agents=0
        )
        == tray.STATE_IDLE
    )


def test_paused_flag_persists(tmp_path: Path) -> None:
    assert tray_state.read_paused(tmp_path) is False
    tray_state.write_paused(tmp_path, True, employees=4)
    assert tray_state.read_paused(tmp_path) is True
    tray_state.write_paused(tmp_path, False)
    assert tray_state.read_paused(tmp_path) is False


def _fake_api(calls: list[tuple[str, str]]):
    def request(base, path, token, *, method="GET", timeout=None):
        calls.append((path, method))
        if path == "/v1/goals":
            return 200, {"goals": [{"state": "running"}, {"state": "completed"}]}
        if path == "/v1/employees":
            return 200, {
                "employee_count": 2,
                "agents": [
                    {"id": "a1", "status": "idle", "employee_name": "小明"},
                    {"id": "a2", "status": "paused"},
                ],
            }
        if path == "/v1/policy/approvals/pending":
            return 200, {"count": 0}
        if path.endswith(("/pause", "/resume")):
            return 200, {}
        return 404, None

    return request


def test_fetch_snapshot_reads_real_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tray_state, "_request_json", _fake_api(calls))
    snapshot = tray_state.fetch_snapshot("http://127.0.0.1:1", "tok")
    assert snapshot["authenticated"] is True
    assert snapshot["employees"] == 2
    assert snapshot["running_goals"] == 1
    assert snapshot["pending_approvals"] == 0
    assert snapshot["state"] == tray.STATE_WORKING


def test_fetch_snapshot_unauthenticated_and_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tray_state,
        "_request_json",
        lambda base, path, token, **kwargs: (401, None),
    )
    snapshot = tray_state.fetch_snapshot("http://127.0.0.1:1", "stale")
    assert snapshot["authenticated"] is False
    assert snapshot["auth_expired"] is True

    snapshot = tray_state.fetch_snapshot("http://127.0.0.1:1", "")
    assert snapshot["authenticated"] is False
    assert snapshot["auth_expired"] is False


def test_pause_and_resume_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tray_state, "_request_json", _fake_api(calls))
    result = tray_state.pause_all("http://127.0.0.1:1", "tok")
    assert result["status"] == 200
    assert result["paused"] == 1  # a2 已暂停，不重复发
    assert result["failed"] == 0
    assert ("/v1/employees/a1/pause", "POST") in calls

    calls.clear()
    result = tray_state.resume_all("http://127.0.0.1:1", "tok")
    assert result["resumed"] == 1
    assert ("/v1/employees/a2/resume", "POST") in calls


def test_poller_pauses_new_employee_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tray_state, "_request_json", _fake_api(calls))
    snapshots: list[dict] = []
    poller = tray_state.TrayStatePoller(
        base_url=lambda: "http://127.0.0.1:1",
        token=lambda: "tok",
        on_snapshot=snapshots.append,
        paused=lambda: True,
    )
    snapshot = poller.poll_once()
    assert snapshot["newly_paused"] == ["小明"]
    assert snapshot["authenticated"] is True
    # 第二轮不再重复提示，也不重复暂停已暂停的个体。
    snapshot = poller.poll_once()
    assert snapshot["newly_paused"] == []


# ---------------------------------------------------------------------- #
# 托盘菜单文案
# ---------------------------------------------------------------------- #
def test_pause_label_states() -> None:
    assert tray.pause_label(paused=False, count=5) == "暂停 AI 员工接活（当前 5 位）"
    assert tray.pause_label(paused=True, count=5) == "恢复 AI 员工接活（已暂停 5 位）"
    # 数量未知时不编数字。
    assert tray.pause_label(paused=False, count=None) == "暂停 AI 员工接活"


def test_system_tray_updates_menu_label() -> None:
    widget = tray.SystemTray(lambda action: None)
    widget.set_action_label(
        tray.ACTION_TOGGLE_PAUSE_EMPLOYEES, "暂停 AI 员工接活（当前 3 位）"
    )
    assert (
        widget.action_labels()[tray.ACTION_TOGGLE_PAUSE_EMPLOYEES]
        == "暂停 AI 员工接活（当前 3 位）"
    )
    # 其它文案不受影响。
    assert widget.action_labels()[tray.ACTION_QUIT] == tray.LABEL_QUIT


# ---------------------------------------------------------------------- #
# 原生窗口会话注入
# ---------------------------------------------------------------------- #
def test_session_bootstrap_js_carries_session() -> None:
    script = native_window.session_bootstrap_js(
        {"token": "tok-123", "principal": "alice", "displayName": "Alice"}
    )
    assert "liuhao.session" in script
    assert "tok-123" in script
    assert "liuhao.last_principal" in script
    assert "alice" in script


def test_auth_hook_covers_login_and_logout() -> None:
    script = native_window.auth_hook_js()
    assert "remember_login" in script
    assert "signed_out" in script
    assert "/v1/auth/login" in script
    assert "/v1/auth/logout" in script
    assert "/v1/accounts/self-register" in script


def test_clear_session_js_removes_keys() -> None:
    script = native_window.clear_session_js()
    assert "liuhao.session" in script
    assert "liuhao.last_principal" in script
    assert "location.reload" in script


# ---------------------------------------------------------------------- #
# app 接线（用裸对象避开 tkinter / 网关）
# ---------------------------------------------------------------------- #
class _FakeService:
    def __init__(self, *, running: bool = True, last_error: str | None = None) -> None:
        self._running = running
        self.last_error = last_error
        self.base_url = "http://127.0.0.1:1"

    def is_running(self) -> bool:
        return self._running


class _FakeTray:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.tooltips: list[str] = []
        self.disabled: set[str] = set()
        self.labels: dict[str, str] = {}
        self.notes: list[tuple[str, str]] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_tooltip(self, text: str) -> None:
        self.tooltips.append(text)

    def set_disabled_actions(self, actions) -> None:
        self.disabled = set(actions or ())

    def set_action_label(self, action: str, label: str) -> None:
        self.labels[action] = label

    def notify(self, title: str, message: str, *, warning: bool = False) -> None:
        self.notes.append((title, message))


def _bare_app(**overrides) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)
    app._tray = _FakeTray()
    app._closing = False
    app.service = _FakeService()
    app.window_mode = WINDOW_NATIVE
    app._boot_pending = False
    app._stopping = False
    app._stop_result = None
    app._last_health_ok = True
    app._paused = False
    app._auto_login = None
    app._auth_lock = None
    app._token = ""
    app._tray_snapshot = None
    app._pause_thread = None
    app._native = None
    app.events = queue.Queue()
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def test_tray_shows_idle_and_honest_tooltip_when_logged_out() -> None:
    app = _bare_app()
    app._tray_snapshot = {"authenticated": False}
    app._sync_tray_state(running=True)
    assert app._tray.states[-1] == tray.STATE_IDLE
    assert "未登录，任务状态不可见" in app._tray.tooltips[-1]
    # 未登录不能替用户暂停员工接活。
    assert tray.ACTION_TOGGLE_PAUSE_EMPLOYEES in app._tray.disabled


def test_tray_shows_waiting_when_authenticated() -> None:
    app = _bare_app()
    app._tray_snapshot = {
        "authenticated": True,
        "state": tray.STATE_WAITING,
        "pending_approvals": 2,
        "employees": 4,
    }
    app._sync_tray_state(running=True)
    app._sync_tray_menu()
    assert app._tray.states[-1] == tray.STATE_WAITING
    assert "等你确认：2 项" in app._tray.tooltips[-1]
    assert app._tray.labels[tray.ACTION_TOGGLE_PAUSE_EMPLOYEES] == (
        "暂停 AI 员工接活（当前 4 位）"
    )


def test_paused_hint_is_in_tooltip() -> None:
    app = _bare_app(_paused=True)
    app._tray_snapshot = {
        "authenticated": True,
        "state": tray.STATE_WORKING,
        "running_goals": 1,
    }
    app._sync_tray_state(running=True)
    assert "执行中：1 个任务" in app._tray.tooltips[-1]
    assert tray.PAUSE_SCOPE_HINT in app._tray.tooltips[-1]


class _FakeAutoLogin:
    def __init__(self, consent: str | None = None) -> None:
        self.consent_value = consent
        self.saved: list[tuple[str, str]] = []
        self.forgotten = 0

    def supported(self) -> bool:
        return True

    def consent(self) -> str | None:
        return self.consent_value

    def record_consent(self, granted: bool) -> None:
        self.consent_value = "granted" if granted else "denied"

    def save(self, principal: str, secret: str) -> bool:
        self.saved.append((principal, secret))
        return True

    def has_credential(self) -> bool:
        return bool(self.saved)

    def forget(self) -> bool:
        self.forgotten += 1
        self.saved.clear()
        return True


def test_remember_login_respects_declined_consent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="src.desktop.app")
    auto = _FakeAutoLogin(consent=None)
    app = _bare_app(_auto_login=auto)
    monkeypatch.setattr(app, "_ask_remember_consent", lambda: False)
    secret = "must-not-be-saved"
    app._remember_login({"principal": "alice", "secret": secret, "token": "tok"})
    assert auto.saved == []
    assert auto.consent_value == "denied"  # 结论持久化，下次启动不再问
    assert secret not in caplog.text


def test_remember_login_saves_after_consent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="src.desktop.app")
    auto = _FakeAutoLogin(consent="granted")
    app = _bare_app(_auto_login=auto)
    secret = "save-me-but-not-log"
    app._remember_login({"principal": "alice", "secret": secret, "token": "tok"})
    assert auto.saved == [("alice", secret)]
    assert secret not in caplog.text


def test_ui_logout_deletes_credential() -> None:
    auto = _FakeAutoLogin(consent="granted")
    auto.saved = [("alice", "secret")]
    app = _bare_app(_auto_login=auto)
    app._on_signed_out()
    assert auto.forgotten == 1
    assert auto.saved == []
    assert app._get_token() == ""
