"""UBX-005 — 真实分布式协调原语（跨进程围栏 / 租约 / 选主）。

为什么存在这个文件
------------------
``src/distribution/lock.py`` 旧实现自称 Redlock，实际是**实例属性 dict**
（``_try_acquire_memory``）：两个 ``DistributedLock`` 实例可以同时 acquire 同一个
key，且 ``release()`` 完全不校验 ``lock_id``——任何人都能释放他人的锁。
``src/distribution/election.py`` 同样把 leader 交给 dict 插入顺序裁决，
``is_leader()`` 不看 TTL，``renew_lease()`` 因先赋值后比较而恒返回 False。

本模块把它们换成**真**原语：

* :class:`FencingToken` —— 严格单调递增、可比较的 **整数**。绝不用 ``uuid4``
  （随机 UUID 无法比较大小，因而无法判定"谁的令牌更新"）。
* :class:`FileLockLease`（默认，零新依赖）—— 操作系统级文件锁 + 原子替换的
  sidecar 状态文件，**真的跨进程**互斥（同一主机）。
* :class:`RedisLease`（可选，真跨节点）—— ``SET NX PX`` + ``INCR`` 单调令牌 +
  **Lua compare-and-delete**，保证 release 绝不会释放他人租约。过期由 Redis
  侧 TTL 判定，因此**不依赖两台机器的时钟一致**。
* :class:`DistributedExecutorLease` —— :class:`~src.kernels.execution.fence.
  ExecutorLease` ABC 的适配器，让 ``ExecutorFence`` **不改一行**即可跑在真实
  协调后端上。

纪律（与 ``src/distribution/bus_backends.py`` 一致）
----------------------------------------------------
**后端不可用就抛 :class:`CoordinationUnavailableError`，绝不静默降级。**
一个会谎报"拿到了锁"的协调层比一个坏掉的协调层更危险：它会让围栏形同虚设。

关于主机 uptime（boot_gen）
--------------------------
本模块**刻意不使用**主机 uptime / boot_gen 判定存活性。``fence.py`` 的
``_read_boot_gen_ms()`` 读的是 ``GetTickCount64`` / ``CLOCK_BOOTTIME``，那是
**每台机器各自独立**的计数器：跨主机比较两个 boot_gen 没有任何意义，
"我的机器开机 3 天、你的开机 5 分钟"推不出谁该被夺权。存活性一律由
**租约自身的到期时间 + 单调令牌/纪元**判定。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from src.kernels.execution.fence import (
    ExecutorLease,
    ExecutorLeaseView,
    FencedExecutorError,
    ReplayDetectedError,
    StaleExecutorError,
)

__all__ = [
    "CoordinationUnavailableError",
    "FencingToken",
    "LeaseView",
    "DistributedLease",
    "FileLockLease",
    "RedisLease",
    "DistributedExecutorLease",
    "get_distributed_lease",
    "BACKEND_ENV",
    "SUPPORTED_BACKENDS",
    "DEFAULT_BACKEND",
    "DEFAULT_LEASE_NAME",
]


#: 选择协调后端的环境变量（形状对齐 ``bus_backends.get_bus_backend(mode=...)``）。
BACKEND_ENV = "LIUHAO_DISTRIBUTED_LEASE_BACKEND"
SUPPORTED_BACKENDS = ("file", "redis")
DEFAULT_BACKEND = "file"
DEFAULT_LEASE_NAME = "default"


class CoordinationUnavailableError(RuntimeError):
    """协调后端不可用。

    抛出它而不是"回退到本地内存/静默放行"是本模块存在的全部理由：一个会谎报
    成功的协调层比一个坏掉的协调层更危险。
    """


class _LockContended(Exception):
    """内部信号：OS 级文件锁被别的进程持有（不是故障，是正常竞争结果）。"""


# =============================================================================
# Fencing token —— 严格单调、可比较的整数
# =============================================================================


class FencingToken(int):
    """严格单调递增、可比较的围栏令牌。

    派生自 ``int``，因此 ``>`` / ``<`` / ``==`` 全部可用——这正是判定
    "谁的租约更新"所必需的。旧实现用 ``uuid.uuid4()`` 做 lock_id，随机值
    无法比较，所以根本无法实现围栏。

    令牌只增不减，且**崩溃后也不会回退**（持久化在 sidecar / Redis INCR 里），
    所以新持有者的令牌必然严格大于被夺权者。
    """

    __slots__ = ()

    def __new__(cls, value: Any) -> "FencingToken":
        ivalue = int(value)
        if ivalue < 0:
            raise ValueError(f"fencing token must be >= 0, got {ivalue}")
        return super().__new__(cls, ivalue)

    @classmethod
    def first(cls) -> "FencingToken":
        return cls(1)

    def next(self) -> "FencingToken":
        return FencingToken(int(self) + 1)

    def __repr__(self) -> str:  # pragma: no cover - 仅为排查可读性
        return f"FencingToken({int(self)})"

    __str__ = __repr__


def parse_token(raw: Any) -> Optional[FencingToken]:
    """把外部传来的令牌（字符串/整数）解析回 :class:`FencingToken`。

    解析失败返回 ``None`` —— 调用方据此**拒绝**操作（fail-closed），而不是
    当成"大概是本人"放行。
    """
    if raw is None:
        return None
    if isinstance(raw, FencingToken):
        return raw
    if isinstance(raw, bool):
        return None
    try:
        return FencingToken(int(str(raw).strip()))
    except (TypeError, ValueError):
        return None


@dataclass
class LeaseView:
    """某个命名租约的当前状态快照。"""

    name: str
    holder: Optional[str]
    token: Optional[FencingToken]
    acquired_at: Optional[float]
    expires_at: Optional[float]
    released_at: Optional[float]
    epoch: int
    #: ``"held"`` | ``"expired"`` | ``"free"`` | ``"released"``
    state: str


# =============================================================================
# 抽象契约
# =============================================================================


class DistributedLease(ABC):
    """命名租约 + 单调围栏令牌。

    每个方法都是**后端原语**级语义，不含任何进程内短路。``release`` 必须校验
    令牌（这是 lock.py:120 的直接反面）。
    """

    name: str

    @abstractmethod
    def acquire(self, owner: str, ttl_sec: float = 30.0) -> Tuple[bool, Optional[FencingToken]]:
        """尝试获取租约。

        返回 ``(True, token)`` 表示持有；``(False, None)`` 表示**被他人持有**。
        同一 owner 重复 acquire 视为续租（令牌不变）。
        """

    @abstractmethod
    def validate(self, owner: str, token: FencingToken) -> bool:
        """``(owner, token)`` 是否仍然是当前有效持有者。"""

    @abstractmethod
    def renew(self, owner: str, token: FencingToken, ttl_sec: float) -> Optional[FencingToken]:
        """续租。令牌不匹配/已过期返回 ``None``（不抛，交由调用方决定）。"""

    @abstractmethod
    def release(self, owner: str, token: FencingToken) -> bool:
        """释放。**必须**校验 ``token``；不匹配返回 ``False`` 且不改动租约。"""

    @abstractmethod
    def current(self) -> LeaseView:
        """当前快照（不修改任何状态）。"""

    @abstractmethod
    def current_epoch(self) -> int:
        """当前全局纪元（``force_new_era`` 会把它 +1）。"""

    @abstractmethod
    def force_new_era(self) -> int:
        """开启新纪元，一次性让所有旧租约失效（脑裂恢复）。返回新纪元号。"""

    @abstractmethod
    def close(self) -> None:
        """释放后端资源（幂等）。"""


def _safe_name(name: str) -> str:
    """把租约名压成合法文件名片段，避免路径穿越与非法字符。"""
    out = []
    for ch in str(name):
        out.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    digest = "".join(out)
    return digest or "default"


# =============================================================================
# 默认后端：OS 级文件锁（同主机跨进程，零新依赖）
# =============================================================================


class _FileState:
    """一块受 OS 级排他锁保护的 JSON 状态。

    * ``locked()`` —— win32 ``msvcrt.locking`` / POSIX ``fcntl.flock``，非阻塞；
      拿不到即抛 :class:`_LockContended`（**这是正常竞争结果，不是故障**）。
      进程被 ``kill -9`` 时内核自动释放，所以崩溃后必然可接管。
    * ``read()`` —— 刻意**不加锁**。写入一律走 ``os.replace``（原子），所以并发
      读不会读到半截 JSON；只读路径（``validate`` / ``current``）因此不会因为
      锁竞争而假失败。所有读-改-写路径仍在 ``locked()`` 内。
    * 进程内另有一把 ``threading.RLock``：Windows 的 ``msvcrt`` 锁在同进程不同
      句柄之间语义较弱，进程内互斥由它兜底。**它只是加深防御，权威判定始终是
      OS 锁 + sidecar。**
    """

    def __init__(self, directory: str, stem: str) -> None:
        self._dir = directory
        self._lock_path = os.path.join(directory, f"{stem}.lock")
        self._state_path = os.path.join(directory, f"{stem}.state.json")
        self._process_lock = threading.RLock()

    @contextlib.contextmanager
    def locked(self):
        with self._process_lock:
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                # 保证锁区域存在（Windows 对超出 EOF 的区域行为不一致）。
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                try:
                    self._lock_fd(fd)
                except OSError as exc:
                    raise _LockContended(str(exc)) from exc
                try:
                    yield
                finally:
                    self._unlock_fd(fd)
            finally:
                os.close(fd)

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

    def read(self) -> Dict[str, Any]:
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, state: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, self._state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class _FileTokenCounter:
    """全局单调围栏令牌计数器（同一 directory 内所有租约共享）。

    为什么必须是**全局**的：``ExecutorFence`` 要求令牌在整个围栏域内严格递增
    （``tests/kernels/execution/test_fence.py::test_token_strictly_increasing``），
    "崩溃接管后新令牌严格大于旧令牌"也依赖它。若每个租约各自计数，两个不同
    executor 会拿到相同的令牌 1，围栏就失效了。

    计数持久化在磁盘上，所以进程崩溃**不会**让令牌回退。
    """

    STEM = "_global_token"

    def __init__(self, directory: str) -> None:
        self._state = _FileState(directory, self.STEM)

    def next(self) -> FencingToken:
        last_exc: Optional[BaseException] = None
        for _ in range(200):  # 竞争是短暂的，短暂重试而不是让调用方失败
            try:
                with self._state.locked():
                    state = self._state.read()
                    token = int(state.get("last_token") or 0) + 1
                    state["last_token"] = token
                    self._state.write(state)
                    return FencingToken(token)
            except _LockContended as exc:
                last_exc = exc
                time.sleep(0.005)
        raise CoordinationUnavailableError(
            f"could not allocate a fencing token: counter stayed contended ({last_exc})"
        )


_COUNTERS: Dict[str, _FileTokenCounter] = {}
_COUNTERS_GUARD = threading.Lock()


def _global_token_counter(directory: str) -> _FileTokenCounter:
    """同一 directory 复用同一个计数器（因此令牌是全局单调的）。"""
    key = os.path.abspath(directory)
    with _COUNTERS_GUARD:
        counter = _COUNTERS.get(key)
        if counter is None:
            counter = _FileTokenCounter(directory)
            _COUNTERS[key] = counter
        return counter


class FileLockLease(DistributedLease):
    """基于 OS 文件锁的租约——**真的跨进程**互斥（同一主机内）。

    两条机制，缺一不可：

    1. **OS 级排他锁**（win32 ``msvcrt.locking`` / POSIX ``fcntl.flock``）。
       进程被 ``kill -9`` 时内核自动释放，所以崩溃后必然可接管。
    2. **sidecar JSON 状态文件**，用 ``os.replace`` 原子替换，存放
       holder / token / expires_at / epoch。读-改-写全部在锁内进行。

    进程内另有一把 ``threading.RLock``：Windows 的 ``msvcrt`` 锁在同进程的不同
    句柄之间语义较弱，进程内互斥由它兜底。**它只是加深防御，真正的权威判定
    始终是 OS 锁 + sidecar。**

    局限（如实说明）：文件锁只在**同一主机**内有效。跨主机请用
    :class:`RedisLease`。过期时间用 wall clock（``time.time()``），同主机内
    无时钟偏差问题；跨主机请同样改用 Redis（由 Redis 侧 TTL 判定，不比时钟）。
    """

    DEFAULT_DIRNAME = "liuhao-coordination"
    DEFAULT_TTL_SEC = 30.0

    def __init__(
        self,
        name: str = DEFAULT_LEASE_NAME,
        directory: Optional[str] = None,
        ttl_sec: float = DEFAULT_TTL_SEC,
    ) -> None:
        self.name = name
        self._dir = directory or os.path.join(tempfile.gettempdir(), self.DEFAULT_DIRNAME)
        os.makedirs(self._dir, exist_ok=True)
        self._state = _FileState(self._dir, _safe_name(name))
        self._default_ttl = float(ttl_sec)
        #: 全局单调令牌计数器（同一 directory 内所有租约共享）。
        self._counter = _global_token_counter(self._dir)

    # --- 委托给共享的文件状态机 -------------------------------------------
    def _read_state(self) -> Dict[str, Any]:
        return self._state.read()

    def _view_from(self, state: Dict[str, Any]) -> LeaseView:
        holder = state.get("holder")
        token = state.get("token")
        epoch = int(state.get("epoch") or 0)
        released_at = state.get("released_at")
        if not holder or token is None:
            if released_at is not None:
                return LeaseView(self.name, None, None, None, None, released_at, epoch, "released")
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        expires_at = float(state.get("expires_at") or 0.0)
        live = expires_at > time.time()
        return LeaseView(
            self.name,
            holder,
            FencingToken(int(token)),
            state.get("acquired_at"),
            expires_at,
            None,
            epoch,
            "held" if live else "expired",
        )

    # --- DistributedLease -----------------------------------------------
    def acquire(self, owner: str, ttl_sec: float = 30.0) -> Tuple[bool, Optional[FencingToken]]:
        ttl = float(self._default_ttl if ttl_sec is None else ttl_sec)
        if ttl <= 0:
            raise ValueError(f"ttl_sec must be > 0, got {ttl}")
        if not owner:
            raise ValueError("owner must be a non-empty string")
        try:
            with self._state.locked():
                state = self._state.read()
                now = time.time()
                holder = state.get("holder")
                expires_at = state.get("expires_at")
                token = state.get("token")

                live = bool(holder) and expires_at is not None and float(expires_at) > now
                if live and holder != owner:
                    # 他人持有且未过期 —— 拒绝。绝不"乐观放行"。
                    return False, None
                if live and holder == owner and token is not None:
                    # 同一持有者续租：令牌保持不变（续租不是夺权）。
                    state["expires_at"] = now + ttl
                    state["released_at"] = None
                    self._state.write(state)
                    return True, FencingToken(int(token))

                # 新授予：从**全局**计数器取号，严格递增且崩溃后不回退。
                new_token = self._counter.next()
                state.update(
                    {
                        "holder": owner,
                        "token": int(new_token),
                        "acquired_at": now,
                        "expires_at": now + ttl,
                        "released_at": None,
                        "epoch": int(state.get("epoch") or 0),
                    }
                )
                self._state.write(state)
                return True, new_token
        except _LockContended:
            return False, None

    def validate(self, owner: str, token: FencingToken) -> bool:
        want = parse_token(token)
        if want is None:
            return False
        state = self._read_state()
        if state.get("holder") != owner:
            return False
        if int(state.get("token") if state.get("token") is not None else -1) != int(want):
            return False
        expires_at = state.get("expires_at")
        if expires_at is None or float(expires_at) <= time.time():
            return False
        return True

    def renew(self, owner: str, token: FencingToken, ttl_sec: float) -> Optional[FencingToken]:
        want = parse_token(token)
        if want is None:
            return None
        ttl = float(ttl_sec)
        if ttl <= 0:
            return None
        try:
            with self._state.locked():
                state = self._state.read()
                if state.get("holder") != owner:
                    return None
                if int(state.get("token") if state.get("token") is not None else -1) != int(want):
                    return None
                expires_at = state.get("expires_at")
                if expires_at is None or float(expires_at) <= time.time():
                    # 已经过期：不能再续，必须重新 acquire（拿到更大的令牌）。
                    return None
                now = time.time()
                state["expires_at"] = now + ttl
                self._state.write(state)
                return want
        except _LockContended:
            return None

    def release(self, owner: str, token: FencingToken) -> bool:
        """释放——**校验令牌**（lock.py:120 的直接修复）。

        令牌不匹配时返回 ``False`` 且**不改动**任何状态：别人持有的租约不会被
        一个错误/陈旧的令牌释放掉。
        """
        want = parse_token(token)
        if want is None:
            return False
        try:
            with self._state.locked():
                state = self._state.read()
                if state.get("holder") != owner:
                    return False
                if int(state.get("token") if state.get("token") is not None else -1) != int(want):
                    return False
                state["holder"] = None
                state["token"] = None
                state["expires_at"] = None
                state["released_at"] = time.time()
                self._state.write(state)
                return True
        except _LockContended:
            return False

    def current(self) -> LeaseView:
        return self._view_from(self._read_state())

    def current_epoch(self) -> int:
        return int(self._read_state().get("epoch") or 0)

    def force_new_era(self) -> int:
        try:
            with self._state.locked():
                state = self._state.read()
                epoch = int(state.get("epoch") or 0) + 1
                state["epoch"] = epoch
                self._state.write(state)
                return epoch
        except _LockContended:
            raise CoordinationUnavailableError(
                f"cannot force a new era: lease {self.name!r} file lock is contended"
            ) from None

    def close(self) -> None:
        return None


# =============================================================================
# 可选后端：Redis（真跨节点）
# =============================================================================


class RedisLease(DistributedLease):
    """基于 Redis 的跨节点租约。

    * 持有标记：``SET <lease> "<owner>|<token>" PX <ttl_ms>``。
      **过期由 Redis 侧判定**，因此不需要两台机器时钟一致。
    * 单调令牌：``INCR <token>``。Redis 重启前不回退，所以新持有者的令牌
      严格大于被夺权者（崩溃接管可证）。
    * 释放/续租/校验：全部走 **Lua compare-and-delete/compare-and-set**，
      ``owner`` 与 ``token`` 有一项不匹配就不动数据——这是 lock.py 那类
      "任意 lock_id 都能解锁"缺陷的直接反面。

    ``redis`` 是**延迟导入**的，所以默认的 file 后端路径不需要 redis-py。
    """

    DEFAULT_URL = "redis://localhost:6379/0"
    DEFAULT_PREFIX = "liuhao:lease:"

    #: KEYS[1]=lease key, KEYS[2]=token key; ARGV[1]=owner, ARGV[2]=ttl_ms
    _ACQUIRE_LUA = """
