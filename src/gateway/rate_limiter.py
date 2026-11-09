"""
Rate Limiting Middleware for LiuHao AI OS Gateway

Token Bucket algorithm implementation with:
- Key-dependent limits (per API key)
- In-memory fallback, Redis-enabled design
- Rate limit headers on responses
- Graceful degradation when Redis unavailable
"""

import asyncio
import ipaddress
import logging
import math
import os
import queue
import threading
import time
import uuid
from _thread import start_new_thread as _start_new_thread
from concurrent.futures import Future
from typing import Dict, Iterable, Optional, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse

#: Paths limited by default, in addition to whatever ``server.rate_limit.paths``
#: configures. Config can *extend* this set but can never shrink it, so a
#: missing/old config file cannot silently leave the gateway unmetered.
DEFAULT_PROTECTED_PATHS: Tuple[str, ...] = (
    "/v1/auth/login",
    "/v1/chat",
    "/v1/goals",
    "/v1/models",
    "/v1/knowledge",
    "/v1/events",
    "/v1/audit",
    "/v1/ai",
    "/v1/projects",
    "/v1/profile",
    "/v1/plugins",
    "/v1/files",
    "/v1/identity",
    "/v1/trust",
    "/v1/networks",
    "/v1/kernels",
    "/v1/alerts",
    "/v1/observability",
    "/v1/policy",
    "/v1/production",
    "/v1/dashboard",
    "/v1/roster",
)

#: 探针端点：**只豁免限流与身份解析，不豁免认证**。
#:
#: 动手前核对的现状（2026-11-08）：``/v1/health``、``/v1/ready``、``/v1/metrics``
#: 由 ``health.py`` 直接挂载、无 FastAPI 依赖；``/v1/metrics/prometheus`` 同样
#: 无依赖。四个都是**公开探针**，本次改动不新增、也不移除任何认证要求。
#:
#: 必须**精确匹配**：``/v1/health/extra``、``/v1/metrics/..`` 之类不会被豁免，
#: 否则别的路径可以借探针入口绕过限流。
PROBE_EXEMPT_PATHS: Tuple[str, ...] = (
    "/v1/health",
    "/v1/ready",
    "/v1/metrics",
    "/v1/metrics/prometheus",
)

#: 身份校验 lane 池（D-1 ④）
#: ---------------------------------------------------------------------------
#: ``_identity`` 会读 API Key 表与 JWT 撤销库（``sqlite3.connect``），属于
#: **阻塞 I/O**，绝不能跑在事件循环线程上；但线程池的 ``submit`` 在满载时
#: 又会卡在 ``Thread.start``（``_started.wait()`` 无超时）。现在改成固定数量
#: 的**专属 lane**（默认 16 条）：
#:
#: * 用 ``_thread.start_new_thread`` 非阻塞启动（不等 ``_started``），
#:   ``daemon=True`` 不拖住进程退出；
#: * 因为没有走 ``Thread.start``，``Thread.is_alive()`` 恒 False、``join()``
#:   会抛错 —— 就绪只能由线程自己第一行 ``ready_flags[i].set()``。**全仓库
#:   禁止对这几条 lane 调 ``is_alive()/join()``**，也不复用依赖 ``Thread``
#:   语义的封装（``ThreadPoolExecutor.submit`` 之类）。
#: * 每条 lane 一个 1 格收件箱。全部都在忙时先做**有界异步等待**
#:   （默认 200ms，见 ``identity_queue_wait_seconds``）：控制台首屏一次
#:   并发 6~9 个请求属于常态，旧实现"立即 503"把这种微突发误报成身份服务
#:   故障；等待只让出事件循环（不占线程、不排队堆积），预算耗尽仍 503。
#:
#: lane 数量可配：``LIUHAO_IDENTITY_LANES``（**启动前**设置，导入期起线程）。
#: 默认 16 是按真实场景留的余量：单成员首屏约 6 路并发，2~3 个成员
#: （桌面端 + 网页端 + 手机端）同时刷新约 12~18 路；解析本身毫秒级，
#: 槽位数才是并发上限。实测 16 lane 下 16 路同时解析 96/96 全部成功。
_DEFAULT_IDENTITY_LANE_COUNT = 16
_IDENTITY_LANE_MIN = 1
_IDENTITY_LANE_MAX = 32


def _configured_identity_lane_count() -> int:
    """读取 lane 数量；非法/越界值退回默认 16，并钳在 [1, 32]。"""
    raw = os.environ.get("LIUHAO_IDENTITY_LANES")
    if raw:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return max(_IDENTITY_LANE_MIN, min(_IDENTITY_LANE_MAX, value))
    return _DEFAULT_IDENTITY_LANE_COUNT


_IDENTITY_LANE_COUNT = _configured_identity_lane_count()

#: 弃置上限：超时但线程还没退出的 lane 最多 4 条 → 总线程 ≤ lane 数 + 4。
_IDENTITY_MAX_ABANDONED = 4

#: 连续超时达到该值即降级：新请求不再排队，直接 503。
_IDENTITY_DEGRADE_THRESHOLD = 3

#: 降级后的自愈探针：``SELECT 1``（1 秒超时）。第一次**立即**探，之后按
#: 1→2→4→8→10 秒指数退避；``_IDENTITY_PROBE_INTERVAL_SECONDS`` 是退避上限，
#: 保留原名是因为回归用例通过它把退避压到 50ms。
_IDENTITY_PROBE_INTERVAL_SECONDS = 30.0
_IDENTITY_PROBE_FIRST_GAP_SECONDS = 1.0
_IDENTITY_PROBE_MAX_GAP_SECONDS = 10.0
_IDENTITY_PROBE_TIMEOUT_SECONDS = 1.0

