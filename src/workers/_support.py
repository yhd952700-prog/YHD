"""worker / scheduler 共用的小工具：审计强制、信号收敛。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any, Dict, Optional

from src.kernels.audit import AuditEventType, AuditScope, log_event

logger = logging.getLogger("liuhao.worker")

PRINCIPAL = "liuhao-worker"


def require_audit_env() -> None:
    """Fail closed: no audit store, no worker.

    `AuditStore` deliberately refuses to fall back to a default path (that
    would silently pollute the frozen evidence store). We surface that
    requirement at process start instead of at the first task.
    """
    if not os.environ.get("AUDIT_DB_PATH"):
        raise RuntimeError(
            "AUDIT_DB_PATH is not set. Workers refuse to run without an audit "
            "store; point AUDIT_DB_PATH at the AuditStore sqlite file."
        )


def audit(
    action: str,
    outcome: str,
    details: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> None:
    # 审计失败 = 进程级失败：绝不吞掉。
    log_event(
        AuditEventType.STATE_CHANGE,
        PRINCIPAL,
        AuditScope.L1,
        outcome,
        details={"action": action, **details},
        correlation_id=correlation_id,
    )


def install_stop_handlers(loop: asyncio.AbstractEventLoop) -> asyncio.Event:
    """SIGINT/SIGTERM/SIGBREAK → asyncio.Event（Windows 兼容）。

    Windows 的 ProactorEventLoop 不支持 `loop.add_signal_handler`，所以用
    `signal.signal` + `call_soon_threadsafe`。
    """
    stop = asyncio.Event()

    def _handle(signum, _frame) -> None:
        logger.info("signal %s received, stopping", signum)
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):  # 非主线程 / 平台不支持
            logger.debug("cannot install handler for %s", name)
    return stop
