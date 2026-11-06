"""父进程看护：启动器/托盘的父进程没了，网关也要跟着退。

为什么需要它
------------
抖音式"双击快捷方式"的真实退出方式是：用户直接关掉控制台窗口，或任务管理器
结束启动器。Windows 不会因为父进程死亡而杀掉子进程 —— 网关会变成孤儿，
继续占着端口、继续持有单实例锁。用户再双击时只会看到"已经在运行（pid=…）"，
却找不到那个窗口去关它。

父进程在拉起网关时把自己的 pid 传进来（``LIUHAO_PARENT_PID``）；这里用
``psutil`` 等待父进程退出，一旦退出就请求 uvicorn 优雅停机。没有父进程信息
时（手动 ``python -m src.gateway``、测试、系统服务）什么都不做。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

PARENT_PID_ENV = "LIUHAO_PARENT_PID"


def watch_parent(
    server: Any,
    *,
    parent_pid: int | None = None,
    interval: float = 2.0,
    on_exit: Callable[[], None] | None = None,
) -> threading.Thread | None:
    """在后台线程里等父进程退出，然后让 ``server`` 优雅停机。

    返回线程（便于测试等待）；没有可看护的父进程时返回 ``None``。
    """
    pid = parent_pid
    if pid is None:
        raw = (os.environ.get(PARENT_PID_ENV) or "").strip()
        try:
            pid = int(raw) if raw else None
        except ValueError:
            pid = None
    if not pid or pid == os.getpid():
        return None

    def _stop() -> None:
        if on_exit is not None:
            on_exit()
            return
        server.should_exit = True

    def _run() -> None:
        try:
            import psutil
        except ImportError:  # 精简运行时：退化为「不处理」，绝不反向拖死网关
            return
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            _stop()
            return
        while True:
            try:
                if parent.wait(timeout=interval) is not None:
                    break
            except psutil.TimeoutExpired:
                continue
            except psutil.Error:
                break
        _stop()

    thread = threading.Thread(target=_run, name="liuhao-parent-watch", daemon=True)
    thread.start()
    return thread


__all__ = ["PARENT_PID_ENV", "watch_parent"]
