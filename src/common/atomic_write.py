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

import os
import tempfile
from pathlib import Path


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
        os.replace(tmp, target)
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
