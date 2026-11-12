"""自动化后端注册。本轮只有 fake；真实后端在下一轮接入。"""

from __future__ import annotations

from typing import Optional

from ..contracts import AutomationBackend, AutomationError, BackendKind
from .fake import FakeAutomationBackend


def create_backend(kind: Optional[BackendKind] = None) -> AutomationBackend:
    """按类型创建后端。未实现的后端明确报错，不做静默降级。"""

    resolved = kind or BackendKind.FAKE
    if resolved is BackendKind.FAKE:
        return FakeAutomationBackend()
    raise AutomationError(
        "backend_not_implemented",
        f"后端 {resolved.value} 本轮未实现（仅支持 fake）",
    )


__all__ = ["FakeAutomationBackend", "create_backend"]
