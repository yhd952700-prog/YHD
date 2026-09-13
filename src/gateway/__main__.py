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

``0.0.0.0`` is the default because that is what a container or sandbox needs;
binding the loopback interface by default would make the image appear broken.

Both the Dockerfile and ``scripts/start_liuhao.py --single-port`` are thin
wrappers around this. The gateway serves the built console itself, so one
process really is the whole product (see ``src/gateway/console_static.py``).
"""
from __future__ import annotations

import logging
import os

import uvicorn

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


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


def main() -> int:
    host, port = resolve_bind()
    log_level = (os.environ.get("LOG_LEVEL") or "info").strip().lower()
    print(f"鎏灏网关启动： http://{host}:{port}  (log_level={log_level})")
    uvicorn.run(
        "src.gateway.main:app",
        host=host,
        port=port,
        log_level=log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
