"""Download an artifact to a temporary directory and verify it.

Two guarantees, both user-visible:

* the artifact bytes must match the SHA256 from the release manifest; a mismatch
  is a hard failure and the file is deleted;
* signature state is reported honestly. With no signature we say
  ``仅哈希校验，无签名``. With a signature but no configured public key we say
  ``未验证签名`` rather than pretending it passed. With a key we do a real
  Ed25519 verification and a bad signature aborts the upgrade.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .errors import ChecksumMismatch, ManifestError, SignatureError
from .models import DownloadResult, ReleaseInfo
from .sources import ReleaseSource

_USER_AGENT = "LiuHao-AI-OS-Updater/1.0"
_CHUNK = 1024 * 1024
# Ed25519 verification needs the whole message in memory. Refuse to pretend to
# verify artifacts larger than this instead of risking an OOM.
_MAX_IN_MEMORY_SIGNATURE_BYTES = 512 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_filename(release: ReleaseInfo, location: str) -> str:
    name = Path(location.split("?", 1)[0]).name if "://" not in location else Path(
        location.split("?", 1)[0]
    ).name
    if not name or name in (".", ".."):
        name = f"liuhao-{release.version}.zip"
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._-")
    return name or f"liuhao-{release.version}.zip"


def _is_http(location: str) -> bool:
    lower = location.lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _copy_local(source: Path, target: Path, max_bytes: int) -> int:
    written = 0
    with open(source, "rb") as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ChecksumMismatch(f"发布包超过允许的大小上限（{max_bytes} 字节）")
            dst.write(chunk)
    return written


def _download_http(url: str, target: Path, timeout: float, max_bytes: int) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ManifestError(f"下载发布包返回 HTTP {status}")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    expected = int(length)
                except ValueError:
                    expected = None
                if expected is not None and expected > max_bytes:
                    raise ChecksumMismatch(
                        f"发布包声明大小 {expected} 超过上限 {max_bytes} 字节"
                    )
            with open(target, "wb") as dst:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ChecksumMismatch(
                            f"发布包超过允许的大小上限（{max_bytes} 字节）"
                        )
                    dst.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ManifestError(f"下载发布包返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManifestError(f"下载发布包失败：网络不可用（{exc}）") from exc
    except http.client.HTTPException as exc:
        # 连接在读完 Content-Length 之前被对端关掉（IncompleteRead 等）。
        # 这不是"清单写错了"，必须说清楚是下载中断，用户才会去重试。
        raise ManifestError(
            f"下载中断：连接提前断开，只收到 {written} 字节；未做任何安装。"
        ) from exc
    return written


def _decode_public_key(value: str | bytes) -> ed25519.Ed25519PublicKey:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    try:
        if b"BEGIN PUBLIC KEY" in raw:
            key = serialization.load_pem_public_key(raw)
        else:
            key = ed25519.Ed25519PublicKey.from_public_bytes(
                base64.b64decode(raw, validate=True)
            )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise SignatureError(f"配置的 Ed25519 公钥无法解析：{exc}") from exc
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise SignatureError("配置的公钥不是 Ed25519 公钥")
    return key


def verify_signature(
    artifact: Path,
    release: ReleaseInfo,
    public_key: Optional[str | bytes],
) -> tuple[bool, str]:
    """Return ``(verified, note)`` without ever claiming an unverified signature."""
    if not release.signature:
        return False, "仅哈希校验，无签名"
    if not public_key:
        return False, "发布清单包含签名，但未配置可信公钥，未验证签名（仅哈希校验）"
    if (release.signature_algorithm or "ed25519").lower() != "ed25519":
        raise SignatureError(
            f"不支持的签名算法：{release.signature_algorithm!r}（当前仅支持 ed25519）"
        )
    size = artifact.stat().st_size
    if size > _MAX_IN_MEMORY_SIGNATURE_BYTES:
        raise SignatureError(
            f"发布包 {size} 字节过大，无法在内存中完成签名验证；拒绝声称签名已验证"
        )
    key = _decode_public_key(public_key)
    try:
        signature = base64.b64decode(release.signature.encode("utf-8"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SignatureError(f"发布清单 signature 不是合法 base64：{exc}") from exc
    try:
        key.verify(signature, artifact.read_bytes())
    except InvalidSignature as exc:
        raise SignatureError("签名验证失败：发布包与签名不匹配，可能被篡改") from exc
    return True, "已使用配置的 Ed25519 公钥验证签名"


def download_release(
    source: ReleaseSource,
    release: ReleaseInfo,
    *,
    target_dir: str | Path | None = None,
    public_key: Optional[str | bytes] = None,
    timeout: float = 30.0,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> DownloadResult:
    """Download ``release`` and verify size, SHA256 and (if configured) signature."""
    location = source.resolve_artifact(release)
    if target_dir is None:
        temp_root = Path(tempfile.mkdtemp(prefix="liuhao-upgrade-download-"))
    else:
        temp_root = Path(target_dir)
        temp_root.mkdir(parents=True, exist_ok=True)
    temporary = target_dir is None
    target = temp_root / _artifact_filename(release, location)
    part = target.with_name(target.name + ".part")

    try:
        if _is_http(location):
            written = _download_http(location, part, timeout, max_bytes)
        else:
            source_path = Path(location)
            if not source_path.is_file():
                raise ManifestError(f"发布包不存在：{source_path}")
            written = _copy_local(source_path, part, max_bytes)
        os.replace(part, target)

        if release.size is not None and written != release.size:
            if written < release.size:
                # 只收到一部分：绝大多数是网络中途断开。旧版本一个字节都没动。
                raise ChecksumMismatch(
                    f"下载未完成：清单 {release.size} 字节，实际只收到 {written} 字节"
                    "（连接中断）。程序未做任何改动，请重试。"
                )
            raise ChecksumMismatch(
                f"发布包大小不符：清单 {release.size} 字节，实际 {written} 字节"
                "（包被追加了内容或清单与包不匹配）"
            )
        digest = _sha256_file(target)
        if digest != release.sha256:
            raise ChecksumMismatch(
                f"SHA256 校验失败：清单 {release.sha256}，实际 {digest}"
            )
        verified, note = verify_signature(target, release, public_key)
        return DownloadResult(
            path=str(target),
            sha256=digest,
            size=written,
            release=release,
            signature_verified=verified,
            signature_note=note,
            temporary=temporary,
        )
    except BaseException:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        if temporary:
            shutil.rmtree(temp_root, ignore_errors=True)
        raise


def cleanup_download(result: DownloadResult) -> None:
    """Remove a temporary download directory (safe to call twice)."""
    if not result.temporary:
        return
    path = Path(result.path)
    if path.exists():
        shutil.rmtree(path.parent, ignore_errors=True)
