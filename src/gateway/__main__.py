"""Run the gateway, honouring ``$PORT``.

Why this module exists
----------------------
Every single-port host -- a container platform, the publishing sandbox, most
PaaS offerings -- does not let the application choose its port. It injects the
port through the environment and expects the process to bind it. A start
command with ``--port 8080`` baked in is silently undeployable there: the app
starts, listens on the wrong port, and the platform reports a failed health
check with no useful error.

So the port (and host) come from the environment:

    PORT   port to bind          (default 8080)
    HOST   interface to bind     (default 0.0.0.0 -- override to 127.0.0.1
                                  when the process should not be reachable
                                  from outside this machine)
    LOG_LEVEL  uvicorn log level (default info)
    LIUHAO_LOG_CONFIG  path to the UTF-8 logging YAML (installed builds point
                       this at their data directory; see ``logging_config``)

``0.0.0.0`` is the default because that is what a container or sandbox needs;
binding the loopback interface by default would make the image appear broken.

Both the Dockerfile and ``scripts/start_liuhao.py --single-port`` are thin
wrappers around this. The gateway serves the built console itself, so one
process really is the whole product (see ``src/gateway/console_static.py``).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from . import startup_profile as _startup_profile
from ..logging_config import load_uvicorn_log_config, resolve_log_config_path

logger = logging.getLogger(__name__)

_startup_profile.enable_from_env()
_startup_profile.enable_from_argv(sys.argv[1:])
_startup_profile.mark("入口模块导入完成（uvicorn 已导入）")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080

#: 唯一生效的日志配置（轮转参数见文件头注释）。
LOG_CONFIG = Path(__file__).resolve().parents[2] / "config" / "logging.yaml"
#: 配置里的 handler 用的是相对路径，先确保目录存在再交给 uvicorn。
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def resolve_bind() -> tuple:
    """Return ``(host, port)`` from the environment.

    A malformed ``PORT`` is a hard error rather than a silent fallback: a
    service that quietly listens somewhere other than where the platform told
    it to is far harder to diagnose than one that refuses to start.
    """
    host = (os.environ.get("HOST") or "").strip() or DEFAULT_HOST
    raw_port = (os.environ.get("PORT") or "").strip()
    if not raw_port:
        return host, DEFAULT_PORT
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit(f"PORT={raw_port!r} is not an integer")
    if not 1 <= port <= 65535:
        raise SystemExit(f"PORT={port} is outside the valid range 1-65535")
    return host, port


def resolve_timeout_options() -> dict:
    """Return uvicorn kwargs derived from ``server.timeouts``.

    Imported lazily so ``python -m src.gateway`` keeps working even if the
    runtime config is unreadable; in that case uvicorn's defaults apply and
    nothing silently claims otherwise.
    """
    try:
        from .main import (
            load_runtime_config,
            uvicorn_timeout_kwargs,
            unenforced_timeouts,
        )

        runtime = load_runtime_config()
    except Exception as exc:  # noqa: BLE001 - never block startup on config
        logger.warning("could not load server.timeouts: %s", exc)
        return {}
    options = uvicorn_timeout_kwargs(runtime)
    unenforced = unenforced_timeouts(runtime)
    if unenforced:
        logger.warning(
            "server.timeouts.%s are socket-level deadlines not exposed by "
            "uvicorn; they are NOT enforced by this process -- configure them "
            "at the reverse proxy / socket layer if required",
            "/".join(unenforced),
        )
    return options


def main() -> int:
    host, port = resolve_bind()
    log_level = (os.environ.get("LOG_LEVEL") or "info").strip().lower()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 读成映射：uvicorn 的 YAML 路径分支用 open() 不带 encoding，中文
    # Windows 下会按 cp936 解码 UTF-8 文件并直接崩（见 logging_config）。
    log_config = load_uvicorn_log_config(resolve_log_config_path(LOG_CONFIG))
    _startup_profile.mark("入口 main() 开始（解析 server.timeouts）")
    timeout_options = resolve_timeout_options()
    _startup_profile.mark("入口配置解析完成")
    print(f"鎏灏网关启动： http://{host}:{port}  (log_level={log_level})")
    _startup_profile.mark("uvicorn.run：绑定端口并运行 lifespan 启动")
    uvicorn.run(
        "src.gateway.main:app",
        host=host,
        port=port,
        log_level=log_level,
        log_config=log_config,
        **timeout_options,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
