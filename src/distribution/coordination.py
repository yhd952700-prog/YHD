"""UBX-005 — 真实分布式协调原语（跨进程围栏 / 租约 / 选主）。

为什么存在这个文件
------------------
``src/distribution/lock.py`` 旧实现自称 Redlock，实际是**实例属性 dict**
（``_try_acquire_memory``）：两个 ``DistributedLock`` 实例可以同时 acquire 同一个
key，且 ``release()`` 完全不校验 ``lock_id``。``src/distribution/election.py``
同样把 leader 交给 dict 插入顺序裁决，``is_leader()`` 不看 TTL，
``renew_lease()`` 恒 ``False``，``_stop_renewal()`` 是 ``pass``。

本模块把它们换成**真**原语：

* :class:`FencingToken` —— 严格单调递增、可比较的 **整数**。绝不用 ``uuid4``。
* :class:`FileLockLease`（默认，零第三方依赖）—— **SQLite（WAL）存状态 + OS 级
  文件锁串行化写者**。为什么不是裸 JSON sidecar，见 :class:`_SqliteStore`。
* :class:`RedisLease`（可选，真跨节点）—— ``SET NX PX`` + ``INCR`` 单调令牌 +
  **Lua compare-and-delete**。
* :class:`DistributedExecutorLease` —— :class:`~src.kernels.execution.fence.
  ExecutorLease` ABC 的适配器，让 ``ExecutorFence`` **不改一行**即可跑在真实
  协调后端上。

三条不可协商的纪律
------------------
1. **失败即闭（fail-closed）。** 任何 I/O 故障都翻译成
   :class:`CoordinationUnavailableError`；在
   :class:`DistributedExecutorLease` 边界上再翻译成
   :class:`ExecutorFenceBackendError`（``ExecutorFenceDenied`` 的子类），
   所以围栏永远不会因为一个裸 ``OSError`` 而放行未持租约的动作。
2. **绝不静默降级。** 后端不可用就抛，不回退到更弱的后端。
3. **写入必须能推进，读取必须不撕裂。** 写者在有并发读的情况下必须能完成
   写入；读者永远看不到半截状态，也永远不会因为读失败而被当成"租约空闲"。

关于主机 uptime（boot_gen）
--------------------------
本模块**刻意不使用**主机 uptime / boot_gen 判定存活性。``fence.py`` 的
``_read_boot_gen_ms()`` 读的是 ``GetTickCount64`` / ``CLOCK_BOOTTIME``，那是
**每台机器各自独立**的计数器：跨主机比较两个 boot_gen 没有任何意义。存活性
一律由**租约自身的到期时间 + 单调令牌 + 纪元**判定。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.kernels.execution.fence import (
    ExecutorFenceDenied,
    ExecutorLease,
    ExecutorLeaseView,
    ExecutorLimitExceeded,
    FencedExecutorError,
    ReplayDetectedError,
    StaleExecutorError,
)

# --------------------------------------------------------------------------- #
# Coordinator observability hooks (best-effort; never break coordination)
# --------------------------------------------------------------------------- #
# Like the fence itself, coordination must never fail open or stall because an
# observability sink is unavailable. Metrics are lazy-imported and every emit is
# wrapped so a metrics error is invisible to the coordination path.
_COORD_METRICS = None


def _coord_metrics():
    global _COORD_METRICS
    if _COORD_METRICS is None:
        try:
            import src.observability.metrics as _m

            _COORD_METRICS = _m
        except Exception:  # pragma: no cover - metrics are optional
            _COORD_METRICS = False
    return _COORD_METRICS


def _emit_coord_metric(fn_name: str, *args) -> None:
    m = _coord_metrics()
    if m is False:
        return
    try:
        getattr(m, fn_name)(*args)
    except Exception:  # pragma: no cover - metrics must never raise
        pass


__all__ = [
    "CoordinationUnavailableError",
    "ExecutorFenceBackendError",
    "FencingToken",
    "LeaseView",
    "DistributedLease",
    "FileLockLease",
    "RedisLease",
    "DistributedExecutorLease",
    "get_distributed_lease",
    "parse_token",
    "process_holder_id",
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

#: SQLite 忙等待（秒）。写者已由 OS 锁先行串行化，这只是兜底。
_SQLITE_BUSY_TIMEOUT = 15.0
#: OS 文件锁 / SQLite 瞬时冲突的重试预算。
_RETRY_ATTEMPTS = 200
_RETRY_BASE_SLEEP = 0.002
_RETRY_MAX_SLEEP = 0.05


class CoordinationUnavailableError(RuntimeError):
    """协调后端不可用（I/O 故障、锁不可得、后端不可达）。

    抛出它而不是"回退到本地/静默放行"是本模块存在的全部理由。
    """


class _LockContended(Exception):
    """内部信号：OS 级文件锁被别的进程持有。

    **这不是故障**，是正常竞争结果；调用方必须**重试**，绝不能把它当成
    "租约被别人持有"而返回 ``False``（那会让约 17% 的 release 假失败）。
    """


class ExecutorFenceBackendError(ExecutorFenceDenied):
    """协调后端本身失败了，因此围栏必须拒绝（fail-closed）。

    它是 ``ExecutorFenceDenied`` 的子类，所以调用方用
    ``except ExecutorFenceDenied`` 就能拦住；它**不是**裸 ``OSError``，因此
    不会被一个泛化的 ``except Exception`` 当成"可以继续"吞掉后放行未持租约的
    动作。
    """


# =============================================================================
# Fencing token —— 严格单调、可比较的整数
# =============================================================================


class FencingToken(int):
    """严格单调递增、可比较的围栏令牌。

    派生自 ``int``，因此 ``>`` / ``<`` / ``==`` 全部可用——这正是判定"谁的租约
    更新"所必需的。旧实现用 ``uuid.uuid4()`` 做 lock_id，随机值无法比较，所以
    根本无法实现围栏。令牌只增不减，且持久化后**崩溃也不回退**。
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
    """解析令牌；失败返回 ``None``（调用方据此**拒绝**，而不是"大概是本人"）。"""
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
    #: 持久化在后端的能力授权（跨进程可读）。
    granted_capabilities: Tuple[str, ...] = ()
    #: 获取时记录的全局纪元（跨进程可读）。``None`` = 未记录。
    era: Optional[int] = None


_PROCESS_HOLDER: Optional[str] = None


