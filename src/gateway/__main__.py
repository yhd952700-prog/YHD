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
    HOST   interface to bind     (default 127.0.0.1 -- loopback only)
    LOG_LEVEL  uvicorn log level (default info)
    LIUHAO_LOG_CONFIG  path to the UTF-8 logging YAML (installed builds point
                       this at their data directory; see ``logging_config``)

``127.0.0.1`` is the default because this is a desktop product first: the
gateway must not be reachable from the LAN unless the operator asks for it.
A container or single-port platform that genuinely needs external reachability
sets ``HOST=0.0.0.0`` **explicitly** (the Dockerfile does exactly that), and
startup prints a loud warning when a non-loopback interface is bound.

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
from ..common.instance_lock import (
    InstanceGuardUnavailable,
    InstanceLock,
    InstanceLockBusy,
    instance_root,
)
from ..common.parent_watch import watch_parent
from ..logging_config import load_uvicorn_log_config, resolve_log_config_path

logger = logging.getLogger(__name__)

_startup_profile.enable_from_env()
_startup_profile.enable_from_argv(sys.argv[1:])
_startup_profile.mark("入口模块导入完成（uvicorn 已导入）")

#: 默认只绑回环：本机可达、局域网不可达。对外暴露必须是显式选择（HOST=0.0.0.0
#: 或 HOST=<网卡地址>），并在启动日志里给出醒目警告。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

#: 唯一生效的日志配置（轮转参数见文件头注释）。
LOG_CONFIG = Path(__file__).resolve().parents[2] / "config" / "logging.yaml"
#: 配置里的 handler 用的是相对路径，先确保目录存在再交给 uvicorn。
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


#: 通配绑定：服务端用它监听所有网卡，但浏览器打不开这种地址。
_WILDCARD_HOSTS = ("0.0.0.0", "::", "[::]", "::0")


def display_host(host: str) -> str:
    """控制台展示用地址：取自实际生效的绑定配置，只把通配地址映射成回环。

    真正监听的仍是 ``host``；这里既不打印打不开的 URL，也不在打印处写死
    字面量（否则将来默认地址一改，控制台就和实际绑定不一致）。
    """
    value = (host or "").strip()
    return "127.0.0.1" if value in _WILDCARD_HOSTS else value


def resolve_log_dir(config: dict | None = None) -> Path:
    """返回「实际生效」的日志目录（取 handler.filename 的父目录）。

    安装版启动器会把 ``handler.filename`` 改写成用户数据目录的绝对路径，这时
    日志落在数据目录；只有开发默认配置（相对路径/未配置）才回落到程序目录
    ``logs``。旧写法无条件 ``mkdir(LOG_DIR)`` 会在程序目录留下一个用户可写的
    空 ``logs/``，这是首启残留，必须按配置推导。
    """
    handlers = (config or {}).get("handlers", {})
    for handler in handlers.values():
        filename = handler.get("filename") if isinstance(handler, dict) else None
        if filename:
            return Path(str(filename)).expanduser().parent
    return LOG_DIR


def is_loopback_host(host: str) -> bool:
    """True when ``host`` only exposes the machine-local loopback interface."""
    value = (host or "").strip()
    if value in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def warn_if_exposed(host: str, port: int) -> None:
    """打印"已对外暴露"的醒目警告。

    绑定回环之外的地址意味着同局域网的其它机器（以及任何能路由到本机的
    设备）都能访问这个网关。这不是默认行为，所以必须显式触发、显式告警，
    不能像以前那样"默认就对外、用户永远不知道"。
    """
    if is_loopback_host(host):
        return
    logger.warning(
        "网关绑定到 %s:%s —— 已对外暴露，局域网可访问；"
        "仅在确实需要从其它设备访问时才这样启动，并确认防火墙/反代已收紧。",
        host,
        port,
    )
    print("!" * 62)
    print(f"!! 警告：网关已绑定 {host}:{port}，已对外暴露，局域网可访问。")
    print("!! 如非必要，请改用默认的 127.0.0.1（只本机可达）。")
    print("!" * 62)


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
        raise SystemExit(
            "端口设置不是数字，网关无法启动。请把 PORT 改成 1-65535 之间的数字后重试。"
        )
    if not 1 <= port <= 65535:
        raise SystemExit(
            "端口设置超出可用范围（1-65535），网关无法启动。请把 PORT 改成该范围内的数字后重试。"
        )
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

    # 单实例：同一份数据目录只允许一个网关。必须在绑定端口之前拿到锁，
    # 否则第二个实例会先抢到端口、再和第一个实例同时写同一份 SQLite。
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
            url=f"http://{display_host(host)}:{port}",
            host=host,
            port=port,
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        return _run(host, port)
    finally:
        guard.clear_metadata()
        guard.release()


