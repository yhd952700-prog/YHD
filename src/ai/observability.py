"""能力层可观测性基础设施（Phase 增强 · 第 12 轮七维审计推荐项）。

解决审计发现的真实缺口：**能力层粒度可观测性缺失**——此前 `src/ai/*`
编排层几乎不打日志，横切关注点全下沉到 kernel action 边界（`@kernel_action`）。
本模块在不改变任何既有行为的前提下，为能力层提供：

- ``get_logger(name)``：返回 ``liuhao.*`` 命名空间下的标准 ``logging.Logger``，
  自动注入 trace / correlation id 到每条日志记录（无需显式传参）。
- ``TraceContext``：基于 ``contextvars`` 的调用链上下文，跨函数/协程透传
  ``trace_id`` 与 ``correlation_id``。
- ``observe(phase)``：函数/方法装饰器，自动记录 ENTER（DEBUG）/ EXIT（INFO，
  含耗时与 outcome）/ ERROR（异常原样抛出，不改控制流）。
- ``recent_traces()``：进程内 capped 环形缓冲，记录最近 N 条能力层调用事件，
  供驾驶舱"最近动态"等消费（可选）。

设计原则：零外部依赖、纯增量、不改既有行为；不对生成器使用 ``@observe``
（生成器语义下 trace 上下文会提前重置），生成器路径改用手动埋点。
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

# ----------------------------------------------------------------------------
# 1. 调用链上下文（跨调用透传 trace_id / correlation_id）
# ----------------------------------------------------------------------------
_trace_id: ContextVar[Optional[str]] = ContextVar("liuhao_trace_id", default=None)
_correlation_id: ContextVar[Optional[str]] = ContextVar("liuhao_correlation_id", default=None)


@dataclass
class TraceContext:
    """可在线程/协程间透传的调用链上下文。"""

    trace_id: Optional[str] = None
    correlation_id: Optional[str] = None

    @classmethod
    def current(cls) -> "TraceContext":
        return cls(trace_id=_trace_id.get(), correlation_id=_correlation_id.get())

    @classmethod
    def set(
        cls,
        trace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        if trace_id is not None:
            _trace_id.set(trace_id)
        if correlation_id is not None:
            _correlation_id.set(correlation_id)

    @classmethod
    def new_trace_id(cls) -> str:
        return uuid.uuid4().hex[:16]

    @classmethod
    def reset(cls) -> None:
        _trace_id.set(None)
        _correlation_id.set(None)


# ----------------------------------------------------------------------------
# 2. 日志器（注入 trace / correlation id）
# ----------------------------------------------------------------------------
_CONFIGURED = False
_ROOT_NAME = "liuhao"


class _TraceFilter(logging.Filter):
    """在每条记录上挂 trace_id / correlation_id，供 Formatter 引用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = TraceContext.current()
        record.trace_id = ctx.trace_id or "-"
        record.correlation_id = ctx.correlation_id or "-"
        return True


def _ensure_configured() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [tid=%(trace_id)s cid=%(correlation_id)s] %(message)s"
        )
    )
    # 关键：把 _TraceFilter 挂到 handler 上（而不是仅 logger）—— 任何 propagate
    # 路径走过的 record 都会被注入 trace_id/correlation_id，避免根 Formatter
    # 在非 liuhao.* logger（如 src.kernels.*）冒泡时抛 Formatting field not found。
    handler.addFilter(_TraceFilter())
    root = logging.getLogger(_ROOT_NAME)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """返回 ``liuhao.<name>`` 下的 logger，已挂 trace 注入 filter。"""
    _ensure_configured()
    logger = logging.getLogger(f"{_ROOT_NAME}.{name}")
    if not any(isinstance(f, _TraceFilter) for f in logger.filters):
        logger.addFilter(_TraceFilter())
    logger.propagate = True
    return logger


# ----------------------------------------------------------------------------
# 3. 调用记录环形缓冲（可选消费：驾驶舱"最近动态"等）
# ----------------------------------------------------------------------------
@dataclass
class TraceRecord:
    ts: float
    phase: str
    outcome: str
    elapsed_ms: float
    trace_id: str


_recent: List[TraceRecord] = []
_MAX_RECENT = 200
_recent_lock = threading.Lock()


def recent_traces(limit: int = 50) -> List[TraceRecord]:
    """返回最近 ``limit`` 条能力层调用记录（按时间升序）。"""
    with _recent_lock:
        return list(_recent[-limit:])


def _record(phase: str, outcome: str, elapsed_ms: float, trace_id: str) -> None:
    with _recent_lock:
        _recent.append(TraceRecord(time.time(), phase, outcome, elapsed_ms, trace_id))
        if len(_recent) > _MAX_RECENT:
            del _recent[: len(_recent) - _MAX_RECENT]


# ----------------------------------------------------------------------------
# 4. observe 装饰器
# ----------------------------------------------------------------------------
def _safe_repr(args: tuple, kwargs: dict, limit: int = 160) -> str:
    try:
        s = f"args={args} kwargs={kwargs}"
    except Exception:
        s = "<unrepr>"
    return s if len(s) <= limit else s[:limit] + "..."


def _outcome_of(result: Any) -> str:
    if isinstance(result, dict):
        st = result.get("status")
        if st is not None:
            return str(st)
    return "ok" if result is not None else "none"


def observe(phase: str, level: int = logging.INFO) -> Callable:
    """装饰函数/方法：记录 ENTER(DEBUG) / EXIT(INFO) / ERROR，并写入环形缓冲。

    不用于生成器函数（生成器语义下 trace 上下文会提前重置）。
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(fn.__module__.split(".")[-1])
            ctx = TraceContext.current()
            trace_id = ctx.trace_id or TraceContext.new_trace_id()
            token = _trace_id.set(trace_id)
            start = time.perf_counter()
            logger.debug("ENTER %s %s", phase, _safe_repr(args, kwargs))
            try:
                result = fn(*args, **kwargs)
                elapsed = (time.perf_counter() - start) * 1000
                outcome = _outcome_of(result)
                logger.log(level, "EXIT  %s ok=%s elapsed_ms=%.1f", phase, outcome, elapsed)
                _record(phase, outcome, elapsed, trace_id)
                return result
            except Exception as exc:  # 原样抛出，仅补一条 ERROR 日志
                elapsed = (time.perf_counter() - start) * 1000
                logger.log(logging.ERROR, "ERROR %s exc=%s elapsed_ms=%.1f",
                           phase, type(exc).__name__, elapsed)
                _record(phase, f"error:{type(exc).__name__}", elapsed, trace_id)
                raise
            finally:
                _trace_id.reset(token)

        return wrapper

    return decorator