def process_holder_id() -> str:
    """本进程的持有者身份（进程级单例 uuid）。

    **为什么 executor_id 不能直接当 holder**：executor_id 是逻辑身份，两个进程
    可以声称同一个 executor_id。若拿它当 holder，第二个进程会被当成"同一持有者
    续租"，从而拿到**完全相同**的令牌 —— 两个进程同时通过校验，围栏整个失效。
    holder 必须是**进程唯一**的。
    """
    global _PROCESS_HOLDER
    if _PROCESS_HOLDER is None:
        _PROCESS_HOLDER = uuid.uuid4().hex
    return _PROCESS_HOLDER


# =============================================================================
# 抽象契约
# =============================================================================


class DistributedLease(ABC):
    """命名租约 + 单调围栏令牌。

    ``release`` **必须**校验 ``token``（lock.py:120 的直接反面）。任何后端故障
    都必须抛 :class:`CoordinationUnavailableError`。
    """

    name: str

    @abstractmethod
    def acquire(
        self,
        owner: str,
        ttl_sec: float = 30.0,
        granted_capabilities: Optional[Sequence[str]] = None,
        era: Optional[int] = None,
    ) -> Tuple[bool, Optional[FencingToken]]:
        """尝试获取租约。

        * ``(True, token)`` —— 持有。
        * ``(False, None)`` —— **被另一个持有者持有且未过期**（明确拒绝）。

        同一 ``owner`` + 同一 ``era`` 重复 acquire 视为续租（令牌不变）。
        **不同的 owner 接管必须铸一个严格更大的新令牌**，绝不能把现任的令牌
        交给另一个 owner。
        """

    @abstractmethod
    def validate(self, owner: str, token: FencingToken) -> bool:
        """``(owner, token)`` 是否仍是当前有效持有者。"""

    @abstractmethod
    def renew(self, owner: str, token: FencingToken, ttl_sec: float) -> Optional[FencingToken]:
        """续租。令牌不匹配 / 已过期 → ``None``；后端故障 → 抛。"""

    @abstractmethod
    def release(self, owner: str, token: FencingToken) -> bool:
        """释放。**必须**校验 token；不匹配返回 ``False`` 且不改动租约。"""

    @abstractmethod
    def current(self) -> LeaseView:
        """当前快照（不改状态；读失败必须抛，不能假装空闲）。"""

    @abstractmethod
    def current_epoch(self) -> int:
        """该租约的当前纪元（``force_new_era`` 会 +1）。"""

    @abstractmethod
    def force_new_era(self) -> int:
        """开启新纪元，一次性让旧租约失效。返回新纪元号。"""

    @abstractmethod
    def count_live(self, prefix: Optional[str] = None, now: Optional[float] = None) -> int:
        """**后端全局**地统计当前仍被持有的租约数（不是进程内视图）。"""

    @abstractmethod
    def close(self) -> None:
        """释放后端资源（幂等）。"""


#: Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）—— 它们不能做文件名主干。
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _safe_name(name: str) -> str:
    """把任意租约名映射成一个**跨平台合法**的文件名主干。

    租约名里带 ``:`` 是常态（``executor:<id>``、``audit:writer``），而 ``:``
    在 Windows/NTFS 上是**非法字符**：旧实现把它直接拼进路径，``os.open`` 立刻
    抛 ``PermissionError [Errno 13]``（不是"锁竞争"，重试多少次都没用）。这里
    统一替换成 ``_``，并附一段短哈希，使 ``a:b`` 与 ``a_b`` 不会撞名。
    """
    raw = str(name)
    out = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in raw)
    out = out.strip(" .") or "default"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    stem = f"{out}-{digest}"[:120]
    if stem.split("-")[0].upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return stem


def _backoff_sleep(attempt: int) -> None:
    delay = min(_RETRY_MAX_SLEEP, _RETRY_BASE_SLEEP * (1.5 ** attempt))
    time.sleep(delay + random.uniform(0.0, delay * 0.5))


def _is_transient(exc: BaseException) -> bool:
    """瞬时冲突（可重试）：OS 锁竞争 / SQLite locked / Windows 共享冲突。

    **必须区分"再试一次就好"与"这个进程根本没权限"**：Windows 上共享冲突是
    ``winerror`` 32/33（Python 同时把它映射成 ``errno`` 13），而真正的
    ACCESS_DENIED 是 ``winerror`` 5。旧判断看到 ``errno == 13`` 就重试，会把
    一次硬权限故障拖成 200 次退避重试（慢，但仍是失败即闭）。现在按
    ``winerror`` 精确分类。
    """
    if isinstance(exc, _LockContended):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in (32, 33):
            # 32 = 文件被占用（共享冲突）；33 = 另一进程已锁住该区域
            return True
        if winerror is not None:
            # 含 5 = ACCESS_DENIED：不是"再试试就好"，必须立刻失败即闭。
            return False
        if getattr(exc, "errno", None) in (11, 35):
            # POSIX: EAGAIN / EWOULDBLOCK（flock 非阻塞拿不到锁）
            return True
    return False


def _with_retry(fn: Callable[[], Any], what: str) -> Any:
    """重试**瞬时**冲突；重试耗尽 → :class:`CoordinationUnavailableError`。

    关键点：竞争（retryable）与"被拒绝"（definitive）必须分开。旧实现把两者
    都返回 ``False``，调用方因此无法区分"再试一次就好"和"你不是持有者"。
    """
    last: Optional[BaseException] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - 分类后再决定
            if not _is_transient(exc):
                raise
            last = exc
            _backoff_sleep(attempt)
    raise CoordinationUnavailableError(
        f"{what}: gave up after {_RETRY_ATTEMPTS} attempts "
        f"(last transient error: {type(last).__name__}: {last})"
    )


def _fail_closed(exc: BaseException, what: str) -> CoordinationUnavailableError:
    """把任意后端异常翻译成 :class:`CoordinationUnavailableError`。"""
    if isinstance(exc, CoordinationUnavailableError):
        return exc
    return CoordinationUnavailableError(f"{what}: {type(exc).__name__}: {exc}")


# =============================================================================
# 默认后端：本地文件（SQLite WAL 存状态 + OS 文件锁串行化写者）
# =============================================================================


