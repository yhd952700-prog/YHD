"""桌面系统层测试：关于页、自启、托盘、优雅停机、诊断包脱敏、防火墙只读检测。"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.brand import BRAND_LOCKUP  # noqa: E402
from src.desktop import autostart, diagnostics, tray  # noqa: E402
from src.desktop.about import collect_about  # noqa: E402
from src.desktop.diagnostics import (  # noqa: E402
    create_bundle,
    redact_text,
    verify_bundle,
)
from src.desktop.firewall import AUTO_MODIFY_FIREWALL, inspect_firewall  # noqa: E402
from src.desktop.probe import http_json, pick_port, port_is_free  # noqa: E402
from src.desktop.service import GRACEFUL_TIMEOUT, SHUTDOWN_LOG_MARKER, stop_process  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows 系统层能力")


def test_about_reports_real_paths(tmp_path: Path) -> None:
    about = collect_about(tmp_path)
    assert about["app"] == BRAND_LOCKUP
    assert about["data_dir"] == str(tmp_path / "data")
    assert about["log_dir"] == str(tmp_path / "logs")
    assert about["build_time_source"] in {"console_dist", "git_commit", "pyproject_mtime", "unknown"}
    assert isinstance(about["python_version"], str)
    assert about["python_version"]
    assert about["diagnostics_dir"]


def test_redaction_removes_keys_and_tokens() -> None:
    raw = (
        "api_key=sk-abcdefgh12345678\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop\n"
        '{"token": "plain-token-value", "password": "hunter2"}\n'
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----\n"
    )
    cleaned = redact_text(raw)
    assert "sk-abcdefgh12345678" not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "plain-token-value" not in cleaned
    assert "hunter2" not in cleaned
    assert "MIIEow" not in cleaned
    assert "REDACTED" in cleaned


def test_diagnostics_bundle_is_whitelisted_and_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    (fake_logs / "app.log").write_text(
        "2026-01-01 api_key=sk-abcdefgh12345678\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop\n"
        "AI_PROVIDER_KEY=sk-live-abcdef123456\n",
        encoding="utf-8",
    )

    data_dir = fake_root / "data"
    data_dir.mkdir()
    (data_dir / "human_identities.json").write_text(
        json.dumps({"username": "owner", "password_hash": "pbkdf2:super-secret-hash"}),
        encoding="utf-8",
    )
    (data_dir / "liuhao.db").write_bytes(b"SQLite format 3\x00fake-database-body")

    import sqlite3

    db_path = data_dir / "model_registry.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE ai_model_configs (name TEXT, provider TEXT, model TEXT, base_url TEXT, "
        "enabled INTEGER, is_default INTEGER, api_key_encrypted TEXT, api_key_masked TEXT)"
    )
    conn.execute(
        "INSERT INTO ai_model_configs VALUES (?,?,?,?,?,?,?,?)",
        (
            "gpt-main",
            "openai",
            "gpt-4o",
            "https://user:pass@api.openai.com/v1",
            1,
            1,
            "sk-encrypted-abcdefgh",
            "sk-...abcd",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(db_path))

    bundle = create_bundle(tmp_path / "out", root=fake_root, log_dir=fake_logs, ports=[65530])
    assert bundle.is_file()
    assert verify_bundle(bundle) == []

    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert all(name.endswith((".json", ".txt")) for name in names), names
        assert not any(name.lower().endswith((".db", ".sqlite", ".sqlite3")) for name in names)

        joined = "\n".join(archive.read(name).decode("utf-8", "replace") for name in names)
        assert "sk-abcdefgh12345678" not in joined
        assert "eyJhbGciOiJIUzI1NiJ9" not in joined
        assert "super-secret-hash" not in joined
        assert "fake-database-body" not in joined

        providers = json.loads(archive.read("providers.json"))
        model = providers["registry"]["models"][0]
        assert model["name"] == "gpt-main"
        assert model["provider"] == "openai"
        assert model["model"] == "gpt-4o"
        assert "user:pass@" not in (model["base_url"] or "")
        assert "api_key" not in json.dumps(model)


def test_bundle_has_no_env_names_scripts_or_usernames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中文用户名 + 空格路径 + env 名 + 脚本/安装路径 + 层级字段，一律不进包。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    (fake_logs / "app.log").write_text(
        "\n".join(
            [
                r"C:\Users\张三 三\AppData\Roaming\liuhao\data",
                "LIUHAO_AUTH_TTL_SECONDS=3600",
                "LIUHAO_MODEL_ROUTER=auto",
                "APP_ENV=production",
                r"D:\LiuHao-AI-OS\scripts\register_human_identity.py",
                "scripts/register_human_identity.py",
                r"C:\Program Files\LiuHao\runtime\python.exe",
                "parent_id=12 root_id=0 depth=3 ancestor=root",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    # 避免读到本机真实模型库，保证包内容只由本用例决定。
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "missing.sqlite3"))

    bundle_path = create_bundle(
        tmp_path / "out", root=fake_root, log_dir=fake_logs, ports=[65530]
    )
    assert verify_bundle(bundle_path) == []

    with zipfile.ZipFile(bundle_path) as archive:
        joined = "\n".join(
            archive.read(name).decode("utf-8", "replace") for name in archive.namelist()
        )

    for leaked in (
        "张三",
        "LIUHAO_AUTH_TTL_SECONDS",
        "LIUHAO_MODEL_ROUTER",
        "APP_ENV",
        "register_human_identity.py",
        "Program Files",
        "parent_id",
        "root_id",
        "depth",
        "ancestor",
    ):
        assert leaked not in joined, f"诊断包泄漏了 {leaked!r}"
    assert "<用户>" in joined
    assert "<环境变量>" in joined


def test_verify_bundle_catches_injected_secret(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("notes.txt", "api_key=sk-abcdefgh12345678\n")
        archive.writestr("database.db", "SQLite format 3\n")
    violations = verify_bundle(bad)
    assert violations
    assert any("notes.txt" in item for item in violations)
    assert any("database.db" in item for item in violations)


def test_pick_port_skips_occupied_port() -> None:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    held = sock.getsockname()[1]
    try:
        assert port_is_free(held) is False
        chosen = pick_port(held, count=10)
        assert chosen != held
        assert port_is_free(chosen) is True
    finally:
        sock.close()


def test_port_is_free_treats_time_wait_as_usable() -> None:
    """服务端先关连接留下 TIME_WAIT 时，端口仍算可用（服务能立刻重绑）。

    CI（Linux）在 ``test_runner_drains_inflight_request_and_refuses_new_ones``
    上就栽在这里：uvicorn 收尾时关掉 keep-alive 连接，127.0.0.1:<port> 进入
    TIME_WAIT，**不带 SO_REUSEADDR** 的 bind 报 EADDRINUSE，被误判成"端口还
    被占用"。Windows 上 TIME_WAIT 不阻止 bind，所以这条在 Windows 恒真；
    真正的回归验证在 Linux CI（以及 WSL）上跑。
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    accepted, _ = listener.accept()
    accepted.close()  # 服务端先关 → 服务端持有 TIME_WAIT
    time.sleep(0.1)
    client.close()
    listener.close()

    assert port_is_free(port) is True


