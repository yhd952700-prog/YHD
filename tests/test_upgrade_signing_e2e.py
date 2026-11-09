"""End-to-end checks for the release signing tool and its verifier contract.

These tests exercise the real Ed25519 implementation (no stubs):

* keygen -> sign manifest -> download + verify accepted;
* the same exported public key in PEM and base64 (``LIUHAO_UPGRADE_PUBLIC_KEY``
  shape) both verify;
* tampered manifest hash / tampered signature / wrong public key are rejected;
* signing refuses an artifact whose bytes do not match the declared hash;
* the private key stays machine-bound and outside the repository.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from src.upgrade import (
    CheckStatus,
    LocalDirectorySource,
    UpgradeConfig,
    UpgradeManager,
)
from src.upgrade.downloader import cleanup_download, download_release, verify_signature
from src.upgrade.errors import ChecksumMismatch, SignatureError
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


def _write_app_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "liuhao-ai-os"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "data").mkdir(exist_ok=True)
    return root


def _write_release(tmp_path: Path, version: str = "0.2.0") -> tuple[Path, Path]:
    release_dir = tmp_path / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    artifact = release_dir / f"liuhao-{version}.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "pyproject.toml",
            f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        )
        archive.writestr("src/app.py", "VALUE = 2\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = {
        "schema": "liuhao.release-manifest/1",
        "channel": "stable",
        "releases": [
            {
                "version": version,
                "artifact": artifact.name,
                "sha256": digest,
                "size": artifact.stat().st_size,
                "notes": f"release {version}",
            }
        ],
    }
    manifest_path = release_dir / "LIUHAO_RELEASE.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, artifact


def _release_info(manifest_path: Path):
    parsed = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    return parsed["_parsed_releases"][0]


def _manager(app_root: Path, release_dir: Path, public_key: str | None) -> UpgradeManager:
    return UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
            public_key=public_key,
        )
    )


def test_signed_release_is_accepted_end_to_end(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _write_release(tmp_path)

    signed = tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)
    assert [item["version"] for item in signed] == ["0.2.0"]

    manager = _manager(
        _write_app_root(tmp_path),
        tmp_path / "release",
        (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
    )
    try:
        check = manager.check()
        assert check.status is CheckStatus.UPDATE_AVAILABLE
        assert check.release is not None
        download = manager.download(check.release)
        assert download.signature_verified is True
        assert download.signature_note == "已使用配置的 Ed25519 公钥验证签名"
        cleanup_download(download)
    finally:
        manager.close()


def test_exported_base64_public_key_is_accepted(tool, tmp_path: Path) -> None:
    """``LIUHAO_UPGRADE_PUBLIC_KEY`` 可以直接用导出的 base64 原文。"""
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _write_release(tmp_path)
    tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)

    public_b64 = (keys / tool.PUBLIC_B64_NAME).read_text(encoding="utf-8").strip()
    release = _release_info(manifest_path)
    result = download_release(
        LocalDirectorySource(tmp_path / "release"),
        release,
        target_dir=tmp_path / "download",
        public_key=public_b64,
    )
    assert result.signature_verified is True
    assert result.signature_note == "已使用配置的 Ed25519 公钥验证签名"


def test_tampered_manifest_hash_is_rejected(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _write_release(tmp_path)
    tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["releases"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    release = _release_info(manifest_path)
    with pytest.raises(ChecksumMismatch, match="SHA256"):
        download_release(
            LocalDirectorySource(tmp_path / "release"),
            release,
            target_dir=tmp_path / "download",
            public_key=(keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        )


def test_tampered_signature_is_rejected(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _write_release(tmp_path)
    tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["releases"][0]["signature"] = base64.b64encode(b"\x00" * 32).decode("ascii")
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    release = _release_info(manifest_path)
    with pytest.raises(SignatureError, match="签名验证失败"):
        download_release(
            LocalDirectorySource(tmp_path / "release"),
            release,
            target_dir=tmp_path / "download",
            public_key=(keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        )


def test_wrong_public_key_is_rejected(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    other_keys = tmp_path / "other-keys"
    tool.generate_keypair(other_keys)
    manifest_path, _ = _write_release(tmp_path)
    tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)

    release = _release_info(manifest_path)
    with pytest.raises(SignatureError, match="签名验证失败"):
        download_release(
            LocalDirectorySource(tmp_path / "release"),
            release,
            target_dir=tmp_path / "download",
            public_key=(other_keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8"),
        )


def test_unsigned_artifact_is_never_called_verified(tool, tmp_path: Path) -> None:
    """红态：没有签名时只能得到"仅哈希校验，无签名"，绝不能声称已验证。"""
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, artifact = _write_release(tmp_path)
    release = _release_info(manifest_path)
    verified, note = verify_signature(
        artifact, release, (keys / tool.PUBLIC_PEM_NAME).read_text(encoding="utf-8")
    )
    assert verified is False
    assert note == "仅哈希校验，无签名"


def test_sign_refuses_artifact_hash_mismatch(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    manifest_path, _ = _write_release(tmp_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["releases"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(tool.SigningError, match="SHA256 与清单不一致"):
        tool.sign_manifest(manifest_path, keys / tool.PRIVATE_FILE_NAME)


def test_private_key_is_machine_bound_and_stays_outside_repo(tool, tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    raw = (keys / tool.PRIVATE_FILE_NAME).read_bytes()
    assert raw.startswith((b"LIUHAO-DPAPI-1:", b"LIUHAO-RAW-1:"))
    assert b"PRIVATE KEY" not in raw

    inside_repo = REPO_ROOT / "tests" / ".tmp-signing-keys"
    with pytest.raises(tool.SigningError, match="仓库"):
        tool.generate_keypair(inside_repo)
    assert not inside_repo.exists()

    with pytest.raises(tool.SigningError, match="已存在"):
        tool.generate_keypair(keys)


def test_cli_keygen_prints_fingerprint_without_private_material(
    tool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = tmp_path / "cli-keys"
    assert tool.main(["keygen", "--key-dir", str(keys)]) == 0
    output = capsys.readouterr().out
    assert "公钥指纹(SHA256)" in output
    assert "PRIVATE KEY" not in output
    assert str(keys) in output