#: 每条 lane 的就绪标记。**只认 Event**：``_thread.start_new_thread`` 下
#: ``Thread.is_alive()`` 不可信。
_READY_FLAGS = [threading.Event() for _ in range(_IDENTITY_LANE_COUNT)]

#: 请求级身份解析预算（默认 2 秒），运维可用环境变量覆盖。
_DEFAULT_IDENTITY_TIMEOUT_SECONDS = 2.0

#: 池满时的**有界等待**预算（默认 0.2 秒）：微突发（控制台首屏并发刷新）
#: 在这个窗口内能拿到 lane 并一次成功；超过预算才 503。必须小于
#: ``test_identity_pool_full_fails_fast`` 的 0.5 秒上限，保持 fail-fast 语义。
_DEFAULT_IDENTITY_QUEUE_WAIT_SECONDS = 0.2

#: 等待期间让出事件循环的粒度。身份解析实测 0.03~2ms，4ms 足够细，
#: 又不会把事件循环变成忙轮询。
_IDENTITY_QUEUE_POLL_SECONDS = 0.004


def identity_timeout_seconds() -> float:
    """读取请求级身份解析超时；非法值退回默认 2 秒。"""
    raw = os.environ.get("LIUHAO_IDENTITY_TIMEOUT_SECONDS")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return _DEFAULT_IDENTITY_TIMEOUT_SECONDS


def identity_queue_wait_seconds() -> float:
    """读取池满时的有界等待预算；0 表示退回旧的"立即 503"。"""
    raw = os.environ.get("LIUHAO_IDENTITY_QUEUE_WAIT_SECONDS")
    if raw:
        try:
            value = float(raw)
            if value >= 0:
                return value
        except (TypeError, ValueError):
            pass
    return _DEFAULT_IDENTITY_QUEUE_WAIT_SECONDS


class IdentityCheckTimeout(RuntimeError):
    """身份解析超过请求级预算：lane 被弃置，并已尝试 ``interrupt()``。"""


class IdentityPoolFull(RuntimeError):
    """有界等待预算耗尽仍没有空闲 lane：503，但不无限排队。"""


class IdentityCheckDegraded(RuntimeError):
    """连续超时已进入降级：新请求直接 503，等探针自愈。"""


class IdentityThreadStartFailed(RuntimeError):
    """本机创建不了身份校验线程（``can't start new thread``）。"""


class _IdentityLane:
    """一条专职身份解析 lane。"""

    __slots__ = ("slot", "thread_ident", "inbox", "abandoned")

    def __init__(self, slot: int):
        self.slot = slot
        self.thread_ident: Optional[int] = None
        self.inbox: "queue.Queue" = queue.Queue(maxsize=1)
        self.abandoned = threading.Event()


_lane_state_lock = threading.Lock()
_idle_lanes: "queue.Queue[_IdentityLane]" = queue.Queue()
_active_lanes: Dict[int, _IdentityLane] = {}
_abandoned_running = 0
_thread_start_failed = False
_consecutive_identity_timeouts = 0
_identity_degraded = False
_probe_thread_started = False
_identity_check_degraded_total = 0
_identity_check_recovered_total = 0
_identity_check_rejected_total = 0

#: 同一请求里 Global 与业务限流各解析一次身份：用 scope 缓存把 DB 读降为一次。
_IDENTITY_SCOPE_KEY = "liuhao.identity"


#: 生成类路径单独成桶。控制面（``/v1/models``、``/v1/accounts`` 等）与聊天
#: 共用同一个 principal 桶时，根账号连调 8 次模型管理接口就把聊天额度从 99
#: 挤到 91 —— 管理操作会直接吃掉对话配额，这里按"路径族"拆开。
CHAT_PATH_PREFIXES: Tuple[str, ...] = ("/v1/chat", "/v1/goals", "/v1/ai")


def path_family(path: str) -> str:
    """把请求归到 ``chat`` / ``control`` 两个额度族。"""
    for prefix in CHAT_PATH_PREFIXES:
        base = prefix.rstrip("/")
        if path == base or path.startswith(base + "/"):
            return "chat"
    return "control"


def looks_like_jwt(token: str) -> bool:
    """JWT 一定是 ``eyJ`` 开头的三段式。

    热路径上先看形态再决定要不要查 API Key 表：已登录用户的请求必然查不到
    API Key，却每次都要付一次 SHA256 + 全表线性扫描。
    """
    return token.startswith("eyJ") and token.count(".") == 2


def limit_code(info: Dict[str, any]) -> str:
    """429 的机器可读分类：按 principal 还是按 IP 命中。"""
    key_id = str(info.get("key_id") or "")
    return "RATE_LIMITED_IP" if key_id.startswith("ip:") else "RATE_LIMITED_PRINCIPAL"


def _parse_trusted_proxies(values: Iterable[str]) -> Tuple:
    networks = []
    for raw in values or ():
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def resolve_client_ip(peer_host: str, forwarded_for: Optional[str],
                      trusted_proxies: Iterable[str]) -> str:
    """Return the effective client address, honouring ``X-Forwarded-For`` only
    when the *direct peer* is an explicitly configured trusted proxy.

    Without this, any caller can spoof ``X-Forwarded-For`` and either frame
    another IP or dodge per-IP throttling.
    """
    peer = str(peer_host or "unknown")
    networks = _parse_trusted_proxies(trusted_proxies)
    if not networks:
        return peer
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_addr in net for net in networks):
        return peer
    hops = [h.strip() for h in (forwarded_for or "").split(",") if h.strip()]
    if not hops:
        return peer
    # Walk right-to-left: the right-most untrusted hop is the client.
    for hop in reversed(hops):
        try:
            hop_addr = ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not any(hop_addr in net for net in networks):
            return str(hop_addr)
    return hops[0]


