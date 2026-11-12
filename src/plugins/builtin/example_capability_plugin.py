"""示例能力插件（真实可加载）。

这个插件存在的唯一目的：把"插件加载器 + Apps 产品面"从纸面变成真实闭环。
它不发明任何能力，只在 ``execute`` 时返回**真实可检视**的载荷 —— 当前插件
注册表的真实心跳（已注册 / 已激活数量）以及能力注册表里当前已登记的能力清单。
没有数据就如实返回空列表 / 0，绝不编造。

它继承自 ``src.plugins.base.Plugin``（即内核层别名成的 ``PluginInterface``），
因此插件加载器 ``load_plugin`` 能用 ``importlib.import_module`` 找到这个子类、
实例化、并由 ``activate_plugin`` 标记为 ACTIVE —— 真正的加载，而非占位符。
"""

from typing import Any, Dict

from src.plugins.base import Plugin, PluginMetadata


class ExampleCapabilityPlugin(Plugin):
    """返回一个真实、可检视的心跳 + 能力清单。"""

    def __init__(self, metadata: PluginMetadata) -> None:
        super().__init__(metadata)

    async def initialize(self) -> None:
        self.status = "initialized"

    async def start(self) -> None:
        self.status = "started"

    async def stop(self) -> None:
        self.status = "stopped"

    async def reload(self) -> None:
        # 重新读取注册表即可，无需额外动作。
        self.run_count += 1

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """返回真实、可检视的载荷：插件注册表心跳 + 能力清单。"""
        from src.kernels.plugin import get_plugin_registry

        registry = get_plugin_registry()
        registered = list(registry.list_plugins())
        active = [
            p for p in registered
            if getattr(p.status, "value", p.status) == "active"
        ]

        capabilities: list[Dict[str, Any]] = []
        try:
            from src.kernels.capability import CapabilityQuery, get_capability_registry

            cap_registry = get_capability_registry()
            capabilities = [
                {
                    "id": entry.id,
                    "namespace": entry.namespace,
                    "version": entry.version,
                    "scope": getattr(entry.scope, "value", str(entry.scope)),
                    "status": getattr(entry.status, "value", str(entry.status)),
                }
                for entry in cap_registry.query(CapabilityQuery())
            ]
        except Exception:
            # 能力内核未初始化/不可用时绝不编造：如实返回空。
            capabilities = []

        return {
            "plugin_id": self.metadata.name,
            "heartbeat": True,
            "registered_plugins": len(registered),
            "active_plugins": len(active),
            "capabilities_known": len(capabilities),
            "capabilities": capabilities,
        }
