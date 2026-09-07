"""network — 十源 packages 层 facade.

协议适配 + 通信总线 + 外部 agent 网关（十源 EDITH）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.kernels.network import (
    NetworkBus,
    Message,
    Route,
    ProtocolAdapter,
    get_network_bus,
)

from src.ai.network_gateway import (
    AgentNetworkGateway,
    ExternalAgent,
    get_agent_network_gateway,
)

__all__ = [
    "NetworkBus",
    "Message",
    "Route",
    "ProtocolAdapter",
    "get_network_bus",
    "AgentNetworkGateway",
    "ExternalAgent",
    "get_agent_network_gateway",
]