class TokenBucket:
    """
    Thread-safe Token Bucket rate limiter.

    Attributes:
        capacity: Maximum tokens (burst size)
        refill_rate: Tokens per second refill rate
        tokens: Current token count
        last_refill: 单调时钟锚点（只用来算"过了多久"）
        last_refill_epoch: 墙钟锚点（只用来对外报 reset）
        lock: Thread safety lock
    """

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = float(capacity)
        # 用单调时钟算 elapsed：系统时间被往回拨时 elapsed 不会变成负数
        # （旧实现用墙钟，回拨 1 小时直接把 tokens 加成 -3550，登录 429 约 59 分钟）。
        self.last_refill = time.monotonic()
        # reset 是给客户端看的 epoch 秒，必须用墙钟；与 elapsed 用的钟分开存。
        self.last_refill_epoch = time.time()
        self.last_used = self.last_refill_epoch
        self.lock = threading.Lock()

    def _refill(self, now: float) -> None:
        """按单调时钟的 elapsed 补充令牌；永不让桶变成负数。"""
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        # 防御：无论 elapsed 因何种原因非正，tokens 都钳在 [0, capacity]。
        self.tokens = max(0.0, self.tokens)
        self.last_refill = now

    def try_consume(self, n: int = 1) -> Tuple[bool, Dict[str, any]]:
        """
        Try to consume n tokens from the bucket.

        Returns:
            (allowed, info_dict) tuple
            info_dict contains: remaining, reset, limit
        """
        with self.lock:
            self._refill(time.monotonic())
            wall_now = time.time()
            self.last_used = wall_now

            allowed = self.tokens >= n

            if allowed:
                self.tokens -= n

            # reset = 真实恢复时刻（epoch 秒），不再固定写 now+1：
            #  * 允许：桶重新装满的时间；
            #  * 拒绝：攒够这次请求所需令牌的时间。
            deficit = (self.capacity if allowed else n) - self.tokens
            wait_s = max(0.0, deficit) / self.refill_rate if self.refill_rate > 0 else 0.0
            info = {
                "limit": self.capacity,
                "remaining": int(self.tokens) if allowed else 0,
                "reset": int(math.ceil(wall_now + wait_s)),
            }

            return allowed, info


