"""
Tests for MCP Adapter
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
import pytest

from src.adapters.mcp.mcp_adapter import MCPAdapter, MCPServerConfig


class TestMCPAdapter:
    """Test MCP Adapter configuration and basic operations."""
    
    def test_adapter_creation(self):
        """Test creating MCP adapter instance."""
        adapter = MCPAdapter()
        assert adapter is not None
        assert adapter.servers == {}
        assert adapter.sessions == {}
    
    def test_add_server(self):
        """Test adding server configuration."""
        adapter = MCPAdapter()
        config = MCPServerConfig(
            name="test-server",
            command="echo",
            args=["hello"],
            env={"TEST": "value"},
            enabled=True
        )
        adapter.add_server(config)
        assert "test-server" in adapter.servers
        assert adapter.servers["test-server"].command == "echo"
    
    def test_remove_server(self):
        """Test removing server configuration."""
        adapter = MCPAdapter()
        config = MCPServerConfig(
            name="test-server",
            command="echo",
            args=["hello"]
        )
        adapter.add_server(config)
        assert adapter.remove_server("test-server") is True
        assert "test-server" not in adapter.servers
        assert adapter.remove_server("nonexistent") is False
    
    def test_save_load_config(self):
        """Test saving and loading configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "servers.json"
            adapter = MCPAdapter(str(config_path))
            
            config = MCPServerConfig(
                name="test-server",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "./data"],
                env={"TEST": "value"},
                enabled=True
            )
            adapter.add_server(config)
            
            # Create new adapter and load
            adapter2 = MCPAdapter(str(config_path))
            adapter2.load_config()
            
            assert "test-server" in adapter2.servers
            assert adapter2.servers["test-server"].command == "npx"
            assert adapter2.servers["test-server"].args == ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
            assert adapter2.servers["test-server"].env == {"TEST": "value"}
            assert adapter2.servers["test-server"].enabled is True
    
    def test_server_config_dataclass(self):
        """Test MCPServerConfig dataclass."""
        config = MCPServerConfig(
            name="test",
            command="cmd",
            args=["arg1", "arg2"],
            env={"KEY": "val"},
            enabled=False
        )
        assert config.name == "test"
        assert config.command == "cmd"
        assert config.args == ["arg1", "arg2"]
        assert config.env == {"KEY": "val"}
        assert config.enabled is False


class TestMCPConfigFile:
    """Test MCP configuration file format."""
    
    def test_default_config_exists(self):
        """Test that default config file exists and is valid JSON."""
        config_path = Path("configs/mcp/servers.json")
        assert config_path.exists(), "Default MCP config file should exist"
        
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert "servers" in data
        assert isinstance(data["servers"], list)
        # Note: servers list may be empty (no MCP servers configured is valid)
        # The adapter handles zero servers gracefully
        
        # Check required fields for each server entry
        for server in data["servers"]:
            assert "name" in server
            assert "command" in server
            assert "args" in server
            assert "enabled" in server


if __name__ == "__main__":
    pytest.main([__file__, "-v"])