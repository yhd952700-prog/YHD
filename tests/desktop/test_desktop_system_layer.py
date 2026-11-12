"""桌面系统层测试：关于页、自启、托盘、优雅停机、诊断包脱敏、防火墙只读检测。"""

from __future__ import annotations

import gc
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
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.brand import BRAND_LOCKUP  # noqa: E402
from src.desktop import about as about_mod  # noqa: E402
from src.desktop import app as app_mod  # noqa: E402
from src.desktop import autostart, diagnostics, tray  # noqa: E402
from src.desktop import probe as probe_mod  # noqa: E402
from src.desktop.about import collect_about  # noqa: E402
from src.desktop.diagnostics import (  # noqa: E402
    create_bundle,
    redact_text,
    verify_bundle,
)
from src.desktop.firewall import AUTO_MODIFY_FIREWALL, inspect_firewall  # noqa: E402
from src.desktop.probe import http_json, pick_port, port_is_free  # noqa: E402
from src.desktop.service import (  # noqa: E402
    GRACEFUL_TIMEOUT,
    SHUTDOWN_LOG_MARKER,
    GatewayService,
    stop_process,
)

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


def test_about_skips_git_subprocess_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非仓库目录下不许创建子进程（安装树没有 .git，以前每次请求 spawn 两次 git）。"""
    calls: list[object] = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("非 git 仓库不应创建子进程")

    def fake_popen(*args, **kwargs):
        calls.append(args)
        raise AssertionError("非 git 仓库不应创建子进程")

    monkeypatch.setattr(about_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(about_mod.subprocess, "Popen", fake_popen)
    about_mod._GIT_CACHE.clear()
    about = collect_about(tmp_path)
    assert calls == []
    assert about["commit"] is None
    assert about["build_time_source"] != "git_commit"


def test_git_fields_cache_hit_and_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit 与构建时间共用一次查询：第二次命中缓存，TTL 过期后重新取。"""
    (tmp_path / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="abc123\x1f2026-01-01T00:00:00+08:00\n")

    monkeypatch.setattr(about_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(about_mod, "_GIT_CACHE_TTL", 60.0)
    about_mod._GIT_CACHE.clear()
    assert about_mod._git_fields(tmp_path) == ("abc123", "2026-01-01T00:00:00+08:00")
    assert about_mod._git_fields(tmp_path) == ("abc123", "2026-01-01T00:00:00+08:00")
    assert len(calls) == 1
    monkeypatch.setattr(about_mod, "_GIT_CACHE_TTL", 0.0)
    about_mod._git_fields(tmp_path)
    assert len(calls) == 2
    about_mod._GIT_CACHE.clear()


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
    # 数据目录也钉到临时目录：数据库 quick_check 不能去读本机真实数据库。
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=fake_logs,
        ports=[65530],
        probe_network=False,
    )
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
    # 避免读到本机真实模型库 / 真实数据目录，保证包内容只由本用例决定。
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "missing.sqlite3"))
    empty_data = tmp_path / "data"
    empty_data.mkdir()
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(empty_data))

    bundle_path = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=fake_logs,
        ports=[65530],
        probe_network=False,
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


def _bundle_payload(bundle: Path, name: str):
    with zipfile.ZipFile(bundle) as archive:
        return json.loads(archive.read(name).decode("utf-8"))


def _bundle_check(bundle: Path, check_id: str) -> dict:
    diagnosis = _bundle_payload(bundle, "diagnosis.json")
    return next(item for item in diagnosis["checks"] if item["id"] == check_id)


def _bundle_logs_and_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "no-models.sqlite3"))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    return logs, data_dir


def test_bundle_flags_incomplete_install_without_src_or_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 src / 缺 runtime 时，installation.json 的完整性清单必须点名缺失项。"""
    fake_root = tmp_path / "app"
    fake_root.mkdir()
    (fake_root / "LiuHao.bat").write_text("@echo off\n", encoding="utf-8")
    console = fake_root / "console"
    console.mkdir()
    (console / "index.html").write_text("<html></html>\n", encoding="utf-8")
    logs, data_dir = _bundle_logs_and_data(tmp_path, monkeypatch)

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    assert verify_bundle(bundle) == []

    installation = _bundle_payload(bundle, "installation.json")
    integrity = installation["integrity"]
    assert integrity["mode"] == "installed"
    assert integrity["complete"] is False
    assert "src" in integrity["missing"]
    assert "runtime/python.exe" in integrity["missing"]

    check = _bundle_check(bundle, "installation_incomplete")
    assert check["status"] == "detected"
    assert check["evidence"][0]["file"] == "installation.json"
    assert check["evidence"][0]["field"] == "integrity.missing"


def test_write_probe_records_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACL 拒绝写入时要留下 PermissionError + errno，而不是只写一个 False。"""
    target = tmp_path / "locked"
    target.mkdir()
    real_open = open

    def deny(path, *args, **kwargs):
        if str(path).startswith(str(target)):
            raise PermissionError(13, "denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", deny)
    probe = diagnostics._probe_directory_writable(target)
    assert probe["writable"] is False
    assert probe["error"]["type"] == "PermissionError"
    assert probe["error"]["errno"] == 13