@WINDOWS_ONLY
def test_autostart_roundtrip_hkcu() -> None:
    value_name = f"LiuHaoAIOS_pytest_{os.getpid()}"
    assert autostart.read(value_name)["enabled"] is False
    try:
        enabled = autostart.enable(REPO_ROOT, value_name=value_name)
        assert enabled["enabled"] is True
        assert "--autostart" in enabled["command"]
        assert "--open-browser" not in enabled["command"]
        assert "liuhao_desktop.py" in enabled["command"]
        assert autostart.read(value_name)["open_browser"] is False

        with_browser = autostart.enable(REPO_ROOT, open_browser=True, value_name=value_name)
        assert with_browser["open_browser"] is True
    finally:
        assert autostart.disable(value_name)["enabled"] is False
        assert autostart.read(value_name)["enabled"] is False


def test_autostart_command_quotes_pythonw_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo with space"
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "liuhao_desktop.py").touch()
    (repo / ".venv" / "Scripts" / "pythonw.exe").touch()
    command = autostart.build_command(repo)
    assert command.startswith('"')
    assert "--autostart" in command
    assert f'"{repo / "scripts" / "liuhao_desktop.py"}"' in command


def test_autostart_command_uses_installed_runtime(tmp_path: Path) -> None:
    """安装布局（app/runtime、无 .venv）也必须落到可执行的 pythonw。"""
    app = tmp_path / "app with space"
    (app / "runtime").mkdir(parents=True)
    (app / "scripts").mkdir()
    (app / "runtime" / "pythonw.exe").touch()
    (app / "scripts" / "launch_installed.py").touch()

    command = autostart.build_command(app)
    assert f'"{app / "runtime" / "pythonw.exe"}"' in command
    assert f'"{app / "scripts" / "launch_installed.py"}"' in command
    assert "--no-browser" in command
    assert "--autostart" not in command

    with_browser = autostart.build_command(app, open_browser=True)
    assert "--no-browser" not in with_browser


