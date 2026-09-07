"""
MCP (Model Context Protocol) Adapter for LiuHao AI OS
Enables integration with external tools and resources via MCP servers.
"""
import asyncio
import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.types import Tool, Resource, Prompt, CallToolResult, ReadResourceResult, GetPromptResult


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str
    args: List[str]
    env: Optional[Dict[str, str]] = None
    enabled: bool = True
    health_check: Optional[Dict[str, Any]] = None


class MCPAdapter:
    """
    MCP Client Adapter - manages connections to multiple MCP servers.
    Provides unified interface for tools, resources, and prompts.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "configs/mcp/servers.json"
        self.servers: Dict[str, MCPServerConfig] = {}
        self.sessions: Dict[str, ClientSession] = {}
        self._server_processes: Dict[str, Any] = {}
        self._tools_cache: Dict[str, List[Tool]] = {}
        self._resources_cache: Dict[str, List[Resource]] = {}
        self._prompts_cache: Dict[str, List[Prompt]] = {}
        
    def load_config(self) -> None:
            """Load MCP server configurations from JSON file."""
            path = Path(self.config_path)
            if not path.exists():
                return

            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for server_data in data.get('servers', []):
                config = MCPServerConfig(
                    name=server_data['name'],
                    command=server_data['command'],
                    args=server_data.get('args', []),
                    env=server_data.get('env'),
                    enabled=server_data.get('enabled', True),
                    health_check=server_data.get('health_check'),
                )
                self.servers[config.name] = config
    
    def save_config(self) -> None:
            """Save current server configurations to JSON file."""
            path = Path(self.config_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                'servers': [
                    {
                        'name': s.name,
                        'command': s.command,
                        'args': s.args,
                        'env': s.env,
                        'enabled': s.enabled,
                        'health_check': s.health_check
                    }
                    for s in self.servers.values()
                ]
            }

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
    
    def add_server(self, config: MCPServerConfig) -> None:
        """Add or update an MCP server configuration."""
        self.servers[config.name] = config
        self.save_config()
    
    def remove_server(self, name: str) -> bool:
        """Remove an MCP server configuration."""
        if name in self.servers:
            del self.servers[name]
            self.save_config()
            return True
        return False
    
    async def connect_server(self, name: str) -> bool:
        """Connect to a specific MCP server."""
        if name not in self.servers:
            raise ValueError(f"Server '{name}' not configured")
        
        if not self.servers[name].enabled:
            raise ValueError(f"Server '{name}' is disabled")
        
        if name in self.sessions:
            return True  # Already connected
        
        config = self.servers[name]
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env
        )
        
        try:
            # Start the server process and create client session
            read_stream, write_stream = await stdio_client(server_params).__aenter__()
            session = ClientSession(read_stream, write_stream)
            await session.__aenter__()
            
            # Initialize the session
            await session.initialize()
            
            self.sessions[name] = session
            self._server_processes[name] = (read_stream, write_stream)
            
            # Cache capabilities
            await self._refresh_cache(name)
            
            return True
        except Exception as e:
            print(f"Failed to connect to MCP server '{name}': {e}")
            return False
    
    async def disconnect_server(self, name: str) -> None:
        """Disconnect from a specific MCP server."""
        if name in self.sessions:
            session = self.sessions[name]
            await session.__aexit__(None, None, None)
            del self.sessions[name]
        
        if name in self._server_processes:
            read_stream, write_stream = self._server_processes[name]
            # The stdio_client context manager handles cleanup
            del self._server_processes[name]
        
        # Clear caches
        self._tools_cache.pop(name, None)
        self._resources_cache.pop(name, None)
        self._prompts_cache.pop(name, None)
    
    async def connect_all(self) -> Dict[str, bool]:
        """Connect to all enabled servers."""
        results = {}
        for name, config in self.servers.items():
            if config.enabled:
                results[name] = await self.connect_server(name)
            else:
                results[name] = False
        return results
    
    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for name in list(self.sessions.keys()):
            await self.disconnect_server(name)
    
    async def _refresh_cache(self, name: str) -> None:
        """Refresh cached capabilities for a server."""
        session = self.sessions.get(name)
        if not session:
            return
        
        try:
            # List tools
            tools_result = await session.list_tools()
            self._tools_cache[name] = tools_result.tools
            
            # List resources
            resources_result = await session.list_resources()
            self._resources_cache[name] = resources_result.resources
            
            # List prompts
            prompts_result = await session.list_prompts()
            self._prompts_cache[name] = prompts_result.prompts
        except Exception as e:
            print(f"Failed to refresh cache for '{name}': {e}")
    
    def get_all_tools(self) -> Dict[str, List[Tool]]:
        """Get all tools from all connected servers."""
        return self._tools_cache.copy()
    
    def get_all_resources(self) -> Dict[str, List[Resource]]:
        """Get all resources from all connected servers."""
        return self._resources_cache.copy()
    
    def get_all_prompts(self) -> Dict[str, List[Prompt]]:
        """Get all prompts from all connected servers."""
        return self._prompts_cache.copy()
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> CallToolResult:
        """Call a tool on a specific server."""
        session = self.sessions.get(server_name)
        if not session:
            raise ValueError(f"Not connected to server '{server_name}'")
        
        return await session.call_tool(tool_name, arguments)
    
    async def read_resource(self, server_name: str, uri: str) -> ReadResourceResult:
        """Read a resource from a specific server."""
        session = self.sessions.get(server_name)
        if not session:
            raise ValueError(f"Not connected to server '{server_name}'")
        
        return await session.read_resource(uri)
    
    async def get_prompt(self, server_name: str, prompt_name: str, arguments: Optional[Dict[str, str]] = None) -> GetPromptResult:
        """Get a prompt from a specific server."""
        session = self.sessions.get(server_name)
        if not session:
            raise ValueError(f"Not connected to server '{name}'")
        
        return await session.get_prompt(prompt_name, arguments or {})
    
    async def list_tools(self, server_name: Optional[str] = None) -> Dict[str, List[Tool]]:
        """List tools from all servers or a specific server."""
        if server_name:
            if server_name not in self._tools_cache:
                await self._refresh_cache(server_name)
            return {server_name: self._tools_cache.get(server_name, [])}
        return self.get_all_tools()
    
    async def list_resources(self, server_name: Optional[str] = None) -> Dict[str, List[Resource]]:
        """List resources from all servers or a specific server."""
        if server_name:
            if server_name not in self._resources_cache:
                await self._refresh_cache(server_name)
            return {server_name: self._resources_cache.get(server_name, [])}
        return self.get_all_resources()
    
    async def list_prompts(self, server_name: Optional[str] = None) -> Dict[str, List[Prompt]]:
        """List prompts from all servers or a specific server."""
        if server_name:
            if server_name not in self._prompts_cache:
                await self._refresh_cache(server_name)
            return {server_name: self._prompts_cache.get(server_name, [])}
        return self.get_all_prompts()


# Global adapter instance
_adapter: Optional[MCPAdapter] = None


def get_mcp_adapter(config_path: Optional[str] = None) -> MCPAdapter:
    """Get the global MCP adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = MCPAdapter(config_path)
        _adapter.load_config()
    return _adapter


