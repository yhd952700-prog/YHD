"""桌面系统层测试：关于页、自启、托盘、诊断包脱敏、防火墙只读检测。"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.desktop import autostart, diagnostics, tray  # noqa: E402
from src.desktop.about import collect_about  # noqa: E402
from src.desktop.diagnostics import (  # noqa: E402
    create_bundle,
    redact_text,
    verify_bundle,
)
from src.desktop.firewall import AUTO_MODIFY_FIREWALL, inspect_firewall  # noqa: E402
from src.desktop.probe import pick_port, port_is_free  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows 系统层能力")


def test_about_reports_real_paths(tmp_path: Path) -> None:
    about = collect_about(tmp_path)
    assert about["app"] == "鎏灏 LIUHAO X"
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
    (repo / ".venv" / "Scripts" / "pythonw.exe").touch()
    command = autostart.build_command(repo)
    assert command.startswith('"')
    assert "--autostart" in command
    assert f'"{repo / "scripts" / "liuhao_desktop.py"}"' in command


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
    assert output["app"] == "鎏灏 LIUHAO X"
    assert "data_dir" in output