class RateLimiter:
    """
    Rate limiter manager with per-key bucket storage.

    Uses API key scopes to determine rate limits.
    Falls back to in-memory if Redis unavailable.
    """

    DEFAULT_LIMITS: Dict[str, Tuple[int, float]] = {
        # (capacity, refill_rate_per_second)
        "admin": (1000, 10.0),
        "provider": (500, 5.0),
        "agent": (200, 2.0),
        "workflow": (300, 3.0),
        "memory": (400, 4.0),
        "metrics": (600, 6.0),
        "readonly": (100, 1.0),
        "custom": (100, 1.0),
    }

    def __init__(
        self,
        *,
        default_capacity: int = 100,
        default_rate: float = 1.0,
        max_buckets: int = 4096,
        idle_ttl_seconds: float = 600.0,
    ):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._default_capacity = default_capacity
        self._default_rate = default_rate
        self._max_buckets = max(1, int(max_buckets))
        self._idle_ttl = max(1.0, float(idle_ttl_seconds))
        self._last_sweep = 0.0

    def _sweep_locked(self, now: float) -> None:
        """Evict idle, fully-refilled buckets. Caller must hold ``self._lock``.

        Without reclamation a caller rotating API keys / source addresses
        grows the dict without bound -- a memory-exhaustion vector. A bucket
        that is full again carries no state worth keeping (a fresh bucket
        would behave identically).

        Sweeping is amortised (at most every 10s, immediately when over cap)
        so the hot path stays O(1).
        """
        if len(self._buckets) < self._max_buckets and now - self._last_sweep < 10.0:
            return
        self._last_sweep = now
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.last_used >= self._idle_ttl
            and bucket.tokens >= bucket.capacity
        ]
        for key in stale:
            self._buckets.pop(key, None)
        if len(self._buckets) < self._max_buckets:
            return
        # Still over the cap: drop the oldest buckets regardless of fill level.
        over = len(self._buckets) - self._max_buckets + 1
        for key in sorted(
            self._buckets.keys(), key=lambda k: self._buckets[k].last_used
        )[:max(1, over)]:
            self._buckets.pop(key, None)

    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _identity(
        self, request: Request, trusted_proxies: Iterable[str] = ()
    ) -> Tuple[str, Optional[any]]:
        """解析请求身份，返回 (基底 key, API Key 对象或 None)。

        每个请求只解析一次：旧实现先在 ``_get_key_id`` 里查一次 API Key，
        再在 ``check_rate_limit`` 里为了拿 scopes 又查一次，Bearer 请求
        等于付两遍 O(n) 扫描。
        """
        from .policy import TOKEN_HEADER

        # Try to get from headers/api key first
        auth_header = request.headers.get(TOKEN_HEADER) or request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            # 形态判断：JWT 不可能是 API Key，没必要查表。
            if not looks_like_jwt(token):
                from ..security.api_keys import validate_api_key

                key_obj = validate_api_key(token)
                if key_obj and key_obj.id:
                    return f"key:{key_obj.id}", key_obj
            # Authenticated humans share one bucket by IP otherwise -- one
            # noisy session would throttle every other user behind it. Key
            # JWT callers by their principal.
            try:
                from ..security.jwt_handler import (
                    IdentityStoreUnavailable,
                    TokenType,
                    get_jwt_handler,
                )

                # 必须传枚举：``validate_token`` 用 ``payload.token_type !=
                # expected_type`` 比较，传字符串永远不相等，随后拼错误信息时
                # ``expected_type.value`` 直接抛 AttributeError —— 被下面的
                # ``except`` 吞掉，于是所有登录用户静默退回"按 IP 一个桶"。
                payload = get_jwt_handler().validate_token(
                    token, expected_type=TokenType.ACCESS
                )
                subject = getattr(payload, "sub", None)
                if subject:
                    return f"sub:{subject}", None
            except IdentityStoreUnavailable:
                # 撤销库读不出来是**基础设施故障**：原样上抛给中间件 -> 503。
                # 绝不能在这里静默退回 IP 桶 —— 那会把"校验没做成"当成
                # "没有身份"，并把故障伪装成正常放行。
                raise
            except Exception:  # noqa: BLE001 - invalid token => fall back to IP
                pass

        # Fall back to IP address (XFF trusted only behind a configured proxy)
        client_host = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        return f"ip:{resolve_client_ip(client_host, forwarded, trusted_proxies)}", None

    def _get_key_id(self, request: Request, trusted_proxies: Iterable[str] = ()) -> str:
        """Extract key identifier from request (API key or IP + path family)."""
        base, _key_obj = self._identity(request, trusted_proxies)
        return f"{base}|{path_family(request.url.path)}"

    def _get_bucket(self, key_id: str, scopes: list) -> TokenBucket:
        """Get or create a token bucket for the given key and scopes."""
        with self._lock:
            self._sweep_locked(time.time())
            # Determine limit based on scopes
            capacity = self._default_capacity
            rate = self._default_rate

            for scope in scopes or []:
                if scope in self.DEFAULT_LIMITS:
                    capacity, rate = self.DEFAULT_LIMITS[scope]
                    break  # Use highest-priority matching scope

            if key_id not in self._buckets:
                self._buckets[key_id] = TokenBucket(capacity, rate)

            return self._buckets[key_id]

    def check_rate_limit(
        self,
        request: Request,
        trusted_proxies: Iterable[str] = (),
        identity: Optional[Tuple[str, Optional[any]]] = None,
    ) -> Tuple[bool, Dict[str, any]]:
        """
        Check rate limit for the request.

        ``identity``：由 :func:`resolve_identity_off_loop` 在**线程池**里预先解析好的
        ``(基底 key, API Key 对象或 None)``。传入它时本函数只做内存里的桶操作，
        不会在事件循环线程上碰撤销库/API Key 表。不传则退回旧行为（同步解析），
        供非 ASGI 调用方使用。

        Returns:
            (allowed, info_dict)
        """
        base, key_obj = identity if identity is not None else self._identity(request, trusted_proxies)
        key_id = f"{base}|{path_family(request.url.path)}"
        bucket = self._get_bucket(key_id, key_obj.scopes if key_obj else [])

        # Try to consume 1 token
        allowed, info = bucket.try_consume(1)

        # Add key identifier to response info
        info["key_id"] = key_id

        return allowed, info

    def add_rate_limit_headers(self, response: Response, allowed: bool, info: Dict[str, any]) -> None:
        """Add rate limit headers to the response."""
        if not allowed:
            reset_time = info.get("reset", 0)
            remaining = info.get("remaining", 0)
            limit = info.get("limit", 100)

            # Set Retry-After header
            if reset_time > time.time():
                response.headers["Retry-After"] = str(max(1, int(reset_time - time.time())))

            # Rate limit exceeded headers
            response.headers["X-Rate-Limit-Limit"] = str(limit)
            response.headers["X-Rate-Limit-Remaining"] = "0"
            response.headers["X-Rate-Limit-Reset"] = str(reset_time)
        else:
            remaining = info.get("remaining", 0)
            limit = info.get("limit", 100)
            reset_time = info.get("reset", 0)

            response.headers["X-Rate-Limit-Limit"] = str(limit)
            response.headers["X-Rate-Limit-Remaining"] = str(max(0, remaining))
            if reset_time > 0:
                response.headers["X-Rate-Limit-Reset"] = str(reset_time)


# Module-level instance
_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the default rate limiter instance."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter


def check_rate_limit(
    request: Request, trusted_proxies: Iterable[str] = ()
) -> Tuple[bool, Dict[str, any]]:
    """Convenience function to check rate limit."""
    return get_rate_limiter().check_rate_limit(request, trusted_proxies)


def add_rate_limit_headers(response: Response, allowed: bool, info: Dict[str, any]) -> None:
    """Convenience function to add rate limit headers."""
    get_rate_limiter().add_rate_limit_headers(response, allowed, info)


def _rate_limit_headers(allowed: bool, info: Dict[str, any]) -> Dict[str, str]:
    headers = {
        "X-Rate-Limit-Limit": str(info.get("limit", 100)),
        "X-Rate-Limit-Remaining": str(max(0, int(info.get("remaining", 0)) if allowed else 0)),
        "X-Rate-Limit-Reset": str(info.get("reset", 0)),
    }
    if not allowed:
        reset = info.get("reset", 0) or 0
        wait = max(1, int(float(reset) - time.time())) if reset else 1
        headers["Retry-After"] = str(wait)
    return headers


logger = logging.getLogger(__name__)


