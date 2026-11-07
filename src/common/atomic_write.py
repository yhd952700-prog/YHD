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
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = open(lock_path, "a+b")
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


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _fsync_directory(target.parent)


def atomic_write_text(path: str | os.PathLike[str], text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text`` atomically."""
    atomic_write_bytes(path, text.encode(encoding))
