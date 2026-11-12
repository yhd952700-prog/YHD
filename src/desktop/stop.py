"""停止安装目录下正在运行的贾维斯桌面壳与网关进程（幂等）。

卸载器在进入文件删除阶段之前调用
``runtime\\python.exe -m src.desktop stop --root <安装目录>``。不先停，
``runtime\\pythonw.exe`` / ``python.exe`` 会被占用，卸载器删不掉，
表现为「卸载完成但服务还在后台跑」。

匹配条件（三条同时满足，避免误杀）：
  * 可执行文件位于 ``root`` 之下；
  * 文件名是 ``python.exe`` / ``pythonw.exe``；
  * 命令行包含贾维斯入口标记（``liuhao_desktop.py`` / ``launch_installed.py`` /
    ``https_proxy.py`` / ``-m src.desktop`` / ``-m src.gateway``）。

``node.exe`` 只在命令行同时出现 ``--vite`` 与本安装根时才算（开发/便携模式的
前端 dev server）；生产安装包不带 node，这条不会误伤系统里的其他 node。

停止后还会校验：安装根下是否仍有匹配进程、以及已知端口（``run/instance.json``
里的网关端口、带 ``--https`` 时的 8443）是否仍在 LISTEN。任一成立即
``stopped=False``，卸载器据此告警，而不是静默继续。

排除当前进程与父进程：卸载器 ``unins000.exe`` 本身不是 python，不会命中；
但 stop 命令自己是 ``-m src.desktop``，必须排除，否则会把自己杀掉。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

_MARKERS = (
    "liuhao_desktop.py",
    "launch_installed.py",
    # HTTPS 反代由 ``scripts/ops/https_proxy.py --https`` 拉起，旧清单漏了它，
    # 卸载后 8443 仍在 LISTEN。
    "https_proxy.py",
    "-m src.desktop",
    "-m src.gateway",
)
_EXECUTABLES = ("python.exe", "pythonw.exe")
_NODE_EXECUTABLES = ("node.exe",)
_HTTPS_PORT = 8443


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
    """终止 ``root`` 下属于贾维斯的 python/pythonw 进程；没有进程时直接返回。"""
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
        exe_name = exe_path.name.lower()
        cmdline = " ".join(info.get("cmdline") or [])
        if exe_name in _NODE_EXECUTABLES:
            # 只认「本安装根 + --vite」的 node；别的 node 进程一律不动。
            if "--vite" not in cmdline or str(base).lower() not in cmdline.lower():
                continue
        elif exe_name in _EXECUTABLES:
            if not _under(exe_path, base):
                continue
            if not any(marker in cmdline for marker in _MARKERS):
                continue
        else:
            continue
        matched.append({"pid": pid, "cmd": cmdline[:300]})
        victims.append(proc)

    if victims:
        # 外部停止也要在桌面日志里留一行"谁停的"：本进程 PID 就是调用者。
        # 写失败不影响停止/卸载流程。
        from .stop_log import append_stop_attribution

        append_stop_attribution(
            base / "logs" / "desktop.log",
            "external",
            caller_pid=os.getpid(),
        )

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

    ports = _candidate_ports(base, matched)
    listening = _still_listening(psutil, ports)

    return {
        "root": str(base),
        "matched": matched,
        "terminated": [item["pid"] for item in matched],
        "forced": forced,
        "remaining": remaining,
        "ports_checked": ports,
        "ports_still_listening": listening,
        "stopped": not remaining and not listening,
    }


def _candidate_ports(base: Path, matched: List[dict[str, Any]]) -> List[int]:
    """要复核的端口：run/instance.json 里的网关端口 + 出现过 --https 时的 8443。"""
    ports: List[int] = []
    for candidate in (base / "run" / "instance.json", base / "data" / "run" / "instance.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # 已停止或从未启动过，没有实例文件属正常
        except (OSError, ValueError) as exc:
            # 实例文件在但读不出来：端口未知，停止复核会少一道证据，
            # 可能把「仍有监听」误判成已停止。
            logger.warning(
                "读取实例文件失败（%s：%s）：无法取得网关端口，停止复核可能漏判监听",
                candidate,
                exc,
            )
            continue
        port = payload.get("port")
        if isinstance(port, int) and port > 0:
            ports.append(port)
    if any("--https" in item.get("cmd", "") for item in matched):
        ports.append(_HTTPS_PORT)
    return sorted(set(ports))


def _still_listening(psutil: Any, ports: List[int]) -> List[int]:
    if not ports:
        return []
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:  # noqa: BLE001 - 无权限时不能证明端口已释放，按未释放报
        return ports
    busy = {conn.laddr.port for conn in connections
            if conn.status == psutil.CONN_LISTEN and conn.laddr}
    return [port for port in ports if port in busy]


__all__ = ["stop_installed"]
