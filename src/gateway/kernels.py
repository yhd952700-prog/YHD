"""内核生命周期可观测 + 主权驱动端点（受人类主权闸门保护）。

- ``GET /v1/kernels``：返回 ``src.kernels._registry.snapshot()`` 的实时快照
  （14 内核 lifecycle，含 context / execution 的「无规范实例」诚实标注）。
- ``POST /v1/kernels/init``：对**已存在**实例调用 ``initialize_all()``。
- ``POST /v1/kernels/shutdown``：对**已存在**实例调用 ``shutdown_all()``。
- ``POST /v1/kernels/{name}/pause``：暂停指定内核（合法前置 READY / UNINITIALIZED）。
- ``POST /v1/kernels/{name}/resume``：恢复指定内核（合法前置 PAUSED）。

全部路由挂在带 ``require_human_principal`` 闸门的 router 上（main.get_app 统一
挂一次；这里每条再显式加一次依赖，确保即使挂载方式变化也不会漏闸门），未持
有效人类令牌的请求会得到 401。

**绝不伪造成功**：factory-only 的 context / execution 没有规范单例，pause/resume
它们会如实返回 409 + 原因；实例不存在的内核也如实返回 409 + 原因，而不是假装
已暂停。非法状态转换（如对 PAUSED 再 pause）由内核抛 ``KernelStateError``，这里
映射成 409 并把内核自己的错误信息原样带出。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from .policy import require_human_principal
from src.kernels._base import KernelStateError
from src.kernels._registry import (
    get_entry,
    initialize_all,
    shutdown_all,
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


@kernels_router.post("/kernels/init")
def post_kernels_init(_: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """主权主体显式驱动「已存在实例」进入 READY。

    只操作已存在的实例（绝不创建）。返回做了什么 / 跳过了什么 / 失败了的什么。
    """
    return initialize_all()


@kernels_router.post("/kernels/shutdown")
def post_kernels_shutdown(_: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """主权主体显式驱动「已存在实例」进入 STOPPED。"""
    return shutdown_all()


@kernels_router.post("/kernels/{name}/pause")
def post_kernels_pause(
    name: str, _: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """暂停指定内核。合法前置：READY / UNINITIALIZED。

    诚实失败语义：
    - 未知内核 -> 404。
    - factory-only（context / execution，无规范单例）-> 409 + 原因。
    - 实例尚不存在（惰性单例未被使用）-> 409 + 原因。
    - 非法状态转换（如已 PAUSED 再 pause）-> 409 + 内核的 KernelStateError 原文。
    """
    entry = get_entry(name)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_kernel", "name": name},
        )
    if entry.reason:
        # factory-only：没有进程级规范单例可暂停；如实说明，不伪造成 PAUSED。
        raise HTTPException(
            status_code=409,
            detail={
                "error": "factory_only_no_canonical_instance",
                "name": name,
                "reason": entry.reason,
            },
        )
    inst = entry.accessor()  # 不创建
    if inst is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_instance_present",
                "name": name,
                "reason": (
                    "内核已登记但当前没有实例（惰性单例尚未被使用）。"
                    "先经其 get_*() 构造实例，或 POST /v1/kernels/init "
                    "后再试。"
                ),
            },
        )
    try:
        inst.pause()
    except KernelStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "illegal_state_transition",
                "name": name,
                "current": getattr(inst, "lifecycle", None),
                "detail": str(exc),
            },
        )
    return {
        "name": name,
        "lifecycle": getattr(inst, "lifecycle", None).value,
        "paused": True,
    }


@kernels_router.post("/kernels/{name}/resume")
def post_kernels_resume(
    name: str, _: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """恢复指定内核。合法前置：PAUSED。

    失败语义与 pause 对称（未知 -> 404；factory-only / 无实例 -> 409；非法转换
    -> 409 + KernelStateError 原文）。
    """
    entry = get_entry(name)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_kernel", "name": name},
        )
    if entry.reason:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "factory_only_no_canonical_instance",
                "name": name,
                "reason": entry.reason,
            },
        )
    inst = entry.accessor()  # 不创建
    if inst is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_instance_present",
                "name": name,
                "reason": (
                    "内核已登记但当前没有实例（惰性单例尚未被使用）。"
                    "无法 resume 一个不存在的实例。"
                ),
            },
        )
    try:
        inst.resume()
    except KernelStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "illegal_state_transition",
                "name": name,
                "current": getattr(inst, "lifecycle", None),
                "detail": str(exc),
            },
        )
    return {
        "name": name,
        "lifecycle": getattr(inst, "lifecycle", None).value,
        "resumed": True,
    }
