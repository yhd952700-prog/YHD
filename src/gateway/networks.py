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
- 只读：本模块不伪造任何总线状态，也不建任何"发送消息"端点。
  真实外部 send 是安全风险且此刻没有真实路径。

唯一允许的写入是 ``/v1/network/research`` 的开关：它只把「是否允许联网检索」
这个**本地偏好**落盘（``data/privacy_settings.json``），本身不发起任何网络请求。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.ai import audit as _audit
from src.ai import web_research
from src.kernels.network import get_network_bus
from src.security.egress_registry import grouped_egress
from .policy import require_bearer_payload, require_human_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/network", tags=["network"])


def _message_scope(payload) -> Tuple[Optional[List[str]], str]:
    """消息可读主体集合（自己或子树），与 audit.py 的账号树口径一致。

    非经核验令牌（进程内直接调用）返回不设限；HTTP 上不可达。
    """
    if payload is None or not hasattr(payload, "sub"):
        return None, "all"
    from .audit import _account_scope, _scope_filter

    context, sovereign = _account_scope(payload)
    principals, mode = _scope_filter(context, sovereign, None)
    return principals, mode


@router.get("/messages")
def get_network_messages(
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """读取网络总线真实状态（只读，账号作用域=自己或子树）。

    数据全部来自网络内核进程单例，绝不另建一份可能分叉 / 造假的副本。
    消息按 ``source`` / ``destination`` 与可读主体集合求交：成员看不到他人
    账号的消息，根看到整棵子树的消息。
    """
    principals, mode = _message_scope(payload)
    bus = get_network_bus()
    stats = bus.stats()
    # get_message_history() 默认返回最近 100 条；这里拉满总线实际容量，避免静默截断。
    history: List[Dict[str, Any]] = [m.to_dict() for m in bus.get_message_history(limit=10_000)]
    if principals is not None:
        visible = set(principals)
        history = [
            message
            for message in history
            if str(message.get("source") or "") in visible
            or str(message.get("destination") or "") in visible
        ]
    return {
        "stats": stats,
        "history": history,
        "read_scope": {
            "mode": mode,
            "principals": principals if principals is not None else "all",
            "root_view": bool(mode == "subtree"),
        },
    }


class ResearchToggle(BaseModel):
    """联网检索的开关。默认关闭，只有明确开启才会出网。"""

    enabled: bool


def _research_state() -> Dict[str, Any]:
    providers = web_research.provider_names()
    env_raw = (os.environ.get(web_research.ENABLE_ENV) or "").strip().lower()
    return {
        # 真实生效值（环境变量优先，其次界面选择，默认 False）。
        "enabled": web_research.enabled(),
        # 界面里保存的选择，便于前端显示"开关状态"而不被环境变量误导。
        "stored_enabled": web_research.stored_research_enabled(),
        # 环境变量是否在强制覆盖；是的话界面开关改了也不生效，必须说明。
        "env_override": env_raw in {"0", "1", "true", "false", "yes", "no", "on", "off"},
        "providers": providers,
        "provider_hosts": [
            web_research.PROVIDER_HOSTS.get(name, name) for name in providers
        ],
        "disclosure": web_research.disclosure_text(providers),
        "max_phrase_chars": web_research.MAX_PHRASE_CHARS,
    }


@router.get("/research")
def get_research_setting() -> Dict[str, Any]:
    """联网检索的当前状态 + 开启前的告知文案（不需要任何网络请求）。"""
    return _research_state()


@router.get("/egress")
def get_egress_registry() -> Dict[str, Any]:
    """出站登记表的只读投影：「关于与隐私」页的清单与代码同源。

    每条都来自 ``src/security/egress_registry.py``，前端不写第二份；
    新增主机若忘了归类，也会落在「其他真实出站」组里而不是消失。
    """
    return grouped_egress()


@router.put("/research")
def set_research_setting(
    payload: ResearchToggle,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """保存用户的联网检索选择（本地偏好，不发任何外部请求）。

    开启/关闭都**立即生效**：``enabled()`` 每次工具调用都重新读这份偏好，
    不依赖进程重启。审计留痕是硬要求 —— 开启等于"授权把关键词发出去"，
    必须能回答"谁、什么时候、开了哪些站"。
    """
    previous = web_research.enabled()
    web_research.set_research_enabled(payload.enabled)
    state = _research_state()
    _audit.emit(
        "p9.privacy.web_research_toggle",
        module="src.gateway.networks",
        actor=principal,
        outcome="success",
        requested=bool(payload.enabled),
        enabled=state["enabled"],
        previous=previous,
        changed=state["enabled"] != previous,
        env_override=state["env_override"],
        hosts=list(state["provider_hosts"]),
        disclosure=web_research.disclosure_text(),
    )
    return state
