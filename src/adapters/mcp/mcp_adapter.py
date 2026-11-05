"""
MCP (Model Context Protocol) Adapter for LiuHao AI OS
Enables integration with external tools and resources via MCP servers.

实现已从 `mcp` 原始 SDK 的 `stdio_client` + `ClientSession` 换成 **FastMCP**
（fastmcp-slim 4.0.3，Apache-2.0）。对外方法签名、配置结构（`configs/mcp/servers.json`）
与策略约束保持不变，调用方无需改动。三类能力：

1. ``transport="stdio"``：本地子进程，行为与旧实现一致（命令必须过白名单）；
2. ``transport="http"``：远程 streamable HTTP，可选 Bearer token / OAuth；
3. ``build_mcp_server()``：把鎏灏现有工具反向暴露成 MCP Server（当前暴露
   ``memory.search`` 与 ``system.status`` 两个 capability）。

接线状态（2026-11-05，阶段 B1）：**适配层已修好，但未接入对话主链路**。
主链路接入涉及工具协议从自研 JSON 切到 function calling，风险大，单独立项。
运行时入口 ``initialize_mcp()`` 受 ``LIUHAO_MCP_ADAPTER_ENABLED`` 开关控制，
默认关闭。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.server import FastMCP
from fastmcp.tools import FunctionTool
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    Prompt,
    ReadResourceResult,
    Resource,
    Tool,
)

logger = logging.getLogger(__name__)

# 接线状态：适配层就绪但未接入对话主链路（见模块 docstring）。
MCP_WIRING_STATUS = "adapter-only"

# 显式开关。默认关闭 —— 打开后 initialize_mcp() 才会真正建立连接。
MCP_ADAPTER_ENABLED_ENV = "LIUHAO_MCP_ADAPTER_ENABLED"

# STDIO 传输会直接执行配置里的 command 字符串（即便连接失败报错，底层命令也已执行
# ——OX Security 2026 披露的架构级缺陷，影响 20 万+ 实例；Anthropic 称「行为符合
# 预期」，拒绝修改协议）。因此在配置透传之前加一道命令白名单：只允许已知的解释器
# 与包运行器，禁止任意可执行文件。可用逗号分隔覆盖。
MCP_STDIO_ALLOWLIST_ENV = "LIUHAO_MCP_STDIO_ALLOWED_COMMANDS"
DEFAULT_STDIO_ALLOWED_COMMANDS = (
    "python", "python3", "py", "node", "npx", "uv", "uvx", "docker",
)

# server 模式暴露的 capability 白名单（对应 kernel_tools.make_tools 的 capability）。
# 其余 kernel_tools 工具（审计查询、受限 Python 计算、文件世界接口等）刻意不暴露：
# 它们的执行面需要 Policy 执法链与沙箱前置条件。
MCP_SERVER_EXPOSED_CAPABILITIES = ("memory.search", "system.status")


class MCPFailureReason(StrEnum):
    """连接失败的可区分原因（禁止把不同故障混成一个布尔 False）。"""

    TIMEOUT = "timeout"
    AUTH = "auth"
    PROTOCOL = "protocol_mismatch"
    UPSTREAM_5XX = "upstream_5xx"
    NOT_FOUND = "not_found"
    COMMAND_NOT_ALLOWED = "command_not_allowed"
    CONNECT = "connect_failed"


class MCPConnectionError(Exception):
    """带分类原因的 MCP 连接异常。"""

    def __init__(
        self,
        server: str,
        reason: MCPFailureReason,
        detail: str = "",
    ) -> None:
        self.server = server
        self.reason = reason
        self.detail = detail
        super().__init__(f"MCP server '{server}' 连接失败 [{reason.value}]: {detail}")


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server.

    兼容旧配置：``transport`` 缺省为 stdio，``command``/``args`` 仍是 stdio 路径的
    主字段；http 路径使用 ``url``（可选 ``headers`` 带 Bearer token、``auth="oauth"``）。
    """

    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    enabled: bool = True
    health_check: Optional[Dict[str, Any]] = None
    transport: str = "stdio"
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth: Optional[str] = None
    timeout: float = 30.0


