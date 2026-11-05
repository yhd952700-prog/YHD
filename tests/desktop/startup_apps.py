"""启动门的测试用 ASGI 应用：模拟 慢启动 / 降级 / 永远起不来 三种真实场景。

慢启动：lifespan 先睡 ``DELAY_SECONDS`` 秒，uvicorn 此时**还没绑定端口** ——
对桌面壳来说就是"连接失败"，与线上冷启动同一现象，可用来验证
"连接失败按 starting 处理"以及真实耗时。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from fastapi import FastAPI

DELAY_SECONDS = 2.0

_STARTED = time.monotonic()


def _payload(state: str, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "elapsed_ms": int((time.monotonic() - _STARTED) * 1000),
        "stages": [{"name": "路由", "ms": 250}, {"name": "核心", "ms": 80}],
        "background": {
            "status": "failed" if errors else "ok",
            "steps": ["情报", "自动化"],
            "errors": errors or [],
        },
    }


@contextlib.asynccontextmanager
async def _slow_lifespan(_app: FastAPI):
    await asyncio.sleep(DELAY_SECONDS)
    yield


slow_ready_app = FastAPI(lifespan=_slow_lifespan)


@slow_ready_app.get("/v1/startup")
def _slow_status() -> dict[str, Any]:
    return _payload("ready")


degraded_app = FastAPI()


@degraded_app.get("/v1/startup")
def _degraded_status() -> dict[str, Any]:
    return _payload("degraded", errors=[{"step": "情报", "error": "SMTP 未配置：缺少 smtp_host"}])


stuck_app = FastAPI()


@stuck_app.get("/v1/startup")
def _stuck_status() -> dict[str, Any]:
    return _payload("starting")
