"""大升级 U2 契约：默认更新源 + 聊天全链演练（模拟源）。

演练口径（全部本地假源、假应用，不打真网络）：
* 未配置任何源时，默认源指向项目 GitHub 发布清单；显式退出后无源；
* 聊天工具 check_update 能查到模拟源里的新版本（只查不装）；
* apply_update 无 confirm 必须拒绝；confirm=true 后走完
  检查→下载→哈希校验→备份→应用，程序换新、数据原样；
* 清单哈希对不上的假包，下载校验必须拦下，绝不进入应用。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.upgrade.dataversion import DATA_SCHEMA_VERSION
from src.upgrade.filemanifest import build_file_manifest_for_zip


def _setup_app(root: Path, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "gateway").mkdir(exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    return root


def _release(release_dir: Path, *, version: str = "0.2.0", bad_hash: bool = False) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/app.py": "VALUE = 2\n",
    }
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if bad_hash:
        sha = "0" * 64
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": sha,
        "size": zip_path.stat().st_size,
        "files": build_file_manifest_for_zip(zip_path),
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    manifest = {"schema": "liuhao.release-manifest/1", "releases": [release]}
    (release_dir / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


class TestDefaultSource:
    def test_default_manifest_url_when_unconfigured(self, monkeypatch):
        from src.upgrade import update_check

        for name in (
            "LIUHAO_UPGRADE_RELEASE_DIR",
            "LIUHAO_UPGRADE_MANIFEST_URL",
            "LIUHAO_UPGRADE_NO_DEFAULT_SOURCE",
        ):
            monkeypatch.delenv(name, raising=False)
        assert update_check.effective_manifest_url() == update_check.DEFAULT_MANIFEST_URL
        assert "github.com/yhd952700-prog/YHD" in update_check.DEFAULT_MANIFEST_URL
        assert update_check.source_configured() is True
        manager = update_check.build_manager_from_env()
        try:
            assert manager.config.source is not None
        finally:
            manager.close()

    def test_opt_out_removes_default_source(self, monkeypatch):
        from src.upgrade import update_check

        monkeypatch.delenv("LIUHAO_UPGRADE_RELEASE_DIR", raising=False)
        monkeypatch.delenv("LIUHAO_UPGRADE_MANIFEST_URL", raising=False)
        monkeypatch.setenv("LIUHAO_UPGRADE_NO_DEFAULT_SOURCE", "1")
        assert update_check.effective_manifest_url() is None
        assert update_check.source_configured() is False


@pytest.fixture()
def chat_env(tmp_path: Path, monkeypatch):
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir)
    monkeypatch.setenv("LIUHAO_APP_ROOT", str(app_root))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(app_root / "data"))
    monkeypatch.setenv("LIUHAO_UPGRADE_RELEASE_DIR", str(release_dir))
    monkeypatch.setenv("LIUHAO_UPGRADE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LIUHAO_UPGRADE_MANIFEST_URL", raising=False)
    return app_root


def _tools():
    from src.ai.tools.kernel_tools import make_tools

    return {t.name: t for t in make_tools("user-alice", include_disabled=True)}


class TestChatUpgradeChain:
    def test_check_then_confirmed_apply(self, chat_env: Path):
        tools = _tools()
        out = json.loads(tools["check_update"].fn())
        assert out["status"] == "ok"
        assert out["update_available"] is True
        assert out["current_version"] == "0.1.0"
        assert out["latest_version"] == "0.2.0"
        # 只查不装：程序仍是旧版
        from src.upgrade import read_current_version

        assert read_current_version(chat_env)[0] == "0.1.0"

        refused = json.loads(tools["apply_update"].fn())
        assert refused["status"] == "refused"
        refused2 = json.loads(tools["apply_update"].fn(confirm=False))
        assert refused2["status"] == "refused"

        applied = json.loads(tools["apply_update"].fn(confirm=True))
        assert applied["status"] == "ok", applied
        assert applied["from_version"] == "0.1.0"
        assert applied["to_version"] == "0.2.0"
        assert applied["backup_id"], "应用前必须先落升级前备份"
        assert read_current_version(chat_env)[0] == "0.2.0"
        assert (chat_env / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        conn = sqlite3.connect(chat_env / "data" / "app.db")
        try:
            rows = [r[0] for r in conn.execute("SELECT v FROM t").fetchall()]
        finally:
            conn.close()
        assert rows == ["keep"], "升级只换程序，数据目录必须原样"

    def test_bad_hash_package_is_rejected_at_download(self, tmp_path: Path, monkeypatch):
        app_root = _setup_app(tmp_path / "app")
        release_dir = tmp_path / "releases"
        _release(release_dir, bad_hash=True)
        monkeypatch.setenv("LIUHAO_APP_ROOT", str(app_root))
        monkeypatch.setenv("LIUHAO_DATA_DIR", str(app_root / "data"))
        monkeypatch.setenv("LIUHAO_UPGRADE_RELEASE_DIR", str(release_dir))
        monkeypatch.setenv("LIUHAO_UPGRADE_STATE_DIR", str(tmp_path / "state"))

        tools = _tools()
        applied = json.loads(tools["apply_update"].fn(confirm=True))
        assert applied["status"] == "failed", "哈希对不上的包必须被拦下"
        from src.upgrade import read_current_version

        assert read_current_version(app_root)[0] == "0.1.0", "被拦下后程序不许动"
