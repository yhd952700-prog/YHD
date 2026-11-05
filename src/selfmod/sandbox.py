"""Process-level isolation for test execution.

This is **not a sandbox**. Windows has no usable gVisor/container sandbox in this
product (single-machine, double-click install), so the honest boundary is:

* the test process runs with ``cwd`` set to a throw-away copy of the workspace;
* it is killed after a wall-clock timeout;
* its process tree is killed when it exceeds a memory (RSS) or CPU-time ceiling;
* on POSIX a hard ``RLIMIT_AS`` / ``RLIMIT_CPU`` is also applied; on Windows a
  Job Object memory cap is attempted best-effort;
* only the tail of its output is retained.

It does **not** prevent: writing outside the copy, network access, reading
secrets readable by the current user, CPU cache side channels, or a child that
escapes the process tree (Job Objects reduce but do not absolutely guarantee
this). See ``docs/self-upgrade.md`` for the full list.
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Sequence

import psutil

from .errors import SandboxError
from .models import ResourceLimits, RunResult

# Environment variables whose *names* look like credentials are dropped before
# the test process starts. This narrows accidental leakage (an inherited
# ``*_API_KEY`` is no longer directly readable via ``os.environ``), but it is
# NOT a boundary: the child can still read any file the current user can read.
_SECRET_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PASSPHRASE",
    "AUTH",
)
_KEEP_ENV = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONIOENCODING",
        "VIRTUAL_ENV",
        "LANG",
        "LC_ALL",
        "TZ",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
)


def _build_env(workdir: Path) -> Dict[str, str]:
    """Minimal environment: inherited values minus credential-looking names."""
    env: Dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in _KEEP_ENV or not any(marker in upper for marker in _SECRET_ENV_MARKERS):
            env[name] = value
    pythonpath = [str(workdir), str(workdir / "src")]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _tail(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            text = "…(输出已截断，仅保留末尾)\n" + text
        return text
    except OSError as exc:
        return f"<无法读取测试输出：{exc}>"


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except psutil.NoSuchProcess:
        return
    except Exception:
        return


def _tree_usage(pid: int) -> tuple[int, float]:
    total_rss = 0
    total_cpu = 0.0
    try:
        root = psutil.Process(pid)
        processes = [root] + root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0, 0.0
    for process in processes:
        try:
            total_rss += process.memory_info().rss
            times = process.cpu_times()
            total_cpu += times.user + times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total_rss, total_cpu


def _windows_job_memory_cap(process: subprocess.Popen, memory_mb: int):
    """Best-effort Windows Job Object memory cap. Returns handle or None."""
    if os.name != "nt" or memory_mb <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimit()
        # JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(handle)
            return None
        if not kernel32.AssignProcessToJobObject(
            handle, ctypes.c_void_p(int(process._handle))  # type: ignore[attr-defined]
        ):
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return None


def _close_windows_job(handle) -> None:
    if handle is None:
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass


def _posix_limits(limits: ResourceLimits):
    if os.name == "nt":
        return None, None

    def _apply() -> None:
        import resource

        if limits.memory_mb > 0:
            value = limits.memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (value, value))
            except ValueError:
                pass
        if limits.cpu_seconds > 0:
            value = int(math.ceil(limits.cpu_seconds))
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (value, value + 1))
            except ValueError:
                pass

    return _apply, True


def run_isolated(
    command: Sequence[str],
    *,
    cwd: str | Path,
    limits: ResourceLimits,
) -> RunResult:
    """Run ``command`` in ``cwd`` with timeout and resource ceilings."""
    if not command:
        raise SandboxError("测试命令为空")
    if limits.timeout_seconds <= 0:
        raise SandboxError("测试超时必须大于 0 秒")
    workdir = Path(cwd)
    if not workdir.is_dir():
        raise SandboxError(f"测试工作目录不存在：{workdir}")

    env = _build_env(workdir)

    preexec_fn, start_new_session = _posix_limits(limits)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )

    output_fd, output_name = tempfile.mkstemp(prefix="liuhao-selfmod-test-", suffix=".log")
    output_path = Path(output_name)
    start = time.monotonic()
    job_handle = None
    try:
        with os.fdopen(output_fd, "wb") as output_file:
            try:
                process = subprocess.Popen(
                    list(command),
                    cwd=str(workdir),
                    stdin=subprocess.DEVNULL,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                    preexec_fn=preexec_fn,
                    start_new_session=start_new_session,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise SandboxError(f"测试命令无法启动：{command[0]!r}（{exc}）") from exc

        job_handle = _windows_job_memory_cap(process, limits.memory_mb)
        timed_out = False
        limit_reason = ""
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            elapsed = time.monotonic() - start
            if elapsed > limits.timeout_seconds:
                timed_out = True
                limit_reason = "timeout"
                _kill_tree(process.pid)
                break
            if limits.memory_mb > 0 or limits.cpu_seconds > 0:
                rss, cpu = _tree_usage(process.pid)
                if limits.memory_mb > 0 and rss > limits.memory_mb * 1024 * 1024:
                    limit_reason = "memory"
                    _kill_tree(process.pid)
                    break
                if limits.cpu_seconds > 0 and cpu > limits.cpu_seconds:
                    limit_reason = "cpu"
                    _kill_tree(process.pid)
                    break
            remaining = limits.timeout_seconds - elapsed
            time.sleep(min(limits.poll_interval_seconds, max(0.01, remaining)))

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_tree(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        duration = time.monotonic() - start
        return RunResult(
            command=list(command),
            returncode=int(process.returncode if process.returncode is not None else -1),
            timed_out=timed_out,
            limit_reason=limit_reason,
            duration_seconds=duration,
            output_tail=_tail(output_path, limits.max_output_bytes),
        )
    finally:
        _close_windows_job(job_handle)
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass


def command_from(value: str | Sequence[str]) -> List[str]:
    """Turn a user-provided command into an argument list (never ``shell=True``)."""
    if isinstance(value, str):
        import shlex

        parts = shlex.split(value, posix=(os.name != "nt"))
        if not parts:
            raise SandboxError("测试命令为空")
        return parts
    return [str(item) for item in value]
