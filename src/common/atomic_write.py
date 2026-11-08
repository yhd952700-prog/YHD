"""Atomic whole-file replacement.

A plain ``open(path, "w")`` truncates the target before the new bytes are
durable: a power cut (or an exception mid-write) leaves a half-written file.
For the hash-chained stores in this project that is worse than a failed write
-- the store then cannot be loaded at all, or loads as a silently truncated
chain.

The sequence implemented here is the standard one:

1. write the new bytes to a temporary file in the SAME directory,
2. ``flush`` + ``fsync`` so the bytes are on disk before anything points at
   them,
3. ``os.replace`` -- an atomic rename that readers either see the old file or
   the new file, never a partial one,
4. best-effort ``fsync`` of the directory so the rename itself survives a
   power cut (POSIX; ignored on Windows, which does not support opening a
   directory handle this way).
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import tempfile
import time
from pathlib import Path
from typing import Iterator

#: ``os.replace`` 在 Windows 上会因目标文件正被别的句柄打开而失败
#: （WinError 5 / sharing violation）。这是**瞬态**冲突，短重试即可跨过；
#: 重试耗尽后仍然抛出，绝不把"没写进去"伪装成成功。
_REPLACE_ATTEMPTS = 8
_REPLACE_RETRY_DELAY = 0.05
_REPLACE_RETRY_CAP = 0.5

#: 跨实例写锁的默认等待上限（秒）。超时抛 ``TimeoutError``，不静默续写。
DEFAULT_LOCK_TIMEOUT_SECONDS = 15.0

#: 读取时的短重试：Windows 上另一个进程正在 ``os.replace`` 时，打开目标
#: 文件可能瞬时返回共享冲突（Errno 13）。只重试，不改变"读不到就报错"。
_READ_ATTEMPTS = 8
_READ_RETRY_DELAY = 0.02


class StorageWriteError(OSError):
    """数据目录不可写（磁盘满 / 只读 / 权限 / 被占用）的中文可行动错误。

    继承 ``OSError`` 以兼容既有的 ``except OSError`` 分支；``str(exc)`` 本身
    就是给人看的中文说明，不含英文堆栈。
    """


#: ``GetVolumeInformationW`` 的 FILE_READ_ONLY_VOLUME 标志位。
_FILE_READ_ONLY_VOLUME = 0x00080000


def volume_is_read_only(directory: str | os.PathLike[str]) -> bool:
    """判断 ``directory`` 所在卷是否为只读卷（写保护）。

    为什么要提前判断：在 Windows 的写保护卷上，为**新建**文件调用
    ``CreateFileW`` 可能长时间阻塞（实测 diskpart 只读 VHD 上 ``mkstemp``
    超 20s 不返回），而不是立刻报错。先做一次廉价查询，把"卡死"变成
    "明确的中文错误"。
    """
    try:
        absolute = os.path.abspath(os.fspath(directory))
    except (TypeError, ValueError):
        return False
    if os.name != "nt":  # pragma: no cover - POSIX path
        try:
            statvfs = os.statvfs(absolute)
            return bool(statvfs.f_flag & getattr(os, "ST_RDONLY", 1))
        except OSError:
            return False
    try:
        import ctypes

        drive = os.path.splitdrive(absolute)[0]
        if not drive:
            return False
        root = drive + "\\"
        flags = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            None,
            0,
            None,
            None,
            ctypes.byref(flags),
            None,
            0,
        )
        return bool(ok) and bool(flags.value & _FILE_READ_ONLY_VOLUME)
    except Exception:  # noqa: BLE001 - 探测失败时按可写处理，交给真正写入报错
        return False


def describe_storage_failure(
    exc: BaseException, target: str | os.PathLike[str]
) -> str:
    """把底层 ``OSError`` 翻译成一句中文可行动提示（含目标路径）。"""
    path = os.fspath(target)
    errno_value = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    disk_full = errno_value in (28, 112) or winerror in (39, 112)
    read_only = winerror == 19 or errno_value == 30  # ERROR_WRITE_PROTECT / EROFS
    if disk_full:
        reason = "磁盘已满"
        action = "请清理该磁盘的可用空间（建议至少保留 100MB）后重试"
    elif read_only:
        reason = "所在磁盘为只读"
        action = "请解除磁盘写保护，或把数据目录改到可写磁盘后重试"
    elif isinstance(exc, PermissionError) or winerror == 5 or errno_value == 13:
        reason = "目录不可写（权限不足或被其他程序占用）"
        action = "请检查目录权限，并关闭正在占用该目录的程序后重试"
    else:
        # 底层只给 "unable to open database file"/"disk I/O error" 这类模糊
        # 信息时，把最常见原因直接写出来，保证用户知道下一步做什么。
        reason = "写入失败（常见原因：磁盘已满、数据目录只读或文件被占用）"
        action = "请先检查磁盘可用空间与目录权限，并关闭占用该目录的程序后重试"
    return (
        f"无法写入「{path}」：{reason}。{action}。"
        f"已成功写入的历史数据不受影响（原始错误：{exc}）。"
    )


_STORAGE_HINTS = (
    "disk is full",
    "database or disk is full",
    "disk i/o error",
    "unable to open database file",
    "readonly database",
    "read-only database",
    "attempt to write a readonly database",
    "no space left",
    "write protected",
    "database is locked",
)


def is_storage_failure(exc: BaseException) -> bool:
    """是否属于磁盘满/只读/被占用这类"存储环境故障"。

    用于启动期降级日志：这类错误给中文可行动提示即可，不应打印英文堆栈。
    """
    if isinstance(exc, StorageWriteError):
        return True
    errno_value = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    if errno_value in (28, 30, 112) or winerror in (5, 19, 39, 112):
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _STORAGE_HINTS)


def ensure_writable_directory(
    directory: str | os.PathLike[str], *, purpose: str = "数据目录"
) -> None:
    """只读卷上立即失败，避免后续临时文件创建阻塞。"""
    if volume_is_read_only(directory):
        raise StorageWriteError(
            f"无法写入{purpose}「{os.fspath(directory)}」：所在磁盘为只读。"
            "请解除磁盘写保护，或把数据目录改到可写磁盘后重试。"
            "已成功写入的历史数据不受影响。"
        )


def read_text_with_retry(
    path: str | os.PathLike[str], encoding: str = "utf-8"
) -> str:
    """读取整个文件，跨过 replace 造成的瞬时共享冲突。

    ``FileNotFoundError`` 原样抛出（调用方用它判断"还没有文件"）；其它
    ``OSError`` 短暂重试后仍失败则抛出，绝不返回半截内容。
    """
    last: OSError | None = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read()
        except FileNotFoundError:
            raise
        except OSError as exc:
            last = exc
            if attempt + 1 >= _READ_ATTEMPTS:
                break
            time.sleep(min(_READ_RETRY_DELAY * (attempt + 1), 0.2))
    assert last is not None
    raise last



def _replace_with_retry(tmp: Path, target: Path) -> None:
    """``os.replace`` with a bounded retry for transient Windows sharing errors."""
    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except OSError as exc:
            last = exc
            if attempt + 1 >= _REPLACE_ATTEMPTS:
                break
            time.sleep(min(_REPLACE_RETRY_DELAY * (attempt + 1), _REPLACE_RETRY_CAP))
    if last is not None:
        raise last


@contextmanager
def exclusive_file_lock(
    lock_path: str | os.PathLike[str],
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    *,
    description: str = "文件",
) -> Iterator[None]:
    """跨进程排他锁（Windows ``msvcrt`` / POSIX ``fcntl``）。

    ``threading.Lock`` 只覆盖同一进程内的线程；多个 worker / 多个网关进程
    同时做「读全量 → 改 → 整份覆盖」时，后写者仍会覆盖先写者。这里用操作
    系统级文件锁真正串行化写事务。锁文件本身可长期存在（内容无意义），
    进程退出时由内核释放，不会留下僵尸锁。

    Windows 的 ``msvcrt`` region lock 需要一个可锁的字节，空文件先写占位
    字节；锁冲突不是错误，而是在 ``timeout`` 内轮询重试。
    """
    lock_path = os.fspath(lock_path)
    directory = os.path.dirname(os.path.abspath(lock_path))
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError as exc:
        raise StorageWriteError(describe_storage_failure(exc, lock_path)) from exc
    acquired = False
    try:
        # Windows 的 region lock 也阻止**读**被锁的字节：不能在持锁期间用
        # ``read(1)`` 判断文件是否为空（另一个进程持锁时这里会直接抛
        # PermissionError）。用 fstat 看大小，必要时抢占写入一个占位字节；
        # 抢不到说明别的进程已持锁，直接进入等锁循环即可。
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - POSIX path
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{description}写锁等待超时（{timeout:.0f}s）：{lock_path}"
                    ) from exc
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - POSIX path
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability for the rename itself."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        # Windows cannot open a directory this way; the rename is still announced
        # by the filesystem metadata and this is a best-effort hardening step.
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _cleanup_orphan_temps(target: Path) -> None:
    """清理上一次崩溃留下的 ``<name>.<random>.tmp`` 孤儿。

    强杀（SIGKILL / TerminateProcess）不会执行 ``except`` 里的 unlink，于是
    临时文件会残留。它不影响主文件读取（主文件永远只有旧版或新版），但会
    累积；在下一次成功写入后顺手清掉。Windows 上仍被其他进程打开的临时
    文件删不掉（PermissionError），天然不会误删活跃写入；POSIX 上 unlink
    对打开中的文件也成功，因此只清理超过 5 分钟的旧孤儿。
    """
    try:
        candidates = list(target.parent.glob(target.name + ".*.tmp"))
    except OSError:
        return
    min_age = 0.0 if os.name == "nt" else 300.0
    now = time.time()
    for candidate in candidates:
        try:
            if min_age and now - candidate.stat().st_mtime < min_age:
                continue
            candidate.unlink()
        except OSError:
            continue


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically.

    存储层故障（磁盘满 / 只读盘 / 无权限 / 被占用）统一转成
    :class:`StorageWriteError`，``str(exc)`` 即中文可行动提示；主文件保持
    上一版完整内容，不会出现半写文件。
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageWriteError(describe_storage_failure(exc, target.parent)) from exc
    # 只读卷上 mkstemp 可能长时间阻塞，必须先快速失败。
    ensure_writable_directory(target.parent)

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
    except OSError as exc:
        raise StorageWriteError(describe_storage_failure(exc, target)) from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, target)
    except BaseException as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, OSError) and not isinstance(exc, StorageWriteError):
            raise StorageWriteError(describe_storage_failure(exc, target)) from exc
        raise
    _fsync_directory(target.parent)
    _cleanup_orphan_temps(target)


def atomic_write_text(path: str | os.PathLike[str], text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text`` atomically."""
    atomic_write_bytes(path, text.encode(encoding))
