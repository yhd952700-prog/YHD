"""Release manifest sources: a local directory or an HTTP(S) manifest.

The two sources deliberately share one contract so the rest of the module does
not care where a release comes from. Both distinguish "cannot check" from
"there is no newer version": an unreachable HTTP source and a missing local
directory are *not* reported as "已是最新".
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from packaging.version import InvalidVersion, Version

from .errors import ManifestError
from .models import ReleaseInfo

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_SCHEMA = "liuhao.release-manifest/1"
_MAX_MANIFEST_BYTES = 1024 * 1024
_USER_AGENT = "LiuHao-AI-OS-Updater/1.0"


@dataclass
class ManifestLoad:
    ok: bool
    manifest: Optional[Dict[str, Any]] = None
    reason: str = ""
    offline: bool = False


def _validate_release(raw: Any, index: int) -> ReleaseInfo:
    if not isinstance(raw, dict):
        raise ManifestError(f"发布清单第 {index} 项不是对象")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestError(f"发布清单第 {index} 项缺少 version")
    try:
        Version(version.strip())
    except InvalidVersion as exc:
        raise ManifestError(f"发布清单第 {index} 项版本号无效：{version!r}") from exc

    artifact = raw.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        raise ManifestError(f"发布清单第 {index} 项缺少 artifact")
    sha256 = str(raw.get("sha256", "")).strip().lower()
    if not _HEX64.match(sha256):
        raise ManifestError(f"发布清单第 {index} 项 sha256 不是 64 位十六进制")

    size = raw.get("size")
    if size is not None and (not isinstance(size, int) or size < 0):
        raise ManifestError(f"发布清单第 {index} 项 size 无效")

    signature = raw.get("signature")
    if signature is not None and not isinstance(signature, str):
        raise ManifestError(f"发布清单第 {index} 项 signature 必须是字符串")

    return ReleaseInfo(
        version=version.strip(),
        artifact=artifact.strip(),
        sha256=sha256,
        size=size,
        signature=signature.strip() if signature else None,
        signature_algorithm=(
            str(raw.get("signature_algorithm") or "ed25519").strip().lower()
            if signature
            else None
        ),
        notes=str(raw.get("notes") or ""),
        released_at=raw.get("released_at"),
    )


def parse_manifest(payload: bytes | str) -> Dict[str, Any]:
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"发布清单不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("发布清单根节点必须是对象")
    schema = data.get("schema")
    if schema is not None and schema != _MANIFEST_SCHEMA:
        raise ManifestError(f"不支持的发布清单 schema：{schema!r}")
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ManifestError("发布清单 releases 必须是非空数组")
    parsed: List[ReleaseInfo] = []
    seen: set[str] = set()
    for index, item in enumerate(releases):
        release = _validate_release(item, index)
        if release.version in seen:
            raise ManifestError(f"发布清单包含重复版本：{release.version}")
        seen.add(release.version)
        parsed.append(release)
    data["_parsed_releases"] = parsed
    return data


def select_latest(manifest: Dict[str, Any]) -> ReleaseInfo:
    releases = manifest.get("_parsed_releases")
    if not isinstance(releases, list) or not releases:
        raise ManifestError("发布清单没有可用发布项")
    return max(releases, key=lambda item: Version(item.version))


class ReleaseSource:
    """Abstract release source."""

    description = ""

    def load_manifest(self) -> ManifestLoad:  # pragma: no cover - interface
        raise NotImplementedError

    def resolve_artifact(self, release: ReleaseInfo) -> str:  # pragma: no cover
        raise NotImplementedError


class LocalDirectorySource(ReleaseSource):
    """A directory containing ``LIUHAO_RELEASE.json`` and the artifact.

    ``release-manifest.json`` is accepted as a legacy alias so already-built
    release directories keep working after the 2026-11 file-name finalisation.
    """

    MANIFEST_NAMES = ("LIUHAO_RELEASE.json", "release-manifest.json")

    def __init__(
        self, directory: str | Path, manifest_name: Optional[str] = None
    ) -> None:
        self.directory = Path(directory)
        self.manifest_name = manifest_name
        self.description = f"本地发布目录 {self.directory}"

    def _manifest_path(self) -> Optional[Path]:
        names = (self.manifest_name,) if self.manifest_name else self.MANIFEST_NAMES
        for name in names:
            candidate = self.directory / name
            if candidate.is_file():
                return candidate
        return None

    def load_manifest(self) -> ManifestLoad:
        if not self.directory.is_dir():
            return ManifestLoad(
                ok=False,
                reason=f"无法检查更新：发布目录不存在（{self.directory}）",
            )
        manifest_path = self._manifest_path()
        if manifest_path is None:
            expected = self.manifest_name or "或".join(self.MANIFEST_NAMES)
            return ManifestLoad(
                ok=False,
                reason=f"无法检查更新：发布目录中没有 {expected}",
            )
        try:
            payload = manifest_path.read_bytes()
        except OSError as exc:
            return ManifestLoad(ok=False, reason=f"无法检查更新：读取发布清单失败（{exc}）")
        if len(payload) > _MAX_MANIFEST_BYTES:
            return ManifestLoad(ok=False, reason="发布清单过大，拒绝加载")
        try:
            manifest = parse_manifest(payload)
        except ManifestError as exc:
            return ManifestLoad(ok=False, reason=f"无法检查更新：发布清单无效（{exc}）")
        return ManifestLoad(ok=True, manifest=manifest)

    def resolve_artifact(self, release: ReleaseInfo) -> str:
        candidate = (self.directory / release.artifact).resolve()
        try:
            candidate.relative_to(self.directory.resolve())
        except ValueError as exc:
            raise ManifestError(
                f"发布清单 artifact 指向目录之外：{release.artifact!r}"
            ) from exc
        return str(candidate)


class HttpManifestSource(ReleaseSource):
    """An HTTP(S) manifest URL. Artifacts are resolved relative to it."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 5.0,
        manifest_sha256: Optional[str] = None,
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ManifestError("更新清单 URL 仅支持 http/https")
        if not parsed.netloc:
            raise ManifestError("更新清单 URL 缺少主机名")
        self.url = url
        self.timeout = timeout
        self.manifest_sha256 = manifest_sha256.lower() if manifest_sha256 else None
        if self.manifest_sha256 and not _HEX64.match(self.manifest_sha256):
            raise ManifestError("manifest_sha256 不是 64 位十六进制")
        self.description = f"HTTP 更新清单 {url}"

    def _fetch(self) -> bytes:
        request = urllib.request.Request(self.url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ManifestError(f"更新源返回 HTTP {status}")
            payload = response.read(_MAX_MANIFEST_BYTES + 1)
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ManifestError("发布清单过大，拒绝加载")
        if self.manifest_sha256:
            digest = hashlib.sha256(payload).hexdigest()
            if digest != self.manifest_sha256:
                raise ManifestError(
                    "发布清单 SHA256 与配置的固定值不一致（清单可能被篡改）"
                )
        return payload

    def load_manifest(self) -> ManifestLoad:
        try:
            payload = self._fetch()
        except urllib.error.HTTPError as exc:
            return ManifestLoad(
                ok=False,
                reason=f"无法检查更新：更新源返回 HTTP {exc.code}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ManifestLoad(
                ok=False,
                reason=f"无法检查更新：网络不可用（{exc}）",
                offline=True,
            )
        except ManifestError as exc:
            return ManifestLoad(ok=False, reason=f"无法检查更新：发布清单无效（{exc}）")
        try:
            manifest = parse_manifest(payload)
        except ManifestError as exc:
            return ManifestLoad(ok=False, reason=f"无法检查更新：发布清单无效（{exc}）")
        return ManifestLoad(ok=True, manifest=manifest)

    def resolve_artifact(self, release: ReleaseInfo) -> str:
        resolved = urllib.parse.urljoin(self.url, release.artifact)
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ManifestError(f"发布包地址无效：{resolved!r}")
        return resolved


def build_source(
    *,
    release_dir: str | Path | None = None,
    manifest_url: str | None = None,
    timeout: float = 5.0,
    manifest_sha256: str | None = None,
) -> Optional[ReleaseSource]:
    """Factory used by the CLI; returns ``None`` when nothing is configured."""
    if release_dir and manifest_url:
        raise ManifestError("release_dir 与 manifest_url 只能配置一个")
    if release_dir:
        return LocalDirectorySource(release_dir)
    if manifest_url:
        return HttpManifestSource(
            manifest_url, timeout=timeout, manifest_sha256=manifest_sha256
        )
    return None
