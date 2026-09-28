"""Distributed Lock for LiuHao AI OS Distribution — UBX-005 重写。

本文件曾是一个**假**实现：``_try_acquire_memory`` 把锁记在实例属性 dict 里，
``node_addrs`` 存下后从未使用，真正的 Redis 连接被注释掉。后果是任意两个
``DistributedLock`` 实例可以同时 acquire 同一个 key，而 ``release()`` 完全不
校验 ``lock_id``——任何人都能释放他人的锁。``check()`` 甚至在锁已过期时仍返回
``True``。

现在它是一个**薄客户端**，把全部互斥判定委托给
:mod:`src.distribution.coordination`：

* ``lock_id`` 不再是 ``uuid.uuid4()``，而是**围栏令牌的十进制字符串**
  （严格单调、可比较的整数，见 :class:`~src.distribution.coordination.
  FencingToken`）。
* ``release()`` **校验** ``lock_id``（lock.py:120 的直接修复）。
* ``check()`` 在锁过期/无人持有时返回 ``(False, None)``。

``_try_acquire_memory`` 已被删除。互斥的权威在协调后端，不在本进程内存里。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.distribution.coordination import (
    BACKEND_ENV,
    DEFAULT_BACKEND,
    DistributedLease,
    FencingToken,
    LeaseView,
    CoordinationUnavailableError,
    get_distributed_lease,
    parse_token,
)

logger = logging.getLogger(__name__)

#: 协调后端不可用是本 API 的合法失败模式（fail-closed），调用方需要能捕获它。
__all__ = [
    "DistributedLock",
    "get_distributed_lock",
    "acquire_lock",
    "release_lock",
    "check_lock",
    "CoordinationUnavailableError",
]


class DistributedLock:
    """分布式锁——协调层的薄客户端。

    互斥由 :class:`~src.distribution.coordination.DistributedLease` 保证：
    默认后端是 OS 级文件锁（同主机跨进程），可选 redis（跨节点）。

    ``node_addrs`` 仅为兼容旧调用签名而保留；真正的后端由 ``backend=`` 或
    :data:`BACKEND_ENV` 选择，与 redis 节点地址不再是同一回事。
    """

    #: 锁 key -> 协调层租约名的前缀（避免与 executor / election 租约撞名）。
    KEY_PREFIX = "lock:"

    def __init__(self, node_addrs: Optional[List[str]] = None, lock_timeout: int = 30000,
                 retry_count: int = 3, retry_delay: float = 100.0,
                 backend: Optional[str] = None,
                 directory: Optional[str] = None) -> None:
        """
        Args:
            node_addrs: 兼容旧签名，已不参与互斥判定（保留以免破坏调用方）。
            lock_timeout: 默认锁超时（毫秒）。
            retry_count: 兼容旧签名；本实现不做静默重试（失败即返回 False）。
            retry_delay: 兼容旧签名，毫秒。
            backend: ``"file"`` / ``"redis"``；None 时读环境变量。
            directory: file 后端的状态目录（测试隔离用）。
        """
        self.node_addrs = list(node_addrs or [])
        self.lock_timeout = lock_timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay / 1000.0
        self._backend = (backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
        self._directory = directory

        # 本客户端的持有者身份。每个实例一个，因此每个进程（默认单例）一个。
        self._owner = f"lock-{uuid.uuid4().hex}"

        # 进程内只记录"本客户端正持有哪些 key"。它不是互斥的依据——协调后端
        # 才是——而是防止同一客户端的两个调用方都以为自己持有同一个 key。
        self._guard = threading.RLock()
        self._held: Dict[str, FencingToken] = {}

    # --- 内部 ---------------------------------------------------------
    def _lease_for(self, lock_key: str) -> DistributedLease:
        return get_distributed_lease(
            f"{self.KEY_PREFIX}{lock_key}",
            backend=self._backend,
            config={"directory": self._directory},
        )

    def _ttl_sec(self, timeout: Optional[int]) -> float:
        return (self.lock_timeout if timeout is None else timeout) / 1000.0

    # --- 公开 API ------------------------------------------------------
    def acquire(self, lock_key: str, timeout: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """获取锁。返回 ``(acquired, lock_id)``。

        ``lock_id`` 是围栏令牌的十进制字符串——**单调可比较**，不是随机 UUID。
        """
        with self._guard:
            if lock_key in self._held:
                # 本客户端已持有：再 acquire 不产生第二个持有者。
                return False, None
            lease = self._lease_for(lock_key)
            ok, token = lease.acquire(self._owner, self._ttl_sec(timeout))
            if not ok or token is None:
                return False, None
            self._held[lock_key] = FencingToken(token)
            return True, str(int(token))

    def release(self, lock_id: str, lock_key: str) -> bool:
        """释放锁。**校验** ``lock_id``。

        旧实现（lock.py:120）根本不看 ``lock_id``，任何字符串都能解锁。这里
        令牌不匹配就返回 ``False`` 且不动任何状态。
        """
        token = parse_token(lock_id)
        if token is None:
            return False
        with self._guard:
            lease = self._lease_for(lock_key)
            if not lease.release(self._owner, token):
                logger.debug("refused to release lock %s: token mismatch", lock_key)
                return False
            self._held.pop(lock_key, None)
            return True

    def renew(self, lock_id: str, lock_key: str, extra_time: int) -> bool:
        """续锁（``extra_time`` 毫秒）。令牌不对则失败。"""
        token = parse_token(lock_id)
        if token is None:
            return False
        with self._guard:
            if self._held.get(lock_key) != token:
                return False
            lease = self._lease_for(lock_key)
            new = lease.renew(self._owner, token, float(extra_time) / 1000.0)
            if new is None:
                self._held.pop(lock_key, None)
                return False
            self._held[lock_key] = FencingToken(new)
            return True

    def check(self, lock_key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """查询锁状态。**过期或无人持有时返回 ``(False, None)``**。

        旧实现在锁已过期时仍返回 ``(True, status)``，调用方因此会把"锁没了"
        读成"锁还在"。
        """
        view = self._lease_for(lock_key).current()
        if view.state != "held":
            return False, None
        return True, self._status(view)

    def _status(self, view: LeaseView) -> Dict[str, Any]:
        now = time.time()
        expires_at = view.expires_at or now
        return {
            "is_held": True,
            "holder": view.holder,
            "token": int(view.token) if view.token is not None else None,
            "acquired_at": view.acquired_at,
            "expires_at": view.expires_at,
            "epoch": view.epoch,
            "time_remaining_ms": max(0, int((expires_at - now) * 1000)),
        }

    def get_stats(self) -> Dict[str, Any]:
        """真实统计：只统计"当前真的被持有"的锁。"""
        with self._guard:
            keys = list(self._held.keys())
        held = 0
        for key in keys:
            if self._lease_for(key).current().state == "held":
                held += 1
        return {
            "backend": self._backend,
            "locked_keys_by_this_client": len(keys),
            "actually_held": held,
            "lock_timeout_ms": self.lock_timeout,
            "owner": self._owner,
        }


# Module-level convenience
_default_lock: Optional[DistributedLock] = None
_default_lock_guard = threading.Lock()


def get_distributed_lock(node_addrs: Optional[List[str]] = None,
                         lock_timeout: int = 30000,
                         retry_count: int = 3,
                         retry_delay: float = 100.0,
                         backend: Optional[str] = None,
                         directory: Optional[str] = None) -> DistributedLock:
    """Get the default Distributed Lock instance.

    进程级单例，因此同一进程内共用同一个 owner —— 进程间互斥由协调后端保证。
    """
    global _default_lock

    if _default_lock is None:
        with _default_lock_guard:
            if _default_lock is None:
                if node_addrs is None:
                    node_addrs = ["localhost:6379"]
                _default_lock = DistributedLock(
                    node_addrs=node_addrs,
                    lock_timeout=lock_timeout,
                    retry_count=retry_count,
                    retry_delay=retry_delay,
                    backend=backend,
                    directory=directory,
                )

    return _default_lock


def acquire_lock(lock_key: str, timeout: Optional[int] = None,
                 node_addrs: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """Convenience function to acquire a distributed lock."""
    lock = get_distributed_lock(node_addrs=node_addrs)
    return lock.acquire(lock_key, timeout)


def release_lock(lock_id: str, lock_key: str,
                 node_addrs: Optional[List[str]] = None) -> bool:
    """Convenience function to release a distributed lock."""
    lock = get_distributed_lock(node_addrs=node_addrs)
    return lock.release(lock_id, lock_key)


def check_lock(lock_key: str,
               node_addrs: Optional[List[str]] = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Convenience function to check a distributed lock status."""
    lock = get_distributed_lock(node_addrs=node_addrs)
    return lock.check(lock_key)