def _identity_lane_worker(lane: _IdentityLane, ready_flag: threading.Event) -> None:
    """lane 的主循环。

    **第一行**打就绪标记：这几条线程不经过 ``Thread.start``，``Thread`` 的
    ``_started`` 永远不会被置位，所以就绪只能由线程自己说。
    """
    lane.thread_ident = threading.get_ident()
    ready_flag.set()
    while True:
        job = lane.inbox.get()
        if job is None:  # pragma: no cover - 进程退出时的收尾
            return
        fn, args, future = job
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 - 原样交给等待方
                try:
                    future.set_exception(exc)
                except Exception:  # pragma: no cover - 等待方已取消
                    pass
        if lane.abandoned.is_set():
            _identity_lane_exit(lane)
            return
        _idle_lanes.put(lane)


def _identity_lane_exit(lane: _IdentityLane) -> None:
    """弃置 lane 自己退出时调用：交回弃置额度，必要时补一条新 lane。"""
    global _abandoned_running
    with _lane_state_lock:
        _abandoned_running = max(0, _abandoned_running - 1)
        slot_taken = lane.slot in _active_lanes
        failed = _thread_start_failed
    if not slot_taken and not failed:
        _start_identity_lane(lane.slot)


def _start_daemon_thread(thread: threading.Thread) -> bool:
    """非阻塞启动一条 daemon 线程（``not_started`` 时不阻塞、不等 ``_started``）。

    ``Thread._bootstrap_inner`` 第一段会 ``del _limbo[self]``；正常 ``start()``
    会先把自己放进 ``_limbo``。我们绕过 ``start()``，所以必须替它登记 ——
    否则线程会在删 ``_limbo`` 时 KeyError 直接死掉（实测 3.12：lane 全灭）。
    """
    limbo = getattr(threading, "_limbo", None)
    limbo_lock = getattr(threading, "_active_limbo_lock", None)
    if limbo is None or limbo_lock is None:  # pragma: no cover - 解释器变更
        return False
    with limbo_lock:
        limbo[thread] = thread
    try:
        _start_new_thread(thread._bootstrap, ())
        return True
    except Exception:  # pragma: no cover - 例如 can't start new thread
        with limbo_lock:
            limbo.pop(thread, None)
        return False


def _start_identity_lane(slot: int) -> Optional[_IdentityLane]:
    """**非阻塞**启动一条 lane；失败置 ``_thread_start_failed``。

    不用 ``threading.Thread.start()``：它末尾的 ``_started.wait()`` 没有超时，
    满载时会把调用方（这里是导入线程或事件循环）无限等住 —— 正是 D-1。
    """
    global _thread_start_failed
    lane = _IdentityLane(slot)
    thread = threading.Thread(
        target=_identity_lane_worker,
        args=(lane, _READY_FLAGS[slot]),
        name=f"liuhao-identity-{slot}",
        daemon=True,
    )
    if not _start_daemon_thread(thread):
        with _lane_state_lock:
            _thread_start_failed = True
        logger.error(
            "evt=identity.lane.start_failed reason=thread_start_failed slot=%d",
            slot,
        )
        return None
    with _lane_state_lock:
        _active_lanes[slot] = lane
    _idle_lanes.put(lane)
    return lane


def _start_identity_lanes() -> None:
    """导入期自预热：**在模块导入时**把 4 条 lane 全部起好。

    为什么不放进 lifespan：``main.py`` 那 8 行已被裁决永久废弃；而且只要任何
    入口 ``import`` 了本模块，lane 就必然存在 —— 接线不会因为漏调某个启动
    函数而静默丢失。
    """
    for slot in range(_IDENTITY_LANE_COUNT):
        if _thread_start_failed:
            break
        _start_identity_lane(slot)


def identity_pool_ready() -> bool:
    """lane 是否已就绪。**只认 Event**（``is_alive()`` 在这里恒为 False）。"""
    if _thread_start_failed:
        return False
    return all(flag.is_set() for flag in _READY_FLAGS)


def _submit_identity_job(fn, *args):
    """把一次身份解析交给一条空闲 lane；没有空闲 lane 就立即失败。"""
    if _thread_start_failed:
        raise IdentityThreadStartFailed("identity lanes could not start on this host")
    if _identity_degraded:
        raise IdentityCheckDegraded("identity check degraded")
    try:
        lane = _idle_lanes.get_nowait()
    except queue.Empty:
        raise IdentityPoolFull("all identity lanes are busy")
    future: Future = Future()
    try:
        lane.inbox.put_nowait((fn, args, future))
    except queue.Full:  # pragma: no cover - 空闲 lane 的收件箱不该满
        _idle_lanes.put(lane)
        raise IdentityPoolFull("identity lane inbox is full")
    return lane, future


def _silence_abandoned_future(future: Future) -> None:
    """超时弃置后取走异常，避免 "exception was never retrieved" 噪声。"""
    try:
        future.exception()
    except Exception:  # pragma: no cover
        pass


def _interrupt_identity_store(thread_ident: Optional[int]) -> bool:
    """从事件循环线程掐断那条 lane 上正在执行的 SQLite 语句。"""
    if thread_ident is None:
        return False
    try:
        from ..security import get_jwt_handler

        store = getattr(get_jwt_handler(), "_revocations", None)
        if store is None:
            return False
        return bool(store.interrupt_and_close(thread_ident))
    except Exception:  # pragma: no cover - 掐断是尽力而为
        return False


