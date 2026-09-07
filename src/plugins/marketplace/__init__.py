"""
Plugin Marketplace Module for LiuHao AI OS

Provides:
- Plugin metadata model
- Plugin version model
- Plugin core model
- Marketplace store for persistence
- Convenience functions
"""

from .models import Plugin, PluginMetadata, PluginVersion, PluginStatus, PluginType
from .store import PluginMarketplaceStore, get_marketplace_store, register, get, list

# Module-level store instance
_default_store: Optional[PluginMarketplaceStore] = None


def get_marketplace_store() -> PluginMarketplaceStore:
    """Get the default marketplace store instance."""
    global _default_store
    if _default_store is None:
        _default_store = PluginMarketplaceStore()
    return _default_store


def register(plugin: Plugin) -> str:
    """Register a plugin using the default store."""
    return get_marketplace_store().register(plugin)


def get(plugin_id: str) -> Optional[Plugin]:
    """Get a plugin by ID using the default store."""
    return get_marketplace_store().get(plugin_id)


def list(filters: Optional[Dict[str, Any]] = None) -> List[Plugin]:
    """List plugins using the default store."""
    return get_marketplace_store().list(filters)


__all__ = [
    "Plugin",
    "PluginMetadata",
    "PluginVersion",
    "PluginStatus",
    "PluginType",
    "PluginMarketplaceStore",
    "get_marketplace_store",
    "register",
    "get",
    "list",
]