def test_autostart_command_refuses_when_entry_missing(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "runtime").mkdir(parents=True)
    (app / "runtime" / "pythonw.exe").touch()
    with pytest.raises(RuntimeError):
        autostart.build_command(app)


def test_state_icon_files_are_valid_ico(tmp_path: Path) -> None:
    for state, rgb in tray.STATE_COLORS.items():
        path = tray.write_state_icon(tmp_path / f"{state}.ico", rgb)
        data = path.read_bytes()
        assert data[:6] == struct.pack("<HHH", 0, 1, 1), state
        assert data[22:26] == struct.pack("<I", 40), state
        assert len(data) == 22 + 40 + 16 * 16 * 4 + 4 * 16


class _FakeStdin:
    """最小的 stdin 替身：收到 shutdown 就触发子进程"自行退出"。"""

    closed = False

    def __init__(self, proc: _FakeProc) -> None:
        self._proc = proc
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    def flush(self) -> None:
        if self.data.strip() == b"shutdown":
            self._proc.shutdown_from_stdin()


class _FakeProc:
    """模拟网关子进程：可优雅退出、可故意无视停止指令（触发强杀）。"""

    def __init__(self, *, ignore_stdin: bool = False, on_shutdown=None) -> None:
        self._done = threading.Event()
        self._exit_code = -9
        self._ignore_stdin = ignore_stdin
        self._on_shutdown = on_shutdown
        self.returncode: int | None = None
        self.killed = False
        self.stdin = _FakeStdin(self)

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake-gateway", timeout or 0)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._done.set()

    def shutdown_from_stdin(self) -> None:
        if self._ignore_stdin:
            return
        if self._on_shutdown is not None:
            self._on_shutdown()
        self._exit_code = 0
        self.returncode = 0
        self._done.set()


def test_stop_process_is_graceful_when_shutdown_is_confirmed(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    log.write_text("INFO:     Started server process\n", encoding="utf-8")
    offset = log.stat().st_size

    def mark_shutdown() -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"INFO:     {SHUTDOWN_LOG_MARKER}.\nINFO:     Finished server process\n")

    proc = _FakeProc(on_shutdown=mark_shutdown)
    result = stop_process(proc, timeout=1.0, log_path=log, log_offset=offset)

    assert result["stopped"] is True
    assert result["forced"] is False
    assert result["shutdown_confirmed"] is True
    assert result["graceful"] is True
    assert result["exit_code"] == 0
    assert proc.killed is False


