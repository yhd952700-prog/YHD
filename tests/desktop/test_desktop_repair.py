"""修复动作的行为测试：幂等、可回退、失败时不乱改。

只覆盖 ``src/desktop/repair.py`` 的编排与文件操作；真实网关的换端口重启在
演练脚本里验证（单元测试不拉起子进程，避免与其它测试抢单实例锁）。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.desktop import repair


class FakeService:
    """够用的网关替身：记录 start/stop，模拟换端口。"""

    def __init__(self, port: int, fallback_port: int | None = None) -> None:
        self.port = port
        self.fallback_port = fallback_port
        self.running = False
        self.starts: list[bool] = []
        self.stops = 0
        self.last_error: str | None = None

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def is_running(self) -> bool:
        return self.running

    def stop(self) -> dict[str, object]:
        self.stops += 1
        was = self.running
        self.running = False
        return {"stopped": was, "graceful": was, "forced": False, "elapsed": 0.1}

    def start(self, timeout: float = 90, *, allow_port_fallback: bool = False) -> bool:
        self.starts.append(allow_port_fallback)
        if allow_port_fallback and self.fallback_port:
            self.port = self.fallback_port
        self.running = True
        return True


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """隔离数据目录配置与相关环境变量，避免碰到真实用户目录。"""
    for key in (
        repair.DATA_DIR_ENV,
        repair.LOG_DIR_ENV,
        repair.CONFIG_ENV,
        repair.CONSOLE_DIST_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "desktop.json"
    monkeypatch.setenv(repair.CONFIG_ENV, str(config))
    yield config
    for key in (repair.DATA_DIR_ENV, repair.LOG_DIR_ENV):
        os.environ.pop(key, None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------- #
# 1. 换端口
# ---------------------------------------------------------------------- #
def test_switch_port_moves_and_is_idempotent(tmp_path: Path) -> None:
    occupied = _free_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", occupied))
    holder.listen(1)
    service = FakeService(occupied, fallback_port=_free_port())
    try:
        first = repair.switch_port(service, log_dir=tmp_path / "logs")
        assert first["ok"] is True
        assert first["changed"] is True
        assert service.starts == [True]
        assert service.port != occupied
        assert any(item["label"] == "新地址" for item in first["evidence"])

        second = repair.switch_port(service, log_dir=tmp_path / "logs")
        assert second["ok"] is True
        assert second["changed"] is False, "重复执行应停在同一个端口，不能一路往上漂"
    finally:
        holder.close()


def test_switch_port_reports_failure_without_lying(tmp_path: Path) -> None:
    class Broken(FakeService):
        def start(self, timeout: float = 90, *, allow_port_fallback: bool = False) -> bool:
            self.last_error = "端口全满"
            return False

    service = Broken(8080)
    result = repair.switch_port(service, log_dir=tmp_path / "logs")
    assert result["ok"] is False
    assert "端口全满" in result["summary"]
    assert result["changed"] is False


# ---------------------------------------------------------------------- #
# 2. 改数据目录
# ---------------------------------------------------------------------- #
def test_change_data_dir_persists_and_self_checks(isolated_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "newdata"
    service = FakeService(8090)
    result = repair.change_data_dir(target, service=service, log_dir=tmp_path / "logs")

    assert result["ok"] is True
    assert result["changed"] is True
    assert (target / "logs").is_dir()
    saved = json.loads(isolated_env.read_text(encoding="utf-8"))
    assert saved["data_dir"] == str(target.resolve())
    assert os.environ[repair.DATA_DIR_ENV] == str(target.resolve())
    assert os.environ[repair.LOG_DIR_ENV] == str(target.resolve() / "logs")
    assert service.starts == [False], "改目录后的重启不允许自动换端口"
    assert any(item["label"] == "写入自检" for item in result["evidence"])

    again = repair.change_data_dir(target, service=service, log_dir=tmp_path / "logs")
    assert again["ok"] is True
    assert again["changed"] is False


def test_change_data_dir_refuses_unwritable_without_touching_config(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repair,
        "probe_directory_writable",
        lambda path: {"path": str(path), "writable": False, "error": {"type": "PermissionError"}},
    )
    result = repair.change_data_dir(tmp_path / "blocked", log_dir=tmp_path / "logs")
    assert result["ok"] is False
    assert result["changed"] is False
    assert not isolated_env.exists(), "探测失败时不能写配置"
    assert "PermissionError" in result["summary"]


def test_change_data_dir_rejects_file_path(isolated_env: Path, tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    result = repair.change_data_dir(file_path, log_dir=tmp_path / "logs")
    assert result["ok"] is False
    assert "文件" in result["summary"]


def test_apply_saved_data_dir_prefers_explicit_env(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    isolated_env.write_text(json.dumps({"data_dir": str(saved)}), encoding="utf-8")

    monkeypatch.setenv(repair.DATA_DIR_ENV, str(tmp_path / "explicit"))
    assert repair.apply_saved_data_dir(log_dir=tmp_path / "logs")["applied"] is False

    monkeypatch.delenv(repair.DATA_DIR_ENV)
    applied = repair.apply_saved_data_dir(log_dir=tmp_path / "logs")
    assert applied["applied"] is True
    assert os.environ[repair.DATA_DIR_ENV] == str(saved)


# ---------------------------------------------------------------------- #
# 3. 打开模型设置
# ---------------------------------------------------------------------- #
def test_open_model_settings_deep_links_and_copies(tmp_path: Path) -> None:
    opened: list[str] = []
    copied: list[str] = []
    result = repair.open_model_settings(
        FakeService(8123),
        "deepseek-chat",
        opener=opened.append,
        clipboard=copied.append,
        log_dir=tmp_path / "logs",
    )
    assert result["ok"] is True
    assert opened == ["http://127.0.0.1:8123/?p=settings"]
    assert copied == ["deepseek-chat"]
    assert "不能自动把光标" in result["summary"]


# ---------------------------------------------------------------------- #
# 4. 分步测连接
# ---------------------------------------------------------------------- #
class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        return


def test_probe_connection_three_steps(tmp_path: Path) -> None:
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/health"
        result = repair.probe_connection(url, log_dir=tmp_path / "logs")
        assert result["ok"] is True
        labels = [item["label"] for item in result["evidence"]]
        assert labels == ["域名", "端口", "DNS", "TCP", "HTTP"]
    finally:
        server.shutdown()
        server.server_close()


def test_probe_connection_reports_dns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(repair.socket, "getaddrinfo", boom)
    result = repair.probe_connection("https://api.invalid/v1", log_dir=tmp_path / "logs")
    assert result["ok"] is False
    assert "DNS 解析失败" in result["summary"]
    assert "api.invalid" in result["summary"]


def test_probe_connection_reports_tcp_failure(tmp_path: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    # 绑定但不 listen：连接会被拒绝，且不会误连上别的服务。
    try:
        result = repair.probe_connection(f"http://127.0.0.1:{port}/", timeout=1.0, log_dir=tmp_path / "logs")
        assert result["ok"] is False
        assert "TCP 连不上" in result["summary"]
    finally:
        sock.close()


# ---------------------------------------------------------------------- #
# 5. 本地库恢复
# ---------------------------------------------------------------------- #
def _corrupt_sqlite(path: Path) -> None:
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64 + b"garbage" * 64)


def test_recover_database_moves_original_and_rebuilds(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    broken = data / "model_registry.sqlite3"
    _corrupt_sqlite(broken)
    assert repair.quick_check(broken) == "failed"

    result = repair.recover_database(data_dir=data, mode="rebuild", log_dir=tmp_path / "logs")
    assert result["ok"] is True
    assert result["changed"] is True
    recovered = [item for item in data.iterdir() if item.name.startswith("recovered-")]
    assert len(recovered) == 1
    assert (recovered[0] / "model_registry.sqlite3").exists(), "原件必须保留"
    assert repair.quick_check(broken) == "ok"
    assert any(item["label"] == "原件保留" for item in result["evidence"])

    again = repair.recover_database(data_dir=data, mode="rebuild", log_dir=tmp_path / "logs")
    assert again["ok"] is True
    assert again["changed"] is False

    reverted = result["revert_action"]()
    assert reverted["ok"] is True
    assert repair.quick_check(broken) == "failed", "回退应把损坏原件放回原位"
    assert not (recovered[0] / "model_registry.sqlite3").exists()


def test_recover_database_prefers_backup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    broken = data / "app.sqlite3"
    _corrupt_sqlite(broken)
    good = data / "app.sqlite3.bak"
    with contextlib.closing(__import__("sqlite3").connect(str(good))) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()

    result = repair.recover_database(data_dir=data, mode="restore", log_dir=tmp_path / "logs")
    assert result["ok"] is True
    assert repair.quick_check(broken) == "ok"
    assert "从备份恢复" in json.dumps(result["evidence"], ensure_ascii=False)


# ---------------------------------------------------------------------- #
# 6. 静态资源
# ---------------------------------------------------------------------- #
def _make_console(root: Path, *, index: bool = True, assets: tuple[str, ...] = ("assets/app.js",)) -> None:
    if index:
        root.mkdir(parents=True, exist_ok=True)
        body = "".join(f'<script src="./{item}"></script>' for item in assets)
        (root / "index.html").write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    else:
        root.mkdir(parents=True, exist_ok=True)


def test_repair_static_assets_copies_only_missing(tmp_path: Path) -> None:
    target = tmp_path / "dist"
    _make_console(target, assets=("assets/app.js", "assets/app.css"))
    (target / "assets").mkdir()
    (target / "assets" / "app.js").write_text("keep-me", encoding="utf-8")

    source = tmp_path / "payload"
    _make_console(source, assets=("assets/app.js", "assets/app.css"))
    (source / "assets").mkdir()
    (source / "assets" / "app.js").write_text("do-not-overwrite", encoding="utf-8")
    (source / "assets" / "app.css").write_text("css", encoding="utf-8")

    result = repair.repair_static_assets(
        console_dir=target, sources=[source], log_dir=tmp_path / "logs"
    )
    assert result["ok"] is True
    assert result["changed"] is True
    assert (target / "assets" / "app.css").is_file()
    assert (target / "assets" / "app.js").read_text(encoding="utf-8") == "keep-me"

    again = repair.repair_static_assets(
        console_dir=target, sources=[source], log_dir=tmp_path / "logs"
    )
    assert again["changed"] is False

    reverted = result["revert_action"]()
    assert reverted["ok"] is True
    assert not (target / "assets" / "app.css").exists(), "回退只删本次新增的文件"
    assert (target / "assets" / "app.js").is_file()


def test_repair_static_assets_restores_missing_index(tmp_path: Path) -> None:
    target = tmp_path / "dist"
    _make_console(target, index=False)
    source = tmp_path / "payload"
    _make_console(source, assets=("assets/app.js",))
    (source / "assets").mkdir()
    (source / "assets" / "app.js").write_text("js", encoding="utf-8")

    result = repair.repair_static_assets(
        console_dir=target, sources=[source], log_dir=tmp_path / "logs"
    )
    assert result["ok"] is True
    assert (target / "index.html").is_file()
    assert (target / "assets" / "app.js").is_file()


def test_repair_static_assets_rejects_other_build(tmp_path: Path) -> None:
    """来源是另一次构建（文件名对不上）时不能假装补齐成功。"""
    target = tmp_path / "dist"
    _make_console(target, assets=("assets/index-NEW.js",))
    source = tmp_path / "payload"
    _make_console(source, assets=("assets/index-OLD.js",))
    (source / "assets").mkdir()
    (source / "assets" / "index-OLD.js").write_text("old", encoding="utf-8")

    result = repair.repair_static_assets(
        console_dir=target, sources=[source], log_dir=tmp_path / "logs"
    )
    assert result["ok"] is False
    assert result["changed"] is False
    assert result["mode"] == "reinstall"
    assert "同一版构建" in result["summary"]


def test_repair_static_assets_without_source_says_reinstall(tmp_path: Path) -> None:
    target = tmp_path / "dist"
    _make_console(target, index=False)
    result = repair.repair_static_assets(
        console_dir=target, sources=[], log_dir=tmp_path / "logs"
    )
    assert result["ok"] is False
    assert result["mode"] == "reinstall"
    assert "重新安装" in result["summary"]


# ---------------------------------------------------------------------- #
# 汇总状态与日志
# ---------------------------------------------------------------------- #
def test_collect_status_reports_all_six(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _corrupt_sqlite(data / "broken.sqlite3")
    dist = tmp_path / "dist"
    _make_console(dist, assets=("assets/app.js",))

    status = repair.collect_status(FakeService(8099), data_dir=data, console_dir=dist)
    assert set(status) == {"port", "data_dir", "models", "databases", "assets"}
    assert status["databases"]["corrupt"] == ["broken.sqlite3"]
    assert status["assets"]["missing"] == ["assets/app.js"]
    assert status["data_dir"]["writable"] is True


def test_repair_log_is_written_and_redacted(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    repair.change_data_dir(tmp_path / "d", log_dir=log_dir)
    text = (log_dir / "repair.log").read_text(encoding="utf-8")
    assert "change_data_dir.start" in text
    assert "change_data_dir.result" in text
    assert "Administrator" not in text

