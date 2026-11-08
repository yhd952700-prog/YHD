"""升级包文件级清单：生成、解析与逐项校验。

发布清单里的 ``releases[].files`` 记录升级包中每个文件的相对路径、SHA256
与字节数。整包 SHA256 只能证明「拿到的是发布者签名的那一坨字节」，证明不了
「这一坨字节里文件齐全」：构建时漏打包 ``src/gateway/chat.py``，整包哈希照样
对得上，安装后就是新旧代码混跑。所以 apply 前必须按文件级清单逐项比对：

* 清单里有、包内没有：拒绝（缺文件）；
* 包内有、清单里没有：拒绝（多出未声明文件）；
* 任一文件大小或内容哈希不符：拒绝。

以上任何一种都在修改任何文件之前拒绝，安装目录保持原状。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .archive import safe_relpath
from .errors import PackageValidationError

FILE_MANIFEST_SCHEMA = "liuhao.file-manifest/1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FileEntry:
    """清单中的一个文件：相对路径 + 内容 SHA256 + 字节数。"""

    path: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


def normalize_entry(raw: Any, *, context: str) -> FileEntry:
    """把清单里的 ``{path, sha256, size}`` 转成 :class:`FileEntry`。"""
    if not isinstance(raw, dict):
        raise PackageValidationError(f"{context} 中含非对象条目：{raw!r}")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise PackageValidationError(f"{context} 中存在缺少 path 的条目")
    try:
        normalized = safe_relpath(path.strip())
    except PackageValidationError as exc:
        raise PackageValidationError(f"{context} 中的路径不安全：{path!r}（{exc}）") from exc
    sha256 = str(raw.get("sha256") or "").strip().lower()
    if not _HEX64.match(sha256):
        raise PackageValidationError(
            f"{context} 中 {normalized} 的 sha256 不是 64 位十六进制"
        )
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise PackageValidationError(f"{context} 中 {normalized} 的 size 无效：{size!r}")
    return FileEntry(path=normalized, sha256=sha256, size=size)


def normalize_entries(raw_entries: Any, *, context: str) -> List[FileEntry]:
    """校验并归一化整个文件级清单，拒绝空清单与重复路径。"""
    if not isinstance(raw_entries, list) or not raw_entries:
        raise PackageValidationError(f"{context} 必须是非空数组")
    entries: List[FileEntry] = []
    seen: set[str] = set()
    for raw in raw_entries:
        entry = normalize_entry(raw, context=context)
        if entry.path in seen:
            raise PackageValidationError(f"{context} 中存在重复路径：{entry.path}")
        seen.add(entry.path)
        entries.append(entry)
    return entries


def manifest_digest(entries: Iterable[FileEntry]) -> str:
    """对文件级清单做稳定摘要（顺序无关），用于记录证据。"""
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.path):
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_file_manifest(archive: zipfile.ZipFile) -> List[Dict[str, Any]]:
    """从已打开的 ZIP 生成文件级清单（含清单文件自身）。"""
    entries: List[Dict[str, Any]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        rel = safe_relpath(info.filename)
        payload = archive.read(info)
        entries.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    entries.sort(key=lambda item: item["path"])
    return entries


def build_file_manifest_for_zip(path: Path | str) -> List[Dict[str, Any]]:
    with zipfile.ZipFile(path, "r") as archive:
        return build_file_manifest(archive)


def _short_list(items: Sequence[str], limit: int = 5) -> str:
    head = ", ".join(items[:limit])
    return head + ("…" if len(items) > limit else "")


def verify_file_manifest(
    archive: zipfile.ZipFile,
    expected: Optional[Sequence[FileEntry]],
    *,
    source: str = "升级包",
) -> Dict[str, Any]:
    """逐项校验 ZIP 内容与文件级清单，返回可写进 ledger 的摘要。

    清单缺失时同样拒绝：没有文件级清单就无法证明包内文件齐全。
    """
    if not expected:
        raise PackageValidationError(
            f"{source}缺少文件级清单（files）：无法确认包内文件齐全，已拒绝升级。"
            "请用新版 packaging/make_release.py 重新生成发布清单。"
        )

    wanted: Dict[str, FileEntry] = {}
    for entry in expected:
        if entry.path in wanted:
            raise PackageValidationError(
                f"{source}的文件级清单存在重复路径：{entry.path}"
            )
        wanted[entry.path] = entry

    actual: Dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        actual[safe_relpath(info.filename)] = info

    missing = sorted(set(wanted) - set(actual))
    extra = sorted(set(actual) - set(wanted))
    problems: List[str] = []
    if missing:
        problems.append(f"缺少 {len(missing)} 个文件（{_short_list(missing)}）")
    if extra:
        problems.append(f"多出 {len(extra)} 个未声明文件（{_short_list(extra)}）")

    checked_bytes = 0
    for rel in sorted(set(wanted) & set(actual)):
        entry = wanted[rel]
        info = actual[rel]
        if int(info.file_size) != entry.size:
            problems.append(
                f"{rel} 大小不符（清单 {entry.size}，实际 {info.file_size}）"
            )
            continue
        payload = archive.read(info)
        checked_bytes += len(payload)
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != entry.sha256:
            problems.append(
                f"{rel} 内容哈希不符（清单 {entry.sha256[:12]}…，实际 {actual_sha[:12]}…）"
            )

    if problems:
        raise PackageValidationError(
            f"{source}与文件级清单不一致，已拒绝升级（安装目录未做任何修改）："
            + "；".join(problems[:8])
            + ("；等" if len(problems) > 8 else "")
        )

    return {
        "schema": FILE_MANIFEST_SCHEMA,
        "files": len(wanted),
        "bytes": sum(entry.size for entry in wanted.values()),
        "checked_bytes": checked_bytes,
        "digest": manifest_digest(wanted.values()),
    }
