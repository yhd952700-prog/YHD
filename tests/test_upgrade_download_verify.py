"""Real download verification: SHA256, signature state, and cleanup.

The signature tests generate a real Ed25519 key, sign the real artifact bytes,
and prove both the pass and the tamper-fail paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.upgrade import (
    ChecksumMismatch,
    LocalDirectorySource,
    ManifestError,
    ReleaseInfo,
    SignatureError,
    download_release,
    select_latest,
)
from src.upgrade.downloader import cleanup_download
from src.upgrade.sources import parse_manifest


def _release_dir(tmp_path: Path, *, version: str = "0.2.0") -> Path:
    directory = tmp_path / "releases"
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / f"liuhao-{version}.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pyproject.toml", f'[project]\nversion = "{version}"\n')
    return directory


def _manifest_for(directory: Path, artifact_name: str, **extra) -> dict:
    artifact = directory / artifact_name
    release = {
        "version": "0.2.0",
        "artifact": artifact_name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size": artifact.stat().st_size,
    }
    release.update(extra)
    manifest = {"schema": "liuhao.release-manifest/1", "releases": [release]}
    (directory / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return parse_manifest((directory / "release-manifest.json").read_bytes())


def test_download_verifies_sha_and_reports_no_signature(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = _manifest_for(directory, "liuhao-0.2.0.zip")
    source = LocalDirectorySource(directory)
    release = select_latest(manifest)
    result = download_release(source, release, target_dir=tmp_path / "download")
    assert Path(result.path).is_file()
    assert result.size == Path(result.path).stat().st_size
    assert result.sha256 == release.sha256
    assert result.signature_verified is False
    assert result.signature_note == "仅哈希校验，无签名"
    cleanup_download(result)
    assert Path(result.path).exists()  # target_dir is caller-owned, never deleted


def test_checksum_mismatch_rejects_and_cleans_partial_file(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = _manifest_for(
        directory, "liuhao-0.2.0.zip", sha256="0" * 64
    )
    release = select_latest(manifest)
    target = tmp_path / "download"
    with pytest.raises(ChecksumMismatch) as exc:
        download_release(
            LocalDirectorySource(directory), release, target_dir=target
        )
    assert "SHA256 校验失败" in str(exc.value)
    assert list(target.iterdir()) == []


def test_signature_present_without_key_is_reported_as_unverified(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = _manifest_for(
        directory,
        "liuhao-0.2.0.zip",
        signature=base64.b64encode(b"fake-signature").decode(),
    )
    release = select_latest(manifest)
    result = download_release(
        LocalDirectorySource(directory), release, target_dir=tmp_path / "download"
    )
    assert result.signature_verified is False
    assert "未配置可信公钥" in result.signature_note
    assert "仅哈希校验" in result.signature_note


def test_real_ed25519_signature_is_verified(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    artifact_bytes = (directory / "liuhao-0.2.0.zip").read_bytes()
    signature = base64.b64encode(private.sign(artifact_bytes)).decode()
    manifest = _manifest_for(
        directory, "liuhao-0.2.0.zip", signature=signature
    )
    release = select_latest(manifest)
    result = download_release(
        LocalDirectorySource(directory),
        release,
        target_dir=tmp_path / "download",
        public_key=public_pem,
    )
    assert result.signature_verified is True
    assert "Ed25519" in result.signature_note


def test_tampered_signature_aborts_download(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = base64.b64encode(private.sign(b"different bytes")).decode()
    manifest = _manifest_for(
        directory, "liuhao-0.2.0.zip", signature=signature
    )
    release = select_latest(manifest)
    target = tmp_path / "download"
    with pytest.raises(SignatureError) as exc:
        download_release(
            LocalDirectorySource(directory),
            release,
            target_dir=target,
            public_key=public_pem,
        )
    assert "签名验证失败" in str(exc.value)
    assert list(target.iterdir()) == []


def test_artifact_path_escaping_release_dir_is_rejected(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    outside = tmp_path / "outside.zip"
    with zipfile.ZipFile(outside, "w") as archive:
        archive.writestr("pyproject.toml", '[project]\nversion = "0.2.0"\n')
    manifest = {
        "schema": "liuhao.release-manifest/1",
        "releases": [
            {
                "version": "0.2.0",
                "artifact": "../outside.zip",
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                "size": outside.stat().st_size,
            }
        ],
    }
    (directory / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    release = select_latest(parse_manifest((directory / "release-manifest.json").read_bytes()))
    with pytest.raises(Exception) as exc:
        LocalDirectorySource(directory).resolve_artifact(release)
    assert "目录之外" in str(exc.value)


class _HttpArtifactSource:
    """把下载地址指到本地 HTTP 服务，复现"连接中途断开"。"""

    description = "本机测试服务"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def resolve_artifact(self, release: ReleaseInfo) -> str:
        return f"{self.base_url}/{release.artifact}"


def test_interrupted_http_download_says_incomplete_and_installs_nothing(
    tmp_path: Path,
) -> None:
    payload = b"PK\x03\x04" + b"x" * 4096

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[: len(payload) // 3])
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        release = ReleaseInfo(
            version="0.2.0",
            artifact="liuhao-0.2.0.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )
        target = tmp_path / "download"
        source = _HttpArtifactSource(f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises((ChecksumMismatch, ManifestError)) as exc:
            download_release(source, release, target_dir=target, timeout=5)
    finally:
        server.shutdown()

    message = str(exc.value)
    assert "下载未完成" in message or "下载中断" in message, message
    # 半截文件不能留在目录里，更不能被当成发布包。
    assert list(target.iterdir()) == []