def test_bundle_creates_missing_log_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日志目录不存在（首次启动 / 被清理）时自动创建，并如实记录 created=true。

    契约：目录不存在 ≠ 不可写。首次启动时日志目录本来就还没建，此时把
    ``directory_not_writable`` 判成 detected 会误导用户去修权限；只有创建
    或写入真的失败才报错（见下一条用例）。
    """
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "src").mkdir()
    (fake_root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "src" / "gateway").mkdir()
    (fake_root / "src" / "gateway" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "src" / "desktop").mkdir()
    (fake_root / "src" / "desktop" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "config").mkdir()
    (fake_root / "config" / "logging.yaml").write_text("", encoding="utf-8")
    missing_logs = tmp_path / "not-created"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "no-models.sqlite3"))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=missing_logs,
        ports=[65530],
        probe_network=False,
    )
    installation = _bundle_payload(bundle, "installation.json")
    probe = installation["logs_dir_write_probe"]
    assert probe["created"] is True
    assert probe["exists"] is True
    assert probe["writable"] is True
    assert probe["error"] is None
    assert missing_logs.is_dir()

    check = _bundle_check(bundle, "directory_not_writable")
    assert check["status"] == "clear"


def test_bundle_flags_log_directory_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录不存在且创建被拒（ACL / 父目录只读）时必须报不可写，并留异常类型。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "src").mkdir()
    (fake_root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "src" / "gateway").mkdir()
    (fake_root / "src" / "gateway" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "src" / "desktop").mkdir()
    (fake_root / "src" / "desktop" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "config").mkdir()
    (fake_root / "config" / "logging.yaml").write_text("", encoding="utf-8")
    missing_logs = tmp_path / "locked-parent" / "logs"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    real_mkdir = Path.mkdir

    def deny_mkdir(self, *args, **kwargs):
        if self == missing_logs:
            raise PermissionError(13, "denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "no-models.sqlite3"))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=missing_logs,
        ports=[65530],
        probe_network=False,
    )
    installation = _bundle_payload(bundle, "installation.json")
    probe = installation["logs_dir_write_probe"]
    assert probe["created"] is False
    assert probe["exists"] is False
    assert probe["writable"] is False
    assert probe["error"]["type"] == "PermissionError"
    assert probe["error"]["errno"] == 13
    assert probe["error"]["missing"] is True

    check = _bundle_check(bundle, "directory_not_writable")
    assert check["status"] == "detected"
    assert "日志目录" in check["conclusion"]


def test_ports_payload_separates_foreign_and_own_listener() -> None:
    foreign = diagnostics._collect_ports(
        [65530],
        {
            65530: {
                "pid": 4242,
                "process": "other.exe",
                "command": "other.exe --serve",
                "app_related": False,
            }
        },
    )
    assert foreign["port_conflicts"][0]["port"] == 65530
    assert "other.exe" in foreign["verdict"]

    own = diagnostics._collect_ports(
        [8080],
        {
            8080: {
                "pid": 99,
                "process": "python.exe",
                "command": "python -m src.gateway",
                "app_related": True,
            }
        },
    )
    assert own["port_conflicts"] == []


def test_bundle_flags_port_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    monkeypatch.setattr(
        diagnostics,
        "_listening_owners",
        lambda: {
            65530: {
                "pid": 4242,
                "process": "other.exe",
                "command": "other.exe --serve",
                "app_related": False,
            }
        },
    )
    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    check = _bundle_check(bundle, "port_conflict")
    assert check["status"] == "detected"
    assert "other.exe" in check["conclusion"]
    assert check["evidence"][0]["field"] == "port_conflicts"


def test_bundle_classifies_invalid_key_from_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    (logs / "app.log").write_text(
        "2026-01-01 10:00:00 INFO model probe failed provider=openai "
        "model=gpt-4o reason=invalid_key detail=Incorrect API key provided: "
        "sk-abcdefgh12345678\n",
        encoding="utf-8",
    )
    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    assert verify_bundle(bundle) == []

    health = _bundle_payload(bundle, "model_health.json")
    assert health["log_verdict"] == "key_invalid"
    assert health["log_signals"]["newest"]["file"].startswith("logs/")
    check = _bundle_check(bundle, "model_key_invalid")
    assert check["status"] == "detected"
    assert check["evidence"][0]["file"] == "model_health.json"

    with zipfile.ZipFile(bundle) as archive:
        joined = "\n".join(
            archive.read(name).decode("utf-8", "replace") for name in archive.namelist()
        )
    assert "sk-abcdefgh12345678" not in joined


def test_bundle_classifies_network_failure_from_logs_and_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    (logs / "app.log").write_text(
        "2026-01-01 10:00:00 WARNING model probe failed provider=openai "
        "model=gpt-4o reason=unreachable detail=ConnectionError\n",
        encoding="utf-8",
    )
    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    health = _bundle_payload(bundle, "model_health.json")
    assert health["log_verdict"] == "network_unreachable"
    assert _bundle_check(bundle, "model_network_unreachable")["status"] == "detected"

    # 端点探测本身：本机监听端口可达，关闭后不可达。
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    model = {"name": "local", "provider": "openai", "base_url": f"http://127.0.0.1:{port}/v1"}
    try:
        assert diagnostics._probe_endpoint(model)["tcp"] == "reachable"
    finally:
        sock.close()
    assert diagnostics._probe_endpoint(model)["tcp"] == "unreachable"


def test_bundle_flags_enabled_model_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有日志时，也要能判定"云端模型根本没填 Key"这一种。"""
    import sqlite3

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    registry = tmp_path / "models.sqlite3"
    conn = sqlite3.connect(registry)
    conn.execute(
        "CREATE TABLE ai_model_configs (name TEXT, provider TEXT, model TEXT, "
        "base_url TEXT, enabled INTEGER, is_default INTEGER, api_key_enc TEXT)"
    )
    conn.execute(
        "INSERT INTO ai_model_configs VALUES (?,?,?,?,?,?,?)",
        ("main", "openai", "gpt-4o", "https://api.openai.com/v1", 1, 1, None),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(registry))

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    providers = _bundle_payload(bundle, "providers.json")
    assert providers["registry"]["models"][0]["has_key"] is False

    check = _bundle_check(bundle, "model_key_invalid")
    assert check["status"] == "detected"
    assert "main" in check["conclusion"]
    assert check["evidence"][0]["field"] == "models_without_key"


def test_log_signal_fallback_recognizes_legacy_error_text() -> None:
    """没有结构化字段时，仍要认得旧日志里的三类特征文本。"""
    refused = (
        "HTTPConnectionPool(host='127.0.0.1', port=11500): Max retries exceeded "
        "(Caused by NewConnectionError(\"[WinError 10061] 由于目标计算机积极拒绝\"))"
    )
    assert diagnostics._classify_signal_line(refused)["category"] == "network_unreachable"
    assert (
        diagnostics._classify_signal_line("openai.AuthenticationError: invalid_api_key")[
            "category"
        ]
        == "key_invalid"
    )
    assert (
        diagnostics._classify_signal_line("error type=insufficient_quota")["category"]
        == "quota_exhausted"
    )
    assert diagnostics._classify_signal_line("GET /assets/index-a.js 200") is None


