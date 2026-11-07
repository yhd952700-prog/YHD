"""Windows ACL / 安全删除的共用实现。

恢复码交接文件、模型主密钥文件、本机恢复证明命名管道都要求“只有当前
Windows 用户可访问”。Windows 的 ``chmod`` 只等于只读位，不能当作访问
控制；这里统一用显式 DACL（去掉继承、只保留当前用户）并在写入后**读回
DACL** 复核，而不是“调用没报错就算成功”。

POSIX 平台退回 ``0600``，并同样读回 mode 位复核。
"""
from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("liuhao.common.windows_acl")


def current_user_sid() -> Any:
    """返回当前进程 token 的用户 SID（仅 Windows）。"""
    import win32api
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_ALL_ACCESS
    )
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        try:
            token.Close()
        except Exception:  # noqa: BLE001
            pass


def current_user_sid_string() -> str:
    """当前用户 SID 的字符串形式；非 Windows 返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import win32security

        return win32security.ConvertSidToStringSid(current_user_sid())
    except Exception:  # noqa: BLE001
        return ""


def tighten_current_user_only(path: Path) -> bool:
    """把文件/目录收紧为“仅当前用户”，返回实际 DACL 是否复核通过。"""
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            return (stat.S_IMODE(path.stat().st_mode) & 0o077) == 0
        except OSError:
            return False
    try:
        import ntsecuritycon
        import win32security

        sid = current_user_sid()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        flags = win32security.DACL_SECURITY_INFORMATION
        flags |= getattr(win32security, "PROTECTED_DACL_SECURITY_INFORMATION", 0x80000000)
        win32security.SetFileSecurity(str(path), flags, descriptor)
        return acl_is_current_user_only(path)
    except Exception:  # noqa: BLE001 - ACL 失败必须按失败处理
        logger.warning("收紧 ACL 失败：%s", path, exc_info=True)
        return False


def acl_is_current_user_only(path: Path) -> bool:
    """读回实际 DACL，判断是否“只有当前用户、无继承、无其他主体”。"""
    if os.name != "nt":
        try:
            return (stat.S_IMODE(path.stat().st_mode) & 0o077) == 0
        except OSError:
            return False
    try:
        import win32security

        wanted = win32security.ConvertSidToStringSid(current_user_sid())
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        return dacl_is_current_user_only(
            descriptor.GetSecurityDescriptorDacl(), wanted
        )
    except Exception:  # noqa: BLE001
        return False


def dacl_entries(dacl: Any) -> list[dict[str, Any]]:
    """把 DACL 展开成可断言的 ACE 列表（只读，供测试与自检）。"""
    if os.name != "nt" or dacl is None:
        return []
    import win32security

    entries: list[dict[str, Any]] = []
    for index in range(dacl.GetAceCount()):
        (ace_type, ace_flags), mask, ace_sid = dacl.GetAce(index)
        entries.append(
            {
                "type": int(ace_type),
                "flags": int(ace_flags),
                "mask": int(mask),
                "sid": win32security.ConvertSidToStringSid(ace_sid),
            }
        )
    return entries


def dacl_is_current_user_only(dacl: Any, wanted_sid: Optional[str] = None) -> bool:
    """对已取出的 DACL 做“只有当前用户”的断言，供文件与命名管道共用。"""
    if os.name != "nt":
        return False
    if dacl is None:
        return False
    try:
        import win32security

        wanted = wanted_sid or win32security.ConvertSidToStringSid(current_user_sid())
        inherited_flag = getattr(win32security, "INHERITED_ACE", 0x10)
        allowed_type = getattr(win32security, "ACCESS_ALLOWED_ACE_TYPE", 0)
        entries = dacl_entries(dacl)
        if not entries:
            return False
        for entry in entries:
            if entry["type"] != allowed_type:
                return False
            if entry["flags"] & inherited_flag:
                return False
            if entry["sid"] != wanted:
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


def secure_delete_file(path: Path, *, passes: int = 1) -> bool:
    """覆盖内容后再删除，避免密钥/密文残留在可恢复的扇区里。

    对普通文件做定长覆盖 + ``fsync`` + 删除；文件不存在视为已删除。
    返回值表示“文件最终不存在”，而不是“曾经写成功”。
    """
    target = Path(path)
    try:
        if not target.exists():
            return True
        size = target.stat().st_size
        if size > 0:
            with target.open("r+b", buffering=0) as handle:
                for _ in range(max(1, passes)):
                    handle.seek(0)
                    remaining = size
                    while remaining > 0:
                        chunk = min(remaining, 64 * 1024)
                        handle.write(secrets.token_bytes(chunk))
                        remaining -= chunk
                    handle.flush()
                    os.fsync(handle.fileno())
        target.unlink(missing_ok=True)
        return not target.exists()
    except OSError:
        logger.warning("安全删除文件失败：%s", target, exc_info=True)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return not target.exists()
