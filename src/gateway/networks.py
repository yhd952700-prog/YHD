"""网络内核只读产品面 —— 把总线的真实状态摊开给人看（只读）。

LHX-C-009 网络内核此前长期处于 PRIMITIVE-ONLY：它的总线与适配器只对内核内部 / 测试
可见，从未走上任何真实产品路径（A2A/MCP/gRPC 适配器根本不存在，``src/ai/network_gateway.py``
是个零 importer 的孤儿模块，其适配器不在真实路径上）。本模块给产品 app 补上第一个诚实的、
人类可见的网络出口：

- ``GET /v1/network/messages`` 返回总线**真实**状态：
  - ``stats``：``bus.stats()`` 的真实统计字典；
  - ``history``：``bus.get_message_history()`` 的真实消息历史列表（序列化为
    ``Message.to_dict()``，因为 Message 对象不可直接 JSON 化）；若总线在全新进程里还没
    有任何消息，诚实返回空列表——这正是诚实真相，不是错误。

- 人类主权闸门（``require_human_principal``）：在 ``main.py`` 挂载时声明
  （``dependencies=[Depends(_require_human)]``），未带令牌 → 401；
- 严格只读：本模块没有任何写入 / 发送入口，不会伪造任何总线状态。
  真实外部 send 是安全风险且此刻没有真实路径，故绝不建发送端点。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter

from src.kernels.network import get_network_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/network", tags=["network"])


@router.get("/messages")
def get_network_messages() -> Dict[str, Any]:
    """读取网络总线真实状态（只读）。

    数据全部来自网络内核进程单例，绝不另建一份可能分叉 / 造假的副本。
    """
    bus = get_network_bus()
    stats = bus.stats()
    # get_message_history() 默认返回最近 100 条；这里拉满总线实际容量，避免静默截断。
    history: List[Dict[str, Any]] = [m.to_dict() for m in bus.get_message_history(limit=10_000)]
    return {
        "stats": stats,
        "history": history,
    }
