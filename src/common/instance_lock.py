"""单实例锁：同一份数据目录只允许一个网关进程。

为什么需要它
------------
双击两次快捷方式、托盘与命令行各起一个、以及「上次关了窗口但进程没退」
都会走到同一条路上：第二个网关照样启动，端口自动换一个，然后两个进程同时
写同一份 SQLite（对话、审计、模型配置、提醒队列）。SQLite 的 WAL 能扛住
「一个写者 + 多个读者」，扛不住「两个写者」——表现是随机的 ``database is
locked``、审计链写坏、提醒重复，而且用户完全看不出根因。

这里用**操作系统级文件锁**（Windows ``msvcrt`` / POSIX ``fcntl``）：
进程活着就持有，进程被杀/崩溃时由内核自动释放，不会留下"僵尸锁"。

两个锁文件：

* ``<root>/run/instance.lock`` —— 真正的互斥（只有持有者能写数据）；
* ``<root>/run/instance.json`` —— 持有者元信息（pid / 地址 / 启动时间），
  让第二个实例能告诉用户「它已经在 http://127.0.0.1:8080 跑着」。

元信息只用于**提示**，任何判定都必须以锁本身为准：文件可以被伪造，锁不能。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

#: 显式指定实例根（启动器会把它指向数据目录）。
INSTANCE_ROOT_ENV = "LIUHAO_INSTANCE_ROOT"

#: 测试/排障时用这个变量把锁放到临时目录，避免和真实实例互斥。
INSTANCE_DIR_ENV = "LIUHAO_INSTANCE_DIR"


def instance_root(default: str | os.PathLike[str]) -> Path:
    """解析「实例归属」的根目录。

    优先级：``LIUHAO_INSTANCE_ROOT`` > ``LIUHAO_DATA_DIR`` > 调用方给的默认值。
    归一化到绝对路径，保证从不同 cwd 启动的两个进程算出同一个锁。
    """
    for name in (INSTANCE_ROOT_ENV, "LIUHAO_DATA_DIR"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return Path(default).expanduser().resolve()


class InstanceLockBusy(Exception):
    """锁被另一个活着的进程持有。``holder`` 可能为空（元信息未写成）。"""

    def __init__(self, holder: Optional[Dict[str, Any]] = None) -> None:
        super().__init__("instance lock is held by another process")
        self.holder = holder or {}

    def describe(self) -> str:
        """给用户看的一句话（不暴露内部实现）。"""
        holder = self.holder
        pid = holder.get("pid")
        url = holder.get("url")
        started = holder.get("started_at")
        bits = []
        if pid:
            bits.append(f"pid={pid}")
        if url:
            bits.append(str(url))
        if started:
            bits.append(f"启动于 {started}")
        detail = "，".join(bits)
        head = "鎏灏已经在运行"
        if detail:
            head += f"（{detail}）"
        return (
            head
            + "。为避免两个进程同时写同一份数据，本次启动已退出；"
            "如需重启，请先关闭已经运行的那个鎏灏。"
        )


class InstanceGuardUnavailable(Exception):
    """锁文件本身不可用（数据目录只读、磁盘满、权限不足）——fail-closed。"""


class InstanceLock:
    """进程生命周期内持有的单实例锁。"""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        subdir: str = "run",
        name: str = "instance",
    ) -> None:
        override = (os.environ.get(INSTANCE_DIR_ENV) or "").strip()
        base = Path(override).expanduser().resolve() if override else Path(root)
        self._dir = base / subdir if subdir else base
        self._lock_path = self._dir / f"{name}.lock"
        self._meta_path = self._dir / f"{name}.json"
        self._fd: Optional[int] = None

    # ------------------------------------------------------------------
    # 持有 / 释放
    # ------------------------------------------------------------------
    def acquire(self) -> None:
        """非阻塞获取；被占用抛 :class:`InstanceLockBusy`，目录不可用抛
        :class:`InstanceGuardUnavailable`。
        """
        if self._fd is not None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise InstanceGuardUnavailable(
                f"无法创建单实例锁文件 {self._lock_path}（{exc}）。"
                "数据目录可能只读或被其它程序占用；为避免两个进程同时写坏数据，"
                "已拒绝启动。"
            ) from exc
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            self._lock_fd(fd)
        except OSError as exc:
            os.close(fd)
            if _is_contended(exc):
                raise InstanceLockBusy(self.read_holder()) from exc
            raise InstanceGuardUnavailable(
                f"无法锁定 {self._lock_path}（{exc}）。已拒绝启动，避免两个进程"
                "同时写同一份数据。"
            ) from exc
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            self._unlock_fd(fd)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @property
    def held(self) -> bool:
        return self._fd is not None

    # ------------------------------------------------------------------
    # 元信息（仅用于提示）
    # ------------------------------------------------------------------
    def publish(self, **info: Any) -> None:
        """把持有者信息写成 JSON；失败不影响锁本身（提示不是安全边界）。"""
        payload = {k: v for k, v in info.items() if v is not None}
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._meta_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._meta_path)
        except OSError:
            pass

    def clear_metadata(self) -> None:
        try:
            self._meta_path.unlink(missing_ok=True)
        except OSError:
            pass

    def read_holder(self) -> Dict[str, Any]:
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def probe(self) -> Optional[Dict[str, Any]]:
        """不长期持有地探测：空闲返回 ``None``，被占用返回持有者信息。

        实现是「能拿到锁就说明空闲，随即释放」。探测本身会短暂持有锁，
        但同一进程不会在此之上再长期持有，因此不存在自锁。
        """
        try:
            self.acquire()
        except InstanceLockBusy as busy:
            return busy.holder or {}
        except InstanceGuardUnavailable:
            # 锁不可用不是"已在运行"；交给真正的启动流程报错。
            return None
        self.release()
        return None

    # ------------------------------------------------------------------
    # OS 原语
    # ------------------------------------------------------------------
    @staticmethod
    def _lock_fd(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_fd(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)


def _is_contended(exc: OSError) -> bool:
    """区分「锁被别的进程拿着」与「目录/权限不可用」。

    Windows ``msvcrt.locking`` 在锁冲突时抛 ``OSError``，``errno`` 是 13
    （EACCES），``winerror`` 是 33；POSIX ``flock`` 是 EAGAIN/EWOULDBLOCK。
    """
    winerror = getattr(exc, "winerror", None)
    if winerror in (32, 33):
        return True
    return getattr(exc, "errno", None) in (11, 13, 35)


__all__ = [
    "INSTANCE_DIR_ENV",
    "INSTANCE_ROOT_ENV",
    "InstanceGuardUnavailable",
    "InstanceLock",
    "InstanceLockBusy",
    "instance_root",
]
