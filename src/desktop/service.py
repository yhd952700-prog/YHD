"""托盘壳对网关子进程的生命周期管理。

与主服务的边界（说清楚，不留半截）
---------------------------------
  * 桌面壳**拥有**网关子进程：``start_liuhao.py`` 是交互式启动器，托盘需要
    精确控制端口与停止动作，所以这里直接拉起 ``uvicorn src.gateway.main:app``，
    并且不复制任何网关代码。
  * 环境契约与启动器一致：先载入仓库根 ``.env``（不覆盖已导出变量），再把
    ``config/human_identities.json`` / ``config/auth_secrets.json`` 指给网关
    （内核在未设置时 fail-closed 加载零个用户，会导致"谁都进不去"）。
  * 端口：默认 8080；被占用时**默认不换端口**（换端口地址就变，书签 /
    PWA / 旧标签会失联），而是把「哪个端口、被谁占、怎么解决」写进
    ``last_error`` 交给 UI。只有显式 ``allow_port_fallback=True`` 的自动恢复
    场景才向上顺延，并写一条 warning 日志说明地址已变化。
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
import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..common.instance_lock import (
    INSTANCE_DIR_ENV,
    InstanceLock,
    InstanceLockBusy,
    instance_root,
)
from ..common.redaction import redact_log_text
from ..logging_config import (
    RotatingTextWriter,
    prune_log_retention,
    resolve_data_dir,
    resolve_log_file,
    rotate_backups,
    scrub_legacy_log_leaks,
)
from . import LOG_DIR, REPO_ROOT
from .probe import http_json, lan_addresses, pick_port, port_is_free, port_owner

logger = logging.getLogger(__name__)

IDENTITY_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"
SECRETS_ENV = "LIUHAO_AUTH_SECRETS_FILE"
#: 默认只绑回环：托盘壳拉起的网关不该默认对局域网开放。
#: 需要手机/其它电脑访问时必须显式指定非回环地址，并在日志里告警。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_TIMEOUT = 120.0


def _env_port() -> int | None:
    """首启向导选过的端口（``LIUHAO_API_PORT``）；没设/非法时返回 None。

    向导与托盘壳跑在同一个进程里（``liuhao_desktop.py app``），向导选端口
    后写入这个环境变量；这里把它当默认值，避免"向导里选了 9000、托盘却
    仍去占 8080"的两套行为。显式 ``--port`` 始终优先。
    """
    raw = (os.environ.get("LIUHAO_API_PORT") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 65535 else None


#: 持久化端口配置的落点覆盖变量（测试/演练用来隔离真实用户目录）。
PORT_FILE_ENV = "LIUHAO_DESKTOP_PORT_FILE"


def desktop_port_file() -> Path:
    """用户选定的端口写到 ``<数据目录>/config/desktop_port.json``。

    为什么落数据目录而不是程序目录：安装版的程序目录可能只读，而且
    ``config/`` 下的业务凭据不能和机器级 UI 偏好混在一起。数据目录是每台
    机器/每个用户可写且随卸载清理的位置。

    格式固定为 ``{"port": 8081, "updated_at": "..."}``；读取时只认 ``port``
    字段且必须在 1–65535，其余内容一律忽略。
    """
    raw = (os.environ.get(PORT_FILE_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(resolve_data_dir()) / "config" / "desktop_port.json"


def load_saved_port() -> int | None:
    """读取上次选定的端口；文件不存在/损坏/非法时返回 None（绝不抛异常）。"""
    try:
        payload = json.loads(desktop_port_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        value = int(payload.get("port"))
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 65535 else None


def save_saved_port(port: int) -> Path:
    """原子写入端口配置；失败抛 ``OSError``，由调用方决定是否只记日志。"""
    value = int(port)
    if not 1 <= value <= 65535:
        raise ValueError(f"端口 {port} 超出合法范围（1-65535）")
    target = desktop_port_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    payload = {
        "port": value,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def effective_port(explicit: int | None = None) -> int:
    """端口优先级：显式 ``--port`` > ``LIUHAO_API_PORT`` > 持久化配置 > 8080。"""
    if explicit is not None:
        return explicit
    return _env_port() or load_saved_port() or DEFAULT_PORT


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


#: 网关子进程的 stdout/stderr 抓取文件上限与保留份数（10 MiB × 2）。
GATEWAY_LOG_MAX_BYTES = 10 * 1024 * 1024
GATEWAY_LOG_BACKUP_COUNT = 1

#: 保留巡检间隔（秒）：启动时先清一轮，之后每小时按「天数 + 总量」复查。
RETENTION_SWEEP_INTERVAL = 3600.0


def _rotate_capture_if_large(path: Path, max_bytes: int = GATEWAY_LOG_MAX_BYTES) -> None:
    """启动前把超限的抓取文件滚成 ``.1``，保证本次 offset 从 0 开始。

    ``RotatingTextWriter`` 也会在行首轮转，但那发生在第一次写入之后；若
    启动时文件已超限，停止阶段按旧 offset 读取-shutdown 标记会落空。
    """
    try:
        if path.stat().st_size < max_bytes:
            return
    except FileNotFoundError:
        return  # 首次启动还没有抓取文件，属正常
    except OSError as exc:
        logger.warning(
            "检查网关抓取文件大小失败（%s：%s）：跳过启动前轮转，停止时可能读不到 shutdown 标记",
            path,
            exc,
        )
        return
    try:
        rotate_backups(str(path), GATEWAY_LOG_BACKUP_COUNT)
    except OSError as exc:
        logger.warning(
            "网关抓取文件轮转失败（%s：%s）：旧内容会继续追加，停止确认可能落空",
            path,
            exc,
        )


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _instance_run_dir(root: Path) -> Path:
    """实例锁目录（与 ``src/common/instance_lock.py`` 的布局保持一致）。"""
    override = (os.environ.get(INSTANCE_DIR_ENV) or "").strip()
    base = Path(override).expanduser().resolve() if override else instance_root(root)
    return base / "run"


#: 数据目录 ``run/`` 下由本产品创建的两把租约锁。都走
#: :class:`InstanceLock`（OS 文件锁 + 同名 json 元信息），进程被强杀后锁自动
#: 释放、文件留下 —— 退出时一起清掉，``run/`` 里不该有"上次的锁"。
LEASE_LOCK_NAMES = ("instance", "local_scheduler")


def cleanup_instance_files(root: Path) -> dict[str, Any]:
    """删除已确认无人持有的锁文件与元信息（停止 / 再次启动时调用）。

    锁的互斥性由操作系统保证：进程被强杀后锁自动释放，但 ``*.lock`` 文件会
    留在 ``run/`` 里。用户会把"上次留下的锁文件"当成故障，因此这里在
    **确认没有活着的持有者**之后再删。

    Windows 上删除是安全的：只要还有进程打开着该文件，``unlink`` 会因共享
    冲突失败，我们保留文件（说明确实有别的实例在用）。任何失败都静默 ——
    清理残留绝不能影响停止 / 启动本身。
    """
    run_dir = _instance_run_dir(root)
    removed: list[str] = []
    kept: list[str] = []
    for name in LEASE_LOCK_NAMES:
        files = (run_dir / f"{name}.json", run_dir / f"{name}.lock")
        if not any(path.exists() for path in files):
            continue
        try:
            holder = InstanceLock(run_dir.parent, name=name).probe()
        except Exception:  # noqa: BLE001 - 探测失败时保守地不动文件
            kept.append(f"{name}.lock")
            continue
        if holder is not None:
            kept.append(f"{name}.lock")
            continue
        for path in files:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                # 文件正被另一个进程打开（Windows 共享冲突）：保留，不报错。
                kept.append(path.name)
            else:
                removed.append(str(path))
    return {
        "cleaned": not kept,
        "removed": removed,
        "kept": kept,
        "reason": None if not kept else "锁仍被其它进程持有",
    }


class _GatewayLogCapture:
    """子进程 stdout/stderr 的**脱敏**落盘器（带轮转）。

    旧实现把 ``Popen`` 的 stdout 直接指向文件：子进程里任何绕过
    ``RedactingFormatter`` 的输出（观测性兜底 handler、第三方 traceback、
    SDK 异常原文）都会原样落盘，完整 API Key 就是这样变成明文的。这里改为
    管道逐行读取，每行先过 :func:`redact_log_text` 再交给
    :class:`RotatingTextWriter`：每次写入才打开文件，尺寸检查、轮转与追加在
    同一把 OS 文件锁内完成，父进程独占句柄，Windows 下不会出现
    ``WinError 32``。
    """

    def __init__(
        self,
        path: Path,
        max_bytes: int = GATEWAY_LOG_MAX_BYTES,
        backup_count: int = GATEWAY_LOG_BACKUP_COUNT,
    ) -> None:
        self._path = Path(path)
        self._writer = RotatingTextWriter(
            self._path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            # 泵线程已经逐行脱敏；这里再脱一次没有收益，且会让"逐行 →
            # redact_log_text → 写盘"的契约在测试里不可见。
            redact=False,
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def size(self) -> int:
        """当前抓取文件的字节数（用于停止后从 offset 读 shutting down 记录）。"""
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def start(self, stream: Any) -> None:
        self._thread = threading.Thread(
            target=self._pump, args=(stream,), name="liuhao-gateway-log", daemon=True
        )
        self._thread.start()

    def _pump(self, stream: Any) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                self.write(line)
        finally:
            with contextlib.suppress(OSError, ValueError):
                stream.close()

    def write(self, raw: bytes | str) -> None:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        data = redact_log_text(text)
        if not data:
            return
        with self._lock:
            if self._closed:
                return
            self._writer.write(data)

    def drain(self, timeout: float = 2.0) -> None:
        """等泵线程把管道里的剩余行写完（停止后读日志前必须调用）。

        子进程退出后管道 EOF，``readline`` 返回空串，泵线程自行结束；这里
        只等它把最后几行落盘，超时也不阻塞停止流程。
        """
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def close(self) -> None:
        self.drain()
        with self._lock:
            self._closed = True
            self._writer.close()


def _read_log_since(path: Path | None, offset: int) -> str:
    """读子进程日志里 offset 之后的新增内容（用于确认 lifespan shutdown）。

    运行中轮转后文件会变小，``offset`` 可能已经越过文件末尾；这时从头读，
    否则关闭标记会被漏掉，优雅退出被误判成"未确认"。
    """
    if path is None:
        return ""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            if offset > size:
                offset = 0
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
    drain: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """按 stdin 停止协议停止子进程，返回可展示、可记录的判定结果。

    ``forced=True`` 只在超过 ``timeout`` 后 kill 时出现；``graceful=True``
    要求进程自己退出、日志里出现 uvicorn 的 ``Application shutdown complete``，
    且**没有**在途任务被取消。任一不满足时，调用方必须如实展示对应原因。

    ``extra_logs`` 用于补充「关闭记录实际落盘位置」：网关的应用日志按
    ``config/logging.yaml`` 写进数据目录的 ``liuhao.log``，控制台抓取文件只承接
    WARNING 及以上。只看抓取文件会把一次真实优雅退出误报成"未确认"。

    ``drain`` 在读取日志**之前**调用：抓取文件由后台泵线程异步写入，不先等它
    把管道剩余行落盘，最后几行（往往正是 ``Application shutdown complete``）
    会被漏读，优雅退出被误判成"未确认"。
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
        if drain is not None:
            drain()
        return result
    if proc.poll() is not None:
        if drain is not None:
            drain()
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
    # 先把抓取线程里剩下的行写完，再读 offset 之后的内容；否则最后几行
    # （包含 shutdown 标记）可能还在管道里。
    if drain is not None:
        drain()
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
        self._capture: _GatewayLogCapture | None = None
        self._log_path: Path | None = None
        self._log_offset = 0
        self._lock = threading.Lock()
        self._retention_thread: threading.Thread | None = None
        self._retention_stop = threading.Event()
        self.last_error: str | None = None
        #: 端口被占用时的结构化信息（端口 + 占用者 PID/程序名/路径），
        #: 供桌面壳弹「二选一」窗口；空闲或其它失败原因为 None。
        self.port_conflict: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # 日志保留（按天 + 按量双上限）
    # ------------------------------------------------------------------ #
    def _retention_dirs(self) -> list[Path]:
        directories = [LOG_DIR]
        try:
            data_logs = resolve_log_file(Path("logs") / "liuhao.log").parent
        except Exception as exc:  # noqa: BLE001 - 数据目录解析失败不影响桌面日志清理
            logger.warning(
                "数据目录日志位置解析失败（%s）：本轮只清理桌面日志目录，数据目录日志不会被裁剪",
                exc,
            )
            data_logs = None
        if data_logs is not None and data_logs not in directories:
            directories.append(data_logs)
        return directories

    def sweep_log_retention(self) -> list[dict[str, Any]]:
        """按「天数 + 总量」清一轮日志，返回每个目录的清理报告。

        报告同时写进各日志目录的 ``retention.json``，诊断包会原样带上
        「保留了什么、删了什么、为什么删」。
        """
        reports: list[dict[str, Any]] = []
        for directory in self._retention_dirs():
            try:
                report = prune_log_retention(directory)
            except Exception:  # noqa: BLE001 - 清理失败不能影响网关启动/运行
                logger.warning("日志保留清理失败：%s", directory.name, exc_info=True)
                continue
            reports.append(report)
            failed = report.get("failed") or []
            if failed:
                logger.warning(
                    "日志保留清理 %s：%d 份未能删除（可能被占用或目录只读）：%s",
                    directory.name,
                    len(failed),
                    "、".join(str(item.get("name", "?")) for item in failed[:5]),
                )
            deleted = report.get("deleted") or []
            if deleted:
                logger.info(
                    "日志保留清理 %s：删除 %d 份、保留 %d 份，当前占用 %.1f MiB"
                    "（上限 %d 天 / %.0f MiB）",
                    directory.name,
                    len(deleted),
                    len(report.get("kept") or []),
                    report.get("total_bytes_after", 0) / (1024 * 1024),
                    report.get("retention_days", 0),
                    report.get("max_total_bytes", 0) / (1024 * 1024),
                )
        return reports

    def _start_retention_janitor(self) -> None:
        thread = self._retention_thread
        if thread is not None and thread.is_alive():
            return
        self._retention_stop.clear()
        self._retention_thread = threading.Thread(
            target=self._retention_loop, name="liuhao-log-retention", daemon=True
        )
        self._retention_thread.start()

    def _retention_loop(self) -> None:
        while not self._retention_stop.wait(RETENTION_SWEEP_INTERVAL):
            self.sweep_log_retention()

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @property
    def root(self) -> Path:
        """实例根：网关子进程的 ``cwd``，也是 ``run/instance.json`` 的父目录。"""
        return self._root

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
    def start(
        self, timeout: float = DEFAULT_TIMEOUT, *, allow_port_fallback: bool = False
    ) -> bool:
        """启动网关并等待 ``/v1/health`` 通过；成功返回 True。

        端口被占用时**默认不换端口**：换了端口地址就变，书签 / PWA / 旧标签
        会指向连不上的地方。只有显式 ``allow_port_fallback=True``（明确的
        自动恢复场景）才向上顺延，并写日志记录实际端口。
        """
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
        self.last_error = None
        self.port_conflict = None
        _load_dotenv(self._root)
        _point_at_console_stores(self._root)

        # 单实例前置检查：托盘和命令行各起一个时，别先拉起一个注定被网关
        # 拒绝的子进程。真正的互斥由子进程持有（src/desktop/_runner.py），
        # 这里只把提示做早、做可读。
        holder = InstanceLock(instance_root(self._root)).probe()
        if holder is not None:
            self.last_error = InstanceLockBusy(holder).describe()
            return False
        # 上次被强杀留下的锁文件/元信息：确认无人持有后清掉（自愈）。
        # 清理失败不影响启动 —— 真正的互斥仍由子进程持有的 OS 锁保证。
        cleanup_instance_files(self._root)
        if not is_loopback_host(self.host):
            logger.warning(
                "网关绑定到 %s —— 已对外暴露，局域网可访问；"
                "仅在确实需要从其它设备访问时才这样启动。",
                self.host,
            )

        requested = effective_port(self.port)
        if not 1 <= requested <= 65535:
            # 端口 0 的语义是"让系统随机分配"，但桌面壳的书签、托盘和局域网入口
            # 都依赖地址不变，随机端口无法回填给 UI。与其静默退回 8080 造成
            # "传了 0 却起了 8080"的错觉，不如明确拒绝并告诉用户怎么改。
            self.last_error = (
                f"端口 {requested} 无效：桌面壳需要固定端口（书签 / 托盘 / 局域网入口都依赖地址不变），"
                "不支持 0（系统随机分配）；请指定 1-65535 之间的端口，"
                "或用 LIUHAO_API_PORT 指定。"
                if requested == 0
                else f"端口 {requested} 超出合法范围（1-65535），"
                "请用 --port 或 LIUHAO_API_PORT 指定其他端口。"
            )
            logger.error(self.last_error)
            return False
        if not port_is_free(requested):
            if not allow_port_fallback:
                owner = port_owner(requested)
                self.port_conflict = {"port": requested, **owner}
                self.last_error = (
                    f"网关端口 {requested} 已被占用（占用者：{owner['description']}）。"
                    "为避免地址变化，启动器不会自动换端口。"
                )
                logger.error(
                    "evt=port.conflict port=%s owner=%s",
                    requested,
                    owner["description"],
                )
                return False
            # 只有显式的自动恢复场景才顺延；必须留下日志，否则用户不知道为什么
            # 地址和上一次不一样了。
            try:
                fallback = pick_port(requested)
            except RuntimeError as exc:
                self.last_error = str(exc)
                return False
            logger.warning(
                "端口 %s 已被占用；自动恢复模式改用 %s（地址会变，请以实际地址为准）",
                requested,
                fallback,
            )
            requested = fallback
        self.port = requested

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "desktop-gateway.log"
        # 旧版本曾把子进程 stdout 原样写进这个文件（完整 API Key 明文）。
        # 新版本从源头堵住了，但用户目录里的旧文件不会自己变干净：启动时
        # 一次性就地脱敏（含 .1 备份），别让用户以为现在的版本还在泄漏。
        try:
            scrubbed = scrub_legacy_log_leaks(LOG_DIR)
        except Exception:  # noqa: BLE001 - 清理失败不能拦住网关启动
            scrubbed = []
            logger.warning("历史日志的一次性脱敏失败，旧文件将在轮转后自然滚出")
        if scrubbed:
            logger.info("已就地脱敏 %d 份历史日志（旧版本写入过未脱敏内容）", len(scrubbed))
        # 按天 + 按量双上限：启动先清一轮，之后每小时由后台巡检兜底。
        self.sweep_log_retention()
        self._start_retention_janitor()
        _rotate_capture_if_large(log_path)
        self._log_path = log_path
        try:
            self._capture = _GatewayLogCapture(log_path)
        except OSError as exc:
            # 没有抓取文件时网关的 stdout/stderr 会退化为 DEVNULL：崩溃栈与
            # 启动失败原因会彻底丢失，必须留下可读记录。
            logger.warning(
                "无法创建网关日志抓取文件（%s：%s）：网关输出将被丢弃，启动崩溃时将只剩退出码",
                log_path,
                exc,
            )
            self._capture = None
        self._log_offset = self._capture.size if self._capture is not None else 0

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
                # 桌面壳是可信本机入口：首次注册/取码/重置都要求桌面端证明，
                # 且 HTTP 对端必须与网关同属一个 Windows 用户。
                child_env["LIUHAO_TRUSTED_LAUNCH"] = "1"
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._root),
                    stdin=subprocess.PIPE,
                    # 管道而不是直接写文件：父进程要逐行脱敏后才能落盘。
                    stdout=subprocess.PIPE if self._capture is not None else subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    creationflags=_no_window_flags(),
                    env=child_env,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_error = f"拉起网关失败：{type(exc).__name__}: {exc}"
            self._close_log()
            return False
        if self._capture is not None and self._proc.stdout is not None:
            self._capture.start(self._proc.stdout)

        if self._wait_health(timeout):
            return True
        self.last_error = self.last_error or f"网关在 {timeout:.0f}s 内未通过 /v1/health"
        self.stop()
        return False

    def _venv_python(self) -> str:
        """网关子进程的解释器：Windows 上优先 ``pythonw.exe``。

        ``python.exe`` 是控制台子系统程序：即使父进程传了 CREATE_NO_WINDOW，
        控制台宿主（conhost.exe）仍会挂进子进程树 —— 用户虽然看不到黑框，
        但"双击图标后不该有命令行进程"这条验收会失败。``pythonw.exe`` 是
        GUI 子系统，同样的 stdin/stdout 管道照常可用（``_runner`` 的优雅
        停止与日志抓取都不依赖真实控制台）。

        非 Windows / 没有 pythonw 时退回原行为。
        """
        import sys

        venv_dir = self._root / ".venv"
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        candidates = (
            (venv_dir / scripts_dir / "pythonw.exe", venv_dir / scripts_dir / "python.exe")
            if os.name == "nt"
            else (venv_dir / scripts_dir / "python",)
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return sys.executable

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
            body = info.get("body")
            # 只看 2xx 会把端口上别人的服务当成就绪；必须核对我们自己的身份。
            if info["ok"] and isinstance(body, dict) and body.get("service") == "liuhao-gateway":
                # 身份对上后再确认进程没死：进程刚好退出时，不能报"已就绪"。
                with self._lock:
                    proc = self._proc
                if proc is not None and proc.poll() is None:
                    return True
            time.sleep(0.5)
        return False

    def stop(self, timeout: float = GRACEFUL_TIMEOUT) -> dict[str, Any]:
        """先优雅停（stdin shutdown），超时才强杀；返回判定结果（见模块文档）。"""
        with self._lock:
            proc = self._proc
            self._proc = None
        # 停掉保留巡检线程：它只在运行期有意义，停止后不应再动日志目录。
        self._retention_stop.set()
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
            drain=self._capture.drain if self._capture is not None else None,
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
        # 退出后的残留清理：确认锁已释放后删掉 run/instance.{lock,json}。
        # 实测缺口：干净退出后 instance.lock 仍留在数据目录里，用户会把它当成
        # "上次崩溃没退干净"的证据。
        result["residue"] = cleanup_instance_files(self._root)
        self._close_log()
        return result

    def _close_log(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.close()


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
