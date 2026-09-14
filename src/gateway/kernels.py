"""内核生命周期可观测端点（受人类主权闸门保护）。

只暴露 ``GET /v1/kernels``，返回 ``src.kernels._registry.snapshot()`` 的内容：
14 个内核各自的 ``lifecycle`` 实时状态（含 context / execution 的「无规范实例」
诚实标注）。**不**提供任何写操作，也**不**在被查询时创建内核实例——注册表本身
是只读探测。

路由在 ``src.gateway.main.get_app()`` 里以
``dependencies=[Depends(require_human_principal)]`` 挂载，因此未持有效人类令牌
的请求会得到 401。这是为了满足「人类主权闸门」要求：内核状态属于系统内部事实，
不应向匿名方暴露。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from .policy import require_human_principal
from src.kernels._registry import (
    snapshot,
    uninitialized_but_present,
    KERNEL_ENTRIES,
)

# 与其他受保护业务路由保持一致：prefix="/v1"，由 main.get_app 统一挂闸门。
kernels_router = APIRouter(prefix="/v1", tags=["kernels"])


@kernels_router.get("/kernels")
def get_kernels(_: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """返回 14 个内核的实时生命周期快照（需有效人类令牌）。"""
    return {
        "kernels": snapshot(),
        "uninitialized_but_present": uninitialized_but_present(),
        "total": len(KERNEL_ENTRIES),
    }