def test_stop_process_confirms_shutdown_recorded_in_application_log(tmp_path: Path) -> None:
    """关闭记录写在应用日志里时，不能因为抓取文件里没有就误报"未确认"。"""
    capture = tmp_path / "desktop-gateway.log"
    capture.write_text("", encoding="utf-8")
    app_log = tmp_path / "liuhao.log"
    app_log.write_text("2026-01-01 | INFO | uvicorn.error | Shutting down\n", encoding="utf-8")
    offset = app_log.stat().st_size

    def mark_shutdown() -> None:
        with app_log.open("a", encoding="utf-8") as handle:
            handle.write(f"2026-01-01 | INFO | uvicorn.error | {SHUTDOWN_LOG_MARKER}.\n")

    proc = _FakeProc(on_shutdown=mark_shutdown)
    result = stop_process(
        proc,
        timeout=1.0,
        log_path=capture,
        log_offset=0,
        extra_logs=[(app_log, offset)],
    )

    assert result["shutdown_confirmed"] is True
    assert result["graceful"] is True


def test_stop_process_flags_inflight_tasks_cancelled_by_timeout(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    log.write_text("INFO:     Started server process\n", encoding="utf-8")
    offset = log.stat().st_size

    def mark_shutdown() -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(
                "WARNING:  Cancel 1 running task(s), timeout graceful shutdown exceeded\n"
                f"INFO:     {SHUTDOWN_LOG_MARKER}.\n"
            )

    proc = _FakeProc(on_shutdown=mark_shutdown)
    result = stop_process(proc, timeout=1.0, log_path=log, log_offset=offset)

    assert result["stopped"] is True
    assert result["forced"] is False
    assert result["shutdown_confirmed"] is True
    assert result["inflight_cancelled"] is True
    # 进程正常退出但确有任务被取消，不能报"优雅停止"
    assert result["graceful"] is False
    assert proc.killed is False


def test_stop_process_forces_kill_when_timeout_and_without_confirming(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    log.write_text("INFO:     Started server process\n", encoding="utf-8")
    proc = _FakeProc(ignore_stdin=True)
    started = time.monotonic()
    result = stop_process(proc, timeout=0.2, log_path=log, log_offset=0)
    elapsed = time.monotonic() - started

    assert result["forced"] is True
    assert proc.killed is True
    assert result["shutdown_confirmed"] is False
    assert result["graceful"] is False
    assert result["elapsed"] >= 0.2
    assert elapsed < 5.0


def test_stop_process_reports_error_when_stdin_unavailable() -> None:
    proc = _FakeProc(ignore_stdin=True)
    proc.stdin.closed = True  # type: ignore[attr-defined]
    result = stop_process(proc, timeout=0.1)

    assert result["error"]
    assert result["forced"] is True
    assert result["graceful"] is False


def test_stop_result_wording_is_honest() -> None:
    from src.desktop.app import _describe_stop

    assert _describe_stop({"stopped": False}) == "服务本来未运行"
    assert "优雅" in _describe_stop({"stopped": True, "graceful": True, "elapsed": 1.2})
    assert "强杀" in _describe_stop({"stopped": True, "forced": True, "elapsed": 10.0})
    assert "取消" in _describe_stop({"stopped": True, "inflight_cancelled": True, "elapsed": 8.1})
    assert "未确认" in _describe_stop({"stopped": True, "graceful": False, "forced": False, "elapsed": 0.3})


def _wait_for_port(port: int, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"runner 未在 {timeout:.0f}s 内监听 {port}")


def test_runner_drains_inflight_request_and_refuses_new_ones(tmp_path: Path) -> None:
    """真 uvicorn + 真 TCP：在途请求被收尾、新请求被拒、进程自己退出。"""
    port = pick_port(8765)
    log_path = tmp_path / "runner.log"
    log_handle = log_path.open("wb")
    env = dict(os.environ)
    # 这里是"直接拉起一个网关"的替身：用独立实例目录，避免与本机真实实例抢锁。
    env["LIUHAO_INSTANCE_ROOT"] = str(tmp_path / f"instance-{port}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "src.desktop._runner",
            "--app",
            "tests.desktop.slow_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    outcome: dict[str, object] = {}

    def call_slow() -> None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/slow", timeout=30) as resp:
                outcome["body"] = resp.read().decode("utf-8", "replace")
                outcome["status"] = resp.status
        except Exception as exc:  # noqa: BLE001 - 把失败原因带进断言
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    try:
        _wait_for_port(port)
        worker = threading.Thread(target=call_slow, name="slow-request", daemon=True)
        worker.start()
        time.sleep(1.0)  # slow_app 会睡 3s，确保停止信号到达时请求仍在途
        result = stop_process(proc, timeout=GRACEFUL_TIMEOUT + 3, log_path=log_path, log_offset=0)
        worker.join(timeout=20)
        assert not worker.is_alive()
        assert outcome.get("status") == 200, outcome
        assert json.loads(str(outcome.get("body"))) == {"done": True}

        assert result["stopped"] is True
        assert result["exit_code"] == 0
        assert result["shutdown_confirmed"] is True
        assert result["forced"] is False
        assert result["graceful"] is True
        assert result["elapsed"] < GRACEFUL_TIMEOUT

        # 停止后新请求已无法被接受：本机对已关闭端口可能回 RST、也可能被
        # 安全软件吞成超时，所以用"端口可重新绑定 + HTTP 失败"双重断言。
        assert port_is_free(port)
        info = http_json(f"http://127.0.0.1:{port}/slow", timeout=2.0)
        assert info["ok"] is False, info
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        log_handle.close()


@WINDOWS_ONLY
def test_tray_adds_icon_handles_menu_and_stops() -> None:
    received: list[str] = []
    widget = tray.SystemTray(received.append, tooltip="测试托盘")
    if not widget.start():
        reason = widget.error or "当前会话没有可用的任务栏通知区"
        widget.stop()
        pytest.skip(f"托盘不可用：{reason}")
    try:
        assert widget.hwnd
        widget.handle_command(1002)
        assert received == [tray.ACTION_SHOW_STATUS]
        widget.handle_command(1001)
        assert received[-1] == tray.ACTION_OPEN_CONSOLE
        widget.handle_command(1004)
        assert received[-1] == tray.ACTION_QUIT
        widget.set_tooltip("运行中")
        widget.set_tooltip("运行中")
        # 三态图标：绿/红/灰，用户一眼看得出服务状态
        assert widget.set_state(tray.STATE_RUNNING) is True
        assert widget.state == tray.STATE_RUNNING
        assert widget.set_state(tray.STATE_ERROR) is True
        assert widget.state == tray.STATE_ERROR
        assert widget.set_state(tray.STATE_STOPPED) is True
        assert widget.state == tray.STATE_STOPPED
        assert widget.set_state("unknown") is False
        # 气泡通知：只验证 Shell 接受了 NIF_INFO 请求；系统是否展示取决于
        # Windows 通知设置（专注助手等），代码层无法断言。
        assert widget.notify("测试通知", "服务状态通知探针") is True
        assert widget.notify("测试警告", "强制停止探针", warning=True) is True
    finally:
        widget.stop()
    assert widget.icon_added is False


@WINDOWS_ONLY
def test_tray_swallows_callback_errors() -> None:
    def broken(_action: str) -> None:
        raise RuntimeError("boom")

    widget = tray.SystemTray(broken)
    if not widget.start():
        widget.stop()
        pytest.skip("托盘不可用")
    try:
        widget.handle_command(1002)  # 不应抛异常，也不应杀死消息循环
        widget.stop()
        assert widget.icon_added is False
    finally:
        widget.stop()


def test_tray_class_name_is_stable_and_scoped() -> None:
    """第二个实例要靠类名找到托盘窗口：同类同键必须稳定，不同键必须不同。"""
    first = tray.tray_class_name(r"D:\data\a")
    assert first == tray.tray_class_name(r"D:\DATA\A")
    assert first != tray.tray_class_name(r"D:\data\b")
    assert first.startswith("LiuHaoTray_")


def test_single_instance_name_is_stable_and_data_dir_scoped() -> None:
    from src.desktop import app as app_module

    name = app_module.single_instance_name(r"D:\data\a")
    assert name == app_module.single_instance_name(r"D:\DATA\A")
    assert name != app_module.single_instance_name(r"D:\data\b")
    assert name.startswith("Local\\LiuHaoDesktopShell_")


@WINDOWS_ONLY
def test_second_instance_is_detected_by_named_mutex(tmp_path: Path) -> None:
    from src.desktop import app as app_module

    _, already = app_module.acquire_single_instance(tmp_path / "one")
    assert already is False
    _, already_again = app_module.acquire_single_instance(tmp_path / "one")
    assert already_again is True
    _, other = app_module.acquire_single_instance(tmp_path / "two")
    assert other is False


@WINDOWS_ONLY
def test_request_show_focuses_running_tray_window(tmp_path: Path) -> None:
    """跨线程走真实的 FindWindow + 注册消息：已有实例必须能被叫醒。"""
    received: list[str] = []
    widget = tray.SystemTray(received.append, instance_key=str(tmp_path / "data"))
    if not widget.start():
        widget.stop()
        pytest.skip(f"托盘不可用：{widget.error}")
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not tray.find_tray_window(str(tmp_path / "data")):
            time.sleep(0.05)
        assert tray.find_tray_window(str(tmp_path / "data")) == widget.hwnd
        assert tray.request_show(str(tmp_path / "data"), timeout=2.0) is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not received:
            time.sleep(0.05)
        assert received == [tray.ACTION_SHOW_STATUS]
        # 没有对应实例时不能假装送达，否则第二个实例会静默退出。
        assert tray.request_show(str(tmp_path / "other"), timeout=0.2) is False
    finally:
        widget.stop()


@WINDOWS_ONLY
def test_handoff_opens_published_console_when_window_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.desktop import app as app_module

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "instance.json").write_text(
        json.dumps({"url": "http://127.0.0.1:18099"}), encoding="utf-8"
    )
    opened: list[str] = []
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "request_show", lambda *_a, **_k: False)
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    app_module.DesktopApp(hidden=True)._handoff_to_running()
    assert opened == ["http://127.0.0.1:18099/"]


def test_open_logs_action_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """托盘菜单「打开日志目录」必须真的打开目录，而不是静默无反应。"""
    from src.desktop import app as app_module

    started: list[str] = []
    monkeypatch.setattr(app_module, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(app_module.os, "startfile", started.append, raising=False)
    monkeypatch.setattr(app_module.webbrowser, "open", started.append)

    app_module.DesktopApp()._handle_action(tray.ACTION_OPEN_LOGS)
    assert (tmp_path / "logs").is_dir()
    assert started and str(tmp_path / "logs") in started[0]


def test_open_console_action_uses_service_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """托盘菜单「打开控制台」必须打开网关实际发布的地址，而不是固定猜端口。"""
    from src.desktop import app as app_module

    opened: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    desktop = app_module.DesktopApp(port=18123)
    desktop.service.port = 18123

    desktop._handle_action(tray.ACTION_OPEN_CONSOLE)
    assert opened == ["http://127.0.0.1:18123/"]


@WINDOWS_ONLY
def test_firewall_inspection_is_read_only() -> None:
    result = inspect_firewall(65530)
    assert result["auto_modify"] is False
    assert AUTO_MODIFY_FIREWALL is False
    assert isinstance(result["guidance"], list)
    assert result["guidance"]
    assert result["risk"] in {"low", "medium", "high"}
    assert "绝不修改" in result["disclaimer"]


def test_cli_about_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    from src.desktop.__main__ import main

    assert main(["about"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["app"] == BRAND_LOCKUP
    assert "data_dir" in output
