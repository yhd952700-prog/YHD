"""敏感路径观察名单（**只记录，不拦截**）。

老板 2026-11-11 拍板：撤掉「审计库 / 日志 / 凭据 / 程序本体 / 全权配置对 AI
只读」这条限制。2026-11-11 第二轮进一步确认：**标准模式与全权模式一致放开**，
不再有「命中即拒绝」的分支。

现在的行为：

* AI 通过本程序的写路径（``FilesystemAdapter.execute(action="write")``）碰到
  这些路径 → 先写一条审计，然后**照常落盘**。
* ``host_command`` 的命令字符串点名这些路径 → 先写一条审计，然后**照常走**
  策略/确认/执行管线。
* 用户本人（``actor="human"``）不写这类记录。

**老实说清能力边界**（对外报告必须照这个口径写，不许含糊）：

* 本模块只负责「认出这是敏感路径」并交给审计，**不再构成任何权限边界**。
* ``host_command`` 的识别只是命令字符串包含判断：路径被拼接/编码，或由子进程
  自己算出（``"aud" + "it_store.db"``、``python -c`` 里再构造），我们看不到。
* 真正的文件系统级保护需要 OS 过滤驱动（minifilter），或让 AI 跑在另一个受限
  账户下 —— 两者当前架构都没有。不要对外声称"文件系统层已拦死"。
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 数据目录下这些**文件名**属于敏感路径（含 -wal/-shm/-journal 兄弟文件）。
#: 命中只写审计，不再拦截。
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

#: 数据目录下这些**子目录**整棵属于敏感路径（审计日志）。
_PROTECTED_DIR_NAMES = frozenset({"logs", "audit_logs"})

#: 安装目录下这些子目录与扩展名属于敏感路径（程序本体）。
_PROTECTED_INSTALL_DIRS = frozenset({"runtime", "src"})
_PROTECTED_INSTALL_EXTS = frozenset({".exe", ".dll"})


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

    # 1) 审计日志目录（整棵属于敏感路径）
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


def note_write(
    path: str | os.PathLike[str] | None,
    *,
    actor: str = "ai",
    correlation_id: str | None = None,
    operation: str = "write",
) -> str | None:
    """AI 写/删前调用：命中敏感路径就写一条审计，然后**照常放行**。

    返回命中的原因码（未命中返回 ``None``），供调用方记录/测试断言；
    **绝不抛异常，也不改变调用方的执行决定**。审计写失败只记日志。
    用户本人的手动操作不经过这里（路由层已区分主体）。
    """
    reason = protected_reason(path)
    if reason is None:
        return None
    from .authority_audit import record_protected_touched

    try:
        record_protected_touched(
            path=str(path),
            reason=reason,
            actor=actor,
            correlation_id=correlation_id,
            channel="filesystem",
            operation=operation,
        )
    except Exception as exc:  # noqa: BLE001 - 只记录，审计故障不改变放行
        logger.warning(
            "sensitive-path audit write failed path=%s reason=%s: %s",
            path,
            reason,
            exc,
        )
    return reason


def scan_command(command: str) -> str | None:
    """命令字符串里是否点名了敏感路径（**识别层，命中也只记录**）。

    这是包含判断，不是文件系统钩子：路径被拼接/编码或由子进程自己算出时
    识别不到。命中后由调用方写审计，命令继续执行。
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
            "file_api": "record-only (FilesystemAdapter.write)",
            "host_command": "command-string scan, record-only; computed paths are invisible",
            "os_level": "none (no minifilter, no restricted account)",
            "blocks_anything": False,
        },
    }