def _abandon_identity_lane(lane: _IdentityLane) -> None:
    """超时处置：掐断 + 弃置 + 在额度内补一条替代 lane（总线程封顶 4+4）。"""
    global _abandoned_running
    if lane.abandoned.is_set():
        return
    lane.abandoned.set()
    with _lane_state_lock:
        if _active_lanes.get(lane.slot) is lane:
            del _active_lanes[lane.slot]
        _abandoned_running += 1
        replace = _abandoned_running <= _IDENTITY_MAX_ABANDONED
    # 先 interrupt 再补替代：``connect()`` 阶段挂起时还没有 conn 对象，
    # interrupt 拿不到，只能弃置 —— 线程上限（4 + 4）保证不会无限增长。
    interrupted = _interrupt_identity_store(lane.thread_ident)
    logger.warning(
        "evt=identity.lane.abandoned slot=%d interrupted=%s abandoned_running=%d",
        lane.slot,
        interrupted,
        _abandoned_running,
    )
    if replace:
        _start_identity_lane(lane.slot)


def _record_identity_timeout() -> None:
    """记一次超时；连续达到阈值就降级并启动自愈探针。"""
    global _consecutive_identity_timeouts, _identity_degraded
    global _identity_check_degraded_total
    degraded_now = False
    with _lane_state_lock:
        _consecutive_identity_timeouts += 1
        count = _consecutive_identity_timeouts
        if count >= _IDENTITY_DEGRADE_THRESHOLD and not _identity_degraded:
            _identity_degraded = True
            _identity_check_degraded_total += 1
            degraded_now = True
        active = len(_active_lanes)
        abandoned = _abandoned_running
    if degraded_now:
        logger.error(
            "evt=identity.check.degraded consecutive_timeouts=%d threshold=%d "
            "lanes_active=%d lanes_abandoned=%d",
            count,
            _IDENTITY_DEGRADE_THRESHOLD,
            active,
            abandoned,
        )
        _ensure_identity_probe_thread()


def _record_identity_success() -> None:
    global _consecutive_identity_timeouts
    with _lane_state_lock:
        _consecutive_identity_timeouts = 0


def _probe_identity_store() -> bool:
    """自愈探针：临时连接、1 秒超时、真读一行。"""
    try:
        from ..security import get_jwt_handler

        store = getattr(get_jwt_handler(), "_revocations", None)
        if store is None:
            return False
        return bool(store.probe(_IDENTITY_PROBE_TIMEOUT_SECONDS))
    except Exception:  # pragma: no cover - 探针自身出错按未恢复处理
        return False


def _restore_identity_lanes() -> None:
    """恢复后把缺的 lane 补齐（仍受 4+4 上限约束）。"""
    with _lane_state_lock:
        missing = [
            slot for slot in range(_IDENTITY_LANE_COUNT) if slot not in _active_lanes
        ]
        failed = _thread_start_failed
    for slot in missing:
        with _lane_state_lock:
            if failed or _abandoned_running + len(_active_lanes) >= (
                _IDENTITY_LANE_COUNT + _IDENTITY_MAX_ABANDONED
            ):
                return
        _start_identity_lane(slot)


def _identity_probe_loop() -> None:
    """降级后自愈：**立刻**探第一次，之后 1→2→4→8→10 秒指数退避。

    旧实现在循环顶部先 ``sleep(30)``，等于降级后至少 30 秒整片 503，即使
    撤销库在这 30 秒里一直是好的（冷启动瞬时卡顿就会造成这种"白屏 30 秒"）。
    现在第一次探测不等待，最坏恢复延迟 = 退避间隔上限（10 秒）。
    这只改变"多快发现库已恢复"，不改变放行条件：探针仍必须真读到一行，
    探测失败期间照旧全部 503（fail-closed 不动）。
    """
    global _identity_degraded, _consecutive_identity_timeouts, _probe_thread_started
    global _identity_check_recovered_total
    gap = 0.0
    try:
        while _identity_degraded:
            if gap:
                time.sleep(gap)
                if not _identity_degraded:
                    return
            if _probe_identity_store():
                with _lane_state_lock:
                    _identity_degraded = False
                    _consecutive_identity_timeouts = 0
                    _identity_check_recovered_total += 1
                logger.info(
                    "evt=identity.check.recovered probe=select1 lanes_active=%d",
                    len(_active_lanes),
                )
                _restore_identity_lanes()
                return
            logger.warning("evt=identity.check.probe_failed probe=select1")
            gap = min(
                max(_IDENTITY_PROBE_FIRST_GAP_SECONDS, gap * 2),
                _IDENTITY_PROBE_MAX_GAP_SECONDS,
                _IDENTITY_PROBE_INTERVAL_SECONDS,
            )
    finally:
        with _lane_state_lock:
            _probe_thread_started = False


def _ensure_identity_probe_thread() -> None:
    global _probe_thread_started
    with _lane_state_lock:
        if _probe_thread_started:
            return
        _probe_thread_started = True
    thread = threading.Thread(
        target=_identity_probe_loop,
        name="liuhao-identity-probe",
        daemon=True,
    )
    if not _start_daemon_thread(thread):
        with _lane_state_lock:
            _probe_thread_started = False
        logger.warning("evt=identity.probe.start_failed")


def identity_check_metrics() -> Dict[str, any]:
    """供 ``/v1/metrics`` 与运维用：两个计数 + 当前状态（重启归零）。"""
    with _lane_state_lock:
        return {
            "identity_check_degraded_total": _identity_check_degraded_total,
            "identity_check_recovered_total": _identity_check_recovered_total,
            "identity_check_rejected_total": _identity_check_rejected_total,
            "degraded": _identity_degraded,
            "ready": identity_pool_ready(),
            "consecutive_timeouts": _consecutive_identity_timeouts,
            "lanes_total": _IDENTITY_LANE_COUNT,
            "lanes_active": len(_active_lanes),
            "lanes_abandoned": _abandoned_running,
        }


