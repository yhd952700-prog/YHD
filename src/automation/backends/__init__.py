"""自动化后端注册。0.4.6 起 ``fake`` 与 ``native``（Windows 真后端）可用。

``native`` 在非 Windows 或缺依赖时明确报错，不做静默降级到 fake：
降级会让调用方以为真操作成功了，实际什么都没动。
"""

from __future__ import annotations

from typing import Optional

from ..contracts import AutomationBackend, AutomationError, BackendKind
from .fake import FakeAutomationBackend
from .native_windows import NativeWindowsBackend


def create_backend(kind: Optional[BackendKind] = None) -> AutomationBackend:
    """按类型创建后端。未实现的后端明确报错，不做静默降级。"""

    resolved = kind or BackendKind.FAKE
    if resolved is BackendKind.FAKE:
        return FakeAutomationBackend()
    if resolved is BackendKind.NATIVE:
        return NativeWindowsBackend()
    raise AutomationError(
        "backend_not_implemented",
        f"后端 {resolved.value} 本轮未实现（仅支持 fake / native）",
    )


__all__ = ["FakeAutomationBackend", "NativeWindowsBackend", "create_backend"]
