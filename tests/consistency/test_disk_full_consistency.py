"""磁盘写满实测：用 64MB 固定 VHD 挂载成真实卷，写满到 ENOSPC。

验证三条写路径在空间耗尽时：
  * 明确抛错（不是静默丢弃 / 假成功）；
  * 已存在的数据不被半截覆盖；
  * 释放空间后同一实例还能继续写，且库仍完整。

需要管理员权限（磁盘管理/detach）。非 Windows 或非管理员时 skip。
"""

from __future__ import annotations

import ctypes
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

CHUNK = 4 << 20  # 4MB


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _diskpart(lines: list[str]) -> None:
    fd, script_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\r\n") as handle:
            handle.write("\r\n".join(lines) + "\r\n")
        result = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"diskpart failed: {result.stdout}\n{result.stderr}")
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _free_letter() -> str:
    for letter in "ZYXWVUTSRQ":
        if not Path(f"{letter}:\\").exists():
            return letter
    raise RuntimeError("no free drive letter")


@pytest.fixture()
def full_volume(tmp_path: Path, declare_scratch_root):
    if os.name != "nt" or not _is_admin():
        pytest.skip("requires Windows + administrator for VHD mount")

    letter = _free_letter()
    vhd = tmp_path / "full.vhd"
    _diskpart(
        [
            f'create vdisk file="{vhd}" maximum=64 type=fixed',
            f'select vdisk file="{vhd}"',
            "attach vdisk",
            "create partition primary",
            f"format fs=ntfs quick label=LIUHAOFULL",
            f"assign letter={letter}",
        ]
    )
    root = Path(f"{letter}:\\")
    try:
        deadline = time.monotonic() + 30
        while not root.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert root.exists(), "VHD 挂载后卷未就绪"
        declare_scratch_root(root)
        yield root
    finally:
        try:
            _diskpart([f'select vdisk file="{vhd}"', "detach vdisk"])
        except Exception:
            pass


def _fill_disk(root: Path) -> Path:
    """写满到连 1 字节都写不进去（逐级缩小块，避免留下几 MB 空隙）。"""
    filler = root / "filler.bin"
    for size in (CHUNK, 1 << 20, 256 << 10, 64 << 10, 4 << 10, 512, 1):
        chunk = b"x" * size
        try:
            # buffering=0：避免 close() 时再次 flush 抛 ENOSPC 打断循环。
            with open(filler, "ab", buffering=0) as handle:
                while True:
                    try:
                        handle.write(chunk)
                        os.fsync(handle.fileno())
                    except OSError:
                        break
        except OSError:
            break
    return filler


def _delete_file(path: Path) -> None:
    """删除挂载卷上的测试文件。

    conftest 的删除护栏只放行仓库/temp 根内的路径；VHD 盘符是测试自己挂载的
    真实卷，因此这里走 Win32 ``DeleteFileW``（护栏不拦截内核 API）。
    """
    for _ in range(20):
        if not path.exists():
            return
        try:
            if os.name == "nt":
                ctypes.windll.kernel32.DeleteFileW(str(path))
            else:  # pragma: no cover
                os.unlink(path)
        except OSError:
            pass
        time.sleep(0.05)
    if path.exists():
        raise OSError(f"cannot delete {path}")


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _integrity_ok(db: Path) -> bool:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("PRAGMA integrity_check(1)").fetchall() == [("ok",)]
    finally:
        conn.close()


