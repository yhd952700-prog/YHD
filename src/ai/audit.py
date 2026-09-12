"""能力层审计埋点（capability-layer audit instrumentation）。

与 ``src/ai/observability.py``（为能力层补**可观测性**）同构，本模块为能力层补
**审计粒度**。

背景（Round 59 运行时复核，2026-09-11）：
``docs/spec/AI-LAYER-DOD-AUDIT.md`` §3.5.2 用 audit store 事件增量探针测了 21 个
能力层关键操作，**15 个产生的审计事件为 0**。原架构论断「能力层通过调用被
``@kernel_action`` 装饰的内核动作，传递性满足 Audited」只对**真的调用了内核动作**
的操作成立；编排/算法类模块一个内核动作都不调，因此在端到端意义上**完全没有审计**。
§3.4 早已指出正确处置方式：「若生产环境要求能力层粒度的审计，则需补一层
orchestration-level 审计埋点」——本模块即该层。

设计契约（与 ``@kernel_action`` 对齐，NO BREAK）：

* **additive**：写审计**绝不**改变被包装方法的返回值或异常行为；
* **fail-loud-not-fatal**：审计存储不可用时**记 warning（不静默吞）**，但业务操作继续。
  审计失败不应让业务停摆，但更不能无声无息——静默失败比不审计更危险；
* **correlation_id**：优先复用 ``observability.TraceContext`` 的 correlation/trace id，
  使审计轨迹与 trace 日志能对上（``CODEX-CONTRACT`` §5 要求 Audited 维带 correlation_id）。

**范围声明**：本模块**不做策略判决**。Policy Controlled 是独立维度，且
``_crosscutting._adjudicate`` 已知为 record-only（判决恒 deny、从不拦截），
在这里再加一个 record-only 判决只会制造第二条噪音记录，无助于控制。

用法::

    from src.ai.audit import audited

    class Foo:
        @audited("p10.bus.send")
        def send(self, msg): ...
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("liuhao.ai.audit")

_LAYER = "capability"


def _resolve_correlation_id(explicit: Optional[str] = None) -> str:
    """复用调用链上下文里的 correlation/trace id；没有则新生成一个。"""
    if explicit:
        return explicit
    try:
        from src.ai.observability import TraceContext

        ctx = TraceContext.current()
        if ctx.correlation_id:
            return ctx.correlation_id
        if ctx.trace_id:
            return ctx.trace_id
    except Exception:  # pragma: no cover - 观测性模块不可用时降级
        pass
    return uuid.uuid4().hex


def emit(
    action: str,
    *,
    module: str,
    outcome: str = "success",
    reason: str = "",
    scope: Optional[Any] = None,
    correlation_id: Optional[str] = None,
    actor: str = _LAYER,
    **details: Any,
) -> bool:
    """写一条能力层审计事件。

    Args:
        action: 逻辑动作名（如 ``"p10.bus.send"``）。
        module: 产生事件的模块名（用于审计回溯到能力层模块）。
        outcome: ``success`` / ``failure``。
        reason: 人类可读原因（失败时尤其重要）。
        scope: ``AuditScope`` 成员；默认 ``L1``（能力层）。
        correlation_id: 显式调用链 id；缺省从 TraceContext 继承。
        actor: 审计主体标识。
        **details: 附加结构化字段。

    Returns:
        True 表示审计已写入；False 表示写入失败（已记 warning）。
    """
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        payload: Dict[str, Any] = {"action": action, "module": module, "layer": _LAYER}
        if reason:
            payload["reason"] = reason
        payload.update(details)

        log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id=actor,
            scope=scope if scope is not None else AuditScope.L1,
            outcome=outcome,
            details=payload,
            correlation_id=_resolve_correlation_id(correlation_id),
        )
        return True
    except Exception as exc:
        # fail-loud-not-fatal：审计失败必须可见，但不能让业务停摆
        logger.warning("capability audit failed for %r (%s): %s", action, module, exc)
        return False


def audited(
    action: str,
    *,
    module: Optional[str] = None,
    scope: Optional[Any] = None,
) -> Callable:
    """把一次能力层关键操作纳入审计（同步与 async 方法都支持）。

    Args:
        action: 逻辑动作名。
        module: 覆盖模块名；缺省取被包装函数的 ``__module__``。
        scope: ``AuditScope`` 成员；缺省 ``L1``。

    Note:
        与 ``@staticmethod`` 叠加时，``@staticmethod`` 必须在**上**、本装饰器在下
        （顺序反了会把被包装的 descriptor 静态化）。
    """

    def decorator(fn: Callable) -> Callable:
        mod = module or getattr(fn, "__module__", "src.ai.unknown")

        def _record(outcome: str, reason: str, started: float) -> None:
            emit(
                action,
                module=mod,
                outcome=outcome,
                reason=reason,
                scope=scope,
                duration_ms=round((time.time() - started) * 1000.0, 3),
            )

        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.time()
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    _record("failure", f"{type(exc).__name__}: {exc}"[:200], started)
                    raise
                _record("success", "", started)
                return result

            return async_wrapper

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.time()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                _record("failure", f"{type(exc).__name__}: {exc}"[:200], started)
                raise
            _record("success", "", started)
            return result

        return wrapper

    return decorator