class _OsMutex:
    """受 OS 级排他锁保护的互斥区（win32 ``msvcrt`` / POSIX ``fcntl``）。

    拿不到锁抛 :class:`_LockContended`（**可重试**）；``os.open`` 本身的故障抛
    :class:`CoordinationUnavailableError`（**故障，不可重试**）——两者必须分开，
    绝不能让裸 ``PermissionError`` 逃逸。进程被 ``kill -9`` 时内核自动释放。
    """

    def __init__(self, directory: str, stem: str) -> None:
        self._lock_path = os.path.join(directory, f"{stem}.lock")
        self._process_lock = threading.RLock()

    @contextlib.contextmanager
    def locked(self):
        with self._process_lock:
            # P0-1：os.open 必须在 try 内。
            try:
                fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError as exc:
                raise _fail_closed(exc, f"cannot open lock file {self._lock_path!r}") from exc
            try:
                try:
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"0")
                except OSError as exc:
                    raise _fail_closed(exc, "cannot size the lock file") from exc
                try:
                    self._lock_fd(fd)
                except OSError as exc:
                    raise _LockContended(str(exc)) from exc
                try:
                    yield
                finally:
                    try:
                        self._unlock_fd(fd)
                    except OSError as exc:
                        raise _fail_closed(exc, "cannot release the OS lock") from exc
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

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


