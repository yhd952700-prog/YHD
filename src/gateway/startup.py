"""只读启动状态接口（给前端"正在启动"用的契约）。

为什么单独一个 router
---------------------
前端在网关还没就绪时需要**明确**知道"后端在启动、进度到哪"，而不是白屏或
无限转圈。这个接口因此必须：

* **不需要鉴权** —— 启动期间用户可能还没登录；
* **只读、无副作用** —— 可以随意轮询；
* **诚实** —— 未完成的阶段不会被写成"完成"，后台预热失败会出现在
  ``background.errors`` 里，而不是假装一切正常。

契约（GET /v1/startup，HTTP 200）::

    {
      "state": "starting" | "ready" | "degraded",
      "core_ready": true | false,
      "elapsed_ms": 1234.5,
      "started_at_unix": 1700000000.0,
      "stages": [{"name": "...", "elapsed_ms": 12.3, "delta_ms": 4.5}, ...],
      "background": {
        "status": "pending" | "running" | "done" | "failed",
        "started_at_ms": 1000.0 | null,
        "finished_at_ms": 2000.0 | null,
        "steps": [{"name": "...", "ok": true, "elapsed_ms": 1.2, "error": ""}],
        "errors": [{"step": "...", "error": "..."}]
      }
    }

语义：``core_ready=true`` 时用户已能登录、发消息、配模型（与 ``/v1/health``
返回 200 等价）。``state="degraded"`` 表示核心可用、但有**非关键**后台初始化
失败（例如对话链预热失败），前端应提示"功能可用但部分组件未就绪"，而不是
当成完全就绪。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import startup_profile

router = APIRouter(prefix="/v1", tags=["startup"])


@router.get("/startup", include_in_schema=False)
def get_startup_status() -> JSONResponse:
    """返回真实的启动阶段快照（见模块文档中的契约）。

    ``degraded`` 与 ``startup_check`` 直接取自
    :mod:`src.observability.boot_report`，与启动日志里的 ``evt=startup.degraded``
    / ``evt=startup.check`` 是同一份数据——用户不必在"页面显示正常"和"日志说
    降级"之间猜哪个是真的。
    """
    from ..observability import boot_report

    snapshot = startup_profile.snapshot()
    state = snapshot["state"]
    if state == "ready" and snapshot["background"]["status"] == "failed":
        state = "degraded"
    degraded = boot_report.degraded_components()
    if degraded:
        state = "degraded"
    return JSONResponse(
        content={
            "state": state,
            "core_ready": snapshot["state"] == "ready",
            "elapsed_ms": snapshot["elapsed_ms"],
            "started_at_unix": snapshot["started_at_unix"],
            "stages": snapshot["stages"],
            "background": snapshot["background"],
            "degraded": degraded,
            "startup_check": boot_report.last_startup_check(),
        },
        headers={"Cache-Control": "no-store"},
    )
