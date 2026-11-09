"""独立升级编排器（supervisor）：停服 → 等锁 → 应用 → 启动 → 健康复核。

为什么必须独立进程
------------------
网关进程自己没法完成这次升级：apply 要替换的正是它正在执行的 ``.py/.pyd``，
Windows 不允许自替换运行中的映像，而且它也无法"重启自己"。所以真正的编排
放在一个**安装根之外**的进程里：

1. 启动时把当前 ``src`` 复制到 ``<data>/run/upgrade-agent/<uuid>/``，从副本
   运行；工作目录、日志、锁、journal 全在 ``<data>`` 下，不持有安装根内任何
   句柄。
2. 只停 ``<data>/run/instance.json`` 里记录的 pid，并且必须先确认该进程确实是
   ``python -m src.gateway``；**绝不按镜像名批量杀进程**。
3. 等锁判据是 ``instance.lock`` 的 ``open+read(1)`` 不再抛 ``PermissionError``
   **且** pid 已消失；硬超时（默认 45s）视为"停不下来"，此时**不应用**。
4. 每个阶段先写 ``<data>/upgrade-state/orchestration.json`` 并 fsync 再动手；
   进程被外部杀死后，下次启动读到非终态阶段会先恢复（回滚 → 启动旧版 →
   健康复核）再谈新的升级请求。
5. 失败链只尝试一次：新版本起不来 → 回滚到预置备份 → 起旧版 → 健康复核；
   旧版也起不来 → ``phase=manual_intervention``，把 ``code.zip`` / 回收区路径
   写进 journal 并给出可读提示，不循环重试。

HTTP 契约不变：``/v1/upgrade/apply`` 仍是"只装不重启"的同步接口；只有显式
传 ``mode="orchestrated"`` 时才改为"写请求文件 + 返回 202"，由本模块执行。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from src.common.instance_lock import (
    InstanceGuardUnavailable,
    InstanceLock,
    InstanceLockBusy,
)

from .keyformat import (
    assess_key_visibility,
    downgrade_key_for_legacy,
    key_format_needs_downgrade,
)

JOURNAL_SCHEMA = "liuhao.upgrade-orchestration/1"
REQUEST_SCHEMA = "liuhao.upgrade-orchestration-request/1"
JOURNAL_NAME = "orchestration.json"
REQUEST_NAME = "orchestration-request.json"
AGENT_LOCK_NAME = "upgrade-agent"

#: 终态：看到这些阶段就说明上次编排已经结束，可以直接开始新的请求。
TERMINAL_PHASES = frozenset(
    {
        "completed",
        "rolled_back",
        "recovered",
        "failed_not_applied",
        "manual_intervention",
    }
)

#: 非终态：上次进程在动手途中消失，下次启动必须先恢复。
INTERRUPTED_PHASES = frozenset(
    {
        "checking",
        "prepared",
        "stopping",
        "waiting_lock",
        "applying",
        "starting",
        "health_checking",
        "rolling_back",
    }
)

DEFAULT_STOP_TIMEOUT_SECONDS = 45.0
DEFAULT_HEALTH_TIMEOUT_SECONDS = 120.0
HEALTH_INTERVAL_SECONDS = 0.5


class OrchestratorBusy(RuntimeError):
    """已经有一次升级编排未结束，拒绝叠加新的请求。"""

    def __init__(self, phase: str, message: str = "") -> None:
        super().__init__(message or f"已有升级正在进行（阶段：{phase or '未知'}）")
        self.phase = phase
        self.message = str(super().__str__())


class OrchestratorError(RuntimeError):
    """编排器自身无法继续（不是升级失败，而是前提不成立）。"""


# ----------------------------------------------------------------------
# 基础工具：时间 / 原子落盘 / journal
# ----------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def orchestration_dir(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir).expanduser().resolve() / "upgrade-state"


def journal_path(data_dir: str | os.PathLike[str]) -> Path:
    return orchestration_dir(data_dir) / JOURNAL_NAME


def request_path(data_dir: str | os.PathLike[str]) -> Path:
    return orchestration_dir(data_dir) / REQUEST_NAME


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """写临时文件 → fsync → 原子替换。

    阶段记录必须在"动手"之前真正落盘：只 rename 不 fsync 时，掉电后可能留下
    一个大小为 0 的 journal，恢复逻辑就失去了依据。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    # 目录项也尽量刷一次；Windows 上打开目录会失败，忽略即可（文件本身已 fsync）。
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class Journal:
    """``orchestration.json`` 的读写器：每次 update 都立即 fsync。"""

    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        *,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.path = journal_path(self.data_dir)
        self.data: Dict[str, Any] = data or {
            "schema": JOURNAL_SCHEMA,
            "request_id": "",
            "attempt": 0,
            "phase": "idle",
            "message": "",
            "from_version": "",
            "target_version": "",
            "backup_id": "",
            "gateway_pid": 0,
            "gateway_port": 0,
            "events": [],
            "started_at": "",
            "updated_at": "",
        }

    @classmethod
    def load(cls, data_dir: str | os.PathLike[str]) -> Optional["Journal"]:
        path = journal_path(data_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("schema") != JOURNAL_SCHEMA:
            return None
        return cls(data_dir, data=data)

    def update(self, phase: str, *, message: str = "", **fields: Any) -> "Journal":
        if not self.data.get("started_at"):
            self.data["started_at"] = _now()
        self.data["phase"] = phase
        self.data["message"] = message
        for key, value in fields.items():
            self.data[key] = value
        self.data["updated_at"] = _now()
        events = self.data.setdefault("events", [])
        events.append({"at": self.data["updated_at"], "phase": phase, "message": message})
        # journal 只在一次编排的生命周期内增长；留最近 200 条足够诊断。
        del events[:-200]
        self.save()
        return self

    def save(self) -> None:
        _atomic_write_json(self.path, self.data)

    def is_terminal(self) -> bool:
        return self.data.get("phase") in TERMINAL_PHASES

    def summary(self) -> Dict[str, Any]:
        return {
            key: self.data.get(key)
            for key in (
                "request_id",
                "attempt",
                "phase",
                "message",
                "from_version",
                "target_version",
                "backup_id",
                "gateway_pid",
                "gateway_port",
                "started_at",
                "updated_at",
            )
        }


def load_journal(data_dir: str | os.PathLike[str]) -> Optional[Dict[str, Any]]:
    journal = Journal.load(data_dir)
    return journal.data if journal else None


def orchestration_summary(data_dir: str | os.PathLike[str]) -> Optional[Dict[str, Any]]:
    """给 ``/v1/upgrade/status`` 用的只读摘要（没有 journal 时返回 None）。"""
    journal = Journal.load(data_dir)
    return journal.summary() if journal else None


def needs_recovery(journal: Optional[Dict[str, Any]]) -> bool:
    if not journal:
        return False
    return journal.get("phase") in INTERRUPTED_PHASES


# ----------------------------------------------------------------------
# 请求文件（API → supervisor 的唯一通道）
# ----------------------------------------------------------------------
def write_request(
    data_dir: str | os.PathLike[str],
    *,
    version: str,
    app_root: str | os.PathLike[str],
    port: Optional[int] = None,
    request_id: Optional[str] = None,
    python: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "schema": REQUEST_SCHEMA,
        "request_id": request_id or uuid.uuid4().hex,
        "version": version,
        "app_root": str(Path(app_root).expanduser().resolve()),
        "port": int(port or 0),
        "python": python or sys.executable,
        "created_at": _now(),
    }
    _atomic_write_json(request_path(data_dir), payload)
    return payload


def read_request(data_dir: str | os.PathLike[str]) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(request_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != REQUEST_SCHEMA:
        return None
    return payload


def recorded_gateway_port(data_dir: str | os.PathLike[str]) -> int:
    """网关上一次运行时记录的端口；读不到就回退到 ``PORT`` / 8080。"""
    try:
        instance = json.loads(
            (Path(data_dir).expanduser().resolve() / "run" / "instance.json").read_text(
                encoding="utf-8"
            )
        )
        port = int(instance.get("port") or 0)
        if port:
            return port
    except (OSError, ValueError):
        pass
    try:
        return int(os.environ.get("PORT") or 0) or 8080
    except ValueError:
        return 8080


def consume_request(data_dir: str | os.PathLike[str]) -> None:
    try:
        request_path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass


# ----------------------------------------------------------------------
# 进程：复制到安装根之外、只按 pid 停网关
# ----------------------------------------------------------------------
def _load_psutil():
    try:
        import psutil  # type: ignore

        return psutil
    except Exception:  # pragma: no cover - 仅在环境缺包时走到
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    psutil = _load_psutil()
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def describe_process(pid: int) -> Dict[str, Any]:
    """进程清单里的一行：pid / 名称 / 路径 / 命令行。"""
    info: Dict[str, Any] = {"pid": pid}
    psutil = _load_psutil()
    if psutil is None:
        info["error"] = "psutil 不可用，无法核对命令行"
        return info
    try:
        proc = psutil.Process(pid)
        info["name"] = proc.name()
        info["exe"] = proc.exe()
        info["cmdline"] = proc.cmdline()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    if port <= 0:
        return False
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _is_our_gateway(
    pid: int, app_root: Path, port: int
) -> Tuple[bool, Dict[str, Any]]:
    """确认这个 pid 确实是本安装树的网关。

    只接受两种证据：命令行含 ``-m src.gateway``，或可执行文件在安装根内。
    两者都拿不到（没有 psutil）就拒绝停止——宁可报错，也不猜测性地杀进程。
    """
    info = describe_process(pid)
    if info.get("error"):
        return False, info
    cmdline: Sequence[str] = info.get("cmdline") or []
    joined = " ".join(str(part) for part in cmdline)
    has_marker = "src.gateway" in joined
    exe = str(info.get("exe") or "")
    under_app = bool(exe) and str(app_root).lower() in exe.lower()
    info["matches_gateway_marker"] = has_marker
    info["exe_under_app_root"] = under_app
    info["control_port_listening"] = _port_listening(port) if port else None
    return bool(has_marker or under_app), info


def stop_recorded_gateway(
    *,
    data_dir: str | os.PathLike[str],
    app_root: str | os.PathLike[str],
    port: Optional[int] = None,
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """按 ``instance.json`` 记录的 pid 停网关；绝不按进程名批量杀。"""
    data = Path(data_dir).expanduser().resolve()
    root = Path(app_root).expanduser().resolve()
    result: Dict[str, Any] = {
        "pid": 0,
        "port": int(port or 0),
        "stopped": False,
        "terminated": False,
        "killed": False,
        "steps": [],
    }
    try:
        instance = json.loads((data / "run" / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        instance = {}
    pid = int(instance.get("pid") or 0)
    recorded_port = int(port or instance.get("port") or 0)
    result["pid"] = pid
    result["port"] = recorded_port
    result["instance"] = {
        key: instance.get(key) for key in ("pid", "url", "started_at")
    }
    if pid <= 0:
        result.update(stopped=True, reason="没有记录到网关进程（可能已经停止）")
        return result
    if not _pid_alive(pid):
        result.update(stopped=True, reason="记录的网关进程已经退出")
        return result

    ok, info = _is_our_gateway(pid, root, recorded_port)
    result["process"] = info
    if not ok:
        result["error"] = (
            "记录的 pid 不能确认为本安装的网关进程，已拒绝停止；"
            "不会按进程名批量杀进程。"
        )
        return result

    psutil = _load_psutil()
    assert psutil is not None  # _is_our_gateway 已经证明可用
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        result["terminated"] = True
        result["steps"].append({"at": _now(), "action": "terminate", "pid": pid})
    except Exception as exc:
        result["error"] = f"发送停止信号失败：{type(exc).__name__}: {exc}"
        return result

    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.25)
    if _pid_alive(pid):
        try:
            psutil.Process(pid).kill()
            result["killed"] = True
            result["steps"].append({"at": _now(), "action": "kill", "pid": pid})
        except Exception as exc:
            result["error"] = f"强制结束失败：{type(exc).__name__}: {exc}"
            return result
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.25)

    result["stopped"] = not _pid_alive(pid)
    if recorded_port:
        port_deadline = time.monotonic() + 10.0
        while time.monotonic() < port_deadline and _port_listening(recorded_port):
            time.sleep(0.25)
        result["port_released"] = not _port_listening(recorded_port)
    else:
        result["port_released"] = None
    if result["stopped"] and result["port_released"] is False:
        result["error"] = f"进程已退出，但控制端口 {recorded_port} 仍被监听"
    return result


def wait_for_lock_release(
    data_dir: str | os.PathLike[str],
    *,
    pid: Optional[int] = None,
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    interval: float = 0.25,
) -> Dict[str, Any]:
    """等 ``instance.lock`` 真正可读，且记录的 pid 已消失。

    只 ``open`` 不读不会触发 ``msvcrt`` 的字节锁；必须 ``read(1)``。
    """
    lock = Path(data_dir).expanduser().resolve() / "run" / "instance.lock"
    deadline = time.monotonic() + max(1.0, timeout)
    attempts = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempts += 1
        if pid and _pid_alive(pid):
            time.sleep(interval)
            continue
        try:
            with open(lock, "rb") as handle:
                handle.read(1)
            return {"released": True, "attempts": attempts, "pid_alive": False}
        except FileNotFoundError:
            return {
                "released": True,
                "attempts": attempts,
                "pid_alive": False,
                "reason": "锁文件不存在",
            }
        except PermissionError as exc:
            last_error = f"PermissionError(errno={getattr(exc, 'errno', '')})"
            time.sleep(interval)
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(interval)
    return {
        "released": False,
        "attempts": attempts,
        "pid_alive": _pid_alive(pid or 0),
        "error": f"等待实例锁释放超时（{timeout:.0f}s，最后一次：{last_error}）",
    }


def copy_runtime_source(destination: Path) -> Path:
    """把当前 ``src`` 复制到安装根之外，supervisor 从副本运行。"""
    source = Path(__file__).resolve().parents[1]
    target = destination / "src"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return destination


def spawn_supervisor(
    *,
    data_dir: str | os.PathLike[str],
    app_root: str | os.PathLike[str],
    port: Optional[int] = None,
    python: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """复制源码到 ``<data>/run/upgrade-agent/<uuid>/`` 并后台拉起 supervisor。"""
    data = Path(data_dir).expanduser().resolve()
    root = Path(app_root).expanduser().resolve()
    runtime_dir = data / "run" / "upgrade-agent" / uuid.uuid4().hex
    runtime_dir.mkdir(parents=True, exist_ok=True)
    copy_runtime_source(runtime_dir)
    # 日志放在运行副本目录里：``<data>/run/**`` 已被数据备份排除。放到
    # ``<data>/logs`` 会被自己写进数据备份，回滚还原时又要覆盖这个**仍被
    # supervisor 打开**的文件，Windows 直接拒绝访问（WinError 5）。
    log_path = runtime_dir / "supervisor.log"
    python_exe = python or sys.executable

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(runtime_dir),
            "LIUHAO_APP_ROOT": str(root),
            "LIUHAO_DATA_DIR": str(data),
            "LIUHAO_INSTANCE_ROOT": str(data),
        }
    )
    if port:
        env["PORT"] = str(port)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    args = [
        python_exe,
        "-m",
        "src.upgrade.orchestrator",
        "--run-dir",
        str(runtime_dir),
        "--app-root",
        str(root),
        "--data-dir",
        str(data),
        "--python",
        python_exe,
    ]
    if port:
        args.extend(["--port", str(port)])
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    handle = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(runtime_dir),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        handle.close()
    return {
        "pid": proc.pid,
        "runtime_dir": str(runtime_dir),
        "log": str(log_path),
        "python": python_exe,
    }


def request_orchestrated_apply(
    *,
    data_dir: str | os.PathLike[str],
    app_root: str | os.PathLike[str],
    version: str,
    port: Optional[int] = None,
    python: Optional[str] = None,
) -> Dict[str, Any]:
    """``mode="orchestrated"`` 的入口：只写请求文件并拉起 supervisor。"""
    journal = Journal.load(data_dir)
    if journal is not None and not journal.is_terminal():
        raise OrchestratorBusy(
            str(journal.data.get("phase") or ""),
            f"已有升级正在进行（阶段：{journal.data.get('phase')}），"
            "本次请求未执行。",
        )
    if journal is not None and needs_recovery(journal.data):
        raise OrchestratorBusy(
            str(journal.data.get("phase") or ""),
            "上次升级未正常结束，需要先由 supervisor 恢复；本次请求未执行。",
        )
    request = write_request(
        data_dir,
        version=version,
        app_root=app_root,
        port=port,
        python=python,
    )
    supervisor = spawn_supervisor(
        data_dir=data_dir,
        app_root=app_root,
        port=port,
        python=python,
    )
    return {"request": request, "supervisor": supervisor}


# ----------------------------------------------------------------------
# 启动网关 / 健康复核
# ----------------------------------------------------------------------
def start_gateway(
    *,
    app_root: str | os.PathLike[str],
    data_dir: str | os.PathLike[str],
    python: str,
    port: int,
    log_path: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    env = os.environ.copy()
    # supervisor 自己的 PYTHONPATH 指向运行副本；网关必须从安装根解析 src。
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "LIUHAO_APP_ROOT": str(app_root),
            "LIUHAO_DATA_DIR": str(data_dir),
            "LIUHAO_INSTANCE_ROOT": str(data_dir),
            "PORT": str(port),
        }
    )
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            [python, "-u", "-m", "src.gateway"],
            cwd=str(app_root),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
    finally:
        handle.close()
    return proc


def probe_gateway_health(
    *,
    host: str = "127.0.0.1",
    port: int,
    timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
    interval: float = HEALTH_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    """只接受 ``/v1/health`` 返回的、含 ``status`` 的 JSON 对象。"""
    from .startup_probe import _probe_health

    deadline = time.monotonic() + max(1.0, timeout)
    rounds = 0
    last: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        rounds += 1
        path, status, body, _all_missing = _probe_health(
            host, port, ("/v1/health",), 1.0
        )
        last = {"path": path, "status_code": status}
        if path and body.get("status"):
            return {
                "ok": True,
                "path": path,
                "status_code": status,
                "status": body.get("status"),
                "version": body.get("version"),
                "rounds": rounds,
            }
        time.sleep(interval)
    return {
        "ok": False,
        "error": "健康检查超时：/v1/health 未在限定时间内返回带 status 的 JSON",
        "rounds": rounds,
        "last": last,
    }


# ----------------------------------------------------------------------
# 编排主流程
# ----------------------------------------------------------------------
def _default_data_dir(app_root: Path) -> Path:
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from .manager import resolve_data_dir

    return Path(resolve_data_dir(app_root, None))


def _reset_env(app_root: Path, data_dir: Path, port: Optional[int]) -> None:
    os.environ["LIUHAO_APP_ROOT"] = str(app_root)
    os.environ["LIUHAO_DATA_DIR"] = str(data_dir)
    os.environ["LIUHAO_INSTANCE_ROOT"] = str(data_dir)
    if port:
        os.environ["PORT"] = str(port)


def _gateway_log_path(data_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return data_dir / "logs" / f"gateway-{stamp}.log"


def _manual_intervention(
    journal: Journal,
    *,
    reason: str,
    backup_id: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    backup_root = str(journal.data.get("backup_root") or "")
    backup_dir = Path(backup_root) / backup_id if (backup_root and backup_id) else None
    code_zip = str(backup_dir / "code.zip") if backup_dir else ""
    payload = {
        "reason": reason,
        "backup_id": backup_id,
        "backup_dir": str(backup_dir) if backup_dir else "",
        "code_zip": code_zip,
        "journal": str(journal.path),
        "log_dir": str(journal.data_dir / "logs"),
        "detail": detail or {},
    }
    journal.update(
        "manual_intervention",
        message=(
            "升级失败且无法自动恢复，需人工介入："
            f"{reason}。旧版本代码包：{code_zip or '（未找到）'}；"
            f"journal：{journal.path}；日志目录：{journal.data_dir / 'logs'}。"
            "请关闭占用文件的进程后，用代码包或备份手工恢复。"
        ),
        intervention=payload,
    )
    return {"ok": False, "phase": "manual_intervention", "error": reason, **payload}


def _start_and_check(
    *,
    app_root: Path,
    data_dir: Path,
    python: str,
    port: int,
    journal: Journal,
    health_timeout: float,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    proc = start_gateway(
        app_root=app_root,
        data_dir=data_dir,
        python=python,
        port=port,
        log_path=_gateway_log_path(data_dir),
        extra_env=extra_env,
    )
    journal.update(
        "starting",
        message=f"已拉起网关 pid={proc.pid}，等待健康复核",
        gateway_pid=proc.pid,
        gateway_port=port,
    )
    health = probe_gateway_health(port=port, timeout=health_timeout)
    return {"proc": proc, "health": health}


def _terminate_started(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _prepare_key_for_target(
    *,
    journal: Journal,
    data_dir: Path,
    target_version: str,
    backup_id: str,
) -> Dict[str, Any]:
    """回滚到 ``target_version`` 之前，确保身份签名密钥对它是可读的。

    为什么必须做：升级前备份**故意**不包含 ``integrity.key`` /
    ``human_identities.json``（密钥与身份库不外泄），所以 ``manager.rollback``
    只恢复代码与普通数据，不会把 0.1.2 迁移过的 DPAPI 密钥变回明文。
    不在这里降级，旧版启动后就是「健康检查通过、0 个账号可用」。

    返回数据兼容报告；无法恢复登录时带 ``blocked=True`` / ``blocked_reason``。
    """
    try:
        before = assess_key_visibility(data_dir)
    except Exception as exc:  # noqa: BLE001 - 检查失败也不能带着未知状态回滚
        return {
            "blocked": True,
            "blocked_reason": (
                f"回滚前数据格式检查失败：{type(exc).__name__}: {exc}。"
                "为避免把用户锁在门外，已停止回滚。"
            ),
            "stage": "assess",
        }
    journal.update(
        "rolling_back",
        message=(
            "数据格式检查："
            f"密钥={before.get('key_format')}，"
            f"已签身份={before.get('rows_signed')}，"
            f"旧版语义可见={before.get('rows_visible_legacy')}"
        ),
        data_compat=before,
    )
    if not key_format_needs_downgrade(before, target_version):
        return before

    journal.update(
        "rolling_back",
        message=(
            f"准备回滚到 {target_version or '旧版本'}："
            "该版本读不懂当前密钥格式，先把签名密钥降级为明文格式"
        ),
        data_compat=before,
    )
    try:
        result = downgrade_key_for_legacy(data_dir)
    except Exception as exc:  # noqa: BLE001
        return {
            "blocked": True,
            "blocked_reason": (
                f"回滚到 {target_version or '旧版本'} 前，无法把身份签名密钥降级："
                f"{type(exc).__name__}: {exc}。"
                "为避免「能启动但登不进」，已停止回滚并转入人工介入。"
            ),
            "stage": "downgrade",
            "before": before,
        }
    try:
        after = assess_key_visibility(data_dir)
    except Exception:  # noqa: BLE001 - 降级已成功，复核失败按报告处理
        after = dict(before)
    journal.update(
        "rolling_back",
        message=(
            "已将身份签名密钥降级为旧版可读的明文格式（降级窗口内密钥以明文落盘）"
            if result.get("changed")
            else "身份签名密钥已是旧版可读的明文格式，无需降级"
        ),
        data_compat=after,
        key_downgrade=result,
    )
    if after.get("rows_signed") and not after.get("rows_visible_legacy"):
        return {
            "blocked": True,
            "blocked_reason": (
                "降级后仍有已登记账号无法通过旧版完整性校验；"
                "继续回滚会让用户登不进去，已中止。"
            ),
            "stage": "verify",
            **after,
        }
    return after


def _verify_account_visibility(data_dir: Path) -> Dict[str, Any]:
    """回滚后的登录可见性复核：健康检查通过 ≠ 用户能登录。"""
    try:
        return assess_key_visibility(data_dir)
    except Exception as exc:  # noqa: BLE001 - 复核失败时如实带上原因
        return {"error": f"{type(exc).__name__}: {exc}"}


def _restore_old_version(
    *,
    manager,
    journal: Journal,
    backup_id: str,
    app_root: Path,
    data_dir: Path,
    python: str,
    port: int,
    health_timeout: float,
    reason: str,
    started_proc: Optional[subprocess.Popen] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """失败链：停新进程 → 回滚 → 起旧版 → 健康复核。只尝试一次。"""
    _terminate_started(started_proc)
    journal.update("rolling_back", message=f"开始回滚：{reason}", backup_id=backup_id)
    try:
        manager.rollback(backup_id=backup_id or None)
        current, _ = manager.current_version()
    except Exception as exc:
        return _manual_intervention(
            journal,
            reason=f"回滚失败：{type(exc).__name__}: {exc}",
            backup_id=backup_id,
            detail={"failed_stage": "rollback"},
        )

    # 回滚不只是"把代码换回去"：数据格式（身份签名密钥）也必须跟着回到
    # 目标版本能读懂的形态，否则旧版启动后 0 个账号可用。
    compat = _prepare_key_for_target(
        journal=journal,
        data_dir=data_dir,
        target_version=current,
        backup_id=backup_id,
    )
    if compat.get("blocked"):
        return _manual_intervention(
            journal,
            reason=str(compat.get("blocked_reason") or "回滚前无法让旧版读懂数据格式"),
            backup_id=backup_id,
            detail={"failed_stage": "key_downgrade", "data_compat": compat},
        )

    journal.update("rolling_back", message=f"代码已回到 {current}，正在启动旧版本")
    try:
        started = _start_and_check(
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            journal=journal,
            health_timeout=health_timeout,
            extra_env=extra_env,
        )
    except Exception as exc:
        return _manual_intervention(
            journal,
            reason=f"旧版本启动失败：{type(exc).__name__}: {exc}",
            backup_id=backup_id,
            detail={"failed_stage": "start_old", "version": current},
        )
    if not started["health"]["ok"]:
        _terminate_started(started["proc"])
        return _manual_intervention(
            journal,
            reason=f"旧版本健康复核失败：{started['health'].get('error')}",
            backup_id=backup_id,
            detail={"failed_stage": "health_old", "version": current},
        )
    # 健康检查通过 ≠ 用户能登录：健康接口在 0 个可用账号时同样返回 ok。
    # 这里必须再按"目标版本语义"复核一次账号可见性，否则会把"锁在门外"
    # 报成回滚成功。
    visibility = _verify_account_visibility(data_dir)
    journal.update(
        "rolling_back",
        message=(
            "回滚后账号可见性复核："
            f"已签身份={visibility.get('rows_signed')}，"
            f"旧版语义可见={visibility.get('rows_visible_legacy')}"
        ),
        data_compat=visibility,
    )
    if visibility.get("legacy_would_lock_out"):
        _terminate_started(started["proc"])
        return _manual_intervention(
            journal,
            reason=(
                f"已回滚到 {current} 且网关健康，但 {visibility.get('rows_signed')} 个"
                "已登记账号在旧版完整性校验下全部不可用（0 个能登录）。"
                "这不等于回滚成功；请按提示恢复数据密钥格式后再启动。"
            ),
            backup_id=backup_id,
            detail={"failed_stage": "login_visibility", "data_compat": visibility},
        )

    journal.update(
        "rolled_back",
        message=f"已回滚到 {current}，旧版本健康复核通过且账号可登录",
        from_version=current,
        gateway_pid=started["proc"].pid,
        health=started["health"],
        data_compat=visibility,
    )
    return {
        "ok": True,
        "phase": "rolled_back",
        "version": current,
        "health": started["health"],
        "gateway_pid": started["proc"].pid,
        "reason": reason,
    }


def orchestrate(
    *,
    app_root: Path,
    data_dir: Path,
    request: Dict[str, Any],
    journal: Journal,
    port: int,
    python: str,
    stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """执行一次完整编排；每个阶段先落 journal 再动手。"""
    from .models import CheckStatus
    from .update_check import build_manager_from_env

    manager = build_manager_from_env()
    target = str(request.get("version") or "")
    try:
        journal.update(
            "checking",
            message=f"检查更新源（目标 {target}）",
            app_root=str(app_root),
            target_version=target,
        )
        try:
            current, _ = manager.current_version()
        except Exception as exc:
            journal.update("failed_not_applied", message=f"无法读取当前版本：{exc}")
            return {"ok": False, "phase": "failed_not_applied", "error": str(exc)}
        journal.update(
            "checking",
            message=f"当前版本 {current}，检查 {target}",
            from_version=current,
        )
        check = manager.check(trigger="orchestrator")
        if check.status is not CheckStatus.UPDATE_AVAILABLE or check.release is None:
            journal.update(
                "failed_not_applied",
                message=check.message or "没有可安装的更新",
            )
            return {
                "ok": False,
                "phase": "failed_not_applied",
                "error": check.message or "没有可安装的更新",
            }
        if target and check.release.version != target:
            message = (
                f"请求升级到 {target}，但当前可用版本是 {check.release.version}；未执行。"
            )
            journal.update("failed_not_applied", message=message)
            return {"ok": False, "phase": "failed_not_applied", "error": message}

        download = manager.download(check.release)
        journal.update(
            "checking",
            message=f"已下载并校验 {check.release.version}",
            target_version=check.release.version,
        )

        # 先把备份落盘、把 backup_id 写进 journal，之后才允许停服。
        backup_id = manager.prepare_backup(check.release)
        journal.update(
            "prepared",
            message=f"升级前备份已落盘：{backup_id}",
            backup_id=backup_id,
            backup_root=str(manager.config.backup_root),
        )
    except Exception as exc:
        journal.update(
            "failed_not_applied",
            message=f"升级前置步骤失败：{type(exc).__name__}: {exc}",
        )
        return {
            "ok": False,
            "phase": "failed_not_applied",
            "error": f"{type(exc).__name__}: {exc}",
        }

    # ---- 停服 + 等锁：失败则把旧服务拉回来，绝不应用 ----
    journal.update("stopping", message="正在停止网关")
    stop = stop_recorded_gateway(
        data_dir=data_dir, app_root=app_root, port=port, timeout=stop_timeout
    )
    journal.update(
        "stopping",
        message=(
            f"停服结果：stopped={stop.get('stopped')} "
            f"pid={stop.get('pid')} port={stop.get('port')}"
        ),
        stop=stop,
    )
    if not stop.get("stopped"):
        _start_and_check(
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            journal=journal,
            health_timeout=health_timeout,
            extra_env=extra_env,
        )
        message = stop.get("error") or "无法停止正在运行的网关，本次不应用升级。"
        journal.update("failed_not_applied", message=message)
        return {"ok": False, "phase": "failed_not_applied", "error": message, "stop": stop}

    journal.update("waiting_lock", message="等待实例锁释放")
    wait = wait_for_lock_release(
        data_dir, pid=stop.get("pid") or None, timeout=stop_timeout
    )
    journal.update(
        "waiting_lock",
        message=(
            f"等锁结果：released={wait.get('released')} "
            f"attempts={wait.get('attempts')}"
        ),
        wait_lock=wait,
    )
    if not wait.get("released"):
        restarted = _start_and_check(
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            journal=journal,
            health_timeout=health_timeout,
            extra_env=extra_env,
        )
        message = (
            wait.get("error")
            + "；已重新启动旧版本。"
            + ("" if restarted["health"]["ok"] else "（旧版本健康复核未通过，请查看日志）")
        )
        journal.update("failed_not_applied", message=message)
        return {
            "ok": False,
            "phase": "failed_not_applied",
            "error": message,
            "wait_lock": wait,
        }

    # ---- 应用 ----
    journal.update("applying", message=f"正在应用 {check.release.version}", backup_id=backup_id)
    try:
        result = manager.apply(
            download.path, check.release, reuse_backup_id=backup_id
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        journal.update("applying", message=f"应用失败：{message}")
        return _restore_old_version(
            manager=manager,
            journal=journal,
            backup_id=backup_id,
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            health_timeout=health_timeout,
            reason=f"应用失败：{message}",
            extra_env=extra_env,
        )

    # ---- 启动 + 健康复核 ----
    journal.update(
        "starting",
        message=f"已应用 {result.to_version}，正在启动新版本",
        backup_id=result.backup_id,
    )
    started_proc: Optional[subprocess.Popen] = None
    try:
        started = _start_and_check(
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            journal=journal,
            health_timeout=health_timeout,
            extra_env=extra_env,
        )
        started_proc = started["proc"]
        health = started["health"]
    except Exception as exc:
        return _restore_old_version(
            manager=manager,
            journal=journal,
            backup_id=backup_id,
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            health_timeout=health_timeout,
            reason=f"新版本启动失败：{type(exc).__name__}: {exc}",
            extra_env=extra_env,
        )

    if not health["ok"]:
        return _restore_old_version(
            manager=manager,
            journal=journal,
            backup_id=backup_id,
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            health_timeout=health_timeout,
            reason=f"新版本健康复核失败：{health.get('error')}",
            started_proc=started_proc,
            extra_env=extra_env,
        )

    journal.update(
        "completed",
        message=(
            f"{result.from_version} → {result.to_version} 已安装并重启，健康复核通过"
            + (
                f"；保留 {len(result.kept_stale_files)} 个不在新包清单里的文件（未删除）"
                if result.kept_stale_files
                else ""
            )
        ),
        backup_id=result.backup_id,
        gateway_pid=started_proc.pid,
        health=health,
        pruned_files=list(result.pruned_files),
        kept_stale_files=list(result.kept_stale_files),
    )
    return {
        "ok": True,
        "phase": "completed",
        "from_version": result.from_version,
        "to_version": result.to_version,
        "installed_files": result.installed_files,
        "pruned_files": list(result.pruned_files),
        "kept_stale_files": list(result.kept_stale_files),
        "backup_id": result.backup_id,
        "gateway_pid": started_proc.pid,
        "health": health,
    }


def recover(
    *,
    app_root: Path,
    data_dir: Path,
    journal: Journal,
    port: int,
    python: str,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """上次 supervisor 在非终态阶段消失：先恢复，再谈升级。"""
    from .update_check import build_manager_from_env

    phase = str(journal.data.get("phase") or "")
    journal.update("rolling_back", message=f"检测到未完成的升级（{phase}），开始恢复")
    recorded_pid = int(journal.data.get("gateway_pid") or 0)
    if recorded_pid:
        stop = stop_recorded_gateway(
            data_dir=data_dir, app_root=app_root, port=port
        )
        journal.update(
            "rolling_back",
            message=f"恢复前停掉上次拉起的网关：stopped={stop.get('stopped')}",
            recovery_stop=stop,
        )

    manager = build_manager_from_env()
    try:
        current, _ = manager.current_version()
    except Exception as exc:
        return _manual_intervention(
            journal,
            reason=f"恢复时无法读取版本：{type(exc).__name__}: {exc}",
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_read_version"},
        )

    from_version = str(journal.data.get("from_version") or "")
    if from_version and current != from_version:
        backup_id = str(journal.data.get("backup_id") or "")
        try:
            manager.rollback(backup_id=backup_id or None)
            current, _ = manager.current_version()
        except Exception as exc:
            return _manual_intervention(
                journal,
                reason=f"恢复回滚失败：{type(exc).__name__}: {exc}",
                backup_id=backup_id,
                detail={"failed_stage": "recover_rollback", "version": current},
            )
    if from_version and current != from_version:
        return _manual_intervention(
            journal,
            reason=f"恢复后版本仍是 {current}，预期 {from_version}",
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_version_mismatch"},
        )

    # 中断恢复同样可能停在"代码已回滚、密钥还没降级"的中间态。
    compat = _prepare_key_for_target(
        journal=journal,
        data_dir=data_dir,
        target_version=current,
        backup_id=str(journal.data.get("backup_id") or ""),
    )
    if compat.get("blocked"):
        return _manual_intervention(
            journal,
            reason=str(compat.get("blocked_reason") or "恢复前无法让旧版读懂数据格式"),
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_key_downgrade", "data_compat": compat},
        )

    try:
        started = _start_and_check(
            app_root=app_root,
            data_dir=data_dir,
            python=python,
            port=port,
            journal=journal,
            health_timeout=health_timeout,
            extra_env=extra_env,
        )
    except Exception as exc:
        return _manual_intervention(
            journal,
            reason=f"恢复启动失败：{type(exc).__name__}: {exc}",
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_start"},
        )
    if not started["health"]["ok"]:
        _terminate_started(started["proc"])
        return _manual_intervention(
            journal,
            reason=f"恢复后健康复核失败：{started['health'].get('error')}",
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_health"},
        )
    visibility = _verify_account_visibility(data_dir)
    if visibility.get("legacy_would_lock_out"):
        _terminate_started(started["proc"])
        return _manual_intervention(
            journal,
            reason=(
                f"已恢复到 {current} 且网关健康，但所有已登记账号在旧版完整性校验下"
                "都不可用（0 个能登录），不能算恢复成功。"
            ),
            backup_id=str(journal.data.get("backup_id") or ""),
            detail={"failed_stage": "recover_login_visibility", "data_compat": visibility},
        )
    journal.update(
        "recovered",
        message=f"已恢复到 {current} 并重新启动，健康复核通过且账号可登录",
        gateway_pid=started["proc"].pid,
        health=started["health"],
        data_compat=visibility,
    )
    return {
        "ok": True,
        "phase": "recovered",
        "version": current,
        "health": started["health"],
        "gateway_pid": started["proc"].pid,
    }


# ----------------------------------------------------------------------
# CLI 入口
# ----------------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立升级编排器（supervisor）")
    parser.add_argument("--app-root", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--run-dir", default="", help="内部参数：从该副本运行，不再复制")
    parser.add_argument("--stop-timeout", type=float, default=DEFAULT_STOP_TIMEOUT_SECONDS)
    parser.add_argument(
        "--health-timeout", type=float, default=DEFAULT_HEALTH_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--recover-only", action="store_true", help="只做恢复，不处理新的升级请求"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _run_child(
    args: argparse.Namespace, app_root: Path, data_dir: Path
) -> int:
    _reset_env(app_root, data_dir, args.port or None)
    port = args.port or recorded_gateway_port(data_dir)

    lock = InstanceLock(data_dir, name=AGENT_LOCK_NAME)
    try:
        lock.acquire()
    except (InstanceLockBusy, InstanceGuardUnavailable) as exc:
        print(
            json.dumps(
                {"ok": False, "phase": "busy", "error": f"supervisor 已在运行：{exc}"},
                ensure_ascii=False,
            )
        )
        return 2

    try:
        journal = Journal.load(data_dir)
        recovered = None
        if journal is not None and needs_recovery(journal.data):
            recovered = recover(
                app_root=app_root,
                data_dir=data_dir,
                journal=journal,
                port=port,
                python=args.python,
                health_timeout=args.health_timeout,
            )
        request = None if args.recover_only else read_request(data_dir)
        result = None
        if request is not None:
            consume_request(data_dir)
            if journal is None or journal.is_terminal():
                journal = Journal(data_dir)
            journal.data["request_id"] = str(request.get("request_id") or "")
            journal.data["attempt"] = int(journal.data.get("attempt") or 0) + 1
            result = orchestrate(
                app_root=app_root,
                data_dir=data_dir,
                request=request,
                journal=journal,
                port=port,
                python=args.python,
                stop_timeout=args.stop_timeout,
                health_timeout=args.health_timeout,
            )
        payload = {
            "ok": bool(
                (recovered is None or recovered.get("ok"))
                and (result is None or result.get("ok"))
            ),
            "recovered": recovered,
            "result": result,
            "journal": journal.summary() if journal else None,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    finally:
        lock.release()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    app_root = (
        Path(args.app_root).expanduser().resolve()
        if args.app_root
        else Path(__file__).resolve().parents[2]
    )
    data_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else _default_data_dir(app_root)
    )
    if not args.run_dir:
        info = spawn_supervisor(
            data_dir=data_dir,
            app_root=app_root,
            port=args.port or None,
            python=args.python,
        )
        print(json.dumps(info, ensure_ascii=False))
        return 0
    return _run_child(args, app_root, data_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