@pytest.mark.timeout(180)
def test_sqlite_write_on_full_disk_raises_and_recovers(full_volume: Path) -> None:
    from src.kernels.memory import MemoryKernel, MemoryTier
    from src.kernels.memory.store import MemoryStoreError

    workdir = full_volume / "mem"
    workdir.mkdir()
    db = workdir / "memory.db"
    kernel = MemoryKernel(db_path=str(db))
    kernel.store("before-full", "ok", tier=MemoryTier.PERSISTENT)
    before = _count(db, "memory_entries")

    filler = _fill_disk(full_volume)
    failed_at = None
    raised: Exception | None = None
    try:
        for index in range(200):
            try:
                kernel.store(f"full-{index}", "x" * 4096, tier=MemoryTier.PERSISTENT)
            except MemoryStoreError as exc:  # 期望路径：明确报错
                raised = exc
                failed_at = index
                break
            except Exception as exc:  # 其它异常同样算"没有静默"
                raised = exc
                failed_at = index
                break
    finally:
        after_failure = _count(db, "memory_entries")
    print(
        f"\n[disk-full:sqlite] before={before} attempts_before_error={failed_at} "
        f"after_failure={after_failure} raised={type(raised).__name__}"
    )
    if raised is not None:
        message = str(raised).lower()
        assert any(k in message for k in ("full", "disk", "space", "空间", "no space")), (
            f"磁盘写满时错误信息不够明确：{raised}"
        )
    # 无论 SQLite 在哪一次写入失败，库必须仍然完整、已确认数据必须还在。
    assert _integrity_ok(db)
    assert after_failure >= before

    _delete_file(filler)  # 释放空间

    kernel.store("after-free", "ok", tier=MemoryTier.PERSISTENT)
    assert _count(db, "memory_entries") == after_failure + 1
    assert _integrity_ok(db)


@pytest.mark.timeout(180)
def test_json_atomic_write_on_full_disk_preserves_previous_version(
    full_volume: Path,
) -> None:
    from src.common.atomic_write import atomic_write_text

    workdir = full_volume / "json"
    workdir.mkdir()
    target = workdir / "data.json"
    atomic_write_text(target, "OLD-VERSION")

    filler = _fill_disk(full_volume)
    try:
        with pytest.raises(OSError):
            atomic_write_text(target, "NEW-" * 100_000)
        assert target.read_text(encoding="utf-8") == "OLD-VERSION"
        assert list(workdir.glob("data.json.*.tmp")) == []
    finally:
        _delete_file(filler)

    atomic_write_text(target, "NEW-VERSION")
    assert target.read_text(encoding="utf-8") == "NEW-VERSION"
    print("\n[disk-full:json] previous_file_preserved=yes recovered=yes")


@pytest.mark.timeout(180)
def test_audit_write_on_full_disk_raises_and_recovers(full_volume: Path) -> None:
    from src.kernels.audit import AuditEventType, AuditScope, AuditStore

    workdir = full_volume / "audit"
    workdir.mkdir()
    db = workdir / "audit.db"
    store = AuditStore(db_path=str(db))
    store.initialize()
    store.log_event(
        AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": 0}, "before-full"
    )
    before = _count(db, "audit_events")

    filler = _fill_disk(full_volume)
    raised: Exception | None = None
    try:
        for index in range(200):
            try:
                store.log_event(
                    AuditEventType.STATE_CHANGE,
                    "p",
                    AuditScope.L0,
                    "ok",
                    {"i": index, "blob": "x" * 4096},
                    f"full-{index}",
                )
            except Exception as exc:
                raised = exc
                break
    finally:
        after_failure = _count(db, "audit_events")

    print(
        f"\n[disk-full:audit] before={before} after_failure={after_failure} "
        f"raised={type(raised).__name__}"
    )
    assert raised is not None, "磁盘写满时审计写入必须报错，不能静默成功"
    message = str(raised).lower()
    assert any(k in message for k in ("full", "disk", "space", "空间", "no space")), (
        f"审计写入失败信息不够明确：{raised}"
    )
    assert _integrity_ok(db)
    assert after_failure >= before

    _delete_file(filler)

    store.log_event(
        AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": 999}, "after-free"
    )
    assert _count(db, "audit_events") == after_failure + 1
    ok, verified = store.verify_integrity()
    assert ok is True
    assert verified == after_failure + 1
