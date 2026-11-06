"""网关子进程入口：把「停止」变成 uvicorn 的优雅收尾。

为什么需要这一层
----------------
Windows 的 ``TerminateProcess``（``Popen.terminate()``）没有可捕获的退出信号，
被结束的进程来不及 flush 或关库。uvicorn 又没有"停止"HTTP 端点。因此桌面壳
不直接杀进程，而是**往本进程 stdin 写一行** ``shutdown``：

  1. 本模块用 ``uvicorn.Config`` 拉起与启动器相同的 ASGI 入口
     ``src.gateway.main:app``（不复制任何网关代码）；
  2. 守护线程读 stdin，收到 ``shutdown`` 或读到 EOF（父进程消失/崩溃）时
     置 ``server.should_exit = True``；
  3. uvicorn 随即停止接收新请求、等待在途请求结束
     （``GRACEFUL_INSIDE`` 秒上限，给父进程的 ``GRACEFUL_TIMEOUT`` 留量），
     再执行 lifespan shutdown —— 网关在这里 flush、关库；
  4. 进程自行退出，父进程拿到真实退出码，不会产生孤儿进程。

本模块只被 ``src.desktop.service`` 以 ``python -m src.desktop._runner`` 拉起。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Any

import uvicorn

from ..common.instance_lock import (
    InstanceGuardUnavailable,
    InstanceLock,
    InstanceLockBusy,
    instance_root,
)
from ..common.parent_watch import watch_parent
from ..logging_config import ensure_utf8_streams, load_uvicorn_log_config
from .service import GRACEFUL_INSIDE

#: 父进程管道里可接受的停止指令（大小写不敏感）。
SHUTDOWN_WORDS = frozenset({"shutdown", "stop", "quit"})

#: 默认 ASGI 入口（与 ``scripts/start_liuhao.py`` 相同）。
DEFAULT_APP = "src.gateway.main:app"


def _watch_stdin(server: uvicorn.Server) -> None:
    """等父进程的停止指令；EOF 同样视为停止（父进程已消失，不做孤儿）。"""
    try:
        for line in sys.stdin:
            if line.strip().lower() in SHUTDOWN_WORDS:
                break
    finally:
        server.should_exit = True


def build_config(
    host: str,
    port: int,
    log_config: str | None = None,
    app: str = DEFAULT_APP,
) -> uvicorn.Config:
    kwargs: dict[str, Any] = {}
    if log_config:
        # 传映射而不是路径：uvicorn 的 YAML 分支用 open() 不带 encoding，
        # 中文 Windows 下会把 UTF-8 的 logging.yaml 按 cp936 解码并崩溃。
        kwargs["log_config"] = load_uvicorn_log_config(log_config)
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        timeout_graceful_shutdown=GRACEFUL_INSIDE,
        **kwargs,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.desktop._runner")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="绑定地址（默认 127.0.0.1，只本机可达；对外需显式指定）",
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-config", default=None, help="uvicorn 日志配置路径（通常为 config/logging.yaml）")
    parser.add_argument("--app", default=DEFAULT_APP, help="ASGI 应用路径（测试时可替换）")
    args = parser.parse_args(argv)

    # 让网关应用层（Host 白名单）知道真实绑定地址：显式对外时该地址必须
    # 被允许，否则启动成功但每个请求都会因 Host 校验被 400。
    os.environ["LIUHAO_LISTEN_HOST"] = args.host
    os.environ["HOST"] = args.host

    ensure_utf8_streams()

    # 与 ``python -m src.gateway`` 共用同一把锁：托盘壳和命令行各自拉起网关时，
    # 第二个进程必须在这里被挡住，不能先绑上端口再和第一个进程写同一份数据。
    guard = InstanceLock(instance_root(Path.cwd()))
    try:
        guard.acquire()
    except InstanceLockBusy as busy:
        print(f"[instance] {busy.describe()}", file=sys.stderr, flush=True)
        return 3
    except InstanceGuardUnavailable as exc:
        print(f"[instance] {exc}", file=sys.stderr, flush=True)
        return 3
    try:
        from datetime import datetime

        guard.publish(
            pid=os.getpid(),
            url=f"http://{args.host}:{args.port}",
            host=args.host,
            port=args.port,
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        server = uvicorn.Server(build_config(args.host, args.port, args.log_config, args.app))
        threading.Thread(
            target=_watch_stdin,
            args=(server,),
            name="liuhao-shutdown-watch",
            daemon=True,
        ).start()
        # 托盘壳崩溃/被强杀时，网关随之优雅退出（否则孤儿进程会一直占着端口）。
        watch_parent(server)
        server.run()
    finally:
        guard.clear_metadata()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
