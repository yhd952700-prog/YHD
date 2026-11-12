"""
Tests for MCP Adapter
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
import mcp.types as mcp_types
from fastmcp import Client
from fastmcp.server import FastMCP

from src.adapters.mcp import mcp_adapter as mcp_module
from src.adapters.mcp.mcp_adapter import (
    MCP_ADAPTER_ENABLED_ENV,
    MCP_WIRING_STATUS,
    MCPAdapter,
    MCPFailureReason,
    MCPServerConfig,
    build_mcp_server,
)


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


def _start_plain_http(behavior: str) -> tuple[ThreadingHTTPServer, str]:
    """启动一个**非 MCP** 的本地 HTTP 假服务，用于验证失败分类。"""

    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            if behavior == "auth":
                self._send(401, b"nope", {"WWW-Authenticate": 'Bearer realm="x"'})
            elif behavior == "upstream":
                self._send(500, b"boom")
            elif behavior == "html":
                self._send(200, b"<html>not an mcp endpoint</html>",
                           {"Content-Type": "text/html"})
            elif behavior == "hang":
                time.sleep(15)
                self._send(200, b"{}", {"Content-Type": "application/json"})
            else:  # pragma: no cover - 防御
                self._send(404, b"unknown")

        def _send(self, code, body=b"", extra=None):
            self.send_response(code)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 契约
            self._respond()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 契约
            self._respond()

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/mcp"


@pytest.fixture
def fastmcp_http_url():
    """真实起一个 FastMCP streamable HTTP 服务（不是 mock）。"""
    server = FastMCP("fake-remote")

    @server.tool(name="ping")
    def ping(text: str) -> str:
        return f"pong:{text}"

    app = server.http_app(path="/mcp")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uvicorn_server.started:
            break
        time.sleep(0.05)
    port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp"
    uvicorn_server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def stdio_server_script(tmp_path) -> Path:
    """真实的 stdio MCP 子进程入口（FastMCP 官方 stdio 传输）。"""
    script = tmp_path / "fake_stdio_server.py"
    script.write_text(
        textwrap.dedent(
            """
            from fastmcp.server import FastMCP

            mcp = FastMCP("fake-stdio")

            @mcp.tool(name="echo")
            def echo(text: str) -> str:
                return f"echo:{text}"

            if __name__ == "__main__":
                mcp.run(show_banner=False)
            """
        ),
        encoding="utf-8",
    )
    return script


class TestMCPStdioTransport:
    """stdio 路径回归：旧能力（子进程 server）不能退化。"""

    async def test_stdio_connect_list_call_disconnect(self, tmp_path, stdio_server_script):
        adapter = MCPAdapter(str(tmp_path / "servers.json"))
        adapter.servers["local"] = MCPServerConfig(
            name="local",
            command=sys.executable,
            args=[str(stdio_server_script)],
            timeout=30.0,
        )

        assert await adapter.connect_server("local") is True
        assert "local" in adapter.sessions

        tools = await adapter.list_tools("local")
        assert {t.name for t in tools["local"]} == {"echo"}  # noqa: SIM118

        result = await adapter.call_tool("local", "echo", {"text": "hi"})
        assert isinstance(result, mcp_types.CallToolResult)
        assert result.is_error is False
        assert result.content[0].text == "echo:hi"

        await adapter.disconnect_server("local")
        assert adapter.sessions == {}


class TestMCPHttpTransport:
    """远程传输：真实本地 FastMCP server + 失败分类。"""

    async def test_streamable_http_connect_and_call(self, tmp_path, fastmcp_http_url):
        adapter = MCPAdapter(str(tmp_path / "servers.json"))
        adapter.servers["remote"] = MCPServerConfig(
            name="remote", transport="http", url=fastmcp_http_url, timeout=15.0
        )

        assert await adapter.connect_server("remote") is True
        tools = await adapter.list_tools("remote")
        assert {t.name for t in tools["remote"]} == {"ping"}  # noqa: SIM118

        result = await adapter.call_tool("remote", "ping", {"text": "hi"})
        assert result.content[0].text == "pong:hi"

        await adapter.disconnect_server("remote")
        assert adapter.sessions == {}

    async def test_headers_are_passed_to_transport(self, tmp_path):
        adapter = MCPAdapter(str(tmp_path / "servers.json"))
        config = MCPServerConfig(
            name="remote",
            transport="http",
            url="http://127.0.0.1:9/mcp",
            headers={"Authorization": "Bearer test-token"},
            auth="oauth",
        )
        transport = adapter._build_transport(config)
        assert transport.headers == {"Authorization": "Bearer test-token"}
        # fastmcp 会把 auth="oauth" 实例化成 OAuth 对象，这里只断言确实接通了认证路径。
        assert transport.auth is not None

    @pytest.mark.parametrize(
        ("behavior", "expected"),
        [
            ("auth", MCPFailureReason.AUTH),
            ("upstream", MCPFailureReason.UPSTREAM_5XX),
            ("html", MCPFailureReason.PROTOCOL),
            ("hang", MCPFailureReason.TIMEOUT),
        ],
    )
    async def test_failure_reasons_are_distinguished(
        self, tmp_path, behavior, expected
    ):
        server, url = _start_plain_http(behavior)
        try:
            adapter = MCPAdapter(str(tmp_path / "servers.json"))
            adapter.servers["remote"] = MCPServerConfig(
                name="remote", transport="http", url=url, timeout=1.5
            )
            assert await adapter.connect_server("remote") is False
            error = adapter.get_connection_error("remote")
            assert error is not None
            assert error.reason is expected
        finally:
            server.shutdown()

    async def test_disallowed_stdio_command_is_rejected_without_executing(self, tmp_path):
        adapter = MCPAdapter(str(tmp_path / "servers.json"))
        adapter.add_server(
            MCPServerConfig(name="bad", command="curl", args=["http://example.com"])
        )
        assert await adapter.connect_server("bad") is False
        error = adapter.get_connection_error("bad")
        assert error is not None
        assert error.reason is MCPFailureReason.COMMAND_NOT_ALLOWED


class TestSecretRedaction:
    """连接失败日志不得带出凭据（token 可能在 URL query 或 header 里）。"""

    def test_credentials_are_redacted_in_logged_detail(self):
        raw = (
            "ConnectError: http://h/mcp?token=abc123 failed | "
            "Authorization: Bearer deadbeef | api_key=zzz"
        )
        cleaned = mcp_module._redact_text(raw)
        for secret in ("abc123", "deadbeef", "zzz"):
            assert secret not in cleaned
        assert "http://h/mcp?token=[REDACTED]" in cleaned


class TestMCPServerMode:
    """把鎏灏现有工具反向暴露为 MCP Server。"""

    async def test_server_lists_and_calls_liuhao_tools(self):
        server = build_mcp_server("tester", status_fn=lambda: {"provider": "test-provider"})
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert {"memory.search", "system.status"} <= names

        async with Client(server) as client:
            result = await client.call_tool("system.status", {})
            assert result.is_error is False
            assert "test-provider" in result.content[0].text


class TestMCPAdapterSwitch:
    """显式开关：默认关闭，且不静默。"""

    def test_wiring_status_is_adapter_only(self):
        assert MCP_WIRING_STATUS == "adapter-only"

    def test_switch_defaults_to_disabled(self, monkeypatch):
        monkeypatch.delenv(MCP_ADAPTER_ENABLED_ENV, raising=False)
        assert mcp_module.is_adapter_enabled() is False

    async def test_initialize_mcp_does_not_connect_when_disabled(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.delenv(MCP_ADAPTER_ENABLED_ENV, raising=False)
        caplog.set_level(logging.INFO)
        mcp_module._adapter = None
        config = tmp_path / "servers.json"
        config.write_text('{"servers": []}', encoding="utf-8")

        adapter = await mcp_module.initialize_mcp(str(config))
        assert adapter.sessions == {}
        assert any(
            "MCP 适配层未启用" in record.message for record in caplog.records
        )
        mcp_module._adapter = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])