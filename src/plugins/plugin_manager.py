"""Plugin Manager with gVisor/Kata Container Sandbox Integration"""

from src.plugins.sandbox.models import SandboxBackend, create_backend
from typing import Dict, Any, Optional
import asyncio


class PluginManager:
    """管理插件生命周期，包含沙箱隔离"""
    
    def __init__(self, backend_type: str = "gvisor"):
        self._backend: SandboxBackend = create_backend(backend_type)
        self._plugins: Dict[str, Dict[str, Any]] = {}
        self._container_limits: Dict[str, Dict[str, Any]] = {}
    
    async def start_plugin(self, plugin_id: str, plugin_config: Dict[str, Any]) -> str:
        """启动插件并创建沙箱容器
        
        Args:
            plugin_id: 插件唯一标识
            plugin_config: 插件配置（包含资源限制等）
            
        Returns:
            容器 ID
        """
        # Apply resource limits from config
        limits = plugin_config.get("resource_limits", {
            "cpu_quota": 50000,  # 50% of 1 CPU
            "memory_limit": 256 * 1024 * 1024,  # 256MB
            "pids_limit": 512
        })
        self._container_limits[plugin_id] = limits
        
        # Create sandbox container
        container_id = await self._backend.create_container(plugin_id, limits)
        
        # Track plugin state
        self._plugins[plugin_id] = {
            "container_id": container_id,
            "status": "running",
            "config": plugin_config,
            "created_at": asyncio.get_event_loop().time()
        }
        
        return container_id
    
    async def execute_plugin_action(self, plugin_id: str, action: str, 
                                   params: Dict[str, Any]) -> Dict[str, Any]:
        """在插件沙箱中执行操作
        
        Args:
            plugin_id: 插件 ID
            action: 要执行的操作
            params: 操作参数
            
        Returns:
            执行结果
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin {plugin_id} not found or not running")
        
        container_id = plugin["container_id"]
        
        # Example: execute a command in the sandboxed environment
        # This could be: run a tool, process a file, etc.
        result = await self._backend.execute_command(container_id, [
            action
        ] + list(params.values()) if params else [])
        
        return result
    
    async def stop_plugin(self, plugin_id: str) -> None:
        """停止插件并销毁沙箱容器"""
        plugin = self._plugins.get(plugin_id)
        if plugin:
            container_id = plugin["container_id"]
            await self._backend.destroy_container(container_id)
            del self._plugins[plugin_id]
    
    async def get_plugin_status(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """获取插件运行状态"""
        return self._plugins.get(plugin_id)


# Global instance
plugin_manager = PluginManager()