def test_bundle_flags_missing_console_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "repo"
    console = fake_root / "console"
    console.mkdir(parents=True)
    (console / "index.html").write_text(
        '<script type="module" src="/assets/main-abc.js"></script>\n', encoding="utf-8"
    )
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    assets = _bundle_payload(bundle, "static_assets.json")
    assert assets["index_present"] is True
    assert assets["missing"] == ["assets/main-abc.js"]
    assert _bundle_check(bundle, "console_assets_missing")["status"] == "detected"

    assets_dir = console / "assets"
    assets_dir.mkdir()
    (assets_dir / "main-abc.js").write_text("console.log(1);\n", encoding="utf-8")
    healthy = create_bundle(
        tmp_path / "out2",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    assert _bundle_payload(healthy, "static_assets.json")["missing"] == []
    assert _bundle_check(healthy, "console_assets_missing")["status"] == "clear"


def test_bundle_flags_corrupt_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, data_dir = _bundle_logs_and_data(tmp_path, monkeypatch)
    good = data_dir / "good.db"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row-{i}" * 20,) for i in range(200)])
    conn.commit()
    conn.close()
    raw = good.read_bytes()
    (data_dir / "broken.db").write_bytes(raw[: len(raw) // 2])

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    assert verify_bundle(bundle) == []

    db_check = _bundle_payload(bundle, "db_check.json")
    by_path = {item["path"]: item for item in db_check["databases"]}
    assert by_path["good.db"]["result"] == "ok"
    assert by_path["broken.db"]["result"] == "failed"

    check = _bundle_check(bundle, "database_corrupt")
    assert check["status"] == "detected"
    assert "broken.db" in check["conclusion"]
    assert check["evidence"][0]["file"] == "db_check.json"


def test_diagnosis_covers_all_eight_failure_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    diagnosis = _bundle_payload(bundle, "diagnosis.json")
    ids = {item["id"] for item in diagnosis["checks"]}
    assert ids == {
        "service_not_running",
        "installation_incomplete",
        "directory_not_writable",
        "disk_space_low",
        "port_conflict",
        "model_key_invalid",
        "model_network_unreachable",
        "console_assets_missing",
        "database_corrupt",
    }
    assert diagnosis["offline"] is True
    assert set(diagnosis["summary"]) == {"detected", "clear", "inconclusive"}
    assert diagnosis["headline"]
    for check in diagnosis["checks"]:
        if check["status"] != "clear":
            assert check["next_step"], check["id"]


def test_diagnosis_flags_service_not_running_without_gateway_process() -> None:
    """服务死了、桌面壳还在时：不能把桌面进程当成"网关还活着"。"""
    diagnosis = diagnostics._build_diagnosis(
        {
            "integrity": {"missing": []},
            "data_dir_write_probe": {"writable": True},
            "logs_dir_write_probe": {"writable": True},
        },
        {"entries": [], "port_conflicts": [], "verdict": "no-conflict"},
        {"missing": []},
        {
            "log_verdict": "unknown",
            "log_signals": {"counts": {}},
            "endpoints": [],
            "endpoint_verdict": "unknown",
            "models_without_key": [],
        },
        {"databases": []},
        {"probes": [{"url": "http://127.0.0.1:8080/v1/ready", "reachable": False}]},
        {"matched": 1, "gateway_matched": 0, "gateway": [], "processes": [{"pid": 1}]},
        {"reachable": False},
    )
    check = next(item for item in diagnosis["checks"] if item["id"] == "service_not_running")
    assert check["status"] == "detected"
    assert "服务没有启动" in check["conclusion"]
    assert check["evidence"][0]["file"] == "ready_probe.json"
    assert "重新启动服务" in check["next_step"]


def test_diagnosis_clears_service_when_gateway_health_answers() -> None:
    diagnosis = diagnostics._build_diagnosis(
        {
            "integrity": {"missing": []},
            "data_dir_write_probe": {"writable": True},
            "logs_dir_write_probe": {"writable": True},
        },
        {"entries": [], "port_conflicts": [], "verdict": "no-conflict"},
        {"missing": []},
        {"log_verdict": "unknown", "log_signals": {"counts": {}}, "endpoints": []},
        {"databases": []},
        {
            "probes": [
                {
                    "url": "http://127.0.0.1:8080/v1/health",
                    "reachable": True,
                    "body": {"service": "liuhao-gateway", "status": "ok"},
                }
            ]
        },
        {"gateway_matched": 1, "gateway": [{"pid": 42}], "processes": []},
        {"reachable": True},
    )
    check = next(item for item in diagnosis["checks"] if item["id"] == "service_not_running")
    assert check["status"] == "clear"


def test_service_guidance_gives_next_action_for_three_dead_modes() -> None:
    killed = diagnostics.service_guidance(running=False)
    assert "已停止" in killed["service"]
    assert "重新启动服务" in killed["next"]

    port_full = diagnostics.service_guidance(
        running=False, last_error="8080..8179 全部被占用，找不到空闲端口"
    )
    assert "端口被占用" in port_full["service"]
    assert "ports.json" in port_full["next"]

    # 默认不自动换端口时的原文（只报被占用的那一个端口）。
    port_busy = diagnostics.service_guidance(
        running=False,
        last_error="端口 8095 已被占用。为避免地址变化，桌面壳不会自动换端口；",
        port=8095,
        requested_port=8095,
    )
    assert "端口 8095 被占用" in port_busy["service"]
    assert "ports.json" in port_busy["next"]

    readonly = diagnostics.service_guidance(running=False, data_dir_writable=False)
    assert "数据目录不可写" in readonly["service"]
    assert "权限" in readonly["next"]

    shifted = diagnostics.service_guidance(running=True, port=8081, requested_port=8080)
    assert "8080" in shifted["next"]
    assert "8081" in shifted["next"]


def test_format_bytes_for_diagnostics_hint() -> None:
    assert diagnostics.format_bytes(512) == "512 B"
    assert diagnostics.format_bytes(1536) == "1.5 KB"
    assert diagnostics.format_bytes(5 * 1024 * 1024) == "5.0 MB"


def test_bundle_masks_real_username_and_recovery_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs, _ = _bundle_logs_and_data(tmp_path, monkeypatch)
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "tester"
    (logs / "liuhao.log").write_text(
        f"path=C:\\Users\\{username}\\secret\nrecovery_code=ABCD-EFGH-1234\n",
        encoding="utf-8",
    )
    bundle = create_bundle(
        tmp_path / "out",
        root=tmp_path / "repo",
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    joined = ""
    with zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():
            joined += archive.read(name).decode("utf-8", "ignore")
    assert f"Users\\{username}" not in joined
    assert "ABCD-EFGH-1234" not in joined
    assert verify_bundle(bundle) == []



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


def _hold_port(family: int, address: str) -> tuple[socket.socket, int]:
    holder = socket.socket(family, socket.SOCK_STREAM)
    holder.bind((address, 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


def _can_bind(family: int, address: str, port: int) -> bool:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((address, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


@WINDOWS_ONLY
@pytest.mark.parametrize(
    ("family", "address", "check_holder_bind"),
    [
        (socket.AF_INET, "127.0.0.1", True),
        (socket.AF_INET6, "::1", True),
        (socket.AF_INET, "0.0.0.0", True),
        (socket.AF_INET6, "::", False),
    ],
    ids=["ipv4-loopback", "ipv6-loopback", "ipv4-wildcard", "ipv6-wildcard"],
)
def test_port_is_free_matches_real_bindability(
    family: int, address: str, check_holder_bind: bool
) -> None:
    """四种占用场景都要判为不可用；释放后与真实可绑性一致。"""
    try:
        holder, port = _hold_port(family, address)
    except OSError as exc:
        pytest.skip(f"{address} 在当前机器不可用：{exc}")
    try:
        if check_holder_bind:
            assert _can_bind(family, address, port) is False, "占用态应确实绑不上"
        assert port_is_free(port) is False, "探测必须与真实可绑性一致"
    finally:
        holder.close()
    assert port_is_free(port) is True
    assert _can_bind(socket.AF_INET, "127.0.0.1", port) is True


@WINDOWS_ONLY
def test_port_is_free_reports_free_port_as_bindable() -> None:
    """普通空闲端口：探测为 True，且真的能绑。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert port_is_free(port) is True
    assert _can_bind(socket.AF_INET, "127.0.0.1", port) is True


@WINDOWS_ONLY
def test_port_probe_does_not_treat_permission_failure_as_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保留端口/权限不足时 bind 失败，不能当成空闲让网关去撞。"""
    monkeypatch.setattr(probe_mod, "_bind_status", lambda *_a, **_k: "denied")
    assert port_is_free(65000) is False


def test_gateway_service_refuses_occupied_port_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """桌面壳/自启与命令行一致：端口被占时明确报错，不静默顺延换端口。"""
    from src.desktop import service as service_mod

    monkeypatch.setattr(service_mod, "port_is_free", lambda _port: False)
    monkeypatch.setattr(
        service_mod,
        "pick_port",
        lambda _port: pytest.fail("未授权的场景不允许静默换端口"),
    )
    monkeypatch.setattr(
        service_mod,
        "port_owner",
        lambda _port: {"pids": [4242], "description": "PID 4242，python.exe，C:/Python/python.exe"},
    )
    svc = GatewayService(port=18099, root=tmp_path)

    assert svc.start() is False
    assert svc.port == 18099
    assert svc.last_error is not None
    assert "网关端口 18099 已被占用" in svc.last_error
    assert "PID 4242，python.exe，C:/Python/python.exe" in svc.last_error
    assert "不会自动换端口" in svc.last_error


def test_gateway_service_port_conflict_is_structured_for_the_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """占用者信息要能直接交给窗口：端口 + PID + 描述，而不是让 UI 再解析句子。"""
    from src.desktop import service as service_mod

    monkeypatch.setattr(service_mod, "port_is_free", lambda _port: False)
    monkeypatch.setattr(
        service_mod,
        "port_owner",
        lambda port: {"pids": [77], "description": f"PID 77，占用端口 {port} 的程序"},
    )
    svc = GatewayService(port=18099, root=tmp_path)

    assert svc.start() is False
    assert svc.port_conflict == {
        "port": 18099,
        "pids": [77],
        "description": "PID 77，占用端口 18099 的程序",
    }


class _FakeWidget:
    def __init__(self) -> None:
        self.text = ""

    def config(self, *, text: str) -> None:
        self.text = text


class _FakeRoot:
    def __init__(self, state: str = "normal") -> None:
        self._state = state
        self.shown = 0

    def state(self) -> str:
        return self._state

    def deiconify(self) -> None:  # pragma: no cover - 只用于记录被调用
        self._state = "normal"


def _bare_desktop_app(port: int = 18099) -> app_mod.DesktopApp:
    """只装配 ``_prompt_port_conflict`` 需要的字段（不创建真实窗口/网关）。"""
    app = object.__new__(app_mod.DesktopApp)
    app._closing = False
    app._root = _FakeRoot()
    app._labels = {"next": _FakeWidget(), "console": _FakeWidget(), "service": _FakeWidget()}
    app._port_dialog_open = False
    app._port_conflict_pending = False
    app._boot_pending = False
    app._stopping = False
    app._boot_result = None
    app._stop_result = None
    app._data_dir_writable = None
    app._requested_port = port
    app.service = SimpleNamespace(
        port=port,
        is_running=lambda: False,
        base_url=f"http://127.0.0.1:{port}",
    )
    app._port_events: list[tuple[str, str]] = []  # type: ignore[attr-defined]
    app._begin_boot = lambda *, note: app._port_events.append(("boot", note))  # type: ignore[method-assign]
    app._log_port_event = lambda message: app._port_events.append(("log", message))  # type: ignore[method-assign]
    app._show_window = lambda: None  # type: ignore[method-assign]
    return app


def test_tray_restart_service_stops_then_starts_without_double_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """托盘「重新启动服务」：网关活着时先停后起，不会并发起两份启动。"""
    app = _bare_desktop_app(18099)
    app.service.is_running = lambda: True
    calls: list[dict[str, str]] = []

    def fake_stop_service(**kwargs: str) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(app, "_stop_service", fake_stop_service)

    app._restart_service()

    # 停止只发起一次；且归因来源必须是 restart，托盘「重新启动」与「停止服务」
    # 在日志里要能区分（_stop_service 的 source 是必传关键字，测试桩不能吞掉）。
    assert calls == [{"source": "restart"}]
    assert app._restart_after_stop is True


def test_browser_mode_port_conflict_offers_switch_without_any_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P0-2：浏览器模式没有状态窗口时，冲突也必须给出可操作出路。"""
    app = _bare_desktop_app(18099)
    app._root = None
    monkeypatch.setenv("LIUHAO_DESKTOP_PORT_FILE", str(tmp_path / "port.json"))
    monkeypatch.setattr(app_mod, "ask_port_choice", lambda *a, **k: ("use", 18100))

    class _Holder:
        def withdraw(self) -> None:
            pass

        def attributes(self, *args: object) -> None:
            pass

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(app_mod.tk, "Tk", lambda: _Holder())

    app._prompt_port_conflict({"port": 18099, "description": "PID 1 python.exe"})

    assert app.service.port == 18100
    assert any("evt=port.switch" in message for _, message in app._port_events)


def test_port_conflict_switch_restarts_in_place_with_new_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """选「换一个端口继续」：改内存端口 + 环境变量 + 持久化并原地重启。"""
    app = _bare_desktop_app(18099)
    monkeypatch.setattr(app_mod, "ask_port_choice", lambda *a, **k: ("use", 18100))
    monkeypatch.setenv("LIUHAO_API_PORT", "18099")
    port_file = tmp_path / "desktop_port.json"
    monkeypatch.setenv("LIUHAO_DESKTOP_PORT_FILE", str(port_file))

    app._prompt_port_conflict({"port": 18099, "description": "PID 1 python.exe"})

    assert app.service.port == 18100
    assert app._requested_port == 18100
    assert os.environ["LIUHAO_API_PORT"] == "18100"
    # P0-3：用户选的端口必须落盘，否则第二次启动又回落到 8080/原端口。
    assert json.loads(port_file.read_text(encoding="utf-8"))["port"] == 18100
    assert any(kind == "boot" and "18100" in text for kind, text in app._port_events)
    assert any(
        kind == "log" and "evt=port.switch old=18099 new=18100" in text
        for kind, text in app._port_events
    )
    assert "18100" in app._labels["console"].text


def test_port_conflict_retry_rechecks_same_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """选「关掉占用程序再试」：只重新检测原端口，不改端口、不关窗口。"""
    app = _bare_desktop_app(18099)
    monkeypatch.setattr(app_mod, "ask_port_choice", lambda *a, **k: ("retry", None))

    app._prompt_port_conflict({"port": 18099, "description": "PID 1 python.exe"})

    assert app.service.port == 18099
    assert app._requested_port == 18099
    boot = [text for kind, text in app._port_events if kind == "boot"]
    assert boot
    assert "重新检测端口 18099" in boot[0]
    assert any("evt=port.retry port=18099" in text for kind, text in app._port_events)


def test_port_conflict_dialog_is_idempotent_and_deferrable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复的启动事件不能叠出两个窗口；关窗等同稍后处理，可再触发。"""
    app = _bare_desktop_app(18099)
    calls: list[str] = []
    monkeypatch.setattr(
        app_mod,
        "ask_port_choice",
        lambda *a, **k: calls.append("ask") or ("cancel", None),
    )

    app._port_dialog_open = True
    app._prompt_port_conflict({"port": 18099, "description": "PID 1 python.exe"})
    assert calls == []  # 已开着窗口：不重复弹

    app._port_dialog_open = False
    app._prompt_port_conflict({"port": 18099, "description": "PID 1 python.exe"})
    assert calls == ["ask"]
    assert app.service.port == 18099
    assert app._port_events[-1][0] == "log"
    assert "evt=port.deferred" in app._port_events[-1][1]
    assert "仍被占用" in app._labels["next"].text


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "真实 Tk 窗口的控件/变量回收依赖 Windows tcl86t 运行时：POSIX 的 Tcl 在"
        " destroy() 后仍有 C 层引用（Tcl after 队列）挂在 Tk 对象上，Python 侧"
        " <no referrers> 且引用计数不归零，Tk 生命周期护栏按设计 fail-closed 拒绝"
        "（防 Tcl_AsyncDelete 跨线程 panic 带走整个 pytest 进程）"
    ),
)
def test_port_choice_dialog_has_exactly_two_clickable_actions() -> None:
    """真实窗口里只有两个按钮，且都能点（不出现第三个选项）。"""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001 - 无图形桌面
        pytest.skip("当前环境没有可用的图形桌面")
    root.withdraw()
    seen: list[str] = []

    def click_retry() -> None:
        retry_button = None

        def walk(widget) -> None:
            nonlocal retry_button
            for child in widget.winfo_children():
                if isinstance(child, tk.Button):
                    seen.append(str(child.cget("text")))
                    if child.cget("text") == app_mod.PORT_RETRY_LABEL:
                        retry_button = child
                walk(child)

        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel):
                walk(child)
        if retry_button is not None:  # 先收齐按钮文本再点，避免销毁窗口打断遍历
            retry_button.invoke()

    root.after(250, click_retry)
    try:
        action, port = app_mod.ask_port_choice(
            root, {"port": 18099, "description": "PID 1 python.exe"}
        )
    finally:
        # 清理顺序是有意的：先在 Tcl 解释器仍存活时回收控件/变量，
        # 再销毁解释器，最后释放 Tkapp。反过来做会让控件/变量在已删除的
        # 解释器上 dealloc —— 实测在 tcl86t.dll 里触发 native 访问违例（page error）。
        gc.collect()
        root.destroy()
        del root
        gc.collect()

    assert action == "retry"
    assert port is None
    assert set(seen) == {app_mod.PORT_RETRY_LABEL, app_mod.PORT_SWITCH_LABEL}


def test_gateway_service_uses_wizard_port_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """向导与托盘壳同进程：向导选过的端口不能被默默退回 8080。"""
    from src.desktop import service as service_mod

    monkeypatch.setenv("LIUHAO_API_PORT", "18098")
    monkeypatch.setattr(service_mod, "port_is_free", lambda _port: False)
    svc = GatewayService(root=tmp_path)

    assert svc.start() is False
    assert "18098" in (svc.last_error or "")


@pytest.mark.parametrize("port", [0, 70000, -1], ids=["zero", "too-large", "negative"])
def test_gateway_service_rejects_invalid_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    """非法端口明确报错，不再静默退回 8080，也不能让 bind 抛 OverflowError。

    端口 0 的语义是"让系统随机分配"，但桌面壳的书签 / 托盘 / 局域网入口都
    依赖地址不变，随机端口无法回填给 UI —— 与其静默起在 8080 让人以为设置
    生效了，不如直接告诉用户该填什么。
    """
    from src.desktop import service as service_mod

    monkeypatch.setattr(
        service_mod, "port_is_free", lambda _port: pytest.fail("非法端口不应进入绑定探测")
    )
    svc = GatewayService(port=port, root=tmp_path)

    assert svc.start() is False
    assert svc.port == port
    assert "1-65535" in (svc.last_error or "")
    if port == 0:
        assert "不支持 0" in (svc.last_error or "")
        assert "8080" not in (svc.last_error or "")


@WINDOWS_ONLY
def test_bind_status_classifies_reserved_range_as_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hyper-V / WinNAT 保留区间 bind 报 WSAEACCES(10013)：判 denied，绝不能当空闲。

    这类端口既没被进程监听、也不是管理员可解的"占用"，但对当前用户就是不
    可绑定；当成空闲会让网关白启动一次再失败。
    """

    class _DeniedSocket:
        def settimeout(self, *_args) -> None:
            pass

        def setsockopt(self, *_args) -> None:
            pass

        def bind(self, _address) -> None:
            exc = PermissionError(13, "denied")
            exc.winerror = 10013
            raise exc

        def close(self) -> None:
            pass

    monkeypatch.setattr(probe_mod.socket, "socket", lambda *_a, **_k: _DeniedSocket())

    assert probe_mod._bind_status(("", 61000), socket.AF_INET) == "denied"
    assert port_is_free(61000) is False


@WINDOWS_ONLY
def test_pick_port_skips_reserved_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动恢复模式必须跳过保留/排除区间，不能在 denied 端口上反复撞。"""
    reserved = {18000, 18001, 18002}

    def fake_bind(address, _family, _v6only=None):
        return "denied" if address[1] in reserved else "free"

    monkeypatch.setattr(probe_mod, "_bind_status", fake_bind)

    assert port_is_free(18000) is False
    assert pick_port(18000, count=5) == 18003


@pytest.mark.skipif(os.name == "nt", reason="Windows 不要求管理员即可绑定 <1024")
def test_port_is_free_rejects_privileged_port_for_non_root() -> None:
    """POSIX 上非 root 绑不了 <1024：必须报不可用，而不是'空闲'。"""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root 可以绑定 <1024，无法验证权限拒绝")
    assert port_is_free(80) is False


@WINDOWS_ONLY
def test_port_is_free_detects_wildcard_listener() -> None:
    """别人占着 0.0.0.0:<port> 时也必须判为"被占用"。

    Windows 允许更具体的 127.0.0.1:<port> 与通配并存，只绑 loopback 探测会
    误报空闲，桌面壳随后会白启动一次网关。
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert port_is_free(port) is False
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
def test_autostart_enable_writes_command_without_open_browser() -> None:
    """新写出的自启命令只含必要参数：不得再写 ``--open-browser``。"""
    value_name = f"LiuHaoAIOS_pytest_{os.getpid()}"
    assert autostart.read(value_name)["enabled"] is False
    try:
        enabled = autostart.enable(REPO_ROOT, value_name=value_name)
        assert enabled["enabled"] is True
        assert "--autostart" in enabled["command"]
        assert "--open-browser" not in enabled["command"]
        assert "liuhao_desktop.py" in enabled["command"]
        assert autostart.read(value_name)["open_browser"] is False
    finally:
        assert autostart.disable(value_name)["enabled"] is False
        assert autostart.read(value_name)["enabled"] is False


@WINDOWS_ONLY
def test_autostart_legacy_value_with_open_browser_is_recognized_and_removed(
    tmp_path: Path,
) -> None:
    """机器上已有的旧注册值（含 ``--open-browser``）仍能识别，卸载时带/不带参数都能清理。"""
    import winreg

    legacy = f"LiuHaoAIOS_pytest_legacy_{os.getpid()}"
    plain = f"{legacy}_plain"
    control = f"OtherApp_pytest_{os.getpid()}"
    root = tmp_path / "LiuHaoLegacy"
    entry = root / "scripts" / "liuhao_desktop.py"
    other_root = tmp_path / "OtherApp"
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(
            key, legacy, 0, winreg.REG_SZ,
            f'"pythonw.exe" "{entry}" --autostart --open-browser',
        )
        winreg.SetValueEx(
            key, plain, 0, winreg.REG_SZ,
            f'"pythonw.exe" "{entry}" --autostart',
        )
        winreg.SetValueEx(
            key, control, 0, winreg.REG_SZ,
            f'"other.exe" "{other_root / "other.exe"}" /background',
        )
    try:
        old = autostart.read(legacy)
        assert old["enabled"] is True
        assert old["open_browser"] is True
        current = autostart.read(plain)
        assert current["enabled"] is True
        assert current["open_browser"] is False

        # 按安装根清理：两种写法都指向该根，必须一起删掉。
        result = autostart.disable(legacy, root=root)
        assert result["enabled"] is False
        assert legacy in result["removed_values"]
        assert plain in result["removed_values"]
        assert autostart.read(legacy)["enabled"] is False
        assert autostart.read(plain)["enabled"] is False
        # 指向别处的其他程序项一根不动。
        assert autostart.read(control)["enabled"] is True
    finally:
        autostart.disable(legacy, root=root)
        autostart.disable(plain)
        autostart.disable(control)


def test_autostart_opens_native_window_not_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """自启入口恒为原生窗口：新命令与旧命令（含 --open-browser）都不切浏览器。"""
    from src.desktop import __main__ as desktop_main

    captured: dict[str, object] = {}

    class _FakeApp:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> int:
            return 0

    monkeypatch.setattr(app_mod, "DesktopApp", _FakeApp)

    assert desktop_main.main(["app", "--autostart"]) == 0
    assert captured["window_mode"] == "native"
    assert captured["open_browser"] is False
    assert captured["hidden"] is True

    captured.clear()
    assert desktop_main.main(["app", "--autostart", "--open-browser"]) == 0
    assert captured["window_mode"] == "native"
    assert captured["open_browser"] is False
    assert captured["hidden"] is True


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
    assert "--open-browser" not in command
    assert "--autostart" not in command


def test_autostart_command_refuses_when_entry_missing(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "runtime").mkdir(parents=True)
    (app / "runtime" / "pythonw.exe").touch()
    with pytest.raises(RuntimeError):
        autostart.build_command(app)


#: 随包交付的成品 ICO：应用图标 + 托盘五态。换图只替换文件，帧结构由本测试锁住。
SHIPPED_ICON_ASSETS = (
    REPO_ROOT / "packaging" / "assets" / "liuhao-app.ico",
    *sorted((REPO_ROOT / "src" / "desktop" / "assets" / "tray").glob("*.ico")),
)
#: Windows 按 DPI 从 ICO 里挑一帧：缺 16/24/32 会糊，缺 256 大图标发虚。
REQUIRED_FRAME_SIZES = (16, 20, 24, 32, 48, 64, 256)


def test_shipped_icon_assets_are_seven_frame_ico() -> None:
    """资产线交付的多帧 ICO 必须保持 7 档（16/20/24/32/48/64/256）。

    守卫的是成品文件本身，不是 :func:`tray.write_state_icon` 的占位圆点：
    占位图只在资产缺失时兜底，用户日常看到的是这里的多帧成品。
    """
    assert len(SHIPPED_ICON_ASSETS) == 6, [p.name for p in SHIPPED_ICON_ASSETS]
    for path in SHIPPED_ICON_ASSETS:
        data = path.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        assert (reserved, kind) == (0, 1), path.name
        assert count == len(REQUIRED_FRAME_SIZES), (path.name, count)
        sizes = []
        for index in range(count):
            base = 6 + 16 * index
            width = data[base] or 256
            height = data[base + 1] or 256
            assert width == height, (path.name, width, height)
            sizes.append(width)
        assert tuple(sizes) == REQUIRED_FRAME_SIZES, (path.name, sizes)


def test_state_icon_files_are_valid_ico(tmp_path: Path) -> None:
    for state, rgb in tray.STATE_COLORS.items():
        path = tray.write_state_icon(tmp_path / f"{state}.ico", rgb)
        data = path.read_bytes()
        count = struct.unpack("<H", data[4:6])[0]
        assert data[:4] == struct.pack("<HH", 0, 1), state
        # 多尺寸是硬要求：只给 16×16 时高缩放会被系统放大成模糊色块。
        assert count == len(tray._ICON_SIZES), state
        expected_offset = 6 + 16 * count
        for index, size in enumerate(tray._ICON_SIZES):
            base = 6 + 16 * index
            width = data[base] or 256
            height = data[base + 1] or 256
            assert (width, height) == (size, size), (state, size)
            bytes_in_res, offset = struct.unpack("<II", data[base + 8 : base + 16])
            mask_row = ((size + 31) // 32) * 4
            assert bytes_in_res == 40 + size * size * 4 + mask_row * size, (state, size)
            assert offset == expected_offset, (state, size)
            assert data[offset : offset + 4] == struct.pack("<I", 40), (state, size)
            expected_offset += bytes_in_res
        assert len(data) == expected_offset, state


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


def test_gateway_service_sweeps_log_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """桌面壳的巡检入口：只删过期备份，活动日志保留并写报告。"""
    import src.desktop.service as service

    monkeypatch.setattr(service, "LOG_DIR", tmp_path)
    active = tmp_path / "desktop-gateway.log"
    active.write_text("current\n", encoding="utf-8")
    stale = tmp_path / "desktop-gateway.log.1"
    stale.write_text("stale\n", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(stale, (old, old))
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_DAYS", "14")

    reports = GatewayService().sweep_log_retention()

    assert active.exists()
    assert not stale.exists()
    desktop_report = next(item for item in reports if item["deleted"])
    assert desktop_report["deleted"][0]["name"] == "desktop-gateway.log.1"
    assert (tmp_path / "retention.json").is_file()


def test_bundle_reports_log_retention_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """诊断包必须能看到保留了什么、删了什么与原因。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    logs, _data_dir = _bundle_logs_and_data(tmp_path, monkeypatch)
    active = logs / "desktop-gateway.log"
    active.write_text("current\n", encoding="utf-8")
    stale = logs / "desktop-gateway.log.1"
    stale.write_text("stale\n", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(stale, (old, old))
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_DAYS", "14")

    bundle = create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=logs,
        ports=[65530],
        probe_network=False,
    )
    report = _bundle_payload(bundle, "log_retention.json")

    assert report["status"] == "ok"
    assert report["retention_days"] == 14
    assert report["deleted"][0]["name"] == "desktop-gateway.log.1"
    assert {item["name"] for item in report["kept"]} == {"desktop-gateway.log"}
    assert not stale.exists()


def test_stop_process_drains_capture_before_reading_log(tmp_path: Path) -> None:
    """后台泵线程里还没落盘的末行，必须参与 shutdown 判定。"""
    log = tmp_path / "desktop-gateway.log"
    log.write_text("INFO:     Started server process\n", encoding="utf-8")
    offset = log.stat().st_size

    def drain() -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"INFO:     {SHUTDOWN_LOG_MARKER}.\n")

    proc = _FakeProc()
    result = stop_process(proc, timeout=1.0, log_path=log, log_offset=offset, drain=drain)

    assert result["stopped"] is True
    assert result["shutdown_confirmed"] is True
    assert result["graceful"] is True


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


def test_tray_retries_icon_add_until_taskbar_ready() -> None:
    """任务栏还没准备好时首次 NIM_ADD 失败：必须退避重试，成功后置 ready。

    开机自启时本进程常比任务栏先起来；一次失败就放弃会让用户永远看不到图标。
    """

    class _Gui:
        NIM_ADD = 0
        NIM_MODIFY = 1
        NIM_DELETE = 2

        def __init__(self) -> None:
            self.delays: list[int] = []
            self.adds = 0

        def Shell_NotifyIcon(self, op: int, _nid) -> bool:
            if op == self.NIM_DELETE:
                return False  # Explorer 重启后 DELETE 本来就失败，必须无害
            self.adds += 1
            return self.adds > 1  # 第一次失败，第二次成功

        def SetTimer(self, _hwnd, _timer_id, delay, _extra) -> None:
            self.delays.append(delay)

    gui = _Gui()
    widget = tray.SystemTray(lambda _action: None)
    widget._w32 = (None, None, gui)  # type: ignore[assignment]
    widget._nid = (1, 1, 0, 0, 0, "tip")
    widget._hwnd = 1
    widget._running = True
    widget._ready_deadline = time.monotonic() + 60

    assert widget._add_icon() is False
    assert widget.icon_added is False
    assert widget.icon_adds == 0

    widget._schedule_icon_retry()
    widget._schedule_icon_retry()
    assert gui.delays == [1000, 2000]  # 退避翻倍
    assert widget._ready.is_set() is False

    widget._retry_icon()
    assert widget.icon_added is True
    assert widget.icon_adds == 1
    assert widget._ready.is_set() is True
    assert widget._error is None


@WINDOWS_ONLY
def test_tray_recovers_icon_after_taskbar_created() -> None:
    """广播 TaskbarCreated（等价 Explorer 重启）后图标重建，双击 / 菜单仍可用。"""
    win32gui = pytest.importorskip("win32gui")
    win32con = pytest.importorskip("win32con")

    received: list[str] = []
    widget = tray.SystemTray(received.append, tooltip="恢复测试")
    if not widget.start(timeout=5.0):
        reason = widget.error or "当前会话没有可用的任务栏通知区"
        widget.stop()
        pytest.skip(f"托盘不可用：{reason}")

    try:
        assert widget.icon_present() is True
        before = (widget.icon_adds, widget.taskbar_recoveries)

        assert tray.broadcast_taskbar_created() is True
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and widget.taskbar_recoveries == before[1]:
            time.sleep(0.05)

        assert widget.taskbar_recoveries == before[1] + 1
        assert widget.icon_adds == before[0] + 1
        assert widget.icon_present() is True
        assert len(widget.menu_items) == len(tray.MENU_ITEMS)
        assert any(item[1] == tray.ACTION_RESTART_SERVICE for item in widget.menu_items)

        win32gui.PostMessage(widget.hwnd, widget._message_id, 0, win32con.WM_LBUTTONDBLCLK)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and tray.ACTION_OPEN_CONSOLE not in received:
            time.sleep(0.05)
        assert tray.ACTION_OPEN_CONSOLE in received

        win32gui.PostMessage(widget.hwnd, win32con.WM_COMMAND, 1002, 0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and tray.ACTION_SHOW_STATUS not in received:
            time.sleep(0.05)
        assert tray.ACTION_SHOW_STATUS in received
    finally:
        widget.stop()
    assert widget.icon_added is False


@WINDOWS_ONLY
def test_port_zero_gui_shows_chinese_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--port 0 的 GUI 端到端：窗口里必须是中文原因，不是崩掉或静默退出。"""
    tk = pytest.importorskip("tkinter")
    from src.desktop import app as app_module
    from src.desktop.service import GatewayService
    from src.desktop.startup import unavailable_gate

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("当前会话没有可用的图形界面")
    root.withdraw()

    app = app_module.DesktopApp(port=0)
    app._root = root
    # 隔离实例锁，避免和正在运行的托盘实例互斥；不碰仓库真实数据目录。
    app.service = GatewayService(port=0, root=tmp_path)
    monkeypatch.setattr(app, "_probe_data_dir_writable", lambda: True)
    try:
        app._build_ui()
        started = app.service.start()
        assert started is False
        gate = unavailable_gate(app.service.last_error, log_dir=app_module.LOG_DIR)
        app._apply_boot_result({"started": started, "gate": gate})
        startup_text = app._labels["startup"].cget("text")
        service_text = app._labels["service"].cget("text")
    finally:
        # 清理顺序是有意的：先在 Tcl 解释器仍存活时断开 app 对控件的引用并回收，
        # 再销毁解释器，最后释放 Tkapp。反过来做会让控件/变量在已删除的解释器上
        # dealloc —— 实测在 tcl86t.dll 里触发 native 访问违例（page error）。
        app._root = None
        app._labels.clear()
        app._scroll = None
        app._wrapped_labels.clear()
        app._diagnostics_button = None
        app._copy_path_button = None
        del app
        gc.collect()
        root.destroy()
        del root
        gc.collect()

    assert "端口 0 无效" in startup_text
    assert "系统随机分配" in startup_text
    assert "1-65535" in startup_text
    assert "。。" not in startup_text
    assert "Traceback" not in startup_text
    assert "启动失败" in service_text
    assert "端口 0" in service_text


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
    # 新契约：发布地址写在 ``instance_root(服务的 root)`` 下；实例根由
    # ``LIUHAO_INSTANCE_ROOT`` 决定，所以这里重定向实例根，而不是只改 DATA_DIR。
    monkeypatch.setenv("LIUHAO_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(app_module, "request_show", lambda *_a, **_k: False)
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    # 服务健康时才直接打开控制台；服务无响应时必须留在提示语，不能打开死页面。
    monkeypatch.setattr(app_module.DesktopApp, "_service_healthy", lambda *_a, **_k: True)

    app_module.DesktopApp(hidden=True)._handoff_to_running()
    # 第二实例不再自动打开浏览器：浏览器只由显式动作触发。
    assert opened == []


def test_open_logs_action_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """托盘菜单「打开日志目录」必须真的打开目录，而不是静默无反应。"""
    from src.desktop import app as app_module

    started: list[str] = []
    monkeypatch.setattr(app_module, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(app_module.os, "startfile", started.append, raising=False)
    monkeypatch.setattr(app_module.webbrowser, "open", started.append)

    app_module.DesktopApp()._handle_action(tray.ACTION_OPEN_LOGS)
    assert (tmp_path / "logs").is_dir()
    assert started
    assert str(tmp_path / "logs") in started[0]


def test_open_console_action_uses_service_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """托盘菜单「打开控制台」必须打开网关实际发布的地址，而不是固定猜端口。"""
    from src.desktop import app as app_module

    opened: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    desktop = app_module.DesktopApp(port=18123, window_mode="browser")
    desktop.service.port = 18123
    monkeypatch.setattr(desktop.service, "is_running", lambda: True)
    monkeypatch.setattr(desktop, "_service_healthy", lambda _url: True)

    desktop._handle_action(tray.ACTION_OPEN_CONSOLE)
    assert opened == ["http://127.0.0.1:18123/?mode=web"]


def test_open_is_blocked_when_port_is_owned_by_someone_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-1：端口上不是鎏灏时，任何打开动作都必须被拦下。"""
    from src.desktop import app as app_module

    opened: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    desktop = app_module.DesktopApp(port=18124, window_mode="browser")
    desktop.service.port = 18124
    desktop._labels = {"next": _FakeWidget()}
    monkeypatch.setattr(desktop.service, "is_running", lambda: True)
    monkeypatch.setattr(desktop, "_service_healthy", lambda _url: False)

    desktop._handle_action(tray.ACTION_OPEN_BROWSER)

    assert opened == []
    assert desktop._labels["next"].text.startswith("下一步：在浏览器中打开已取消")


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
