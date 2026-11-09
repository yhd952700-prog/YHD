"""Signed-manifest bootstrap: automatic upgrade without re-distributing hashes.

The old remote policy required ``manifest_sha256`` (a per-release value) to be
configured on every client. That made "automatic" upgrade impossible: someone
had to push a new hash before every release. These tests prove the new contract:

* only the long-lived Ed25519 public key is configured;
* a signed manifest (root ``manifest_signature`` + monotonic ``sequence``) is
  accepted with no pinned hash and drives check -> download -> verify;
* unsigned / tampered / replayed / downgraded manifests are rejected;
* replaying an older signed artifact is rejected at download time.

Real keys and a real HTTP server are used; only the https/loopback URL policy
is bypassed for the local server (that policy is covered separately).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.upgrade import CheckStatus, HttpManifestSource, UpgradeConfig, UpgradeManager
from src.upgrade.errors import SignatureError
from src.upgrade.sources import parse_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "scripts" / "ops" / "sign_release_manifest.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("liuhao_sign_release_manifest", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs) -> None:
        return


@pytest.fixture
def release_server(tmp_path: Path):
    releases = tmp_path / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_QuietHandler, directory=str(releases))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield releases, f"http://127.0.0.1:{server.server_port}/LIUHAO_RELEASE.json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_app_root(tmp_path: Path, version: str = "0.1.1") -> Path:
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "data").mkdir(exist_ok=True)
    return root


def _make_release(release_dir: Path, version: str, *, notes: str = "") -> tuple[Path, Path]:
    artifact = release_dir / f"liuhao-{version}.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "pyproject.toml",
            f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        )
        archive.writestr("src/app.py", f"VERSION = {version!r}\n")
    manifest_path = release_dir / "LIUHAO_RELEASE.json"
    manifest = {
        "schema": "liuhao.release-manifest/1",
        "channel": "stable",
        "releases": [
            {
                "version": version,
                "artifact": artifact.name,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "size": artifact.stat().st_size,
                "notes": notes or f"release {version}",
            }
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path, artifact


def _sign(tool, manifest_path: Path, key_path: Path, sequence: int) -> None:
    tool.sign_manifest(manifest_path, key_path, sequence=sequence)


def _manager(
    app_root: Path, url: str, public_key: str, monkeypatch: pytest.MonkeyPatch
) -> UpgradeManager:
    # 本机 HTTP 服务只用于跑真实链路；https/私网策略由安全用例单独覆盖。
    monkeypatch.setattr(
        "src.upgrade.sources.validate_remote_url", lambda value, **_kw: value
    )
    monkeypatch.setattr(
        "src.upgrade.downloader.validate_remote_url", lambda value, **_kw: value
    )
    return UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=HttpManifestSource(url, timeout=5.0),  # 不配置 manifest_sha256
            public_key=public_key,
        )
    )


def test_signed_manifest_bootstraps_without_pinned_hash(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.1.1 -> 检查到 v0.1.2 -> 下载 -> 验签通过，全程不下发新哈希。"""
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _make_release(releases, "0.1.2")
    _sign(tool, manifest_path, keys / tool.PRIVATE_FILE_NAME, 1)

    manager = _manager(
        _write_app_root(tmp_path),
        url,
        (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        monkeypatch,
    )
    try:
        assert manager.config.source.manifest_sha256 is None
        check = manager.check()
        assert check.status is CheckStatus.UPDATE_AVAILABLE, check.message
        assert check.release is not None
        download = manager.download(check.release)
        assert download.signature_verified is True
        assert download.signature_note == "已使用配置的 Ed25519 公钥验证签名"

        state = json.loads(manager.manifest_trust_store.path.read_text(encoding="utf-8"))
        assert state["sequence"] == 1
        assert state["version"] == "0.1.2"

        # 同一份清单重复检查（同 sequence、同摘要）必须幂等通过，不能误判重放。
        again = manager.check()
        assert again.status is CheckStatus.UPDATE_AVAILABLE
    finally:
        manager.close()


def test_unsigned_remote_manifest_is_rejected_without_pin(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    _make_release(releases, "0.1.2")

    manager = _manager(
        _write_app_root(tmp_path),
        url,
        (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        monkeypatch,
    )
    try:
        result = manager.check()
        assert result.status is CheckStatus.INVALID_MANIFEST
        assert "必须携带清单签名" in result.message
        assert "已是最新" not in result.message
    finally:
        manager.close()


def test_tampered_manifest_content_is_rejected(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _make_release(releases, "0.1.2")
    _sign(tool, manifest_path, keys / tool.PRIVATE_FILE_NAME, 1)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["channel"] = "tampered"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    manager = _manager(
        _write_app_root(tmp_path),
        url,
        (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        monkeypatch,
    )
    try:
        result = manager.check()
        assert result.status is CheckStatus.INVALID_MANIFEST
        assert "签名验证失败" in result.message
    finally:
        manager.close()


def _prime_trust(tool, tmp_path: Path, releases: Path, url: str,
                 keys: Path, monkeypatch: pytest.MonkeyPatch) -> UpgradeManager:
    manifest_path, _ = _make_release(releases, "0.1.2")
    _sign(tool, manifest_path, keys / tool.PRIVATE_FILE_NAME, 2)
    manager = _manager(
        _write_app_root(tmp_path),
        url,
        (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        monkeypatch,
    )
    check = manager.check()
    assert check.status is CheckStatus.UPDATE_AVAILABLE, check.message
    return manager


def test_replayed_older_sequence_is_rejected(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manager = _prime_trust(tool, tmp_path, releases, url, keys, monkeypatch)
    try:
        # 攻击者重放一份"合法但更旧"的签名清单（sequence=1）。
        old_manifest, _ = _make_release(releases, "0.1.1")
        _sign(tool, old_manifest, keys / tool.PRIVATE_FILE_NAME, 1)
        result = manager.check()
        assert result.status is CheckStatus.INVALID_MANIFEST
        assert "sequence 回退" in result.message
        assert "重放" in result.message
    finally:
        manager.close()


def test_same_sequence_different_content_is_rejected(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manager = _prime_trust(tool, tmp_path, releases, url, keys, monkeypatch)
    try:
        changed, _ = _make_release(releases, "0.1.2", notes="forked content")
        _sign(tool, changed, keys / tool.PRIVATE_FILE_NAME, 2)
        result = manager.check()
        assert result.status is CheckStatus.INVALID_MANIFEST
        assert "内容不一致" in result.message
    finally:
        manager.close()


def test_downgrade_manifest_with_higher_sequence_is_rejected(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manager = _prime_trust(tool, tmp_path, releases, url, keys, monkeypatch)
    try:
        # 持钥者签了一份序号更大、但版本回退的清单：仍必须拒绝。
        older, _ = _make_release(releases, "0.1.1")
        _sign(tool, older, keys / tool.PRIVATE_FILE_NAME, 3)
        result = manager.check()
        assert result.status is CheckStatus.INVALID_MANIFEST
        assert "拒绝降级清单" in result.message
    finally:
        manager.close()


def test_old_artifact_replay_is_rejected_on_download(
    tool, tmp_path: Path, release_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases, url = release_server
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manager = _prime_trust(tool, tmp_path, releases, url, keys, monkeypatch)
    try:
        old_manifest, _ = _make_release(releases, "0.1.1")
        _sign(tool, old_manifest, keys / tool.PRIVATE_FILE_NAME, 3)
        old_release = parse_manifest(old_manifest.read_text(encoding="utf-8"))[
            "_parsed_releases"
        ][0]
        with pytest.raises(SignatureError, match="重放旧版本发布包"):
            manager.download(old_release)
    finally:
        manager.close()