redis.replicate_commands()
local cur = redis.call('GET', KEYS[1])
if cur then
  local i = string.find(cur, '|', 1, true)
  if not i then
    return {0, '', 'malformed'}
  end
  local holder = string.sub(cur, 1, i - 1)
  local tok = string.sub(cur, i + 1)
  if holder ~= ARGV[1] then
    return {0, '', ''}
  end
  redis.call('SET', KEYS[1], cur, 'PX', tonumber(ARGV[2]))
  return {1, tok, 'renewed'}
end
local tok = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1] .. '|' .. tostring(tok), 'PX', tonumber(ARGV[2]))
return {1, tostring(tok), 'acquired'}
"""

    #: KEYS[1]=lease key; ARGV[1]=owner, ARGV[2]=token
    _RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
local holder = string.sub(cur, 1, i - 1)
local tok = string.sub(cur, i + 1)
if holder ~= ARGV[1] or tok ~= ARGV[2] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""

    #: KEYS[1]=lease key; ARGV[1]=owner, ARGV[2]=token, ARGV[3]=ttl_ms
    _RENEW_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
local holder = string.sub(cur, 1, i - 1)
local tok = string.sub(cur, i + 1)
if holder ~= ARGV[1] or tok ~= ARGV[2] then return 0 end
redis.call('SET', KEYS[1], cur, 'PX', tonumber(ARGV[3]))
return 1
"""

    #: KEYS[1]=lease key; ARGV[1]=owner, ARGV[2]=token
    _VALIDATE_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
