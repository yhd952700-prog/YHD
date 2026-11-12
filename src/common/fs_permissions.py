"""文件访问权限的统一入口（Windows ACL / POSIX 0600）。

为什么需要这个模块
------------------
Windows 的 ``os.chmod(mode)`` 只能切换只读位，**不构成访问控制**：文件实际
继承的是父目录 DACL。``%TEMP%`` 之类的目录默认带 ``BUILTIN\\Users:RX``，
把主密钥、审计链密钥写在里面且只做 ``chmod(0o600)``，等于把密钥暴露给本机
所有用户。

本模块是唯一入口：

* ``restrict_to_current_user``：去掉继承、只授予当前用户，并**读回复核**；
* ``is_restricted_to_current_user``：只读检查，用于决定是否需要收紧；
* ``ensure_restricted_to_current_user``：读取路径上的自愈——已受保护时零成本
  返回，未受保护才收紧。老装机用户的历史文件因此在下次启动时自动修复。

Windows 侧必须用 ``SetNamedSecurityInfo`` 而不是 ``SetFileSecurity``：
实测后者即使传入 ``PROTECTED_DACL_SECURITY_INFORMATION``，读回的
``SE_DACL_PROTECTED`` 仍未置位（control=``0x8004``），只是把显式 ACE 写了
进去；一旦父目录 ACL 再次传播，继承条目仍会回来。只有前者能同时做到
「替换 DACL」+「保护 DACL」。

POSIX 平台退回 ``0600`` 并复核 mode 位。
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from .windows_acl import dacl_is_current_user_only

logger = logging.getLogger("liuhao.common.fs_permissions")


def restrict_to_current_user(path: Path) -> bool:
    """把文件/目录收紧为「仅当前用户可访问」，返回实际 ACL 的复核结果。"""
    target = Path(path)
    if os.name != "nt":
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
            return (stat.S_IMODE(target.stat().st_mode) & 0o077) == 0
        except OSError:
            logger.warning("收紧文件权限失败：%s", target, exc_info=True)
            return False
    try:
        import ntsecuritycon
        import win32api
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_ALL_ACCESS
        )
        try:
            sid = win32security.GetTokenInformation(
                token, win32security.TokenUser
            )[0]
        finally:
            token.Close()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
        win32security.SetNamedSecurityInfo(
            str(target),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
    except Exception:  # noqa: BLE001 - ACL 失败必须按失败处理
        logger.warning("收紧文件 ACL 失败：%s", target, exc_info=True)
        return False
    return is_restricted_to_current_user(target)


def is_restricted_to_current_user(path: Path) -> bool:
    """只读检查：DACL 已保护、且只剩当前用户一条允许 ACE。"""
    target = Path(path)
    if os.name != "nt":
        try:
            return (stat.S_IMODE(target.stat().st_mode) & 0o077) == 0
        except OSError:
            return False
    try:
        import win32security

        descriptor = win32security.GetFileSecurity(
            str(target), win32security.DACL_SECURITY_INFORMATION
        )
        control = descriptor.GetSecurityDescriptorControl()[0]
        if not control & win32security.SE_DACL_PROTECTED:
            return False
        return dacl_is_current_user_only(descriptor.GetSecurityDescriptorDacl())
    except Exception:  # noqa: BLE001 - 读不到就按“未收紧”处理
        return False


def ensure_restricted_to_current_user(path: Path) -> bool:
    """读取路径上的自愈：未受保护就收紧，已受保护则不产生额外写入。

    返回最终是否处于「仅当前用户」状态。文件不存在时返回 ``False``（不创建）。
    收紧失败只记录告警，由调用方决定是否继续——密钥已经落盘，读不到才是更大
    的故障。
    """
    target = Path(path)
    try:
        if not target.exists():
            return False
        if is_restricted_to_current_user(target):
            return True
    except OSError:
        return False
    tightened = restrict_to_current_user(target)
    if tightened:
        logger.info("已自动收紧文件权限为仅当前用户：%s", target)
    else:
        logger.warning("文件权限仍非仅当前用户（收紧失败）：%s", target)
    return tightened