def identity_unavailable_payload(reason: str = "unavailable") -> Dict[str, any]:
    """503 的**结构化**响应体（四字段 + 可追踪 id）。

    ``reason="busy"``：已短暂排队但所有 lane 仍被占满 —— 这是**并发过高**，
    不是身份库坏了，恢复时间是秒级（``retry_after=2``），所以文案必须和
    "撤销库读不出/降级"（30 秒、探测自愈）区分开，否则用户会被建议白等。
    """
    correlation_id = uuid.uuid4().hex[:16]
    retry_after = 30
    if reason == "thread_start_failed":
        code = "IDENTITY_THREAD_START_FAILED"
        message = "本机无法创建身份校验线程，请重启鎏灏；这次不是你密码的问题"
    elif reason == "busy":
        code = "IDENTITY_UNAVAILABLE"
        message = (
            "本机身份校验并发已满（本次已短暂排队），请 2 秒后重试；"
            "这次不是你密码的问题"
        )
        retry_after = 2
    else:
        code = "IDENTITY_UNAVAILABLE"
        message = (
            "本机身份校验暂时不可用（通常几秒内自动恢复），请稍后重试；"
            "这次不是你密码的问题"
        )
    return {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
        "retry_after": retry_after,
    }


def _count_identity_rejection() -> None:
    global _identity_check_rejected_total
    with _lane_state_lock:
        _identity_check_rejected_total += 1


def identity_unavailable_response(reason: str = "unavailable") -> JSONResponse:
    """中间件直接构造的 503（不走 ``HTTPException(detail=...)``）。"""
    payload = identity_unavailable_payload(reason)
    _count_identity_rejection()
    return JSONResponse(
        status_code=503,
        content=payload,
        headers={
            "Retry-After": str(payload["retry_after"]),
            "X-Correlation-ID": payload["correlation_id"],
        },
    )


def identity_unavailable_exception(reason: str = "unavailable"):
    """依赖函数里唯一能"返回"响应的方式：抛带结构化 detail 的 503。

    ``policy.require_bearer_payload`` 是 FastAPI 依赖，不能直接返回
    ``JSONResponse``；四个字段放在 ``detail`` 里，前端
    ``describeDetail(record.detail)`` 仍原样取到 ``message``。
    """
    from fastapi import HTTPException

    payload = identity_unavailable_payload(reason)
    _count_identity_rejection()
    return HTTPException(
        status_code=503,
        detail=payload,
        headers={
            "Retry-After": str(payload["retry_after"]),
            "X-Correlation-ID": payload["correlation_id"],
        },
    )


async def _acquire_identity_lane(limiter: RateLimiter, request: Request,
                                 trusted_proxies: Iterable[str]):
    """拿一条空闲 lane；满载时做**有界异步等待**，绝不阻塞事件循环。

    D-1 的教训是"不能在事件循环线程上等阻塞 I/O"；这里等的是 lane 释放，
    每次只 ``await asyncio.sleep(4ms)`` 让出控制权，最多等
    ``identity_queue_wait_seconds()``（默认 0.2 秒）。超预算仍没有 lane
    才抛 :class:`IdentityPoolFull` —— 控制台首屏 6~9 个并发请求这类微突发
    不再被误报成"身份服务不可用"，同时保留 fail-fast：不会无限排队。
    """
    budget = identity_queue_wait_seconds()
    deadline = time.monotonic() + budget
    while True:
        try:
            return _submit_identity_job(limiter._identity, request, trusted_proxies)
        except IdentityPoolFull:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(_IDENTITY_QUEUE_POLL_SECONDS, remaining))


async def resolve_identity_off_loop(
    limiter: RateLimiter,
    request: Request,
    trusted_proxies: Iterable[str] = (),
) -> Tuple[str, Optional[any]]:
    """在 lane 上解析请求身份，**绝不在事件循环线程上**读撤销库。

    D-1 现场（2026-11-08 19:44，py-spy）：事件循环停在
    ``RevocationStore._connect`` → ``JWTHandler.validate_token`` → ``_identity``
    → ``RateLimitMiddleware.__call__``，5 秒 CPU 只涨 0.02 秒。

    请求级预算（默认 2 秒）用 ``asyncio.wait_for`` 包住；超时先
    ``interrupt()/close()`` 掐断那条 lane 的 SQLite 语句，再弃置并补替代
    lane —— 槽位不会被永久占死。连续超时达到阈值则降级（新请求直接 503）。
    """
    scope = request.scope
    cached = scope.get(_IDENTITY_SCOPE_KEY)
    if cached is not None:
        return cached
    lane, future = await _acquire_identity_lane(limiter, request, trusted_proxies)
    timeout = identity_timeout_seconds()
    try:
        identity = await asyncio.wait_for(asyncio.wrap_future(future), timeout)
    except asyncio.TimeoutError:
        future.add_done_callback(_silence_abandoned_future)
        _abandon_identity_lane(lane)
        _record_identity_timeout()
        raise IdentityCheckTimeout(f"identity check exceeded {timeout}s")
    _record_identity_success()
    scope[_IDENTITY_SCOPE_KEY] = identity
    return identity


#: 导入期自预热：接线只要 import 本模块就必然生效，不会静默丢失。
_start_identity_lanes()


