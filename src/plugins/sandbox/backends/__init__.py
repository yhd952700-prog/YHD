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
    ExecutionStatus,
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)
from .gvisor import GVisorBackend
from .subprocess_backend import SubprocessBackend
from .monty_backend import MontyBackend, probe_monty
from .restricted_python_backend import RestrictedPythonBackend, probe_restricted_python
from .manager import SandboxBackendManager, get_sandbox_manager

__all__ = [
    "ExecutionStatus",
    "SandboxBackendBase",
    "SandboxBackendType",
    "SandboxBackendStatus",
    "ResourceLimits",
    "ExecutionResult",
    "GVisorBackend",
    "SubprocessBackend",
    "MontyBackend",
    "RestrictedPythonBackend",
    "probe_restricted_python",
    "probe_monty",
    "SandboxBackendManager",
    "get_sandbox_manager",
]
