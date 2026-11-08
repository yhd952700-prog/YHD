"""Provider 真实 token 用量的进程内计量器。

为什么需要这一层
----------------
账本（``src.gateway.account_usage``）只负责"写一行"，但它此前拿到的是
``len(text) // 4`` 的估算值：请求侧用的是用户这一句话，响应侧用的是最终
回复。两者都忽略了 system prompt、历史、工具轮次，也永远不可能等于
provider 返回的 ``usage``。估算值被标了 ``estimated=1``，但"估算"和
"provider 亲口告诉我们"是两回事。

这一层把 provider 的真实 usage 汇总到**当前记账作用域**：

* 对话：一轮（可能含多次工具调用）= 一个 meter = 账本一行；
* 派活/Goal：一次目标执行 = 一个 meter，按任务 label 再拆分；
* provider 只在**拿到真实 usage** 时调用 :func:`report_usage`；
  拿不到（如 MockProvider）就什么都不报，账本回退到估算并继续标
  ``estimated=1``——绝不把估算伪装成真实。

线程传播
--------
``ContextVar`` 只在同线程/同协程内自动继承。以下三处显式复制上下文：

* ``llm_support.generate_with_timeout``（模型调用超时线程）
* ``ai/tools/executor.py``（工具执行线程）
* ``goal_task_graph.execute_graph``（任务并行线程池）

复制的是同一个 ``UsageMeter`` 实例（对象内部有锁），所以子线程的
``report_usage`` 会累加到父作用域，不会丢段。
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_CURRENT: contextvars.ContextVar[Optional["UsageMeter"]] = contextvars.ContextVar(
    "liuhao_usage_meter", default=None
)
_LABEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "liuhao_usage_label", default=""
)

#: 没有 task label 的模型调用（例如目标拆解、总结）归到这个键。
PLANNING_LABEL = "__planning__"


class UsageMeter:
    """线程安全的一次记账作用域。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens_in = 0
        self._tokens_out = 0
        self._calls = 0
        self._model = ""
        self._by_label: Dict[str, Dict[str, Any]] = {}

    def add(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        label: Optional[str] = None,
    ) -> None:
        tokens_in = max(0, int(tokens_in or 0))
        tokens_out = max(0, int(tokens_out or 0))
        if tokens_in == 0 and tokens_out == 0:
            return
        key = str(label or "").strip() or PLANNING_LABEL
        with self._lock:
            self._tokens_in += tokens_in
            self._tokens_out += tokens_out
            self._calls += 1
            if model:
                self._model = str(model)
            bucket = self._by_label.setdefault(
                key,
                {
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "calls": 0,
                    "model": "",
                },
            )
            bucket["tokens_in"] += tokens_in
            bucket["tokens_out"] += tokens_out
            bucket["calls"] += 1
            if model:
                bucket["model"] = str(model)

    def snapshot(self) -> Dict[str, Any]:
        """返回可直接落账的汇总（``reported`` 表示至少一次真实 usage）。"""
        with self._lock:
            total_tokens = self._tokens_in + self._tokens_out
            return {
                "tokens_in": self._tokens_in,
                "tokens_out": self._tokens_out,
                "total_tokens": total_tokens,
                "calls": self._calls,
                "model": self._model,
                "reported": self._calls > 0,
                "by_label": {
                    key: dict(value) for key, value in self._by_label.items()
                },
            }


def current_meter() -> Optional[UsageMeter]:
    return _CURRENT.get()


@contextmanager
def meter_scope() -> Iterator[UsageMeter]:
    """新开一个记账作用域；退出后 provider 上报自动失效。

    注意：``ContextVar`` 的 token 只能在**同一个 context** 里 reset。
    Starlette 的同步流式生成器会在每次 ``next()`` 时切换 context，所以
    流式路径不要用这个长生命周期作用域，改用 :func:`use_meter` 逐次包住
    ``next()``。
    """
    meter = UsageMeter()
    token = _CURRENT.set(meter)
    try:
        yield meter
    finally:
        _CURRENT.reset(token)


@contextmanager
def use_meter(meter: Optional[UsageMeter]) -> Iterator[None]:
    """把**已有** meter 临时挂到当前 context（进出必须在同一 context）。"""
    if meter is None:
        yield
        return
    token = _CURRENT.set(meter)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def label_scope(label: str) -> Iterator[None]:
    """给范围内的模型调用打任务标签（用于按任务/员工拆分用量）。"""
    token = _LABEL.set(str(label or ""))
    try:
        yield
    finally:
        _LABEL.reset(token)


def report_usage(model: str, tokens_in: int, tokens_out: int) -> None:
    """provider 拿到真实 usage 后调用；无作用域时静默丢弃（不造账）。"""
    meter = _CURRENT.get()
    if meter is None:
        return
    meter.add(model, tokens_in, tokens_out, _LABEL.get())


def snapshot(meter: Optional[UsageMeter] = None) -> Dict[str, Any]:
    """取当前（或指定）作用域的汇总；没有作用域时返回空账。"""
    target = meter or _CURRENT.get()
    if target is None:
        return {
            "tokens_in": 0,
            "tokens_out": 0,
            "total_tokens": 0,
            "calls": 0,
            "model": "",
            "reported": False,
            "by_label": {},
        }
    return target.snapshot()


__all__ = [
    "PLANNING_LABEL",
    "UsageMeter",
    "current_meter",
    "label_scope",
    "meter_scope",
    "report_usage",
    "snapshot",
    "use_meter",
]
