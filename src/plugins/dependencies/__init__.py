"""
Plugin Dependencies Module for LiuHao AI OS

Provides:
- Dependency specification model
- Dependency resolution model
- Dependency conflict model
- Plugin dependency model
- Dependencies store for persistence
- Convenience functions
"""

from typing import Any, Dict, List, Optional

from .models import (
    DependencySpec,
    DependencyResolution,
    DependencyConflict,
    PluginDependency,
)
from .store import PluginDependenciesStore

# Module-level store instance
_default_store = None


def get_dependencies_store() -> PluginDependenciesStore:
    """Get the default dependencies store instance."""
    global _default_store
    if _default_store is None:
        _default_store = PluginDependenciesStore()
    return _default_store


def register_dep(plugin_id: str, dependency: DependencySpec) -> str:
    """Register a dependency using the default store."""
    return get_dependencies_store().register(plugin_id, dependency)


def get_deps(plugin_id: str) -> Optional[PluginDependency]:
    """Get plugin dependencies using the default store."""
    return get_dependencies_store().get(plugin_id)


def list_deps(filters: Optional[Dict[str, Any]] = None) -> List[PluginDependency]:
    """List plugin dependencies using the default store."""
    return get_dependencies_store().list(filters)


def get_conflict(plugin_id: str) -> Optional[DependencyConflict]:
    """Get conflict for a plugin using the default store."""
    return get_dependencies_store().get_conflict(plugin_id)


__all__ = [
    "DependencySpec",
    "DependencyResolution",
    "DependencyConflict",
    "PluginDependency",
    "PluginDependenciesStore",
    "get_dependencies_store",
    "register_dep",
    "get_deps",
    "list_deps",
    "get_conflict",
]
