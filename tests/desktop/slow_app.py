"""仅用于桌面停止协议的集成测试：一个会"干一会儿活"的 ASGI 应用。

真实网关的启动受本机数据/模型初始化影响，不适合作为秒级回归用例；这里用
同一个 ``src.desktop._runner`` 入口 + 真 uvicorn + 真 TCP，验证的是停机协议
本身（在途请求收尾、新连接被拒、进程自行退出）。
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

app = FastAPI()


@app.get("/slow")
async def slow() -> dict[str, bool]:
    await asyncio.sleep(3.0)
    return {"done": True}
