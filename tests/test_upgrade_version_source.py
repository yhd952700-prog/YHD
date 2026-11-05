"""Real checks for version discovery and update-source semantics.

Regression targets:
- an unreachable/offline source must never be reported as "已是最新";
- a missing update source must say "未配置" instead of pretending to check;
- a malformed manifest is a failure, not "no update";
- the real HTTP path (manifest + artifact relative URL) works.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.error
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.upgrade import (
    CheckStatus,
    HttpManifestSource,
    LocalDirectorySource,
    UpgradeConfig,
    UpgradeManager,
    read_current_version,
)


def _write_pyproject(root: Path, version: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    _write_pyproject(root, "0.1.0")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "data").mkdir()
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    (root / "data" / "state.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def _make_release(
    release_dir: Path,
    *,
    version: str,
    files: dict[str, str],
    signature: str | None = None,
    signature_algorithm: str | None = None,
    declared_sha: str | None = None,
) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    digest = declared_sha or hashlib.sha256(zip_path.read_bytes()).hexdigest()
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": digest,
        "size": zip_path.stat().st_size,
        "notes": f"release {version}",
    }
    if signature:
        release["signature"] = signature
        release["signature_algorithm"] = signature_algorithm or "ed25519"
    manifest = {"schema": "liuhao.release-manifest/1", "channel": "stable", "releases": [release]}
    (release_dir / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return release


def _manager(app_root: Path, source=None) -> UpgradeManager:
    return UpgradeManager(
        UpgradeConfig(app_root=app_root, source=source)
    )


def test_read_current_version_from_pyproject(app_root: Path) -> None:
    version, source = read_current_version(app_root)
    assert version == "0.1.0"
    assert source == "pyproject.toml"


def test_check_without_source_is_not_up_to_date(app_root: Path) -> None:
    manager = _manager(app_root, None)
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.NOT_CONFIGURED
    assert "无法检查更新" in result.message
    assert "已是最新" not in result.message


def test_check_reports_available_update(app_root: Path, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    _make_release(
        releases,
        version="0.2.0",
        files={
            "pyproject.toml": '[project]\nname = "liuhao-ai-os"\nversion = "0.2.0"\n',
            "src/app.py": "VALUE = 2\n",
        },
    )
    manager = _manager(app_root, LocalDirectorySource(releases))
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.UPDATE_AVAILABLE
    assert result.current_version == "0.1.0"
    assert result.latest_version == "0.2.0"
    assert result.release is not None and result.release.version == "0.2.0"


def test_check_reports_up_to_date_only_for_a_real_manifest(app_root: Path, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    _make_release(
        releases,
        version="0.1.0",
        files={"pyproject.toml": '[project]\nname = "liuhao-ai-os"\nversion = "0.1.0"\n'},
    )
    manager = _manager(app_root, LocalDirectorySource(releases))
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.UP_TO_DATE
    assert "已是最新版本 0.1.0" in result.message


def test_missing_release_dir_is_unreachable_not_up_to_date(app_root: Path, tmp_path: Path) -> None:
    source = LocalDirectorySource(tmp_path / "does-not-exist")
    manager = _manager(app_root, source)
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.UNREACHABLE
    assert "无法检查更新" in result.message
    assert "已是最新" not in result.message


def test_invalid_manifest_is_a_failure(app_root: Path, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    _make_release(
        releases,
        version="0.2.0",
        files={"pyproject.toml": '[project]\nversion = "0.2.0"\n'},
        declared_sha="not-a-sha",
    )
    manager = _manager(app_root, LocalDirectorySource(releases))
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.INVALID_MANIFEST
    assert "无法检查更新" in result.message


def test_offline_http_source_says_cannot_check(
    app_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _offline(*_args, **_kwargs):
        raise urllib.error.URLError(OSError("network is offline"))

    monkeypatch.setattr("src.upgrade.sources.urllib.request.urlopen", _offline)
    source = HttpManifestSource("http://127.0.0.1:9/release-manifest.json", timeout=0.5)
    manager = _manager(app_root, source)
    try:
        result = manager.check()
    finally:
        manager.close()
    assert result.status is CheckStatus.UNREACHABLE
    assert result.offline is True
    assert result.message.startswith("无法检查更新")
    assert "已是最新" not in result.message


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs) -> None:  # noqa: D401 - quiet server
        return


@pytest.fixture
def http_release_server(tmp_path: Path):
    releases = tmp_path / "releases"
    _make_release(
        releases,
        version="0.2.0",
        files={
            "pyproject.toml": '[project]\nname = "liuhao-ai-os"\nversion = "0.2.0"\n',
            "src/app.py": "VALUE = 2\n",
        },
    )
    handler = partial(_QuietHandler, directory=str(releases))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/release-manifest.json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_source_real_manifest_and_download(app_root: Path, http_release_server: str) -> None:
    source = HttpManifestSource(http_release_server, timeout=5.0)
    manager = _manager(app_root, source)
    try:
        result = manager.check()
        assert result.status is CheckStatus.UPDATE_AVAILABLE
        assert result.release is not None
        download = manager.download(result.release)
        assert Path(download.path).is_file()
        assert download.sha256 == result.release.sha256
        assert download.signature_note == "仅哈希校验，无签名"
    finally:
        manager.close()