class RateLimitMiddleware:
    """Pure-ASGI adapter that applies the existing ``RateLimiter``.

    ``RateLimiter`` is a plain helper (not an ASGI callable), which is why
    ``app.add_middleware(RateLimiter)`` never worked and the limiter stayed
    dead code. This adapter is the missing wiring; it does not re-implement
    limits, so there remains exactly one place that decides them.

    Applied to prefixes from ``server.rate_limit.paths`` **merged with**
    ``DEFAULT_PROTECTED_PATHS``: config may add paths but cannot shrink the
    protected set, so an old config file cannot silently unmeter the gateway.

    探针端点（:data:`PROBE_EXEMPT_PATHS`）即使被配置写进 ``paths`` 也一律跳过：
    它们只豁免限流与身份解析，**认证要求不变**（四个端点本来就是公开探针）。
    精确匹配，配置无法借 ``/v1/health/...`` 之类的前缀把别的路径放进来。
    """

    def __init__(
        self,
        app,
        paths: Tuple[str, ...] = DEFAULT_PROTECTED_PATHS,
        trusted_proxies: Iterable[str] = (),
    ):
        self.app = app
        self.paths = tuple(paths or DEFAULT_PROTECTED_PATHS)
        self.trusted_proxies = tuple(trusted_proxies or ())

    def _matches(self, path: str) -> bool:
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.paths)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in PROBE_EXEMPT_PATHS or not self._matches(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        limiter = get_rate_limiter()
        # 身份解析在专职 lane 上做：撤销库/API Key 表都是阻塞 I/O（D-1）。
        # 超时/池满/降级/撤销库读不出 -> 503（不是 401，也不是沉默放行）。
        try:
            identity = await resolve_identity_off_loop(
                limiter, request, self.trusted_proxies
            )
        except IdentityThreadStartFailed:
            await identity_unavailable_response("thread_start_failed")(
                scope, receive, send
            )
            return
        except IdentityPoolFull:
            # 已短暂排队仍拿不到 lane：并发过高，秒级可恢复（Retry-After=2）。
            logger.warning(
                "evt=identity.check.rejected reason=IdentityPoolFull path=%s", path
            )
            await identity_unavailable_response("busy")(scope, receive, send)
            return
        except Exception as exc:  # noqa: BLE001 - 身份基础设施故障统一 503
            logger.warning(
                "evt=identity.check.rejected reason=%s path=%s",
                type(exc).__name__,
                path,
            )
            await identity_unavailable_response("unavailable")(scope, receive, send)
            return
        allowed, info = limiter.check_rate_limit(
            request, self.trusted_proxies, identity=identity
        )
        headers = _rate_limit_headers(allowed, info)

        if not allowed:
            response = JSONResponse(
                {
                    "detail": "请求过于频繁，请稍后再试（rate limit exceeded）",
                    "code": limit_code(info),
                },
                status_code=429,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        from starlette.datastructures import MutableHeaders

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(raw=message.setdefault("headers", []))
                for key, value in headers.items():
                    mutable[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class GlobalRateLimitMiddleware:
    """Per-client budget for **all** API traffic.

    ``server.limits.rate_limit_per_minute`` used to be dead config. This
    adapter implements it as a per-IP (or per-key) budget over every ``/v1``
    route, on top of the per-business limits above.

    探针端点（:data:`PROBE_EXEMPT_PATHS`）**精确匹配**豁免，不按前缀：旧实现用
    ``startswith("/v1/metrics/prometheus")``，``/v1/metrics/prometheus-x`` 这类
    路径也会被顺手放过。这里只跳过限流与身份解析，认证要求不变。
    """

    def __init__(
        self,
        app,
        limit_per_minute: int = 0,
        trusted_proxies: Iterable[str] = (),
        max_buckets: int = 4096,
    ):
        self.app = app
        self.trusted_proxies = tuple(trusted_proxies or ())
        self.limit = max(0, int(limit_per_minute or 0))
        self._limiter = RateLimiter(
            default_capacity=max(1, self.limit),
            default_rate=max(self.limit, 1) / 60.0,
            max_buckets=max_buckets,
            idle_ttl_seconds=300.0,
        )

    def _applies(self, path: str) -> bool:
        if self.limit <= 0 or not path.startswith("/v1"):
            return False
        return path not in PROBE_EXEMPT_PATHS

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self._applies(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        # 与业务限流共用 scope 里的身份缓存：一次请求最多读一遍撤销库。
        # 外部限流先于业务限流执行，这里也必须把身份故障如实回 503。
        try:
            identity = await resolve_identity_off_loop(
                self._limiter, request, self.trusted_proxies
            )
        except IdentityThreadStartFailed:
            await identity_unavailable_response("thread_start_failed")(
                scope, receive, send
            )
            return
        except IdentityPoolFull:
            # 已短暂排队仍拿不到 lane：并发过高，秒级可恢复（Retry-After=2）。
            logger.warning(
                "evt=identity.check.rejected reason=IdentityPoolFull path=%s",
                scope.get("path", ""),
            )
            await identity_unavailable_response("busy")(scope, receive, send)
            return
        except Exception as exc:  # noqa: BLE001 - 身份基础设施故障统一 503
            logger.warning(
                "evt=identity.check.rejected reason=%s path=%s",
                type(exc).__name__,
                scope.get("path", ""),
            )
            await identity_unavailable_response("unavailable")(scope, receive, send)
            return
        allowed, info = self._limiter.check_rate_limit(
            request, self.trusted_proxies, identity=identity
        )
        if not allowed:
            await JSONResponse(
                {
                    "detail": "请求过于频繁，请稍后再试（global rate limit exceeded）",
                    "code": limit_code(info),
                },
                status_code=429,
                headers=_rate_limit_headers(allowed, info),
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)
