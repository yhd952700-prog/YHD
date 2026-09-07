"""
Sandbox Backends Package for LiuHao AI OS

Exports:
- SandboxBackendBase: Abstract base for all backends
- SandboxBackendType: Enum of supported backends
- SandboxBackendStatus: Health status enum
- ResourceLimits: Resource constraint model
- ExecutionResult: Result of sandboxed execution
- GVisorBackend: gVisor (runsc) implementation
- SubprocessBackend: Fallback subprocess implementation
- SandboxBackendManager: Backend selection and management
"""

from .base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)
from .gvisor import GVisorBackend
from .subprocess_backend import SubprocessBackend
from .manager import SandboxBackendManager, get_sandbox_manager

__all__ = [
    "SandboxBackendBase",
    "SandboxBackendType",
    "SandboxBackendStatus",
    "ResourceLimits",
    "ExecutionResult",
    "GVisorBackend",
    "SubprocessBackend",
    "SandboxBackendManager",
    "get_sandbox_manager",
]