class _SqliteStore:
    """按目录共享的 SQLite（WAL）状态存储。

    **为什么不是裸 JSON sidecar + ``os.replace``**：那套设计在 POSIX 上成立，
    在 Windows 上不成立。Windows 的 ``os.replace`` 需要对目标文件持有 DELETE
    权限，而一个正在读该文件的 reader 句柄默认**不带** ``FILE_SHARE_DELETE``，
    于是写者的替换被拒绝（``PermissionError`` WinError 5）。实测：1 个读进程 →
    写者只完成 2 次写入；3 个读进程 → 写者 400 次写入里成功 0 次。而
    ``ExecutorFence._enforce`` 对**每一个动作**都会 ``current()`` + ``validate()``，
    也就是说热路径本身就是那个 reader —— 后端恰好在围栏最忙的时候失效。

    SQLite WAL 正是为"1 写者 + N 读者"设计的：写者不会被读者阻塞，读者永远看到
    已提交的一致快照（不会读到半截状态）。SQLite 是标准库，不引入新依赖。
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS leases (
        name                 TEXT PRIMARY KEY,
        holder               TEXT,
        token                INTEGER,
        acquired_at          REAL,
        expires_at           REAL,
        released_at          REAL,
        epoch                INTEGER NOT NULL DEFAULT 0,
        era                  INTEGER,
        granted_capabilities TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        id           INTEGER PRIMARY KEY CHECK (id = 1),
        global_token INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS consumed (
        lease_name     TEXT NOT NULL,
        token          INTEGER NOT NULL,
        correlation_id TEXT NOT NULL,
        PRIMARY KEY (lease_name, token, correlation_id)
    );
    """

    def __init__(self, directory: str) -> None:
        self._dir = directory
        self._path = os.path.join(directory, "leases.db")
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    def _open_conn(self) -> sqlite3.Connection:
        """打开并初始化连接；**建库/切 WAL 的瞬时冲突要重试**。

        实测：4 个进程同时首次打开同一个库时，``PRAGMA journal_mode=WAL`` +
        ``CREATE TABLE`` 会因为另一个进程正持有排他锁而报
        ``OperationalError: attempt to write a readonly database``。它是瞬时的
        （对方一放手就好），但旧实现在 ``_get_conn`` 里一次性失败即闭 ⇒ 后端在
        并发冷启动时会**随机**不可用。这里按瞬时冲突重试。
        """
        last: Optional[BaseException] = None
        for attempt in range(_RETRY_ATTEMPTS):
            conn: Optional[sqlite3.Connection] = None
            try:
                conn = sqlite3.connect(
                    self._path,
                    timeout=_SQLITE_BUSY_TIMEOUT,
                    isolation_level=None,
                    check_same_thread=False,
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(f"PRAGMA busy_timeout={int(_SQLITE_BUSY_TIMEOUT * 1000)}")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(self._SCHEMA)
                return conn
            except BaseException as exc:  # noqa: BLE001
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                if not _is_transient(exc):
                    raise
                last = exc
                _backoff_sleep(attempt)
        assert last is not None
        raise last

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    try:
                        self._conn = self._open_conn()
                    except (sqlite3.Error, OSError) as exc:
                        raise _fail_closed(
                            exc, f"cannot open lease store {self._path!r}"
                        ) from exc
        return self._conn

    def _execute(self, sql: str, params: Tuple = (), *, write: bool = False):
        """执行一条 SQL；任何后端故障翻译成 CoordinationUnavailableError。"""
        with self._lock:
            conn = self._get_conn()
            try:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
                if write:
                    conn.execute("COMMIT")
                return rows
            except BaseException as exc:  # noqa: BLE001
                if write:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass
                raise _fail_closed(exc, "lease store command failed") from exc

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        rows = self._execute(
            "SELECT holder, token, acquired_at, expires_at, released_at, epoch, era, "
            "granted_capabilities FROM leases WHERE name = ?",
            (name,),
        )
        if not rows:
            return None
        holder, token, acq, exp, rel, epoch, era, caps = rows[0]
        return {
            "holder": holder,
            "token": token,
            "acquired_at": acq,
            "expires_at": exp,
            "released_at": rel,
            "epoch": int(epoch or 0),
            "era": era,
            "granted_capabilities": tuple(json.loads(caps)) if caps else (),
        }

    def count_live(self, prefix: Optional[str], now: float) -> int:
        if prefix:
            esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self._execute(
                "SELECT COUNT(*) FROM leases WHERE name LIKE ? ESCAPE '\\' "
                "AND holder IS NOT NULL AND released_at IS NULL AND expires_at > ?",
                (esc + "%", now),
            )
        else:
            rows = self._execute(
                "SELECT COUNT(*) FROM leases WHERE holder IS NOT NULL "
                "AND released_at IS NULL AND expires_at > ?",
                (now,),
            )
        return int(rows[0][0]) if rows else 0

    def write(
        self,
        name: str,
        holder: Optional[str],
        token: Optional[int],
        acquired_at: Optional[float],
        expires_at: Optional[float],
        released_at: Optional[float],
        epoch: int,
        era: Optional[int],
        capabilities: Sequence[str],
    ) -> None:
        self._execute(
            "INSERT INTO leases (name, holder, token, acquired_at, expires_at, "
            "released_at, epoch, era, granted_capabilities) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, token=excluded.token, "
            "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at, "
            "released_at=excluded.released_at, epoch=excluded.epoch, era=excluded.era, "
            "granted_capabilities=excluded.granted_capabilities",
            (
                name,
                holder,
                token,
                acquired_at,
                expires_at,
                released_at,
                epoch,
                era,
                json.dumps(list(capabilities or ())),
            ),
            write=True,
        )

    def next_global_token(self) -> int:
        rows = self._execute("SELECT global_token FROM meta WHERE id = 1")
        seq = int(rows[0][0]) if rows else 0
        seq += 1
        self._execute(
            "INSERT INTO meta (id, global_token) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET global_token = excluded.global_token",
            (seq,),
            write=True,
        )
        return seq

    def bump_epoch(self, name: str) -> int:
        row = self.read(name)
        epoch = int(row["epoch"]) + 1 if row else 1
        # 必须是 upsert：纪元租约从没被 acquire 过时会**没有行**，纯 UPDATE 影响
        # 0 行 → force_new_era() 静默无效（旧持有者照样通过 validate）。
        self._execute(
            "INSERT INTO leases (name, epoch) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET epoch = excluded.epoch",
            (name, epoch),
            write=True,
        )
        return epoch

    def consume(self, name: str, token: int, correlation_id: str) -> None:
        """标记 (lease, token, correlation_id) 已消费；重复消费 → ValueError。"""
        try:
            self._execute(
                "INSERT INTO consumed (lease_name, token, correlation_id) VALUES (?,?,?)",
                (name, int(token), correlation_id),
                write=True,
            )
        except CoordinationUnavailableError as exc:
            if "UNIQUE" in str(exc):
                raise ValueError("already consumed") from exc
            raise


_STORES: Dict[str, _SqliteStore] = {}
_STORES_GUARD = threading.Lock()


def _store_for(directory: str) -> _SqliteStore:
    key = os.path.abspath(directory)
    with _STORES_GUARD:
        store = _STORES.get(key)
        if store is None:
            store = _SqliteStore(directory)
            _STORES[key] = store
        return store


class FileLockLease(DistributedLease):
    """本地文件后端：**SQLite(WAL) 存状态 + OS 级文件锁串行化写者**。

    * **跨进程**：真。写者由 OS 锁串行化，``kill -9`` 时内核自动释放。
    * **并发读 + 写**：WAL 保证写者不被读者阻塞、读者看不到半截状态（裸 JSON
      sidecar 在 Windows 上做不到，见 :class:`_SqliteStore`）。
    * **令牌全局单调**：来自持久化在 ``meta`` 表里的计数器，崩溃不回退。
    * **零第三方依赖**：只用标准库 ``sqlite3``。

    局限（如实说明）：文件锁与 SQLite 只在**同一主机**内有效。跨主机请用
    :class:`RedisLease`。过期时间用 wall clock（同主机内无偏差问题）。
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
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError as exc:
            raise _fail_closed(exc, f"cannot create lease directory {self._dir!r}") from exc
        self._mutex = _OsMutex(self._dir, _safe_name(name))
        self._store = _store_for(self._dir)
        self._default_ttl = float(ttl_sec)

    def _view_from(self, row: Optional[Dict[str, Any]]) -> LeaseView:
        if row is None:
            return LeaseView(self.name, None, None, None, None, None, 0, "free")
        holder = row["holder"]
        token = row["token"]
        released_at = row["released_at"]
        if not holder or token is None:
            state = "released" if released_at is not None else "free"
            return LeaseView(
                self.name, None, None, None, None, released_at, row["epoch"], state
            )
        expires_at = float(row["expires_at"] or 0.0)
        live = expires_at > time.time()
        return LeaseView(
            self.name,
            holder,
            FencingToken(int(token)),
            row["acquired_at"],
            expires_at,
            released_at,
            row["epoch"],
            "held" if live else "expired",
            tuple(row["granted_capabilities"] or ()),
            row["era"],
        )

    # --- DistributedLease ---
    def acquire(
        self,
        owner: str,
        ttl_sec: float = 30.0,
        granted_capabilities: Optional[Sequence[str]] = None,
        era: Optional[int] = None,
    ) -> Tuple[bool, Optional[FencingToken]]:
        ttl = float(self._default_ttl if ttl_sec is None else ttl_sec)
        if ttl <= 0:
            raise ValueError(f"ttl_sec must be > 0, got {ttl}")
        if not owner:
            raise ValueError("owner must be a non-empty string")
        caps = tuple(granted_capabilities or ())

        def _attempt() -> Tuple[bool, Optional[FencingToken]]:
            with self._mutex.locked():
                row = self._store.read(self.name)
                now = time.time()
                if row and row["holder"] and row["token"] is not None:
                    expires_at = row["expires_at"]
                    live = expires_at is not None and float(expires_at) > now
                    if live and row["holder"] == owner and row["era"] == era:
                        # 同一持有者 + 同一纪元：续租，令牌不变。
                        self._store.write(
                            self.name, owner, row["token"], row["acquired_at"],
                            now + ttl, None, row["epoch"], era, caps,
                        )
                        return True, FencingToken(int(row["token"]))
                    if live:
                        # 他人持有（或本持有者换了纪元）且未过期 —— 明确拒绝。
                        # 注意：绝不把现任令牌交给另一个 owner。
                        return False, None
                # 空闲 / 已释放 / 已过期 / 同持有者换纪元
                # → 铸一个**严格更大**的全局新令牌。
                new_token = self._store.next_global_token()
                self._store.write(
                    self.name, owner, new_token, now, now + ttl, None,
                    int(row["epoch"]) if row else 0, era, caps,
                )
                return True, FencingToken(new_token)

        return _with_retry(_attempt, f"acquire lease {self.name!r}")

    def validate(self, owner: str, token: FencingToken) -> bool:
        want = parse_token(token)
        if want is None:
            return False
        row = _with_retry(lambda: self._store.read(self.name), f"read lease {self.name!r}")
        if row is None or row["holder"] != owner:
            return False
        if row["token"] is None or int(row["token"]) != int(want):
            return False
        if row["expires_at"] is None or float(row["expires_at"]) <= time.time():
            return False
        return True

    def renew(self, owner: str, token: FencingToken, ttl_sec: float) -> Optional[FencingToken]:
        want = parse_token(token)
        if want is None:
            return None
        ttl = float(ttl_sec)
        if ttl <= 0:
            return None

        def _attempt() -> Optional[FencingToken]:
            with self._mutex.locked():
                row = self._store.read(self.name)
                if row is None or row["holder"] != owner:
                    return None
                if row["token"] is None or int(row["token"]) != int(want):
                    return None
                if row["expires_at"] is None or float(row["expires_at"]) <= time.time():
                    return None  # 已过期：必须重新 acquire 拿更大的令牌
                now = time.time()
                self._store.write(
                    self.name, owner, row["token"], row["acquired_at"], now + ttl,
                    None, row["epoch"], row["era"], row["granted_capabilities"],
                )
                return want

        return _with_retry(_attempt, f"renew lease {self.name!r}")

    def release(self, owner: str, token: FencingToken) -> bool:
        """释放 —— **校验令牌**（lock.py:120 的直接修复）。

        * 持有者不对 / 令牌不对 → ``False``（**明确拒绝**，不是"锁竞争"）。
        * 锁竞争 / 瞬时 I/O → 内部重试；重试耗尽才抛
          :class:`CoordinationUnavailableError`。

        旧实现把这两种情况都返回 ``False``，调用方无从区分。
        """
        want = parse_token(token)
        if want is None:
            return False

        def _attempt() -> bool:
            with self._mutex.locked():
                row = self._store.read(self.name)
                if row is None or row["holder"] != owner:
                    return False
                if row["token"] is None or int(row["token"]) != int(want):
                    return False
                self._store.write(
                    self.name, None, None, row["acquired_at"], None, time.time(),
                    row["epoch"], row["era"], row["granted_capabilities"],
                )
                return True

        return _with_retry(_attempt, f"release lease {self.name!r}")

    def current(self) -> LeaseView:
        row = _with_retry(lambda: self._store.read(self.name), f"read lease {self.name!r}")
        return self._view_from(row)

    def current_epoch(self) -> int:
        row = _with_retry(lambda: self._store.read(self.name), f"read lease {self.name!r}")
        return int(row["epoch"]) if row else 0

    def force_new_era(self) -> int:
        def _attempt() -> int:
            with self._mutex.locked():
                return self._store.bump_epoch(self.name)

        return _with_retry(_attempt, f"force a new era on {self.name!r}")

    def count_live(self, prefix: Optional[str] = None, now: Optional[float] = None) -> int:
        return _with_retry(
            lambda: self._store.count_live(prefix, time.time() if now is None else now),
            "count live leases",
        )

    def close(self) -> None:
        return None


# =============================================================================
# 可选后端：Redis（真跨节点）—— Lua 已用 fakeredis(+lupa) 真跑过；真 redis 未验证
# =============================================================================


class RedisLease(DistributedLease):
    """基于 Redis 的跨节点租约。

    * 持有标记：``SET <lease> "<owner>|<token>|<era>" PX <ttl_ms>``。过期**由
      Redis 侧判定**，因此不需要两台机器时钟一致。
    * 单调令牌：``INCR`` 一个**全局**键。
    * 释放/续租/校验：全部走 **Lua compare-and-delete / compare-and-set**。

    .. note::
       **Lua 的验证状态（如实）**：4 段 Lua 已用 ``fakeredis`` + ``lupa``（真 Lua
       解释器）执行验证过 —— 见 ``TestRedisLuaScriptsActuallyExecute``。但
       ``fakeredis`` / ``lupa`` **没有声明进 pyproject.toml**，所以 CI 上那组
       **仍然 skip**；``TestRealRedisEndToEnd``（真 ``redis-server``）在本仓库
       环境同样 skip。换言之：Lua **逻辑**已验证，**真 Redis 上的行为未验证**。
       ``redis`` 是延迟导入的。
    """

    DEFAULT_URL = "redis://localhost:6379/0"
    DEFAULT_PREFIX = "liuhao:lease:"

    #: KEYS[1]=lease, KEYS[2]=global token; ARGV[1]=owner, ARGV[2]=ttl_ms,
    #: ARGV[3]=era ("" if none), ARGV[4]=capabilities json
    _ACQUIRE_LUA = """
if redis.replicate_commands then redis.replicate_commands() end
local cur = redis.call('GET', KEYS[1])
if cur then
  local i = string.find(cur, '|', 1, true)
  if not i then return {0, '', 'malformed'} end
  local holder = string.sub(cur, 1, i - 1)
  local rest = string.sub(cur, i + 1)
  local j = string.find(rest, '|', 1, true)
  if not j then return {0, '', 'malformed'} end
  local tok = string.sub(rest, 1, j - 1)
  local era = string.sub(rest, j + 1)
  if holder ~= ARGV[1] then
    return {0, '', ''}
  end
  if era ~= ARGV[3] then
    return {0, '', 'era-changed'}
  end
  redis.call('SET', KEYS[1], cur, 'PX', tonumber(ARGV[2]))
  return {1, tok, 'renewed'}
end
local tok = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1] .. '|' .. tostring(tok) .. '|' .. ARGV[3],
           'PX', tonumber(ARGV[2]))
