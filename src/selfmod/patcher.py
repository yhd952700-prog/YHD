"""Patch generation (diff) and application to a copy.

A proposal is a list of whole-file edits. The unified diff is generated with
``difflib`` from the *current* bytes on disk, which is what the human reviews.
Base hashes are stored so the service can refuse a stale proposal later.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.common.atomic_write import atomic_write_text

from .errors import InvalidEdit
from .models import FileEdit
from .paths import PathPolicy


def read_text(path: Path, *, max_bytes: int) -> Optional[str]:
    if not path.exists():
        return None
    if path.is_dir():
        raise InvalidEdit(f"目标不是普通文件：{path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise InvalidEdit(f"文件过大，拒绝生成补丁（{size} > {max_bytes} 字节）：{path}")
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidEdit(f"文件不是 UTF-8 文本，拒绝自改：{path}") from exc


def hash_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_diff(
    edits: Sequence[FileEdit],
    policy: PathPolicy,
    *,
    max_file_bytes: int,
    max_diff_bytes: int = 4 * 1024 * 1024,
) -> Tuple[str, Dict[str, Optional[str]]]:
    """Return ``(unified_diff_text, base_hashes)`` without touching the workspace."""
    chunks: List[str] = []
    base_hashes: Dict[str, Optional[str]] = {}
    seen: set[str] = set()
    total = 0
    for edit in edits:
        rel = policy.normalise(edit.path)
        if rel in seen:
            raise InvalidEdit(f"同一路径被重复修改：{rel}")
        seen.add(rel)
        path = policy.workspace / rel
        old = read_text(path, max_bytes=max_file_bytes)
        if old is None and edit.content is None:
            raise InvalidEdit(f"试图删除不存在的文件：{rel}")
        if edit.content is not None:
            try:
                edit.content.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise InvalidEdit(f"新内容不是合法 UTF-8：{rel}") from exc
        base_hashes[rel] = hash_text(old)
        diff_lines = difflib.unified_diff(
            (old or "").splitlines(),
            (edit.content or "").splitlines(),
            fromfile=f"a/{rel}" if old is not None else "/dev/null",
            tofile=f"b/{rel}" if edit.content is not None else "/dev/null",
            lineterm="",
        )
        text = "\n".join(diff_lines)
        total += len(text)
        if total > max_diff_bytes:
            raise InvalidEdit(f"补丁过大（>{max_diff_bytes} 字节），拒绝生成")
        chunks.append(text)
    return "\n".join(chunk for chunk in chunks if chunk), base_hashes


def render(proposal) -> str:
    def value(key, default=None):
        if isinstance(proposal, dict):
            return proposal.get(key, default)
        return getattr(proposal, key, default)

    status = value("status")
    status_text = getattr(status, "value", status)
    command = value("test_command")
    header = [
        f"提案 {value('id')} [{status_text}]",
        f"标题：{value('title')}",
        f"理由：{value('rationale')}",
        f"测试命令：{' '.join(command) if command else '未配置'}",
        "",
    ]
    return "\n".join(header) + (value("diff", "") or "（无差异）")


def apply_edits(
    tree_root: Path,
    edits: Iterable[FileEdit],
    policy: PathPolicy,
) -> List[str]:
    """Apply edits atomically inside ``tree_root`` (never the real workspace here)."""
    copy_policy = PathPolicy(tree_root, blocked_prefixes=policy.blocked_prefixes)
    applied: List[str] = []
    for edit in edits:
        rel = copy_policy.normalise(edit.path)
        target = copy_policy.workspace / rel
        if edit.content is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write_text(target, edit.content)
        applied.append(rel)
    return applied
