"""身份库默认落点必须与安装版数据目录一致（P1-10）。

真实缺陷：SQLite 后端未指定 ``LIUHAO_HUMAN_IDENTITIES_DB`` 时，默认路径写死在
仓库里（``Path(__file__).parents[3] / "data" / "human_identities.sqlite3``），
实测身份库落在 ``D:\\LiuHao-AI-OS\\data\\human_identities.sqlite3``，与
``scripts/launch_installed.py`` 的数据目录约定（
``LIUHAO_DATA_DIR`` / ``%LOCALAPPDATA%\\LiuHao\\data``）不一致；安装版卸载或
升级会把仓库目录一起清掉/覆盖，身份数据随之丢失。
"""

from __future__ import annotations

import os
from pathlib import Path

from src.kernels.identity._persistence import (
    default_data_dir,
    default_sqlite_path,
    resolve_human_identity_store,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_IDENTITY_DB = REPO_ROOT / "data" / "human_identities.sqlite3"


def _clear_location_env(monkeypatch) -> None:
    for name in (
        "LIUHAO_DATA_DIR",
        "LIUHAO_HUMAN_IDENTITIES_DB",
        "LIUHAO_HUMAN_IDENTITIES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_matches_installed_data_dir(monkeypatch, tmp_path):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "sqlite")

    expected = (
        tmp_path / "localappdata" / "LiuHao" / "data" / "config"
        / "human_identities.sqlite3"
    )
    assert default_data_dir() == expected.parents[1]
    assert default_sqlite_path() == expected
    assert REPO_ROOT not in expected.parents, "default leaked into the repo"
    assert not expected.exists()


def test_luhao_data_dir_env_overrides_default(monkeypatch, tmp_path):
    _clear_location_env(monkeypatch)
    data_dir = tmp_path / "soak-data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    assert default_sqlite_path() == data_dir / "config" / "human_identities.sqlite3"


def test_explicit_db_env_still_wins(monkeypatch, tmp_path):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    explicit = tmp_path / "elsewhere" / "identities.sqlite3"
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "sqlite")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_DB", str(explicit))

    store = resolve_human_identity_store()
    assert Path(store.location) == explicit
    assert default_sqlite_path() != explicit


def test_writes_go_to_data_dir_and_never_to_repo(monkeypatch, tmp_path):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "sqlite")

    before = (
        (REPO_IDENTITY_DB.stat().st_mtime_ns, REPO_IDENTITY_DB.stat().st_size)
        if REPO_IDENTITY_DB.exists()
        else None
    )

    store = resolve_human_identity_store()
    assert Path(store.location) == default_sqlite_path()
    assert store.upsert(
        {"principal": "alice", "display_name": "Alice", "permissions": [], "scope": "L0"}
    )

    created = default_sqlite_path()
    assert created.is_file()
    assert REPO_ROOT not in created.parents

    after = (
        (REPO_IDENTITY_DB.stat().st_mtime_ns, REPO_IDENTITY_DB.stat().st_size)
        if REPO_IDENTITY_DB.exists()
        else None
    )
    if before is None:
        assert not REPO_IDENTITY_DB.exists(), "identity db was written into the repo"
    else:
        assert before == after, "repo identity db was modified"


def test_installed_layout_directory_is_created_on_first_write(monkeypatch, tmp_path):
    """``<data>/config`` 在新装机器上还不存在，首次写入必须自己建目录。"""
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "fresh"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "sqlite")

    store = resolve_human_identity_store()
    config_dir = default_sqlite_path().parent
    assert not config_dir.exists()
    assert store.upsert({"principal": "bob", "permissions": [], "scope": "L0"}), (
        os.environ.get("LOCALAPPDATA")
    )
    assert config_dir.is_dir()
    assert default_sqlite_path().is_file()
