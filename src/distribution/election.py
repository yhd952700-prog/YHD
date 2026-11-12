"""Leader Election for LiuHao AI OS Distribution — UBX-005 重写。

本文件曾是一个**假**实现：leader 由 ``self._candidates`` 这个实例 dict 的
**插入顺序**裁决（"first contestant wins"），``is_leader()`` 不查 TTL，
``renew_lease()`` 因为先给 ``last_heartbeat`` 赋值再比较而恒返回 ``False``，
``_stop_renewal()`` 是 ``pass``（续租线程永不停止）。

现在是**基于租约**的选主：leader = 当前持有协调层租约的人。

* 谁先拿到租约谁就是 leader；他人拿到即被拒（不再有"两个 leader"）。
* ``is_leader()`` **检查 TTL**：租约过期即自动退位并通知 listener。
* ``renew_lease()`` 真的续租（委托给 :class:`~src.distribution.coordination.
  DistributedLease`）。
* ``_stop_renewal()`` 用 :class:`threading.Event` **真的**停掉线程。
* 过期由后端判定（文件后端用 wall clock，redis 后端用 Redis 侧 TTL），
  与主机 uptime 无关。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src.distribution.coordination import (
    DistributedLease,
    FencingToken,
    get_distributed_lease,
)

logger = logging.getLogger(__name__)

#: 选主使用的租约名（与 executor / lock 租约隔离）。
LEADER_LEASE_NAME = "leader-election"


class LeaderElection:
    """基于租约的领导选举。

    Multiple contestants compete to become the leader by acquiring ONE named
    coordination lease. The winner holds the lease and can perform leader-only
    operations. When the lease expires (or is lost), ``is_leader()`` demotes
    automatically and a new contestant can take over with a strictly greater
    fencing token.
    """

    def __init__(self, lease_duration: int = 10000,
                 renewal_deadline: int = 5000,
                 session_ttl: int = 20000,
                 lease: Optional[DistributedLease] = None) -> None:
        """
        Args:
            lease_duration: leader 租约时长（毫秒）。
            renewal_deadline: 多久续一次（毫秒），应 < lease_duration。
            session_ttl: 兼容旧签名；存活性由租约到期决定，不再单独使用。
            lease: 注入的租约（测试用）。默认取协调层的 ``leader-election``。
        """
        self.lease_duration = lease_duration
        self.renewal_deadline = renewal_deadline
        self.session_ttl = session_ttl

        # Election state
        self._is_leader = False
        self._leader_id: Optional[str] = None
        self._token: Optional[FencingToken] = None
        self._expires_at: float = 0.0
        self._lease: DistributedLease = lease if lease is not None else get_distributed_lease(
            LEADER_LEASE_NAME
        )

        # 仅用于统计展示 —— **不参与**领导裁决。
        self._candidates: Dict[str, Dict[str, Any]] = {}
        self._listeners: List[Callable[[str, bool], None]] = []

        self._stop_event = threading.Event()
        self._renewal_task: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    # --- 生命周期 ------------------------------------------------------
    @property
    def ttl_sec(self) -> float:
        return self.lease_duration / 1000.0

    def start_election(self, contestant_id: str) -> bool:
        """参选。拿到租约即成为 leader，否则返回 ``False``。"""
        if not contestant_id:
            raise ValueError("contestant_id must be a non-empty string")
        with self._lock:
            self._candidates[contestant_id] = {
                "last_heartbeat": time.time(),
                "lease_start": time.time(),
            }
            ok, token = self._lease.acquire(contestant_id, self.ttl_sec)
            if not ok or token is None:
                # 别人正持有 —— 明确落选，绝不自封 leader。
                self._is_leader = False
                return False
            self._token = FencingToken(token)
            self._expires_at = time.time() + self.ttl_sec
            self._become_leader(contestant_id)
            return True

    def _become_leader(self, contestant_id: str) -> None:
        self._is_leader = True
        self._leader_id = contestant_id
        self._start_renewal()

        for listener in list(self._listeners):
            try:
                listener(contestant_id, True)
            except Exception as exc:  # noqa: BLE001 - 坏 listener 不该掀翻选主
                logger.error("Error in election listener: %s", exc)

        logger.info("%s is now the leader (token=%s)", contestant_id, self._token)

    def _demote(self) -> None:
        """租约已失 —— 立刻退位并通知 listener（不再谎报 leader）。"""
        if not self._is_leader and self._leader_id is None:
            return
        old = self._leader_id
        self._is_leader = False
        self._leader_id = None
        self._token = None
        self._stop_renewal()
        if old is not None:
            for listener in list(self._listeners):
                try:
                    listener(old, False)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error in election listener: %s", exc)
            logger.info("%s lost leadership (lease expired or superseded)", old)

    def renew_lease(self, contestant_id: str) -> bool:
        """真的续租。旧实现恒返回 ``False``（先赋值后比较），这里修掉了。"""
        with self._lock:
            if contestant_id not in self._candidates:
                return False
            self._candidates[contestant_id]["last_heartbeat"] = time.time()

            if not (self._is_leader and self._leader_id == contestant_id):
                return False
            if self._token is None:
                return False

            new = self._lease.renew(contestant_id, self._token, self.ttl_sec)
            if new is None:
                # 续不动 => 租约已被夺走或已过期。
                self._demote()
                return False
            self._token = FencingToken(new)
            self._expires_at = time.time() + self.ttl_sec
            return True

    def step_down(self, contestant_id: str) -> bool:
        """主动退位：释放租约，让他人可以立刻接管（拿到更大的令牌）。"""
        with self._lock:
            if not (self._is_leader and self._leader_id == contestant_id):
                return False
            token = self._token
            self._is_leader = False
            self._leader_id = None
            self._token = None
        # 也在锁外停线程，避免与续租线程互等。
        self._stop_renewal()
        if token is not None:
            self._lease.release(contestant_id, token)

            for listener in list(self._listeners):
                try:
                    listener(contestant_id, False)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error in election listener: %s", exc)

            logger.info("%s stepped down as leader", contestant_id)
            return True

    def is_leader(self, contestant_id: Optional[str] = None) -> bool:
        """是否是当前 leader —— **检查 TTL**。

        旧实现只读一个布尔位，租约过期后仍自称 leader（脑裂的教科书成因）。
        这里：本地到期时间或后端判定任一不成立即退位并返回 ``False``。
        """
        with self._lock:
            if self._leader_id is None or self._token is None:
                return False
            if contestant_id is not None and contestant_id != self._leader_id:
                return False
            expired = time.time() >= self._expires_at
            if not expired and self._lease.validate(self._leader_id, self._token):
                return True
        # 退位必须**在锁外**做：_demote() 会停续租线程，而那个线程可能正等着
        # 这把锁——在锁内 join 会自锁。
        self._demote()
        return False

    def current_token(self) -> Optional[FencingToken]:
        """当前 leader 的围栏令牌（用于证明接管后令牌严格变大）。"""
        return self._token

    # --- listeners ------------------------------------------------------
    def add_listener(self, listener: Callable[[str, bool], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, bool], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # --- 续租线程 --------------------------------------------------------
    def _start_renewal(self) -> None:
        self._stop_event.clear()

        def renewal_loop() -> None:
            interval = max(0.05, self.renewal_deadline / 1000.0)
            while not self._stop_event.wait(interval):
                try:
                    leader = self._leader_id
                    if leader is None:
                        break
                    if not self.renew_lease(leader):
                        break  # 续不动 = 已失主，线程退出（不空转）
                except Exception as exc:  # noqa: BLE001
                    logger.error("Lease renewal failed: %s", exc)
                    break

        self._renewal_task = threading.Thread(
            target=renewal_loop, name="leader-lease-renewal", daemon=True
        )
        self._renewal_task.start()

    def _stop_renewal(self) -> None:
        """真的停掉续租线程。旧实现是 ``pass``（线程泄漏）。"""
        self._stop_event.set()
        task = self._renewal_task
        self._renewal_task = None
        if task is not None and task.is_alive() and task is not threading.current_thread():
            # 有界 join：即使续租线程此刻正等着 _lock 也不会把调用方永久卡死。
            task.join(timeout=1.0)

    # --- 观测 ------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        view = self._lease.current()
        return {
            "is_leader": self._is_leader,
            "leader_id": self._leader_id,
            "token": int(self._token) if self._token is not None else None,
            "contestant_count": len(self._candidates),
            "active_contestants": len(
                [
                    c
                    for c in self._candidates
                    if time.time() - self._candidates[c]["last_heartbeat"]
                    < self.lease_duration / 1000.0
                ]
            ),
            "lease_holder": view.holder,
            "lease_state": view.state,
        }


# Module-level convenience
_default_election: Optional[LeaderElection] = None
_default_election_guard = threading.Lock()


def get_leader_election(lease_duration: int = 10000,
                        renewal_deadline: int = 5000,
                        session_ttl: int = 20000,
                        lease: Optional[DistributedLease] = None) -> LeaderElection:
    """Get the default Leader Election instance."""
    global _default_election

    if _default_election is None:
        with _default_election_guard:
            if _default_election is None:
                _default_election = LeaderElection(
                    lease_duration=lease_duration,
                    renewal_deadline=renewal_deadline,
                    session_ttl=session_ttl,
                    lease=lease,
                )

    return _default_election


def start_election(contestant_id: str) -> bool:
    """Convenience function to start an election."""
    election = get_leader_election()
    return election.start_election(contestant_id)


def is_leader(contestant_id: str) -> bool:
    """Convenience function to check if a contestant is the leader."""
    election = get_leader_election()
    return election.is_leader(contestant_id)


def renew_lease(contestant_id: str) -> bool:
    """Convenience function to renew a leader lease."""
    election = get_leader_election()
    return election.renew_lease(contestant_id)