if string.sub(cur, 1, i - 1) ~= ARGV[1] then return 0 end
if string.sub(cur, i + 1) ~= ARGV[2] then return 0 end
return 1
"""

    def __init__(
        self,
        name: str = DEFAULT_LEASE_NAME,
        url: Optional[str] = None,
        client: Any = None,
        prefix: str = DEFAULT_PREFIX,
        connect_timeout: float = 2.0,
    ) -> None:
        self.name = name
        self._url = url or self.DEFAULT_URL
        self._prefix = prefix
        self._connect_timeout = connect_timeout
        self._lease_key = f"{prefix}{name}"
        # 令牌计数器是**全局**的（不按 name 分），理由同 _FileTokenCounter：
        # ExecutorFence 要求围栏令牌在整个域内严格递增。
        self._token_key = f"{prefix}__global_token__"
        self._epoch_key = f"{prefix}{name}:epoch"
        self._client = client
        self._injected_client = client is not None
        self._connected = False

    # --- 生命周期 ---
    def connect(self) -> None:
        if self._client is None:
            try:
                import redis  # 延迟导入：默认 file 路径不需要 redis-py
            except ImportError as exc:
                raise CoordinationUnavailableError(
                    "redis coordination backend needs redis-py: "
                    "pip install 'redis>=5.0.0'"
                ) from exc
            self._client = redis.Redis.from_url(
                self._url, socket_connect_timeout=self._connect_timeout
            )
        try:
            pong = self._client.ping()
        except Exception as exc:  # noqa: BLE001 - 任何连接故障都必须是同一个结论
            raise CoordinationUnavailableError(
                f"cannot reach Redis coordination backend "
                f"({self._url}): {type(exc).__name__}: {exc}"
            ) from exc
        if not pong:
            raise CoordinationUnavailableError("Redis coordination backend ping failed")
        self._connected = True

    def close(self) -> None:
        client, self._client = self._client, None
        self._connected = False
        if client is not None and not self._injected_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def _client_or_raise(self) -> Any:
        if self._client is None:
            raise CoordinationUnavailableError(
                "Redis coordination backend is not connected; call connect() first"
            )
        return self._client

    def _run(self, script: str, keys, args):
        client = self._client_or_raise
        try:
            return client.eval(script, len(keys), *keys, *args)
        except Exception as exc:  # noqa: BLE001 - 后端不可达必须变成 fail-closed
            raise CoordinationUnavailableError(
                f"Redis coordination command failed: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _check_owner(owner: str) -> None:
        if not owner:
            raise ValueError("owner must be a non-empty string")
        if "|" in owner:
            raise ValueError("owner must not contain '|' (it is the lease encoding separator)")

    # --- DistributedLease ---
    def acquire(self, owner: str, ttl_sec: float = 30.0) -> Tuple[bool, Optional[FencingToken]]:
        self._check_owner(owner)
        ttl_ms = max(1, int(float(ttl_sec) * 1000.0))
        result = self._run(self._ACQUIRE_LUA, [self._lease_key, self._token_key], [owner, ttl_ms])
        ok = int(result[0]) == 1
        if not ok:
            return False, None
        return True, FencingToken(int(result[1]))

    def validate(self, owner: str, token: FencingToken) -> bool:
        want = parse_token(token)
        if want is None:
            return False
        self._check_owner(owner)
        result = self._run(self._VALIDATE_LUA, [self._lease_key], [owner, str(int(want))])
        return int(result) == 1

    def renew(self, owner: str, token: FencingToken, ttl_sec: float) -> Optional[FencingToken]:
        want = parse_token(token)
        if want is None:
            return None
        self._check_owner(owner)
        ttl_ms = max(1, int(float(ttl_sec) * 1000.0))
        result = self._run(self._RENEW_LUA, [self._lease_key], [owner, str(int(want)), ttl_ms])
        if int(result) != 1:
            return None
        return want

    def release(self, owner: str, token: FencingToken) -> bool:
        """compare-and-delete：令牌不对就一步都不做。"""
        want = parse_token(token)
        if want is None:
            return False
        self._check_owner(owner)
        result = self._run(self._RELEASE_LUA, [self._lease_key], [owner, str(int(want))])
        return int(result) == 1

    def current(self) -> LeaseView:
        client = self._client_or_raise
        try:
            raw = client.get(self._lease_key)
            epoch = int(client.get(self._epoch_key) or 0)
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination read failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not raw:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        idx = raw.find("|")
        if idx < 0:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        holder = raw[:idx]
        try:
            token = FencingToken(int(raw[idx + 1:]))
        except ValueError:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        # Redis 未过期即持有：过期时间取自服务端 PTTL，不比本地时钟。
        try:
            pttl = client.pttl(self._lease_key)
        except Exception:  # noqa: BLE001
            pttl = -1
        expires_at = None if pttl is None or int(pttl) < 0 else time.time() + int(pttl) / 1000.0
        return LeaseView(self.name, holder, token, None, expires_at, None, epoch, "held")

    def current_epoch(self) -> int:
        client = self._client_or_raise
        try:
            return int(client.get(self._epoch_key) or 0)
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination read failed: {type(exc).__name__}: {exc}"
            ) from exc

    def force_new_era(self) -> int:
        client = self._client_or_raise
        try:
            return int(client.incr(self._epoch_key))
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination epoch bump failed: {type(exc).__name__}: {exc}"
            ) from exc


# =============================================================================
# 工厂 —— 绝不静默降级
# =============================================================================


def get_distributed_lease(
    name: str = DEFAULT_LEASE_NAME,
    backend: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    client: Any = None,
) -> DistributedLease:
    """构造租约后端。**失败即抛**，绝不回退到更弱的后端。

    :param name: 租约名（不同名 = 不同互斥域）。
    :param backend: ``"file"``（默认）或 ``"redis"``。为 ``None`` 时读
        :data:`BACKEND_ENV`（``LIUHAO_DISTRIBUTED_LEASE_BACKEND``）。
    :param config: ``file`` 用 ``{"directory": ..., "ttl_sec": ...}``；
        ``redis`` 用 ``{"url": ..., "prefix": ..., "connect_timeout": ...}``。
    :param client: 可注入的 redis client（测试用）。
    """
    config = config or {}
    raw = (backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()

    if raw == "file":
        return FileLockLease(
            name,
            directory=config.get("directory"),
            ttl_sec=config.get("ttl_sec", FileLockLease.DEFAULT_TTL_SEC),
        )

    if raw == "redis":
        lease = RedisLease(
            name,
            url=config.get("url"),
            client=client,
            prefix=config.get("prefix", RedisLease.DEFAULT_PREFIX),
            connect_timeout=config.get("connect_timeout", 2.0),
        )
        lease.connect()  # 连不上 -> CoordinationUnavailableError（不降级）
        return lease

    raise ValueError(
        f"Unsupported coordination backend: {raw!r}. Supported: {list(SUPPORTED_BACKENDS)}"
    )


# =============================================================================
# 桥接：让 ExecutorFence 不改一行就跑在真实协调后端上
# =============================================================================


class DistributedExecutorLease(ExecutorLease):
    """:class:`~src.kernels.execution.fence.ExecutorLease` 的协调后端实现。

    每个 executor_id 对应一个命名租约（默认 ``executor:<id>``）；持有者身份
    就是 executor_id 本身（这是必须唯一的东西）。``owner`` 与
    ``granted_capabilities`` 属于"谁授权的"信息，不是互斥判据，记录在进程内。

    关于 boot_gen：本实现**记录但不使用**。见模块 docstring —— 主机 uptime 是
    每台机器独立的计数器，跨主机比较没有意义。存活性只由**租约到期时间 +
    单调令牌 + 纪元**判定，这也是这里唯一被信任的判据。

    已知边界（如实记录）：capabilities 授权与纪元快照记录在**获取方进程**内；
    一个从未 acquire 过的进程拿不到授权记录，``validate`` 会按 default-deny
    返回 ``False``。这与 ``ExecutorFence`` 的 fail-closed 立场一致。
    """

    DEFAULT_ERA_NAME = "executor-fence-era"

    def __init__(
        self,
        lease_factory: Optional[Callable[[str], DistributedLease]] = None,
        heartbeat_timeout: float = 60.0,
        era_name: str = DEFAULT_ERA_NAME,
    ) -> None:
        if lease_factory is None:

            def lease_factory(nm: str) -> DistributedLease:
                return get_distributed_lease(nm)

        self._factory = lease_factory
        self._heartbeat_timeout = float(heartbeat_timeout)
        self._era_lease: DistributedLease = self._factory(era_name)
        self._leases: Dict[str, DistributedLease] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._consumed: set = set()
        self._lock = threading.RLock()

    # --- 内部 ---
    def _lease_for(self, executor_id: str) -> DistributedLease:
        with self._lock:
            lease = self._leases.get(executor_id)
            if lease is None:
                lease = self._factory(f"executor:{executor_id}")
                self._leases[executor_id] = lease
            return lease

    def _acquire(
        self,
        executor_id: str,
        owner: str,
        ttl_sec: float,
        granted_capabilities,
        boot_gen,
        my_last_token,
    ) -> int:
        lease = self._lease_for(executor_id)
        before = lease.current()
        ok, token = lease.acquire(executor_id, ttl_sec)
        if not ok or token is None:
            view = lease.current()
            raise StaleExecutorError(
                f"lease for executor {executor_id!r} is held by {view.holder!r} "
                f"until {view.expires_at}"
            )
        token = FencingToken(token)
        # 只有在"本来就是我持有"的情况下令牌变大才是被夺权；全新授予不算。
        if (
            my_last_token is not None
            and before.holder == executor_id
            and before.token is not None
            and token > int(my_last_token)
        ):
            raise FencedExecutorError(
                f"executor {executor_id!r} was fenced: token {int(token)} "
                f"> my last {int(my_last_token)}"
            )
        era = self._era_lease.current_epoch()
        with self._lock:
            self._meta[executor_id] = {
                "owner": owner,
                "capabilities": tuple(granted_capabilities or ()),
                # boot_gen 只做记录；绝不参与存活性判定（跨主机无意义）。
                "boot_gen": boot_gen,
                "epoch": era,
                "token": int(token),
            }
        return int(token)

    def _granted(self, executor_id: str) -> tuple:
        with self._lock:
            meta = self._meta.get(executor_id)
        return tuple(meta.get("capabilities") or ()) if meta else ()

    # --- ExecutorLease ABC ---
    def acquire(
        self,
        executor_id,
        owner,
        ttl_sec,
        granted_capabilities,
        boot_gen=None,
        my_last_token=None,
    ):
        return self._acquire(
            executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
        )

    def acquire_within(
        self,
        executor_id,
        owner,
        ttl_sec,
        granted_capabilities,
        boot_gen=None,
        my_last_token=None,
    ):
        # 没有外部事务可加入（协调后端自带原子性），语义与 acquire 相同。
        return self._acquire(
            executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
        )

    def validate(self, executor_id, token, capabilities=None):
        if parse_token(token) is None:
            return False
        lease = self._lease_for(executor_id)
        if not lease.validate(executor_id, token):
            return False
        with self._lock:
            meta = self._meta.get(executor_id)
        if meta is None:
            # 没有授权记录 => default-deny（与 ExecutorFence 的立场一致）。
            return False
        if meta.get("epoch") != self._era_lease.current_epoch():
            return False  # 纪元被 force_new_era 推进 => 旧租约整体失效
        if capabilities is not None:
            if not set(capabilities).issubset(set(meta.get("capabilities") or ())):
                return False
        return True

    def is_stale(self, executor_id, token):
        return not self.validate(executor_id, token)

    def heartbeat(self, executor_id, token):
        if parse_token(token) is None:
            return None
        self._lease_for(executor_id).renew(executor_id, token, self._heartbeat_timeout)
        return None

    def renew(self, executor_id, token, ttl_sec):
        if parse_token(token) is None:
            raise StaleExecutorError(f"cannot renew: invalid token for {executor_id!r}")
        new = self._lease_for(executor_id).renew(executor_id, token, ttl_sec)
        if new is None:
            raise StaleExecutorError(f"cannot renew: no live lease for {executor_id!r}")
        with self._lock:
            meta = self._meta.get(executor_id)
            if meta is not None:
                meta["token"] = int(new)
        return int(new)

    def release(self, executor_id, token):
        if parse_token(token) is None:
            return False
        released = self._lease_for(executor_id).release(executor_id, token)
        if released:
            with self._lock:
                self._meta.pop(executor_id, None)
        return released

    def consume_token(self, executor_id, token, correlation_id):
        want = parse_token(token)
        if want is None:
            raise ReplayDetectedError(f"cannot consume an invalid token for {executor_id!r}")
        with self._lock:
            key = (executor_id, int(want), correlation_id)
            if key in self._consumed:
                raise ReplayDetectedError(
                    f"replay of (executor={executor_id}, token={int(want)}, "
                    f"corr={correlation_id})"
                )
            self._consumed.add(key)

    def force_new_era(self):
        return self._era_lease.force_new_era()

    def current(self, executor_id):
        lease = self._lease_for(executor_id)
        view = lease.current()
        with self._lock:
            meta = self._meta.get(executor_id) or {}
        granted = tuple(meta.get("capabilities") or ())
        if view.state in ("free", "released"):
            return ExecutorLeaseView(
                None, None, None, None, None, None, None, None, (), None, "unknown"
            )
        state = "held" if view.state == "held" else "stale"
        if meta and meta.get("epoch") is not None:
            if meta["epoch"] != self._era_lease.current_epoch():
                state = "stale"
        return ExecutorLeaseView(
            executor_id,
            view.token,
            meta.get("epoch"),
            meta.get("owner"),
            view.acquired_at,
            view.expires_at,
            view.acquired_at,
            meta.get("boot_gen"),
            granted,
            view.released_at,
            state,
        )

    def count_active(self, now=None):
        with self._lock:
            ids = list(self._leases.keys())
        active = 0
        for eid in ids:
            if self._lease_for(eid).current().state == "held":
                active += 1
        return active

    def known_executor(self, executor_id):
        return self._lease_for(executor_id).current().holder is not None


def new_executor_id() -> str:
    """生成一个稳定的 executor 身份（uuid，绝不用 PID）。"""
    return uuid.uuid4().hex
