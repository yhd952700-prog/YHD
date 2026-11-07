"""构建环境闸与冒烟隔离的回归测试。

两条历史故障都在这里钉死：
* 构建机残留 ``LIUHAO_*`` / ``AUDIT_DB_*`` 指向源码仓库 → 构建必须中止；
* 冒烟脚本继承当前 shell 的环境 → 注册与启动命中不同库 → 登录 403。
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("env_guard_under_test", "packaging/env_guard.py")
smoke = _load("smoke_payload_under_test", "packaging/smoke_payload.py")


def test_repo_path_in_process_env_is_flagged():
    findings = guard.scan(
        {"LIUHAO_HUMAN_IDENTITIES_DB": str(REPO / "data" / "human_identities.sqlite3")},
        REPO,
        [Path(r"D:\cache\temp")],
    )
    assert len(findings) == 1
    assert findings[0]["name"] == "LIUHAO_HUMAN_IDENTITIES_DB"
    assert "源码仓库" in findings[0]["reason"]


def test_temp_dir_path_is_flagged():
    findings = guard.scan(
        {"AUDIT_DB_PATH": r"D:\cache\temp\somewhere\audit_store.db"},
        REPO,
        [Path(r"D:\cache\temp")],
    )
    assert [item["name"] for item in findings] == ["AUDIT_DB_PATH"]
    assert "临时目录" in findings[0]["reason"]


def test_non_path_value_is_ignored():
    findings = guard.scan(
        {"LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "local-test-integrity-key-2026"},
        REPO,
        [Path(r"D:\cache\temp")],
    )
    assert findings == []


def test_clean_env_has_no_findings():
    findings = guard.scan(
        {"LIUHAO_DATA_DIR": r"E:\Somewhere\LiuHaoData", "PATH": r"C:\Windows"},
        REPO,
        [Path(r"D:\cache\temp")],
    )
    assert findings == []


def test_cli_exits_nonzero_when_env_is_dirty():
    env = dict(os.environ)
    env["LIUHAO_HUMAN_IDENTITIES_DB"] = str(REPO / "data" / "human_identities.sqlite3")
    result = subprocess.run(
        [sys.executable, str(REPO / "packaging" / "env_guard.py"), "--repo", str(REPO)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "LIUHAO_HUMAN_IDENTITIES_DB" in result.stdout
    assert "构建/冒烟必须中止" in result.stdout


def test_smoke_child_env_strips_inherited_variables(monkeypatch):
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_DB", str(REPO / "data" / "human_identities.sqlite3")
    )
    monkeypatch.setenv("AUDIT_DB_PATH", str(REPO / "data" / "audit_store.db"))
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(REPO / "config" / "auth_secrets.json"))
    child = smoke._isolated_env(Path(r"D:\cache\temp\smoke-data"))
    assert "LIUHAO_HUMAN_IDENTITIES_DB" not in child
    assert "AUDIT_DB_PATH" not in child
    assert "LIUHAO_AUTH_SECRETS_FILE" not in child
    assert child["LIUHAO_DATA_DIR"] == r"D:\cache\temp\smoke-data"


def test_smoke_register_check_reads_data_dir(tmp_path):
    good = tmp_path / "config" / "human_identities.json"
    good.parent.mkdir(parents=True)
    good.write_text(
        '{"humans": [{"principal": "smoke"}]}', encoding="utf-8"
    )
    assert smoke._registered_in_data_dir(tmp_path) is True

    good.write_text('{"humans": [{"principal": "someone-else"}]}', encoding="utf-8")
    assert smoke._registered_in_data_dir(tmp_path) is False
