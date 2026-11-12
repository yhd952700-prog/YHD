"""受限静态预览服务 —— 让智能体「交得出东西」，但不获得命令执行权。

设计约束（对应验收项一）
------------------------
* **只以任务工作区为静态根**：根在注册工具时固定，调用方不能传任意目录；
  可选 ``entry`` 是工作区内的相对页面路径，越界 / 跨盘 / 含 ``..`` 一律拒绝。
* **不执行任何命令**：只用 Python 标准库 ``http.server`` 起一个静态文件服务，
  没有 shell、没有子进程、不接受模型给的命令行。
* **只绑 127.0.0.1、端口自动选空闲**：不会出网，也不和既有端口冲突。
* **可停止、会自动清理**：返回 ``handle``（preview id）供 ``preview_stop``；
  任务结束 / 失败时由 :class:`~src.ai.agent_runtime.AgentRuntime` 统一停掉；
  另有空闲超时 watchdog 与进程退出兜底，避免残留。
* **自带可访问性自检**：启动后立刻对目标页面做一次真实 HTTP GET，把
  ``http_status`` / 响应字节数 / SHA256 / 页面标题写进返回值。模型拿到的
  “页面可访问”是这次探测的结果，不是它自己的说法。
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from .workspace import WorkspaceViolation, is_inside, resolve_in_workspace

logger = logging.getLogger("liuhao.ai.preview_server")

#: 空闲多久自动停（秒）。<=0 表示关闭空闲回收（仍会在任务结束时停）。
DEFAULT_IDLE_SECONDS = 600.0
_IDLE_ENV = "LIUHAO_PREVIEW_IDLE_SECONDS"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MAX_CHECK_BYTES = 4 * 1024 * 1024


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + (
        f".{int(time.time() * 1000) % 1000:03d}"
    )


def _idle_seconds() -> float:
    raw = (os.environ.get(_IDLE_ENV) or "").strip()
    try:
        return float(raw) if raw else DEFAULT_IDLE_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_IDLE_SECONDS


def _has_parent_segment(raw: str) -> bool:
    """路径里是否含 ``..`` 段（``/`` 与 ``\\`` 都算分隔符）。

    即使 ``realpath`` 之后仍落在工作区内，例如 ``a/../todo.html``，也按要求
    直接拒绝 —— 预览入口不接受任何带 ``..`` 的路径写法。
    """
    return any(part == ".." for part in str(raw).replace("\\", "/").split("/"))


def _reject(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "rejected", "error": message}


def _self_check(url: str, timeout: float = 3.0) -> Dict[str, Any]:
    """对本地预览地址做一次真实 HTTP GET（只访问 127.0.0.1）。"""
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(_MAX_CHECK_BYTES)
            status = int(getattr(response, "status", response.getcode()) or 0)
            content_type = response.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001 - 探测失败要如实返回
        return {
            "reachable": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.time() - started) * 1000),
            "checked_at": _now_iso(),
        }

    title = ""
    match = _TITLE_RE.search(body.decode("utf-8", errors="ignore"))
    if match:
        title = " ".join(match.group(1).split())[:120]
    return {
        "reachable": True,
        "url": url,
        "http_status": status,
        "content_type": content_type,
        "content_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "title": title,
        "latency_ms": int((time.time() - started) * 1000),
        "checked_at": _now_iso(),
    }


class PreviewServerManager:
    """进程内静态预览服务登记表（线程安全，可一个进程多个预览）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._watchdog: Optional[threading.Thread] = None

    def _touch(self, preview_id: str) -> None:
        with self._lock:
            record = self._servers.get(preview_id)
            if record is not None:
                record["last_access"] = time.time()

    def start(
        self,
        root: str,
        entry: str = "",
        idle_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """在 ``root``（必须是调用方固定好的工作区）上启动一个静态服务。"""
        root_text = os.path.abspath(str(root or ""))
        if not os.path.isdir(root_text):
            return {
                "success": False,
                "status": "failed",
                "error": f"预览根目录不存在：{root_text}",
            }
        root_real = os.path.realpath(root_text)

        entry_path: Optional[str] = None
        entry_rel = ""
        raw_entry = str(entry or "").strip()
        if raw_entry:
            if _has_parent_segment(raw_entry):
                return _reject(f"预览入口路径不允许包含 '..'：{raw_entry!r}")
            try:
                entry_path = resolve_in_workspace(raw_entry, root_real)
            except WorkspaceViolation as exc:
                return _reject(f"预览入口必须在工作区内：{exc}")
            if not is_inside(root_real, entry_path):
                return _reject(f"预览入口越界：{raw_entry!r}")
            if not os.path.isfile(entry_path):
                return _reject(f"预览入口文件不存在：{raw_entry!r}")
            entry_rel = os.path.relpath(entry_path, root_real).replace(os.sep, "/")
        else:
            # 模型没给入口时只从工作区里挑一个**已存在**的页面，不凭空造路径。
            try:
                pages = [
                    name for name in os.listdir(root_real)
                    if name.lower().endswith((".html", ".htm"))
                    and os.path.isfile(os.path.join(root_real, name))
                ]
                pages.sort(
                    key=lambda name: os.path.getmtime(os.path.join(root_real, name)),
                    reverse=True,
                )
                if pages:
                    entry_path = os.path.join(root_real, pages[0])
                    entry_rel = pages[0]
            except OSError:
                pages = []

        preview_id = uuid.uuid4().hex[:10]
        manager = self

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=root_real, **kwargs)

            def log_message(self, fmt: str, *args: Any) -> None:
                manager._touch(preview_id)
                logger.debug("preview %s: %s", preview_id, fmt % args)

            def _guard(self) -> bool:
                target = self.translate_path(self.path)
                if not is_inside(root_real, target):
                    self.send_error(403, "Forbidden")
                    return False
                manager._touch(preview_id)
                return True

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self._guard():
                    super().do_GET()

            def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
                if self._guard():
                    super().do_HEAD()

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        except OSError as exc:
            return {
                "success": False,
                "status": "failed",
                "error": f"无法在 127.0.0.1 启动预览服务：{exc}",
            }
        httpd.daemon_threads = True
        port = int(httpd.server_address[1])
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            name=f"liuhao-preview-{preview_id}",
            daemon=True,
        )
        idle = _idle_seconds() if idle_seconds is None else float(idle_seconds)
        record = {
            "preview_id": preview_id,
            "server": httpd,
            "thread": thread,
            "port": port,
            "root": root_real,
            "started_at": _now_iso(),
            "last_access": time.time(),
            "idle_seconds": idle,
        }
        with self._lock:
            self._servers[preview_id] = record
        thread.start()
        self._ensure_watchdog()

        base_url = f"http://127.0.0.1:{port}/"
        entry_url = base_url + (urllib.parse.quote(entry_rel) if entry_rel else "")
        check = _self_check(entry_url)

        result: Dict[str, Any] = {
            "success": bool(check.get("reachable")),
            "status": "executed" if check.get("reachable") else "preview_unreachable",
            "handle": preview_id,
            "preview_id": preview_id,
            "url": base_url,
            "entry_url": entry_url,
            "entry": entry_rel,
            "root": root_real,
            "port": port,
            "host": "127.0.0.1",
            "pid": os.getpid(),
            "thread": thread.name,
            "started_at": record["started_at"],
            "files": self._html_files(root_real),
            "self_check": check,
        }
        if not check.get("reachable"):
            result["error"] = (
                "预览服务已启动，但对目标页面的自检请求失败："
                f"{check.get('error') or 'HTTP ' + str(check.get('http_status'))}"
                "；该步不能算完成。"
            )
        return result

    @staticmethod
    def _html_files(root: str, limit: int = 30) -> List[str]:
        try:
            return sorted(
                name for name in os.listdir(root)
                if name.lower().endswith((".html", ".htm"))
            )[:limit]
        except OSError:
            return []

    def stop(self, preview_id: Optional[str] = None, reason: str = "") -> List[str]:
        """停止指定预览（``None`` = 全部）。返回真正停掉的 handle 列表。"""
        with self._lock:
            if preview_id is None:
                ids = list(self._servers)
            else:
                ids = [preview_id] if preview_id in self._servers else []
            records = [self._servers.pop(item) for item in ids]
        stopped: List[str] = []
        for record in records:
            try:
                record["server"].shutdown()
            except Exception:  # noqa: BLE001 - 停止要尽力而为
                pass
            try:
                record["server"].server_close()
            except Exception:  # noqa: BLE001
                pass
            try:
                record["thread"].join(2.0)
            except Exception:  # noqa: BLE001
                pass
            stopped.append(str(record["preview_id"]))
            logger.info(
                "evt=preview_stopped preview_id=%s port=%s reason=%s",
                record["preview_id"],
                record.get("port"),
                reason or "unspecified",
            )
        return stopped

    def active(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "preview_id": record["preview_id"],
                    "url": f"http://127.0.0.1:{record['port']}/",
                    "port": record["port"],
                    "root": record["root"],
                    "started_at": record["started_at"],
                }
                for record in self._servers.values()
            ]

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return

        def _loop() -> None:
            while True:
                time.sleep(5)
                idle = _idle_seconds()
                if idle <= 0:
                    continue
                now = time.time()
                with self._lock:
                    expired = [
                        preview_id
                        for preview_id, record in self._servers.items()
                        if now - float(record.get("last_access") or 0) > idle
                    ]
                for preview_id in expired:
                    logger.info(
                        "evt=preview_idle_stopped preview_id=%s idle_seconds=%s",
                        preview_id,
                        idle,
                    )
                    self.stop(preview_id, reason="idle_timeout")

        thread = threading.Thread(
            target=_loop, name="liuhao-preview-watchdog", daemon=True
        )
        self._watchdog = thread
        thread.start()


_MANAGER: Optional[PreviewServerManager] = None
_MANAGER_LOCK = threading.Lock()


def get_preview_manager() -> PreviewServerManager:
    """进程级单例：任务结束 / 进程退出时能统一回收。"""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = PreviewServerManager()
    return _MANAGER


def stop_all_previews(reason: str = "shutdown") -> List[str]:
    """停止本进程里所有预览；无预览时安全返回空列表。"""
    manager = _MANAGER
    if manager is None:
        return []
    return manager.stop(None, reason=reason)


atexit.register(stop_all_previews, reason="process_exit")

__all__ = [
    "DEFAULT_IDLE_SECONDS",
    "PreviewServerManager",
    "get_preview_manager",
    "stop_all_previews",
]
