"""全权模式下也不许 AI 动的东西（路径级硬规则）。

老板 2026-11-11 定的四类路径，对 AI 任务（含 SYSTEM_PRINCIPAL、员工、定时任务）
**只读**：删除/覆盖一律拒绝并写审计。用户本人的手动操作不受影响 —— 这条只约束
AI。

**老实说清能力边界**（对外报告必须照这个口径写，不许含糊）：

* 走本程序自己的写路径（``FilesystemAdapter.execute(action="write")``、
  ``file_write`` 工具、所有经 ``resolve_in_workspace`` 的写入）是**真的**拦住：
  落盘前检查真实路径（realpath，``..`` 与符号链接已展开），绕不过去。
* 走 ``host_command`` 的任意子进程（``python -c``、``curl | powershell``、
  自己拉起来的 exe）**只在命令字符串层拦**：命令里出现受保护路径就拒绝，但路径
  若是拼出来的（``"aud" + "it_store.db"``）或由子进程自己算出，我们看不到。
* 真正的文件系统级拦截需要 OS 过滤驱动（minifilter），或让 AI 跑在另一个受限
  账户下 —— 两者当前架构都没有。不要对外声称"文件系统层已拦死"。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 数据目录下这些**文件名**一律只读（含 -wal/-shm/-journal 兄弟文件）。
_PROTECTED_NAMES = frozenset(
    {
        "audit_store.db",
        "audit_chain.key",
        "auth_secrets.json",
        "master.key",
        "integrity.key",
        "model_config.key",
        "authority_mode.json",
        "host_command_confirm.json",
        "models.json",
    }
)

#: 数据目录下这些**子目录**整棵只读（审计日志）。
_PROTECTED_DIR_NAMES = frozenset({"logs", "audit_logs"})

#: 安装目录下这些子目录与扩展名只读（程序本体）。
_PROTECTED_INSTALL_DIRS = frozenset({"runtime", "src"})
_PROTECTED_INSTALL_EXTS = frozenset({".exe", ".dll"})


class ProtectedPathError(PermissionError):
    """AI 试图写入/删除受保护路径。"""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"protected path: {reason} ({path})")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class _Roots:
    data: str
    install: str


def _roots() -> _Roots:
    from ..common.data_paths import data_root

    install = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _REPO_ROOT
    try:
        data = str(Path(data_root()).resolve())
    except Exception:  # pragma: no cover - 数据根不可用时按仓库内 data/ 兜底
        data = str((_REPO_ROOT / "data").resolve())
    # 两边都归一化成同一种大小写/分隔符，否则 Windows 上 "D:\\" 与 "d:\\" 会被判成
    # 两个不同前缀，保护规则静默失效。
    return _Roots(data=_norm(data), install=_norm(install))


def _norm(path: str | os.PathLike[str]) -> str:
    raw = os.fspath(path)
    try:
        return os.path.normcase(os.path.realpath(raw))
    except OSError:  # pragma: no cover - 极端路径
        return os.path.normcase(os.path.abspath(raw))


def _under(child: str, parent: str) -> bool:
    child = child.rstrip("\\/")
    parent = parent.rstrip("\\/")
    return child == parent or child.startswith((parent + os.sep, parent + "/"))


def _strip_db_suffix(name: str) -> str:
    for suffix in ("-wal", "-shm", "-journal"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def protected_reason(path: str | os.PathLike[str] | None) -> str | None:
    """返回受保护原因；不是受保护路径则 ``None``。

    判定只看**真实路径**，且只在两个根之内生效（数据目录 / 安装目录）—— 世界上
    任何一个叫 ``master.key`` 的文件都不该被这条规则误伤。
    """
    if not path:
        return None
    real = _norm(path)
    roots = _roots()

    # 1) 审计日志目录（整棵只读）
    for name in _PROTECTED_DIR_NAMES:
        if _under(real, os.path.join(roots.data, name)):
            return "audit-or-key-material"

    # 2) 审计库 / 密钥 / 模式配置（含 SQLite 的 -wal/-shm/-journal 兄弟文件）
    base = _strip_db_suffix(os.path.basename(real))
    if base in _PROTECTED_NAMES and (
        _under(real, roots.data) or _under(real, roots.install)
    ):
        return "audit-or-key-material"

    # 3) 程序本体：安装目录下的 runtime/、src/、*.exe、*.dll
    if _under(real, roots.install):
        install_parts = Path(roots.install).parts
        tail = Path(real).parts[len(install_parts) :]
        if tail and tail[0].lower() in _PROTECTED_INSTALL_DIRS:
            return "application-body"
        if os.path.splitext(real)[1].lower() in _PROTECTED_INSTALL_EXTS:
            return "application-body"
    return None


def guard_write(
    path: str | os.PathLike[str] | None,
    *,
    actor: str = "ai",
    correlation_id: str | None = None,
) -> None:
    """AI 写/删前调用。受保护则写审计并抛 :class:`ProtectedPathError`。

    用户本人的手动操作不走这里（路由层已区分主体），因此不受限制。
    """
    reason = protected_reason(path)
    if reason is None:
        return
    from .authority_audit import record_protected_denied

    record_protected_denied(
        path=str(path),
        reason=reason,
        actor=actor,
        correlation_id=correlation_id,
        channel="filesystem",
    )
    raise ProtectedPathError(str(path), reason)


def command_mentions_protected(command: str) -> str | None:
    """命令字符串里是否点名了受保护路径（**解析层**，绕过成本不为零但不难）。

    命中即拒绝：这些路径没有"命令式操作"的正当场景，宁可多拦。
    """
    if not command:
        return None
    lowered = command.lower().replace("/", "\\")
    roots = _roots()
    data = roots.data.lower().replace("/", "\\").rstrip("\\")
    install = roots.install.lower().replace("/", "\\").rstrip("\\")

    if data and data in lowered:
        return "audit-or-key-material"
    if install and install in lowered and any(
        token in lowered for token in ("\\.exe", "\\.dll", "\\src\\", "\\runtime\\")
    ):
        return "application-body"

    for name in _PROTECTED_NAMES:
        if name in lowered:
            return "audit-or-key-material"
    for token in ("\\src\\", "\\runtime\\"):
        if token in lowered:
            return "application-body"
    return None


def describe() -> dict:
    roots = _roots()
    return {
        "data_root": roots.data,
        "install_root": roots.install,
        "protected_names": sorted(_PROTECTED_NAMES),
        "protected_dirs": sorted(_PROTECTED_DIR_NAMES | _PROTECTED_INSTALL_DIRS),
        "protected_extensions": sorted(_PROTECTED_INSTALL_EXTS),
        "enforcement": {
            "file_api": "blocking (FilesystemAdapter.write / file_write)",
            "host_command": "command-string scan only; computed paths can bypass",
            "os_level": "none (no minifilter, no restricted account)",
        },
    }