def is_adapter_enabled() -> bool:
    """适配层运行时开关（默认关闭）。"""
    return os.getenv(MCP_ADAPTER_ENABLED_ENV, "").strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _stdio_allowlist() -> set[str]:
    raw = os.getenv(MCP_STDIO_ALLOWLIST_ENV, "")
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return set(names) or set(DEFAULT_STDIO_ALLOWED_COMMANDS)


def _command_basename(command: str) -> str:
    name = Path(command).name.lower()
    return name[:-4] if name.endswith(".exe") else name


class MCPAdapter:
    """
    MCP Client Adapter - manages connections to multiple MCP servers.
    Provides unified interface for tools, resources, and prompts.
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "configs/mcp/servers.json"
        self.servers: Dict[str, MCPServerConfig] = {}
        self.sessions: Dict[str, Client] = {}
        self._tools_cache: Dict[str, List[Tool]] = {}
        self._resources_cache: Dict[str, List[Resource]] = {}
        self._prompts_cache: Dict[str, List[Prompt]] = {}
        self._connection_errors: Dict[str, MCPConnectionError] = {}

    # ------------------------------------------------------------------ config
    def load_config(self) -> None:
        """Load MCP server configurations from JSON file."""
        path = Path(self.config_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for server_data in data.get("servers", []):
            config = MCPServerConfig(
                name=server_data["name"],
                command=server_data.get("command", ""),
                args=server_data.get("args", []),
                env=server_data.get("env"),
                enabled=server_data.get("enabled", True),
                health_check=server_data.get("health_check"),
                transport=server_data.get("transport", "stdio"),
                url=server_data.get("url"),
                headers=server_data.get("headers"),
                auth=server_data.get("auth"),
                timeout=float(server_data.get("timeout", 30.0)),
            )
            self.servers[config.name] = config

    def save_config(self) -> None:
        """Save current server configurations to JSON file."""
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "env": s.env,
                    "enabled": s.enabled,
                    "health_check": s.health_check,
                    "transport": s.transport,
                    "url": s.url,
                    "headers": s.headers,
                    "auth": s.auth,
                    "timeout": s.timeout,
                }
                for s in self.servers.values()
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
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

    def get_connection_error(self, name: str) -> Optional[MCPConnectionError]:
        """返回某 server 最近一次连接失败的分类原因（成功连接后清空）。"""
        return self._connection_errors.get(name)

    # ---------------------------------------------------------- transport 构建
    def _build_transport(self, config: MCPServerConfig):
        transport = (config.transport or "stdio").strip().lower()
        if transport in ("http", "streamable-http", "streamable_http"):
            if not config.url:
                raise MCPConnectionError(
                    config.name,
                    MCPFailureReason.CONNECT,
                    "transport=http 但未配置 url",
                )
            return StreamableHttpTransport(
                url=config.url,
                headers=config.headers,
                auth=config.auth,
            )
        if transport != "stdio":
            raise MCPConnectionError(
                config.name,
                MCPFailureReason.CONNECT,
                f"不支持的 transport: {config.transport}",
            )

        command = config.command or ""
        if not command:
            raise MCPConnectionError(
                config.name, MCPFailureReason.CONNECT, "transport=stdio 但未配置 command"
            )
        if _command_basename(command) not in _stdio_allowlist():
            raise MCPConnectionError(
                config.name,
                MCPFailureReason.COMMAND_NOT_ALLOWED,
                f"command '{_command_basename(command)}' 不在白名单内"
                f"（可用 {MCP_STDIO_ALLOWLIST_ENV} 覆盖）",
            )
        return StdioTransport(command=command, args=list(config.args or []), env=config.env)

    # -------------------------------------------------------------- connection
    async def connect_server(self, name: str) -> bool:
        """Connect to a specific MCP server.

        返回 False 时原因不会丢失：``get_connection_error(name)`` 给出
        TIMEOUT / AUTH / PROTOCOL / UPSTREAM_5XX / NOT_FOUND / COMMAND_NOT_ALLOWED /
        CONNECT 之一，同时以结构化日志告警。
        """
        if name not in self.servers:
            raise ValueError(f"Server '{name}' not configured")

        if not self.servers[name].enabled:
            raise ValueError(f"Server '{name}' is disabled")

        if name in self.sessions:
            return True  # Already connected

        config = self.servers[name]
        client: Optional[Client] = None

        try:
            # 传输构建也在 try 内：命令白名单拒绝属于连接失败的一种，必须走同一个
            # 分类 + 结构化日志出口（且此时还没有任何子进程被拉起）。
            client = Client(
                self._build_transport(config),
                name=config.name,
                timeout=config.timeout,
                init_timeout=config.timeout,
            )
            await client.__aenter__()
        except Exception as exc:  # noqa: BLE001 - 需要按原因分类，不能直接放行
            error = await self._classify_failure(config, exc)
            self._connection_errors[name] = error
            logger.warning(
                "MCP 连接失败",
                extra={
                    "event": "mcp.connect.failed",
                    "mcp_server": name,
                    "transport": config.transport,
                    "reason": error.reason.value,
                    "detail": _redact_text(error.detail),
                },
            )
            if client is not None:
                try:
                    await client.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001 - 清理失败不应掩盖原始原因
                    logger.debug(
                        "MCP 连接清理失败",
                        extra={"event": "mcp.connect.cleanup_failed", "mcp_server": name},
                    )
            return False

        self.sessions[name] = client
        self._connection_errors.pop(name, None)
        await self._refresh_cache(name)
        logger.info(
            "MCP 已连接",
            extra={"event": "mcp.server.connected", "mcp_server": name,
                   "transport": config.transport},
        )
        return True

    async def disconnect_server(self, name: str) -> None:
        """Disconnect from a specific MCP server."""
        if name in self.sessions:
            client = self.sessions.pop(name)
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - 断开失败不应拖垮其余 server
                logger.warning(
                    "MCP 断开失败",
                    extra={"event": "mcp.disconnect.failed", "mcp_server": name,
                           "detail": str(exc)},
                )

        # Clear caches
        self._tools_cache.pop(name, None)
        self._resources_cache.pop(name, None)
        self._prompts_cache.pop(name, None)
        self._connection_errors.pop(name, None)

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
        client = self.sessions.get(name)
        if not client:
            return

        try:
            self._tools_cache[name] = list(await client.list_tools())
            self._resources_cache[name] = list(await client.list_resources())
            self._prompts_cache[name] = list(await client.list_prompts())
        except Exception as exc:  # noqa: BLE001 - 单个能力缺失不应判连接失败
            logger.warning(
                "MCP 能力缓存刷新失败",
                extra={"event": "mcp.cache.refresh_failed", "mcp_server": name,
                       "detail": str(exc)},
            )

    # ----------------------------------------------------------------- queries
    def get_all_tools(self) -> Dict[str, List[Tool]]:
        """Get all tools from all connected servers."""
        return self._tools_cache.copy()

    def get_all_resources(self) -> Dict[str, List[Resource]]:
        """Get all resources from all connected servers."""
        return self._resources_cache.copy()

    def get_all_prompts(self) -> Dict[str, List[Prompt]]:
        """Get all prompts from all connected servers."""
        return self._prompts_cache.copy()

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> CallToolResult:
        """Call a tool on a specific server."""
        client = self.sessions.get(server_name)
        if not client:
            raise ValueError(f"Not connected to server '{server_name}'")

        result = await client.call_tool(tool_name, arguments)
        # FastMCP 客户端返回其自有结果类型；转换回 mcp.types.CallToolResult，
        # 保持本适配器对外契约不变。
        return CallToolResult(
            content=result.content,
            structured_content=result.structured_content,
            is_error=result.is_error,
        )

    async def read_resource(self, server_name: str, uri: str) -> ReadResourceResult:
        """Read a resource from a specific server."""
        client = self.sessions.get(server_name)
        if not client:
            raise ValueError(f"Not connected to server '{server_name}'")

        contents = await client.read_resource(uri)
        return ReadResourceResult(contents=list(contents))

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: Optional[Dict[str, str]] = None,
    ) -> GetPromptResult:
        """Get a prompt from a specific server."""
        client = self.sessions.get(server_name)
        if not client:
            raise ValueError(f"Not connected to server '{server_name}'")

        result = await client.get_prompt(prompt_name, arguments or {})
        return GetPromptResult(
            description=result.description,
            messages=result.messages,
        )

    async def list_tools(self, server_name: Optional[str] = None) -> Dict[str, List[Tool]]:
        """List tools from all servers or a specific server."""
        if server_name:
            if server_name not in self._tools_cache:
                await self._refresh_cache(server_name)
            return {server_name: self._tools_cache.get(server_name, [])}
        return self.get_all_tools()

    async def list_resources(
        self, server_name: Optional[str] = None
    ) -> Dict[str, List[Resource]]:
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

    # -------------------------------------------------------- error classification
    async def _classify_failure(
        self, config: MCPServerConfig, exc: BaseException
    ) -> MCPConnectionError:
        """把底层异常映射为可区分原因；HTTP 路径再用状态码探针细化。"""
        if isinstance(exc, MCPConnectionError):
            return exc

        detail = _exception_text(exc)
        if _looks_like_timeout(exc):
            return MCPConnectionError(config.name, MCPFailureReason.TIMEOUT, detail)

        if (config.transport or "stdio").strip().lower() in (
            "http", "streamable-http", "streamable_http",
        ):
            probe = await asyncio.to_thread(self._probe_http_status, config)
            if probe is not None:
                reason, probe_detail = probe
                return MCPConnectionError(config.name, reason, probe_detail or detail)

        if _looks_like_protocol(exc, detail):
            return MCPConnectionError(config.name, MCPFailureReason.PROTOCOL, detail)
        return MCPConnectionError(config.name, MCPFailureReason.CONNECT, detail)

    def _probe_http_status(
        self, config: MCPServerConfig
    ) -> Optional[tuple[MCPFailureReason, str]]:
        """用一次轻量 HTTP 探针拿到状态码，区分认证失败与上游 5xx。

        FastMCP 客户端把 401 与 500 都折成 ``MCPError(-32603, 'Server returned an
        error response')``，异常里不含状态码，只能靠探针还原。
        探针只读状态码、不携带业务载荷；连接不上或探针超时返回 None，由调用方按
        原始异常归类。
        """
        if not config.url:
            return None
        timeout = min(float(config.timeout or 30.0), 5.0)
        try:
            response = httpx.get(
                config.url,
                headers=config.headers,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            return (MCPFailureReason.TIMEOUT, "连接探针超时")
        except httpx.HTTPError as exc:
            return (MCPFailureReason.CONNECT, f"连接探针失败: {exc}")

        status = response.status_code
        if status in (401, 403):
            return (MCPFailureReason.AUTH, f"HTTP {status}（认证/授权失败）")
        if status == 404:
            return (MCPFailureReason.NOT_FOUND, "HTTP 404（endpoint 不存在）")
        if status >= 500:
            return (MCPFailureReason.UPSTREAM_5XX, f"HTTP {status}（上游错误）")
        return None


#: 日志里可能出现的凭据形态。远程 server 允许把 token 放在 URL query 或 header 中，
#: 连接失败时底层异常文本可能带出完整 URL，因此落日志前统一打码（只写诊断信息）。
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s*)?)[^\s,;'\"]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;'\"]+"),
    re.compile(r"(?i)((?:api[_-]?key|access_token|token|secret|password)\s*=\s*)[^\s&,;'\"]+"),
)


def _redact_text(text: str) -> str:
    """把日志文本里的凭据值替换为 ``[REDACTED]``。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def _exception_leaves(exc: BaseException) -> List[BaseException]:
    """展开 ExceptionGroup，拿到真正的原因。"""
    leaves: List[BaseException] = []
    inner = getattr(exc, "exceptions", None)
    if inner:
        for sub in inner:
            leaves.extend(_exception_leaves(sub))
        return leaves
    leaves.append(exc)
    return leaves


def _exception_text(exc: BaseException) -> str:
    return " | ".join(
        f"{type(leaf).__name__}: {leaf}" for leaf in _exception_leaves(exc)
    )


def _looks_like_timeout(exc: BaseException) -> bool:
    for leaf in _exception_leaves(exc):
        if isinstance(leaf, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
            return True
        text = str(leaf).lower()
        if "timeout" in text or "timed out" in text:
            return True
    return False


def _looks_like_protocol(exc: BaseException, detail: str) -> bool:
    lowered = detail.lower()
    markers = (
        "unexpected content type",
        "unsupported protocol",
        "protocol version",
        "failed to initialize server session",
        "invalid request",
    )
    if any(marker in lowered for marker in markers):
        return True
    return any(getattr(leaf, "code", None) == -32600 for leaf in _exception_leaves(exc))


# ---------------------------------------------------------------- server 模式
def build_mcp_server(
    principal: str,
    status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    *,
    name: str = "liuhao-ai-os",
) -> FastMCP:
    """把鎏灏现有工具反向暴露为 MCP Server。

    当前只暴露 ``MCP_SERVER_EXPOSED_CAPABILITIES``（memory.search / system.status），
    其余 kernel_tools 工具（审计查询、受限 Python 计算、文件世界接口等）**不暴露**，
    因为它们的执行面需要 Policy 执法链与沙箱前置条件（见模块顶部说明）。

    ``status_fn`` 为 None 时 ``system.status`` 仍会暴露，但返回不可用说明而不是伪造状态。
    """
    # 惰性导入：避免适配层被 import 时拉起记忆/审计内核（也为避免潜在循环导入）。
    from ...ai.tools.kernel_tools import make_tools

    tools = {t.capability: t for t in make_tools(principal, status_fn)}
    tools = {k: v for k, v in tools.items() if not k.startswith("automation.")}
    server = FastMCP(name=name, instructions="鎏灏 AI OS 内置工具（只读，风险 LOW）")

    if "memory.search" in tools:
        tool = tools["memory.search"]

        def memory_search(query: str) -> str:
            return tool.fn(query)

        server.add_tool(
            FunctionTool.from_function(
                memory_search,
                name="memory.search",
                description=tool.description,
            )
        )

    if status_fn is not None:
        tool = tools["system.status"]

        def system_status() -> str:
            return tool.fn()

        server.add_tool(
            FunctionTool.from_function(
                system_status,
                name="system.status",
                description=tool.description,
            )
        )

    return server


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
    """Initialize MCP adapter and connect to all servers.

    受 ``LIUHAO_MCP_ADAPTER_ENABLED`` 控制：开关关闭（默认）时只返回适配器实例，
    不建立任何连接，并留一条明确的 info 日志 —— 不做「静默不接线」。
    """
    adapter = get_mcp_adapter(config_path)
    if not is_adapter_enabled():
        logger.info(
            "MCP 适配层未启用（只读配置，不建立连接）",
            extra={"event": "mcp.adapter.disabled", "env": MCP_ADAPTER_ENABLED_ENV},
        )
        return adapter
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
                cmd = hc.get("command", [])
                timeout = hc.get("timeout", 10)
                retries = hc.get("retries", 3)

                for attempt in range(retries):
                    try:
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            timeout=timeout,
                            text=True,
                        )
                        if result.returncode == 0:
                            logger.info(
                                "MCP 健康检查通过",
                                extra={"event": "mcp.health.passed", "mcp_server": name},
                            )
                            break
                        logger.warning(
                            "MCP 健康检查失败",
                            extra={"event": "mcp.health.failed", "mcp_server": name,
                                   "attempt": attempt + 1, "retries": retries,
                                   "detail": (result.stderr or "").strip()},
                        )
                    except subprocess.TimeoutExpired:
                        logger.warning(
                            "MCP 健康检查超时",
                            extra={"event": "mcp.health.timeout", "mcp_server": name,
                                   "attempt": attempt + 1, "retries": retries},
                        )
                    except Exception as exc:  # noqa: BLE001 - 健康检查不允许打断循环
                        logger.warning(
                            "MCP 健康检查异常",
                            extra={"event": "mcp.health.error", "mcp_server": name,
                                   "attempt": attempt + 1, "retries": retries,
                                   "detail": str(exc)},
                        )

                    if attempt < retries - 1:
                        await asyncio.sleep(2)
                else:
                    logger.error(
                        "MCP 健康检查多次失败",
                        extra={"event": "mcp.health.exhausted", "mcp_server": name,
                               "retries": retries},
                    )
        except Exception as exc:  # noqa: BLE001 - 健康检查循环必须存活
            logger.error(
                "MCP 健康检查循环异常",
                extra={"event": "mcp.health.loop_error", "detail": str(exc)},
            )

        await asyncio.sleep(interval)
