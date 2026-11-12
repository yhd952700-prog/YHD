"""构建环境闸与冒烟隔离的回归测试。

两条历史故障都在这里钉死：
* 构建机残留 ``LIUHAO_*`` / ``AUDIT_DB_*`` 指向源码仓库 → 构建必须中止；
* 冒烟脚本继承当前 shell 的环境 → 注册与启动命中不同库 → 登录 403。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _declared_temp_root() -> Path:
    """守卫扫描用例里「已声明的临时根」的平台样例。

    Windows 构建机用盘符路径；POSIX 用系统临时目录下的固定子目录。
    两者的共同语义：位于仓库之外、且被显式声明为临时根。
    """
    if os.name == "nt":
        return Path(r"D:\cache\temp")
    return Path(tempfile.gettempdir()) / "liuhao-env-guard-temp"


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
    temp_root = _declared_temp_root()
    findings = guard.scan(
        {"AUDIT_DB_PATH": str(temp_root / "somewhere" / "audit_store.db")},
        REPO,
        [temp_root],
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
    if os.name == "nt":
        data_dir_value = r"E:\Somewhere\LiuHaoData"
        path_env = r"C:\Windows"
    else:
        # POSIX 样例：仓库之外、系统临时目录之外的用户目录路径。
        data_dir_value = str(Path.home() / "Somewhere" / "LiuHaoData")
        path_env = "/usr/bin"
    findings = guard.scan(
        {"LIUHAO_DATA_DIR": data_dir_value, "PATH": path_env},
        REPO,
        [_declared_temp_root()],
    )
    assert findings == []


def test_url_value_is_not_treated_as_path():
    # LIUHAO_UPGRADE_MANIFEST_URL 是 https 地址，不是本地路径；曾因按路径解析
    # 相对当前目录，被误判成「指向源码仓库」而卡死出包。
    findings = guard.scan(
        {
            "LIUHAO_UPGRADE_MANIFEST_URL": (
                "https://yhd952700-prog.github.io/liuhao-release/updates/LIUHAO_RELEASE.json"
            )
        },
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
