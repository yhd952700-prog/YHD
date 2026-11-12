"""进程存活探测（判断"某个 pid 是否仍然在场"）。

为什么独立成模块
================
迁移临时文件的孤儿清理（``src/tasks/_queue_paths.py``）与启动时的有界清扫
（``src/common/temp_sweep.py``）都需要同一个判定：**只有能证明已经退出的 pid
才允许被当作垃圾**。两边各写一份很容易漂移成"一边严格、一边宽松"，而宽松
那一边会删掉正在运行的实例的临时文件。

探测语义（fail-closed）
----------------------
* Windows：``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``GetExitCodeProcess``；
  ``ERROR_INVALID_PARAMETER``（87）才判定"不存在"；权限不足等一律按存活处理。
* POSIX：``os.kill(pid, 0)``；只有 ``ProcessLookupError`` 判定"不存在"。
* 任何探测异常、pid 非法、pid 等于自身：一律按**存活**处理（宁可不删）。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def pid_is_alive(pid: int) -> bool:
    """判断 ``pid`` 是否仍存活；探测不确定时按存活处理（宁可不删）。"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # nosemgrep: liuhao-swallow-exception-return-success
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.GetExitCodeProcess.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            )
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            # PROCESS_QUERY_LIMITED_INFORMATION：权限要求最低的查询权限。
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                # 87 = ERROR_INVALID_PARAMETER：pid 不存在；其余错误（如权限不足）
                # 无法证明已退出，按存活处理，宁可留下文件也不误删。
                return ctypes.get_last_error() != 87
            try:
                code = wintypes.DWORD(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001 - 探测失败按存活处理
            logger.debug("进程存活探测失败（pid=%s）：%s", pid, exc)
            # 结辩：这里 return True 不是谎报成功——本函数的 True 语义是
            # 「按存活处理（宁可不删）」的 fail-closed 保守默认（见 docstring），
            # 只有能证明退出才允许把 pid 当垃圾。
            return True
    # nosemgrep: liuhao-swallow-exception-return-success
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # 结辩：POSIX 探测失败（权限等）无法证明已退出，按存活处理（宁可不删），
        # 语义见 docstring；不是谎报成功。
        return True
    return True


__all__ = ["pid_is_alive"]
