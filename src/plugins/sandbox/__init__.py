"""
Plugin Sandbox Module for LiuHao AI OS

Provides:
- Sandbox execution context model
- Sandbox result model
- Sandbox status enumeration
- Resource limits model
- Sandbox store for persistence
- Convenience functions
"""

from typing import Optional

from .models import (
    SandboxExecutionContext,
    SandboxResult,
    SandboxStatus,
    ResourceLimits,
)
from .store import PluginSandboxStore, get_sandbox_store, create_context, store_result

# Module-level store instance
_default_store: Optional[PluginSandboxStore] = None


def get_sandbox_store() -> PluginSandboxStore:
    """Get the default sandbox store instance."""
    global _default_store
    if _default_store is None:
        _default_store = PluginSandboxStore()
    return _default_store


def create_context(context: SandboxExecutionContext) -> str:
    """Create a sandbox execution context using the default store."""
    return get_sandbox_store().create_context(context)


def store_result(result: SandboxResult) -> str:
    """Store a sandbox result using the default store."""
    return get_sandbox_store().store_result(result)

__all__ = [
    "SandboxExecutionContext",
    "SandboxResult",
    "SandboxStatus",
    "ResourceLimits",
    "PluginSandboxStore",
    "get_sandbox_store",
    "create_context",
    "store_result",
]