redis.call('SET', KEYS[1] .. ':caps', ARGV[4], 'PX', tonumber(ARGV[2]))
return {1, tostring(tok), 'acquired'}
"""

    #: KEYS[1]=lease; ARGV[1]=owner, ARGV[2]=token
    _RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
local holder = string.sub(cur, 1, i - 1)
local rest = string.sub(cur, i + 1)
local j = string.find(rest, '|', 1, true)
if not j then return 0 end
if holder ~= ARGV[1] or string.sub(rest, 1, j - 1) ~= ARGV[2] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""

    #: KEYS[1]=lease; ARGV[1]=owner, ARGV[2]=token, ARGV[3]=ttl_ms
    _RENEW_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
local holder = string.sub(cur, 1, i - 1)
local rest = string.sub(cur, i + 1)
local j = string.find(rest, '|', 1, true)
if not j then return 0 end
if holder ~= ARGV[1] or string.sub(rest, 1, j - 1) ~= ARGV[2] then return 0 end
redis.call('SET', KEYS[1], cur, 'PX', tonumber(ARGV[3]))
return 1
"""

    #: KEYS[1]=lease; ARGV[1]=owner, ARGV[2]=token
    _VALIDATE_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local i = string.find(cur, '|', 1, true)
if not i then return 0 end
local holder = string.sub(cur, 1, i - 1)
local rest = string.sub(cur, i + 1)
local j = string.find(rest, '|', 1, true)
if not j then return 0 end
if holder ~= ARGV[1] or string.sub(rest, 1, j - 1) ~= ARGV[2] then return 0 end
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
        self._caps_key = f"{prefix}{name}:caps"
        # 令牌计数器是**全局**的（不按 name 分），否则令牌不再全局单调。
        self._token_key = f"{prefix}__global_token__"
        self._epoch_key = f"{prefix}{name}:epoch"
        self._client = client
        self._injected_client = client is not None
        self._connected = False

    def connect(self) -> None:
        if self._client is None:
            try:
                import redis  # 延迟导入：默认 file 路径不需要 redis-py
            except ImportError as exc:
                raise CoordinationUnavailableError(
                    "redis coordination backend needs redis-py: pip install 'redis>=5.0.0'"
                ) from exc
            self._client = redis.Redis.from_url(
                self._url, socket_connect_timeout=self._connect_timeout
            )
        try:
            pong = self._client.ping()
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"cannot reach Redis coordination backend ({self._url}): "
                f"{type(exc).__name__}: {exc}"
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

    def _run(self, script: str, keys: List[str], args: List[Any]):
        client = self._client_or_raise
        try:
            return client.eval(script, len(keys), *keys, *args)
        except Exception as exc:  # noqa: BLE001 - 后端不可达必须 fail-closed
            raise CoordinationUnavailableError(
                f"Redis coordination command failed: {type(exc).__name__}: {exc}"
            ) from exc

    def _get(self, key: str):
        client = self._client_or_raise
        try:
            return client.get(key)
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination read failed: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _check_owner(owner: str) -> None:
        # 编程/配置错误 → ValueError（响亮；且与 bus_backends.get_bus_backend 一致）。
        if not owner:
            raise ValueError("owner must be a non-empty string")
        if "|" in owner:
            raise ValueError("owner must not contain '|' (lease encoding separator)")

    def acquire(
        self,
        owner: str,
        ttl_sec: float = 30.0,
        granted_capabilities: Optional[Sequence[str]] = None,
        era: Optional[int] = None,
    ) -> Tuple[bool, Optional[FencingToken]]:
        self._check_owner(owner)
        ttl_ms = max(1, int(float(ttl_sec) * 1000.0))
        result = self._run(
            self._ACQUIRE_LUA,
            [self._lease_key, self._token_key],
            [
                owner,
                ttl_ms,
                "" if era is None else str(era),
                json.dumps(list(granted_capabilities or ())),
            ],
        )
        if int(result[0]) != 1:
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
        return want if int(result) == 1 else None

    def release(self, owner: str, token: FencingToken) -> bool:
        want = parse_token(token)
        if want is None:
            return False
        self._check_owner(owner)
        result = self._run(self._RELEASE_LUA, [self._lease_key], [owner, str(int(want))])
        return int(result) == 1

    def current(self) -> LeaseView:
        raw = self._get(self._lease_key)
        epoch = int(self._get(self._epoch_key) or 0)
        caps_raw = self._get(self._caps_key)
        caps: Tuple[str, ...] = ()
        if caps_raw:
            if isinstance(caps_raw, bytes):
                caps_raw = caps_raw.decode("utf-8")
            try:
                caps = tuple(json.loads(caps_raw))
            except ValueError:
                caps = ()
        if not raw:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        parts = raw.split("|", 2)
        if len(parts) < 3:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        holder, tok_s, era_s = parts
        try:
            token = FencingToken(int(tok_s))
        except ValueError:
            return LeaseView(self.name, None, None, None, None, None, epoch, "free")
        client = self._client_or_raise
        try:
            pttl = client.pttl(self._lease_key)
        except Exception:  # noqa: BLE001
            pttl = -1
        expires_at = None if pttl is None or int(pttl) < 0 else time.time() + int(pttl) / 1000.0
        return LeaseView(
            self.name, holder, token, None, expires_at, None, epoch, "held", caps,
            int(era_s) if era_s else None,
        )

    def current_epoch(self) -> int:
        return int(self._get(self._epoch_key) or 0)

    def force_new_era(self) -> int:
        client = self._client_or_raise
        try:
            return int(client.incr(self._epoch_key))
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination epoch bump failed: {type(exc).__name__}: {exc}"
            ) from exc

    def count_live(self, prefix: Optional[str] = None, now: Optional[float] = None) -> int:
        """按前缀扫描仍存在的租约键。

        Redis 没有"按前缀计数"的原语，只能 SCAN + GET，因此是 O(N) 且**不是原子
        快照**。依赖它的 ``max_executors`` 上限请知悉这一点。
        """
        client = self._client_or_raise
        pattern = f"{self._prefix}{prefix or ''}*"
        try:
            live = 0
            for key in client.scan_iter(match=pattern, count=200):
                raw_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                if raw_key.endswith(":caps") or raw_key.endswith(":epoch"):
                    continue
                if raw_key == self._token_key:
                    continue
                if client.get(key):
                    live += 1
            return live
        except Exception as exc:  # noqa: BLE001
            raise CoordinationUnavailableError(
                f"Redis coordination scan failed: {type(exc).__name__}: {exc}"
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

    :param backend: ``"file"``（默认）或 ``"redis"``；``None`` 时读
        :data:`BACKEND_ENV`。
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
        lease.connect()  # 连不上 → CoordinationUnavailableError（不降级）
        return lease

    raise ValueError(
        f"Unsupported coordination backend: {raw!r}. Supported: {list(SUPPORTED_BACKENDS)}"
    )


# =============================================================================
# 桥接：让 ExecutorFence 不改一行就跑在真实协调后端上
# =============================================================================


class DistributedExecutorLease(ExecutorLease):
    """:class:`~src.kernels.execution.fence.ExecutorLease` 的协调后端实现。

    每个 executor_id 对应一个命名租约（``executor:<id>``）。**租约的 holder 是
    进程唯一的** uuid（:func:`process_holder_id`），**不是** executor_id —— 否则
    两个声称同一 executor_id 的进程会被当成"同一持有者续租"而拿到**同一个**令牌，
    围栏彻底失效。

    能力授权（``granted_capabilities``）与获取时的纪元（``era``）**持久化在后端**，
    因此任何进程都能校验一个"不是自己获取"的令牌；``current().state`` 也能区分
    "unknown"（无租约）与"stale"（有租约但不属于本进程/已过期）。

    ``count_active()`` 走后端的**全局**计数，不是进程内视图，所以
    ``max_executors`` 的 N+1 上限跨进程成立。

    关于 boot_gen：只记录、不参与存活性判定（见模块 docstring）。
    """

    DEFAULT_ERA_NAME = "executor-fence-era"
    LEASE_PREFIX = "executor:"
    #: 准入租约（名字刻意**不带** LEASE_PREFIX，免得被 count_live 数进去）。
    ADMISSION_NAME = "admission:executor-fence"

    def __init__(
        self,
        lease_factory: Optional[Callable[[str], DistributedLease]] = None,
        heartbeat_timeout: float = 60.0,
        era_name: str = DEFAULT_ERA_NAME,
        max_executors: Optional[int] = None,
        admission_ttl: float = 10.0,
    ) -> None:
        if lease_factory is None:

            def lease_factory(nm: str) -> DistributedLease:
                return get_distributed_lease(nm)

        self._factory = lease_factory
        self._heartbeat_timeout = float(heartbeat_timeout)
        # P0-3(b)：N+1 上限要么**跨进程成立**，要么明确声明是进程内口径。这里选
        # 前者：把 count+acquire 包进一个全局准入临界区，使它成为原子操作。
        self._max_executors = (
            int(max_executors) if max_executors is not None else None
        )
        self._admission_ttl = float(admission_ttl)
        self._admission_thread_lock = threading.RLock()
        self._admission: Optional[DistributedLease] = None
        self._holder = process_holder_id()
        self._era_lease: DistributedLease = self._factory(era_name)
        self._leases: Dict[str, DistributedLease] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._heartbeat_failures = 0

    # --- 准入（N+1 上限的原子性）---
    @property
    def max_executors(self) -> Optional[int]:
        """本桥接**实际强制**的并发执行者上限（``None`` = 不限）。"""
        return self._max_executors

    @contextlib.contextmanager
    def _admission_section(self):
        """把"数一次 + 获取一次"变成原子的全局临界区。

        ``ExecutorFence.acquire_for`` 的上限逻辑是 **先 count_active() 后
        lease.acquire()**，两步之间没有锁 ⇒ 4 个进程同时看到 0，于是全部放行
        （实测：max_executors=2 却放行了 3~4 个）。这里用一个**专门的准入租约**
        把这两步串起来：跨进程靠租约（持有者进程被 kill 时靠 TTL 自动释放），
        进程内靠 RLock。
        """
        if self._max_executors is None:
            yield
            return
        with self._admission_thread_lock:
            if self._admission is None:
                self._admission = self._factory(self.ADMISSION_NAME)
            lease = self._admission
            token = None
            for attempt in range(_RETRY_ATTEMPTS):
                ok, token = lease.acquire(self._holder, self._admission_ttl)
                if ok and token is not None:
                    break
                # A refused admission acquire (another holder transiently holds
                # the admission lease) is contention: count it, then back off.
                _emit_coord_metric("record_coordinator_admission_contention")
                _backoff_sleep(attempt)
            else:
                raise CoordinationUnavailableError(
                    "cannot enter the executor admission section "
                    f"(lease {self.ADMISSION_NAME!r} is not obtainable)"
                )
            try:
                yield
            finally:
                try:
                    lease.release(self._holder, token)
                except Exception:  # noqa: BLE001 - 释放失败不能盖掉主流程
                    pass

    @contextlib.contextmanager
    def _admission_check(self, executor_id: str, before: LeaseView):
        """准入临界区 + 上限判定（``max_executors is None`` 时是空操作）。"""
        if self._max_executors is None:
            yield
            return
        with self._admission_section():
            mine_is_live = (
                before.state == "held" and before.holder == self._holder
            )
            if not mine_is_live:
                live = self._count_live_executors()
                if live >= self._max_executors:
                    raise ExecutorLimitExceeded(
                        f"concurrent executor cap {self._max_executors} reached "
                        f"(live={live})"
                    )
            yield

    def _count_live_executors(self) -> int:
        probe = self._factory(f"{self.LEASE_PREFIX}__count__")
        return probe.count_live(prefix=self.LEASE_PREFIX)

    # --- 内部 ---
    def _lease_for(self, executor_id: str) -> DistributedLease:
        with self._lock:
            lease = self._leases.get(executor_id)
            if lease is None:
                lease = self._factory(f"{self.LEASE_PREFIX}{executor_id}")
                self._leases[executor_id] = lease
            return lease

    def _era(self) -> int:
        return self._era_lease.current_epoch()

    def _guarded(self, what: str, fn: Callable[[], Any]) -> Any:
        """P0-1：后端任何异常都必须以 ``ExecutorFenceDenied`` 子类冒出去。"""
        try:
            return fn()
        except ExecutorFenceDenied:
            raise
        except BaseException as exc:  # noqa: BLE001 - 含 OSError / sqlite3.Error
            raise ExecutorFenceBackendError(
                f"{what}: coordination backend failed ({type(exc).__name__}: {exc})"
            ) from exc

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
        era = self._era()
        # 我上次持有的令牌 < 现任令牌 ⇒ 我被人夺权了。这是围栏的核心信号。
        # my_last_token 为 None 时按 -1 处理（与 SqliteExecutorLease 完全一致：
        # "我说不出上次的令牌" ⇒ 任何仍活着的租约都比我新 ⇒ 拒绝，绝不把现任
        # 令牌交给一个拿不出身份证明的进程）。
        #
        # 注意检查必须在 acquire **之前**：否则一旦 acquire 成功再抛异常，就会
        # 留下一条"我持有但其实我已经被拒绝"的租约。
        remembered = (self._meta.get(executor_id) or {}).get("token")
        raw_last = my_last_token if my_last_token is not None else remembered
        last = -1 if raw_last is None else int(raw_last)
        if before.state == "held" and before.token is not None and int(before.token) > last:
            raise FencedExecutorError(
                f"executor {executor_id!r} was fenced: token {int(before.token)} "
                f"> my last {last}"
            )
        with self._admission_check(executor_id, before):
            ok, token = lease.acquire(
                self._holder, ttl_sec, list(granted_capabilities or ()), era
            )
        if not ok or token is None:
            raise StaleExecutorError(
                f"lease for executor {executor_id!r} is held by another live process "
                f"(holder={before.holder!r}) until {before.expires_at}"
            )
        token = FencingToken(token)
        with self._lock:
            self._meta[executor_id] = {
                "owner": owner,
                "capabilities": tuple(granted_capabilities or ()),
                # boot_gen 只做记录；绝不参与存活性判定（跨主机无意义）。
                "boot_gen": boot_gen,
                "token": int(token),
                "era": era,
            }
        return int(token)

    # --- ExecutorLease ABC ---
    def acquire(
        self, executor_id, owner, ttl_sec, granted_capabilities,
        boot_gen=None, my_last_token=None,
    ):
        return self._guarded(
            "executor fence acquire",
            lambda: self._acquire(
                executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
            ),
        )

    def acquire_within(
        self, executor_id, owner, ttl_sec, granted_capabilities,
        boot_gen=None, my_last_token=None,
    ):
        # 没有外部事务可加入（后端自带原子性），语义与 acquire 相同。
        return self.acquire(
            executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
        )

    def validate(self, executor_id, token, capabilities=None):
        return self._guarded(
            "executor fence validate",
            lambda: self._validate(executor_id, token, capabilities),
        )

    def _validate(self, executor_id, token, capabilities) -> bool:
        want = parse_token(token)
        if want is None:
            return False
        view = self._lease_for(executor_id).current()
        if view.state != "held":
            return False
        if view.holder != self._holder:
            # 另一个进程正持有这个 executor_id —— 同一时刻只允许一个。
            return False
        if view.token is None or int(view.token) != int(want):
            return False
        if view.era != self._era():
            return False  # 纪元被 force_new_era 推进
        if capabilities is not None and not set(capabilities).issubset(
            set(view.granted_capabilities or ())
        ):
            return False
        return True

    def is_stale(self, executor_id, token):
        return not self.validate(executor_id, token)

    def heartbeat(self, executor_id, token):
        """续租。**失败必须可见**。

        旧实现无论成功失败都返回 ``None``，并吞掉 ``renew()`` 抛出的
        ``StaleExecutorError``，调用方无法察觉心跳已经失效。
        """
        want = parse_token(token)
        if want is None:
            self._heartbeat_failures += 1
            raise StaleExecutorError(f"heartbeat with an invalid token for {executor_id!r}")
        new = self._guarded(
            "executor fence heartbeat",
            lambda: self._lease_for(executor_id).renew(
                self._holder, want, self._heartbeat_timeout
            ),
        )
        if new is None:
            self._heartbeat_failures += 1
            raise StaleExecutorError(
                f"heartbeat failed: no live lease for executor {executor_id!r}"
            )
        return int(new)

    def renew(self, executor_id, token, ttl_sec):
        want = parse_token(token)
        if want is None:
            raise StaleExecutorError(f"cannot renew: invalid token for {executor_id!r}")
        new = self._guarded(
            "executor fence renew",
            lambda: self._lease_for(executor_id).renew(self._holder, want, ttl_sec),
        )
        if new is None:
            _emit_coord_metric("record_fence_renew_failure")
            raise StaleExecutorError(f"cannot renew: no live lease for {executor_id!r}")
        with self._lock:
            meta = self._meta.get(executor_id)
            if meta is not None:
                meta["token"] = int(new)
        return int(new)

    def release(self, executor_id, token):
        want = parse_token(token)
        if want is None:
            return False
        released = self._guarded(
            "executor fence release",
            lambda: self._lease_for(executor_id).release(self._holder, want),
        )
        if released:
            with self._lock:
                self._meta.pop(executor_id, None)
        return bool(released)

    def consume_token(self, executor_id, token, correlation_id):
        # P0-1：consume 也在围栏边界内 —— 后端在这里挂掉同样必须是"拒绝"，
        # 否则一个裸 CoordinationUnavailableError 会穿过 enforce 落到调用方的
        # ``except Exception`` 上，动作被当成已授权执行。
        return self._guarded(
            "executor fence consume",
            lambda: self._consume_token(executor_id, token, correlation_id),
        )

    def _consume_token(self, executor_id, token, correlation_id):
        want = parse_token(token)
        if want is None:
            raise ReplayDetectedError(f"cannot consume an invalid token for {executor_id!r}")
        lease = self._lease_for(executor_id)
        store = getattr(lease, "_store", None)
        if store is None:
            # 非文件后端：退回进程内去重（如实记录这一限制）。
            return self._consume_local(executor_id, want, correlation_id)
        try:
            store.consume(lease.name, int(want), correlation_id)
        except ValueError as exc:
            raise ReplayDetectedError(
                f"replay of (executor={executor_id}, token={int(want)}, "
                f"corr={correlation_id})"
            ) from exc

    def _consume_local(self, executor_id, token, correlation_id):
        if not hasattr(self, "_consumed"):
            self._consumed = set()
        key = (executor_id, int(token), correlation_id)
        if key in self._consumed:
            raise ReplayDetectedError(
                f"replay of (executor={executor_id}, token={int(token)}, "
                f"corr={correlation_id})"
            )
        self._consumed.add(key)

    def force_new_era(self):
        return self._guarded("executor fence force_new_era", self._era_lease.force_new_era)

    def current(self, executor_id):
        return self._guarded("executor fence current", lambda: self._current(executor_id))

    def _current(self, executor_id) -> ExecutorLeaseView:
        view = self._lease_for(executor_id).current()
        with self._lock:
            meta = self._meta.get(executor_id) or {}
        if view.state in ("free", "released"):
            return ExecutorLeaseView(
                None, None, None, None, None, None, None, None, (), view.released_at, "unknown"
            )
        state = "held" if view.state == "held" else "stale"
        if view.holder != self._holder:
            state = "stale"
        elif view.era is not None and view.era != self._era():
            state = "stale"
        caps = tuple(view.granted_capabilities or ()) or tuple(meta.get("capabilities") or ())
        return ExecutorLeaseView(
            executor_id,
            view.token,
            view.era,
            meta.get("owner"),
            view.acquired_at,
            view.expires_at,
            view.acquired_at,
            meta.get("boot_gen"),
            caps,
            view.released_at,
            state,
        )

    def count_active(self, now=None):
        """**后端全局**计数（跨进程成立），不是进程内视图。"""
        probe = self._factory(f"{self.LEASE_PREFIX}__count__")
        return self._guarded(
            "executor fence count_active",
            lambda: probe.count_live(prefix=self.LEASE_PREFIX, now=now),
        )

    def known_executor(self, executor_id):
        return self._guarded(
            "executor fence known_executor",
            lambda: self._lease_for(executor_id).current().holder is not None,
        )

    @property
    def heartbeat_failures(self) -> int:
        """心跳失败次数（让"心跳悄悄失效"可见）。"""
        return self._heartbeat_failures


def new_executor_id() -> str:
    """生成一个稳定的 executor 身份（uuid，绝不用 PID）。"""
    return uuid.uuid4().hex
