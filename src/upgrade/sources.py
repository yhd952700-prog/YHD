"""Release manifest sources: a local directory, a local manifest file, or an
HTTP(S) manifest.

The sources deliberately share one contract so the rest of the module does not
care where a release comes from. They distinguish "cannot check" from "there is
no newer version": an unreachable HTTP source and a missing local file are
*not* reported as "已是最新".
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from packaging.version import InvalidVersion, Version

from .errors import ManifestError, PackageValidationError
from .filemanifest import normalize_entries
from .models import ReleaseInfo

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_SCHEMA = "liuhao.release-manifest/1"
_MANIFEST_SIGNATURE_FIELD = "manifest_signature"
_MANIFEST_SIGNATURE_ALGORITHM_FIELD = "manifest_signature_algorithm"
_MANIFEST_SEQUENCE_FIELD = "sequence"
#: 文件级清单要给每个文件记一行路径/哈希/大小，完整安装树约 100KB 量级。
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_USER_AGENT = "LiuHao-AI-OS-Updater/1.0"

_PRIVATE_ADDRESS_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_unspecified",
    "is_multicast",
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """升级流量一律不跟随 30x：重定向可把已校验的 https 目标换到 http/内网。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ManifestError(
            f"更新源禁止重定向（收到 HTTP {code}）；请直接在配置里填写最终地址。"
        )


def _coerce_address(address):
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def validate_remote_url(
    url: str, *, context: str = "更新源", resolve: bool = True
) -> str:
    """强制 https、禁止内嵌凭据、拒绝私网/回环/链路本地目标。

    ``resolve=True`` 时会在发起请求前解析域名并逐地址拒绝私网（含
    ``169.254.169.254``）。失败即 ``ManifestError``，调用方按“无法检查更新”
    处理，而不是把它当成“已是最新”。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ManifestError(f"{context}必须使用 https，收到：{parsed.scheme or '(空)'}")
    if not parsed.hostname:
        raise ManifestError(f"{context} URL 缺少主机名")
    if parsed.username or parsed.password:
        raise ManifestError(f"{context} URL 不能包含用户名或密码")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ManifestError(f"{context} URL 端口无效：{exc}") from exc

    def _reject(address, source: str) -> None:
        normalized = _coerce_address(address)
        if any(getattr(normalized, flag) for flag in _PRIVATE_ADDRESS_FLAGS):
            raise ManifestError(
                f"{context}指向私网/回环/链路本地地址，拒绝访问（{source}）。"
            )

    host = parsed.hostname
    literal = None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        _reject(literal, host)
        return url
    if not resolve:
        return url
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise ManifestError(f"{context}主机名无法解析：{host}（{exc}）") from exc
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise ManifestError(f"{context}解析结果异常：{host}") from exc
        _reject(address, host)
    return url


def open_no_redirect(request: urllib.request.Request, *, timeout: float):
    """打开 URL，但不跟随任何 30x 重定向。"""
    opener = urllib.request.build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)


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

    files = raw.get("files")
    parsed_files = None
    if files is not None:
        try:
            parsed_files = tuple(
                normalize_entries(files, context=f"发布清单第 {index} 项 files")
            )
        except PackageValidationError as exc:
            raise ManifestError(str(exc)) from exc

    data_schema_version = raw.get("data_schema_version")
    if data_schema_version is not None and (
        not isinstance(data_schema_version, int)
        or isinstance(data_schema_version, bool)
        or data_schema_version < 1
    ):
        raise ManifestError(f"发布清单第 {index} 项 data_schema_version 无效")

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
        files=parsed_files,
        data_schema_version=data_schema_version,
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
    signature = data.get(_MANIFEST_SIGNATURE_FIELD)
    if signature is not None and not isinstance(signature, str):
        raise ManifestError("发布清单 manifest_signature 必须是字符串")
    algorithm = data.get(_MANIFEST_SIGNATURE_ALGORITHM_FIELD)
    if algorithm is not None and str(algorithm).strip().lower() != "ed25519":
        raise ManifestError(
            f"不支持的清单签名算法：{algorithm!r}（当前仅支持 ed25519）"
        )
    sequence = data.get(_MANIFEST_SEQUENCE_FIELD)
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
    ):
        raise ManifestError("发布清单 sequence 必须是 >= 1 的整数")
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
    #: 远端源（HTTPS）：其清单必须可被 Ed25519 签名验证或在首次引导时固定哈希。
    is_remote = False

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


class LocalManifestFileSource(ReleaseSource):
    """A single manifest file; artifacts are resolved next to it.

    This is the source a release directory produces, but pointed at one
    explicit file instead of a directory listing. It lets users run the
    documented command::

        python -m src.upgrade check --manifest dist/release/LIUHAO_RELEASE.json
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.description = f"本地发布清单 {self.path}"

    def load_manifest(self) -> ManifestLoad:
        if not self.path.is_file():
            return ManifestLoad(
                ok=False,
                reason=f"无法检查更新：发布清单不存在（{self.path}）",
            )
        try:
            payload = self.path.read_bytes()
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
        base = self.path.parent.resolve()
        candidate = (base / release.artifact).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ManifestError(
                f"发布清单 artifact 指向目录之外：{release.artifact!r}"
            ) from exc
        return str(candidate)


class HttpManifestSource(ReleaseSource):
    """An HTTPS manifest URL. Artifacts are resolved relative to it.

    远端更新是供应链入口：强制 https、拒绝私网/回环、禁止 30x 跟随。清单的
    信任依据有两种：

    * **推荐**：清单自带 Ed25519 ``manifest_signature``，客户端只固定公钥，
      每次发布无需再下发新的哈希（见 ``manifest_trust``）；
    * **兼容/首次引导**：配置了 ``manifest_sha256`` 时按固定哈希校验。该值
      每次发布都会变，不应作为长期方案。
    """

    is_remote = True

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 5.0,
        manifest_sha256: Optional[str] = None,
    ) -> None:
        validate_remote_url(url, context="更新清单 URL", resolve=False)
        digest = (manifest_sha256 or "").strip().lower()
        if digest and not _HEX64.match(digest):
            raise ManifestError("manifest_sha256 不是 64 位十六进制")
        self.url = url
        self.timeout = timeout
        self.manifest_sha256 = digest or None
        self.description = f"HTTPS 更新清单 {url}"

    def _fetch(self) -> bytes:
        validate_remote_url(self.url, context="更新清单", resolve=True)
        request = urllib.request.Request(self.url, headers={"User-Agent": _USER_AGENT})
        with open_no_redirect(request, timeout=self.timeout) as response:
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
        validate_remote_url(resolved, context="发布包地址", resolve=False)
        return resolved


def build_source(
    *,
    release_dir: str | Path | None = None,
    manifest_url: str | None = None,
    manifest_path: str | Path | None = None,
    timeout: float = 5.0,
    manifest_sha256: str | None = None,
) -> Optional[ReleaseSource]:
    """Factory used by the CLI; returns ``None`` when nothing is configured."""
    configured = [
        name
        for name, value in (
            ("release_dir", release_dir),
            ("manifest_url", manifest_url),
            ("manifest_path", manifest_path),
        )
        if value
    ]
    if len(configured) > 1:
        raise ManifestError("release_dir、manifest_url 与 manifest_path 只能配置一个")
    if release_dir:
        return LocalDirectorySource(release_dir)
    if manifest_path:
        return LocalManifestFileSource(manifest_path)
    if manifest_url:
        return HttpManifestSource(
            manifest_url, timeout=timeout, manifest_sha256=manifest_sha256
        )
    return None
