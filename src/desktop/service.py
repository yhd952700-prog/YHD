"""托盘壳对网关子进程的生命周期管理。

与主服务的边界（说清楚，不留半截）
---------------------------------
  * 桌面壳**拥有**网关子进程：``start_liuhao.py`` 是交互式启动器，托盘需要
    精确控制端口与停止动作，所以这里直接拉起 ``uvicorn src.gateway.main:app``，
    并且不复制任何网关代码。
  * 环境契约与启动器一致：先载入仓库根 ``.env``（不覆盖已导出变量），再把
    ``config/human_identities.json`` / ``config/auth_secrets.json`` 指给网关
    （内核在未设置时 fail-closed 加载零个用户，会导致"谁都进不去"）。
  * 端口：默认 8080；被占用时向上找空闲端口并在 UI 显示实际端口。
  * 停止：**先优雅、超时才强杀**。Windows 的 ``TerminateProcess`` 没有可捕获
    的退出信号，所以子进程入口改成 ``src.desktop._runner``：桌面壳往它的
    stdin 写一行 ``shutdown``，子进程置 ``server.should_exit = True``，uvicorn
    随即停止接新请求、等在途请求结束（子进程侧上限 ``GRACEFUL_INSIDE`` 秒）、
    执行 lifespan shutdown（网关在此 flush 并关库），然后自行退出。
  * 父进程最多等 ``GRACEFUL_TIMEOUT`` 秒；超时才 ``kill()``，并把
    ``forced=True`` 写进日志与界面 —— **不静默降级**。判定依据是子进程日志里
    的 uvicorn ``Application shutdown complete``：拿不到它就如实报"未确认
    优雅收尾"，不假装成功。
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import LOG_DIR, REPO_ROOT
from .probe import http_json, lan_addresses, pick_port, port_is_free

logger = logging.getLogger(__name__)

IDENTITY_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"
SECRETS_ENV = "LIUHAO_AUTH_SECRETS_FILE"
#: 默认只绑回环：托盘壳拉起的网关不该默认对局域网开放。
#: 需要手机/其它电脑访问时必须显式指定非回环地址，并在日志里告警。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_TIMEOUT = 120.0

#: 父进程等待子进程自行退出的上限（秒）。超过才强杀并记录。
GRACEFUL_TIMEOUT = 10.0

#: 子进程内 uvicorn 等待在途请求的上限（秒）。比 ``GRACEFUL_TIMEOUT`` 少 2s，
#: 留出 lifespan shutdown（flush / 关库）与进程退出的时间。
GRACEFUL_INSIDE = 8.0

#: 与 ``src.desktop._runner`` 约定的停止指令（写到子进程 stdin）。
SHUTDOWN_COMMAND = b"shutdown\n"

#: uvicorn 完成 lifespan shutdown 的标志串；拿不到就说明"未确认优雅收尾"。
SHUTDOWN_LOG_MARKER = "Application shutdown complete"

#: uvicorn 在途请求超出等待上限时的日志（WARNING，落进子进程抓取文件）。
#: 出现它说明进程虽正常退出，但**有任务被取消** —— 也算降级，必须如实展示。
SHUTDOWN_CANCEL_MARKER = "timeout graceful shutdown exceeded"


def is_loopback_host(host: str) -> bool:
    """绑定地址是否只覆盖本机回环。"""
    value = (host or "").strip()
    if value in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


#: 网关子进程的 stdout/stderr 抓取文件上限。运行中不轮转（Windows 下子进程
#: 持有该句柄，重命名会失败），只在启动前把超限文件滚成 ``.1`` 并只留一份。
GATEWAY_LOG_MAX_BYTES = 10 * 1024 * 1024


def _rotate_capture_if_large(path: Path, max_bytes: int = GATEWAY_LOG_MAX_BYTES) -> None:
    """把超过 ``max_bytes`` 的抓取文件滚成 ``<name>.1``（保留一份）。"""
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    backup = path.with_name(path.name + ".1")
    with contextlib.suppress(OSError):
        backup.unlink()
    with contextlib.suppress(OSError):
        path.rename(backup)


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _read_log_since(path: Path | None, offset: int) -> str:
    """读子进程日志里 offset 之后的新增内容（用于确认 lifespan shutdown）。"""
    if path is None:
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, offset))
            data = handle.read()
    except (OSError, ValueError):
        return ""
    return data.decode("utf-8", errors="replace")


def stop_process(
    proc: Any,
    *,
    timeout: float = GRACEFUL_TIMEOUT,
    log_path: Path | None = None,
    log_offset: int = 0,
    extra_logs: list[tuple[Path, int]] | None = None,
) -> dict[str, Any]:
    """按 stdin 停止协议停止子进程，返回可展示、可记录的判定结果。

    ``forced=True`` 只在超过 ``timeout`` 后 kill 时出现；``graceful=True``
    要求进程自己退出、日志里出现 uvicorn 的 ``Application shutdown complete``，
    且**没有**在途任务被取消。任一不满足时，调用方必须如实展示对应原因。

    ``extra_logs`` 用于补充「关闭记录实际落盘位置」：网关的应用日志按
    ``config/logging.yaml`` 写进数据目录的 ``liuhao.log``，控制台抓取文件只承接
    WARNING 及以上。只看抓取文件会把一次真实优雅退出误报成"未确认"。
    """
    result: dict[str, Any] = {
        "stopped": False,
        "graceful": False,
        "forced": False,
        "inflight_cancelled": False,
        "elapsed": 0.0,
        "exit_code": None,
        "shutdown_confirmed": False,
        "error": None,
    }
    if proc is None:
        return result
    if proc.poll() is not None:
        result.update(stopped=True, exit_code=proc.returncode)
        return result

    started = time.monotonic()
    stdin = getattr(proc, "stdin", None)
    if stdin is None or getattr(stdin, "closed", False):
        result["error"] = "子进程没有可用的 stdin，无法发送优雅停止指令"
    else:
        try:
            stdin.write(SHUTDOWN_COMMAND)
            stdin.flush()
        except (OSError, ValueError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result["forced"] = True
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2.0)

    result["elapsed"] = round(time.monotonic() - started, 2)
    result["exit_code"] = proc.returncode
    result["stopped"] = proc.returncode is not None
    new_log = _read_log_since(log_path, log_offset)
    for extra_path, extra_offset in extra_logs or []:
        new_log += "\n" + _read_log_since(extra_path, extra_offset)
    result["shutdown_confirmed"] = SHUTDOWN_LOG_MARKER in new_log
    result["inflight_cancelled"] = SHUTDOWN_CANCEL_MARKER in new_log.lower()
    result["graceful"] = (
        result["stopped"]
        and not result["forced"]
        and result["shutdown_confirmed"]
        and not result["inflight_cancelled"]
    )
    return result


def _load_dotenv(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_file, override=False)


def _point_at_console_stores(root: Path) -> None:
    for env_name, filename in ((IDENTITY_ENV, "human_identities.json"), (SECRETS_ENV, "auth_secrets.json")):
        path = root / "config" / filename
        if path.is_file() and not (os.environ.get(env_name) or "").strip():
            os.environ[env_name] = str(path)


class GatewayService:
    """网关子进程控制器（桌面壳独占）。"""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        root: Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._root = Path(root or REPO_ROOT)
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None
        self._log_path: Path | None = None
        self._log_offset = 0
        self._lock = threading.Lock()
        self.last_error: str | None = None

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    @property
    def console_ready(self) -> bool:
        return (self._root / "apps" / "console" / "console" / "dist" / "index.html").is_file()

    @property
    def console_dist(self) -> Path:
        return self._root / "apps" / "console" / "console" / "dist"

    def lan_urls(self) -> list[str]:
        if not self.port:
            return []
        return [f"http://{ip}:{self.port}/" for ip in lan_addresses()]

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """启动网关并等待 ``/v1/health`` 通过；成功返回 True。"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
        self.last_error = None
        _load_dotenv(self._root)
        _point_at_console_stores(self._root)

        # 单实例前置检查：托盘和命令行各起一个时，别先拉起一个注定被网关
        # 拒绝的子进程。真正的互斥由子进程持有（src/desktop/_runner.py），
        # 这里只把提示做早、做可读。
        from ..common.instance_lock import InstanceLock, InstanceLockBusy, instance_root

        holder = InstanceLock(instance_root(self._root)).probe()
        if holder is not None:
            self.last_error = InstanceLockBusy(holder).describe()
            return False
        if not is_loopback_host(self.host):
            logger.warning(
                "网关绑定到 %s —— 已对外暴露，局域网可访问；"
                "仅在确实需要从其它设备访问时才这样启动。",
                self.host,
            )

        requested = self.port or DEFAULT_PORT
        if not port_is_free(requested):
            try:
                requested = pick_port(requested)
            except RuntimeError as exc:
                self.last_error = str(exc)
                return False
        self.port = requested

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "desktop-gateway.log"
        _rotate_capture_if_large(log_path)
        try:
            self._log_offset = log_path.stat().st_size
        except OSError:
            self._log_offset = 0
        self._log_path = log_path
        try:
            self._log_handle = open(log_path, "ab")  # noqa: SIM115 - 由 stop() 关闭
        except OSError:
            self._log_handle = subprocess.DEVNULL

        # 应用日志由网关自己按 config/logging.yaml 轮转（10 MiB × 5）。
        # 这个抓取文件只承接控制台 WARNING 及以上与启动崩溃栈，增长很小。
        # 入口是 src.desktop._runner：它负责把 stdin 的 shutdown 转成
        # uvicorn 的 should_exit，从而让停止可以优雅收尾（见模块文档）。
        log_config = self._root / "config" / "logging.yaml"
        cmd = [
            os.environ.get("LIUHAO_DESKTOP_PYTHON") or self._venv_python(),
            "-u",
            "-m",
            "src.desktop._runner",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if log_config.is_file():
            cmd += ["--log-config", str(log_config)]
        try:
            with self._lock:
                child_env = dict(os.environ)
                # 托盘被强杀时，别把网关留成孤儿继续占端口/持锁。
                child_env["LIUHAO_PARENT_PID"] = str(os.getpid())
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._root),
                    stdin=subprocess.PIPE,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=_no_window_flags(),
                    env=child_env,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_error = f"拉起网关失败：{type(exc).__name__}: {exc}"
            self._close_log()
            return False

        if self._wait_health(timeout):
            return True
        self.last_error = self.last_error or f"网关在 {timeout:.0f}s 内未通过 /v1/health"
        self.stop()
        return False

    def _venv_python(self) -> str:
        import sys

        venv_dir = self._root / ".venv"
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        candidate = venv_dir / scripts_dir / "python.exe"
        return str(candidate) if candidate.is_file() else sys.executable

    def _wait_health(self, timeout: float) -> bool:
        base = self.base_url
        if not base:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                proc = self._proc
            if proc is not None and proc.poll() is not None:
                self.last_error = f"网关进程提前退出（code={proc.returncode}），详见 logs/desktop-gateway.log"
                return False
            # 2s 而不是 1s：机器繁忙时 /v1/health 自身可能超过 1s，误判成"没起来"。
            info = http_json(f"{base}/v1/health", timeout=2.0)
            if info["ok"]:
                return True
            time.sleep(0.5)
        return False

    def stop(self, timeout: float = GRACEFUL_TIMEOUT) -> dict[str, Any]:
        """先优雅停（stdin shutdown），超时才强杀；返回判定结果（见模块文档）。"""
        with self._lock:
            proc = self._proc
            self._proc = None
        # 关闭记录写在应用日志里（config/logging.yaml 指向数据目录），抓取文件
        # 只承接 WARNING 及以上；两处都要看，否则会把优雅退出误判成"未确认"。
        app_log = LOG_DIR / "liuhao.log"
        try:
            app_log_offset = app_log.stat().st_size
        except OSError:
            app_log_offset = 0
        result = stop_process(
            proc,
            timeout=timeout,
            log_path=self._log_path,
            log_offset=self._log_offset,
            extra_logs=[(app_log, app_log_offset)],
        )
        if result["forced"]:
            logger.warning(
                "网关未在 %.0fs 内优雅退出，已强制结束（exit_code=%s，日志见 logs/desktop-gateway.log）",
                timeout,
                result["exit_code"],
            )
        elif result["stopped"]:
            logger.info(
                "网关已停止：graceful=%s，在途任务被取消=%s，用时 %.2fs，shutdown_确认=%s",
                result["graceful"],
                result["inflight_cancelled"],
                result["elapsed"],
                result["shutdown_confirmed"],
            )
            if result["inflight_cancelled"]:
                logger.warning(
                    "网关退出时有在途任务因超出 %.0fs 宽限被取消（日志见 logs/desktop-gateway.log）",
                    GRACEFUL_INSIDE,
                )
        if result["error"]:
            logger.warning("优雅停止指令发送失败：%s", result["error"])
        self._close_log()
        return result

    def _close_log(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle not in (None, subprocess.DEVNULL):
            with contextlib.suppress(OSError):
                handle.close()


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "GRACEFUL_INSIDE",
    "GRACEFUL_TIMEOUT",
    "SHUTDOWN_CANCEL_MARKER",
    "SHUTDOWN_LOG_MARKER",
    "GatewayService",
    "stop_process",
]