async def initialize_mcp(config_path: Optional[str] = None) -> MCPAdapter:
    """Initialize MCP adapter and connect to all servers."""
    adapter = get_mcp_adapter(config_path)
    await adapter.connect_all()
    return adapter


async def shutdown_mcp() -> None:
    """Shutdown MCP adapter and disconnect all servers."""
    global _adapter
    if _adapter:
        await _adapter.disconnect_all()
        _adapter = None


async def run_health_checks(adapter: MCPAdapter, interval: int = 60) -> None:
    """Run periodic health checks on all connected MCP servers.
    
    Args:
        adapter: MCPAdapter instance
        interval: Check interval in seconds
    """
    import asyncio
    import subprocess
    
    while True:
        try:
            for name, config in adapter.servers.items():
                if not config.enabled or not config.health_check:
                    continue
                
                if name not in adapter.sessions:
                    # Server not connected, skip health check
                    continue
                
                hc = config.health_check
                cmd = hc.get('command', [])
                timeout = hc.get('timeout', 10)
                retries = hc.get('retries', 3)
                
                for attempt in range(retries):
                    try:
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            timeout=timeout,
                            text=True
                        )
                        if result.returncode == 0:
                            print(f"✓ Health check passed for MCP server: {name}")
                            break
                        else:
                            print(f"⚠ Health check failed for {name} (attempt {attempt + 1}/{retries}): {result.stderr}")
                    except subprocess.TimeoutExpired:
                        print(f"⚠ Health check timeout for {name} (attempt {attempt + 1}/{retries})")
                    except Exception as e:
                        print(f"⚠ Health check error for {name} (attempt {attempt + 1}/{retries}): {e}")
                    
                    if attempt < retries - 1:
                        await asyncio.sleep(2)
                else:
                    # All retries failed
                    print(f"✗ Health check failed for {name} after {retries} attempts, marking as unhealthy")
                    # Could trigger reconnection logic here
        except Exception as e:
            print(f"Error in health check loop: {e}")
        
        await asyncio.sleep(interval)