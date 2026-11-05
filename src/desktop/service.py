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
  * 停止：调用 ``terminate()`` 结束子进程。Windows 的 ``TerminateProcess``
    没有可以捕获的优雅退出信号，因此停止是**立即结束**；SQLite WAL 本身
    可安全恢复。停止后托盘仍驻留，用户可退出后重新启动。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import LOG_DIR, REPO_ROOT
from .probe import http_json, lan_addresses, pick_port, port_is_free

IDENTITY_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"
SECRETS_ENV = "LIUHAO_AUTH_SECRETS_FILE"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_TIMEOUT = 120.0

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
            self._log_handle = open(log_path, "ab")  # noqa: SIM115 - 由 stop() 关闭
        except OSError:
            self._log_handle = subprocess.DEVNULL

        # 应用日志由网关自己按 config/logging.yaml 轮转（10 MiB × 5）。
        # 这个抓取文件只承接控制台 WARNING 及以上与启动崩溃栈，增长很小。
        log_config = self._root / "config" / "logging.yaml"
        cmd = [
            os.environ.get("LIUHAO_DESKTOP_PYTHON") or self._venv_python(),
            "-u",
            "-m",
            "uvicorn",
            "src.gateway.main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if log_config.is_file():
            cmd += ["--log-config", str(log_config)]
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._root),
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=_no_window_flags(),
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

        candidate = self._root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        return str(candidate) if candidate.is_file() else sys.executable

    def _wait_health(self, timeout: float) -> bool:
        assert self.base_url is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                proc = self._proc
            if proc is not None and proc.poll() is not None:
                self.last_error = f"网关进程提前退出（code={proc.returncode}），详见 logs/desktop-gateway.log"
                return False
            info = http_json(f"{self.base_url}/v1/health", timeout=1.0)
            if info["ok"]:
                return True
            time.sleep(0.5)
        return False

    def stop(self, timeout: float = 8.0) -> None:
        """立即结束网关子进程（见模块文档的停止语义）。"""
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=2.0)
        self._close_log()

    def _close_log(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle not in (None, subprocess.DEVNULL):
            with contextlib.suppress(OSError):
                handle.close()


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "GatewayService"]
