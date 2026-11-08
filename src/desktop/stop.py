"""停止安装目录下正在运行的鎏灏桌面壳与网关进程（幂等）。

卸载器在进入文件删除阶段之前调用
``runtime\\python.exe -m src.desktop stop --root <安装目录>``。不先停，
``runtime\\pythonw.exe`` / ``python.exe`` 会被占用，卸载器删不掉，
表现为「卸载完成但服务还在后台跑」。

匹配条件（三条同时满足，避免误杀）：
  * 可执行文件位于 ``root`` 之下；
  * 文件名是 ``python.exe`` / ``pythonw.exe``；
  * 命令行包含鎏灏入口标记（``liuhao_desktop.py`` / ``launch_installed.py`` /
    ``-m src.desktop`` / ``-m src.gateway``）。

排除当前进程与父进程：卸载器 ``unins000.exe`` 本身不是 python，不会命中；
但 stop 命令自己是 ``-m src.desktop``，必须排除，否则会把自己杀掉。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, List

_MARKERS = (
    "liuhao_desktop.py",
    "launch_installed.py",
    "-m src.desktop",
    "-m src.gateway",
)
_EXECUTABLES = ("python.exe", "pythonw.exe")


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def stop_installed(
    root: str | Path | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """终止 ``root`` 下属于鎏灏的 python/pythonw 进程；没有进程时直接返回。"""
    try:
        import psutil
    except Exception as exc:  # noqa: BLE001 - 打包环境缺 psutil 时如实报错
        return {
            "root": str(root),
            "stopped": False,
            "error": f"psutil 不可用：{type(exc).__name__}: {exc}",
        }

    base = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    me = os.getpid()
    parent = os.getppid()
    matched: List[dict[str, Any]] = []
    victims: List[Any] = []

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        info = proc.info
        pid = int(info.get("pid") or 0)
        if pid <= 0 or pid in (me, parent):
            continue
        exe = info.get("exe") or ""
        if not exe:
            continue
        exe_path = Path(exe)
        if exe_path.name.lower() not in _EXECUTABLES:
            continue
        if not _under(exe_path, base):
            continue
        cmdline = " ".join(info.get("cmdline") or [])
        if not any(marker in cmdline for marker in _MARKERS):
            continue
        matched.append({"pid": pid, "cmd": cmdline[:300]})
        victims.append(proc)

    for proc in victims:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - 单个进程拒绝退出不影响继续
            pass

    deadline = time.monotonic() + max(timeout, 0.0)
    while victims and time.monotonic() < deadline:
        alive = []
        for proc in victims:
            try:
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    alive.append(proc)
            except Exception:  # noqa: BLE001 - 已退出 / 句柄失效
                pass
        victims = alive
        if victims:
            time.sleep(0.1)

    forced: List[int] = []
    for proc in victims:
        try:
            proc.kill()
            forced.append(proc.pid)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass

    remaining: List[int] = []
    for proc in victims:
        try:
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                remaining.append(proc.pid)
        except Exception:  # noqa: BLE001
            pass

    return {
        "root": str(base),
        "matched": matched,
        "terminated": [item["pid"] for item in matched],
        "forced": forced,
        "remaining": remaining,
        "stopped": not remaining,
    }


__all__ = ["stop_installed"]
