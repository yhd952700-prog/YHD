"""只读启动状态接口（给前端"正在启动"用的契约）。

为什么单独一个 router
---------------------
前端在网关还没就绪时需要**明确**知道"后端在启动、进度到哪"，而不是白屏或
无限转圈。这个接口因此必须：

* **仅限本机回环访问** —— 前端/启动器都在同一台机器轮询；对外监听时远端
  请求拿不到启动阶段、耗时与降级组件名（避免把内部启动拓扑变成侦察面）；
  可用 ``LIUHAO_STARTUP_STATUS=0`` 显式关闭整条接口。**同机反向代理后面
  所有请求都会被当成回环**，生产必须在代理层屏蔽该路径（见
  ``docs/startup-profile.md``）；
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

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..common.redaction import redact_log_text
from . import startup_profile

router = APIRouter(prefix="/v1", tags=["startup"])

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
#: ASGI 测试客户端的 peer 名不是真实网络地址；真实远端无法伪造这两个值。
_LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1",
                       "testclient", "testserver"}

#: 显式关闭开关：``LIUHAO_STARTUP_STATUS=0`` 时连本机回环也一律 404。
#: 默认不设 = 仅回环可读（桌面启动器 ``scripts/start_liuhao.py`` 依赖它判定
#: ready，所以不默认整条关闭；反代部署建议显式置 0 并在代理层屏蔽，
#: 见 ``docs/startup-profile.md`` 的"安全边界"）。
_STARTUP_STATUS_ENV = "LIUHAO_STARTUP_STATUS"
_DISABLED_VALUES = {"0", "false", "off", "no", "disabled"}


def _endpoint_enabled() -> bool:
    raw = (os.environ.get(_STARTUP_STATUS_ENV) or "").strip().lower()
    return raw not in _DISABLED_VALUES


def _is_local_client(request: Request) -> bool:
    client = request.client
    host = str(getattr(client, "host", "") or "").strip().lower()
    return host in _LOCAL_CLIENT_HOSTS


def _sanitize(value):
    """公开启动页的结构化脱敏：去掉绝对路径/用户名，并遮蔽非回环监听地址。"""
    if isinstance(value, str):
        # 免鉴权接口用更严的日志级脱敏：任意绝对路径都折叠成 <本地路径>/文件名。
        return redact_log_text(value)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        result = {key: _sanitize(item) for key, item in value.items()}
        host = str(result.get("host") or "")
        if host and host not in _LOOPBACK_HOSTS:
            result["host"] = "<本机地址>"
        if result.get("key_fp"):
            # 密钥指纹没有必要出现在免鉴权的启动页上。
            result["key_fp"] = "<已省略>"
        return result
    return value


@router.get("/startup", include_in_schema=False)
def get_startup_status(request: Request) -> JSONResponse:
    """返回真实的启动阶段快照（见模块文档中的契约）。

    仅回环客户端可读：启动阶段/耗时/降级组件名是内部拓扑，不应在对外监听
    时向远端匿名暴露。远端统一按"资源不存在"处理。``LIUHAO_STARTUP_STATUS=0``
    可显式关闭整条接口（连回环也 404）。

    注意：回环判定依赖 ASGI 对端（``request.client``）。若部署在同机反向
    代理之后，代理到网关的对端就是 ``127.0.0.1``，所有外部请求都会被判为
    本机——生产必须在代理层屏蔽 ``/v1/startup``，或把开关置 0。

    ``degraded`` 与 ``startup_check`` 直接取自
    :mod:`src.observability.boot_report`，与启动日志里的 ``evt=startup.degraded``
    / ``evt=startup.check`` 是同一份数据——用户不必在"页面显示正常"和"日志说
    降级"之间猜哪个是真的。
    """
    if not _endpoint_enabled() or not _is_local_client(request):
        raise HTTPException(status_code=404, detail="Not Found")

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
            "background": _sanitize(snapshot["background"]),
            "degraded": _sanitize(degraded),
            "startup_check": _sanitize(boot_report.last_startup_check()),
        },
        headers={"Cache-Control": "no-store"},
    )