def _run(host: str, port: int) -> int:
    # 让应用层（Host 白名单）知道真实的绑定地址：显式对外时把该地址加入
    # 允许的 Host，否则启动器"能启动、但每个请求都被 Host 校验 400"。
    os.environ["LIUHAO_LISTEN_HOST"] = host
    log_level = (os.environ.get("LOG_LEVEL") or "info").strip().lower()
    # 读成映射：uvicorn 的 YAML 路径分支用 open() 不带 encoding，中文
    # Windows 下会按 cp936 解码 UTF-8 文件并直接崩（见 logging_config）。
    try:
        log_config = load_uvicorn_log_config(resolve_log_config_path(LOG_CONFIG))
        # 只建「实际写日志的那个目录」：安装版是数据目录，不再碰程序目录。
        resolve_log_dir(log_config).mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        # 数据目录不可写 / 磁盘满 / 安全软件拦截目录创建，都会在这里变成
        # 一条英文 traceback。用户看不懂，也给不出下一步动作。
        print(
            "[FAIL] 无法准备日志目录：数据目录可能不可写、磁盘空间不足，"
            "或安全软件拦截了写入。请确认数据目录可写、磁盘有余量后重试。",
            file=sys.stderr,
            flush=True,
        )
        logger.debug("准备日志目录失败", exc_info=True)
        return 1
    _startup_profile.mark("入口 main() 开始（解析 server.timeouts）")
    timeout_options = resolve_timeout_options()
    _startup_profile.mark("入口配置解析完成")
    warn_if_exposed(host, port)
    print(f"鎏灏网关启动： http://{display_host(host)}:{port}  (log_level={log_level})")
    _startup_profile.mark("uvicorn.run：绑定端口并运行 lifespan 启动")
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                "src.gateway.main:app",
                host=host,
                port=port,
                log_level=log_level,
                log_config=log_config,
                **timeout_options,
            )
        )
        # 启动器被直接关掉（关控制台窗口/任务管理器结束）时，别把网关留成
        # 孤儿进程继续占端口、继续持锁——见 src/common/parent_watch.py。
        watch_parent(server)
        server.run()
    except SystemExit as exc:
        # 端口被其它程序占用时 uvicorn 只打一行 "address already in use" 就退出；
        # 用户双击快捷方式看不到任何可行动的提示。退出码 3 是 uvicorn 的
        # STARTUP_FAILURE（绑定失败与 lifespan 失败都用它），所以按端口是否
        # 真的被占用区分，避免把「核心启动失败」误报成「端口冲突」。
        if exc.code == 3:
            if _port_in_use(host, port):
                _print_bind_failure(host, port)
            else:
                print(
                    "[FAIL] 网关启动失败：端口正常，问题出在核心启动阶段。"
                    "常见原因是数据目录不可写、磁盘空间不足，或安全软件拦截了程序。"
                    "请先确认数据目录可写、磁盘有余量，然后重试；详细原因见日志。",
                    file=sys.stderr,
                    flush=True,
                )
            return 1
        raise
    except OSError as exc:
        _print_bind_failure(host, port, exc)
        return 1
    return 0


def _port_in_use(host: str, port: int) -> bool:
    """True when ``host:port`` is already bound（与启动器的探测口径一致）。"""
    import socket

    probe_host = host if host not in _WILDCARD_HOSTS else "127.0.0.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind((probe_host, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def _print_bind_failure(host: str, port: int, exc: OSError | None = None) -> None:
    """端口不可用时的可读提示（不把裸 errno / 英文异常甩给用户）。"""
    if exc is not None:
        # 原始异常只进调试日志，供技术支持定位；控制台文案保持中性，
        # 不用 error 级别，避免它又被控制台 handler 原样打出来。
        logger.debug("网关绑定 %s:%s 失败", host, port, exc_info=True)
    print(
        f"[FAIL] 端口 {display_host(host)}:{port} 被其它程序占用，网关无法启动。",
        file=sys.stderr,
        flush=True,
    )
    print(
        "       请关闭占用该端口的程序，或设置环境变量 PORT 换一个端口后重试。",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
