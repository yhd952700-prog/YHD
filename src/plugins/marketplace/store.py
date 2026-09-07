"""
Plugin Marketplace Store for LiuHao AI OS

Provides:
- Plugin metadata persistence
- Version management
- Search and filtering
- Integrity verification
"""

from pathlib import Path
import json
import hashlib
import time
from typing import Dict, List, Optional, Any

from .models import Plugin, PluginMetadata, PluginVersion, PluginStatus, PluginType


class PluginMarketplaceStore:
    """
    Plugin marketplace storage with integrity verification.
    
    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Search and filtering
    - Version management
    """
    
    def __init__(self, storage_path: str = "data/plugins/marketplace/plugins.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._plugins: Dict[str, Plugin] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()
    
    def _load(self) -> None:
        """Load existing plugins from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._plugins = {
                    k: Plugin.from_dict(v) for k, v in data.get("plugins", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                # Rebuild hash chain if missing or inconsistent
                if not self._hash_chain or len(self._hash_chain) != len(self._plugins):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load marketplace: {e}")
                self._plugins = {}
                self._hash_chain = None
        else:
            self._plugins = {}
            self._hash_chain = None
    
    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current plugins."""
        chain: List[str] = []
        sorted_ids = sorted(self._plugins.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            plugin = self._plugins[eid]
            plugin_dict = plugin.to_dict()
            plugin_dict["prev_hash"] = prev_hash
            plugin_data = json.dumps(plugin_dict, sort_keys=True, separators=(",", ":"))
            plugin_hash = hashlib.sha256(plugin_data.encode()).hexdigest()
            chain.append(plugin_hash)
            prev_hash = plugin_hash
        
        self._hash_chain = chain
        self._save()
    
    def _save(self) -> None:
        """Persist plugins to storage with hash chain."""
        if self._hash_chain is None:
            self._build_hash_chain()
        
        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "plugins": {k: v.to_dict() for k, v in self._plugins.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    # ==================== Plugin Operations ====================
    
    def register(self, plugin: Plugin) -> str:
        """
        Register a new plugin.
        
        Args:
            plugin: The plugin to register
            
        Returns:
            The plugin ID
        """
        eid = plugin.plugin_id
        self._plugins[eid] = plugin
        self._save()
        return eid
    
    def update(self, plugin: Plugin) -> str:
        """
        Update an existing plugin.
        
        Args:
            plugin: The plugin to update
            
        Returns:
            The plugin ID
        """
        eid = plugin.plugin_id
        self._plugins[eid] = plugin
        self._save()
        return eid
    
    def get(self, plugin_id: str) -> Optional[Plugin]:
        """Get a plugin by ID."""
        return self._plugins.get(plugin_id)
    
    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[Plugin]:
        """
        List plugins with optional filters.
        
        Supported filter keys:
        - status
        - plugin_type
        - tag (matches any tag in tags list)
        - author
        - name
        """
        plugins = list(self._plugins.values())
        
        if not filters:
            return plugins
        
        result = []
        for plugin in plugins:
            match = True
            for key, value in filters.items():
                if key == "tag":
                    # Match if any filter tag is in plugin tags
                    if not any(v in plugin.tags for v in value if isinstance(value, list)):
                        match = False
                        break
                elif hasattr(plugin, key):
                    plugin_val = getattr(plugin, key, None)
                    # Handle list comparison for some fields
                    if isinstance(plugin_val, list) and isinstance(value, list):
                        if not set(value).issubset(set(plugin_val)):
                            match = False
                            break
                    elif plugin_val != value:
                        match = False
                        break
                else:
                    match = False
                    break
            if match:
                result.append(plugin)
        return result
    
    def filter_by_status(self, status: str) -> List[Plugin]:
        """Filter plugins by status."""
        return [p for p in self._plugins.values() if p.status == status]
    
    def filter_by_type(self, plugin_type: str) -> List[Plugin]:
        """Filter plugins by type."""
        return [p for p in self._plugins.values() if p.metadata.plugin_type == plugin_type]
    
    def filter_by_tag(self, tag: str) -> List[Plugin]:
        """Filter plugins by tag."""
        return [p for p in self._plugins.values() if tag in p.tags]
    
    def filter_by_name(self, name: str) -> List[Plugin]:
        """Filter plugins by name."""
        return [p for p in self._plugins.values() if p.name == name]
    
    # ==================== Version Operations ====================
    
    def add_version(self, plugin_id: str, version: PluginVersion) -> Optional[str]:
        """
        Add a new version to an existing plugin.
        
        Args:
            plugin_id: The plugin ID
            version: The version to add
            
        Returns:
            The plugin ID if successful, None if plugin not found
        """
        plugin = self._plugins.get(plugin_id)
        if plugin:
            plugin.current_version = version
            plugin.updated_at = datetime.now()
            self._save()
            return plugin_id
        return None
    
    # ==================== Integrity ====================
    
    def verify_integrity(self) -> bool:
        """Verify the hash chain integrity."""
        if not self._plugins or not self._hash_chain:
            return True
        
        chain = self._hash_chain
        if len(chain) != len(self._plugins):
            return False
        
        sorted_ids = sorted(self._plugins.keys())
        prev_hash = "genesis"
        for i, eid in enumerate(sorted_ids):
            plugin = self._plugins[eid]
            plugin_dict = plugin.to_dict()
            plugin_dict["prev_hash"] = prev_hash
            plugin_data = json.dumps(plugin_dict, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(plugin_data.encode()).hexdigest()
            
            if expected_hash != chain[i]:
                return False
            
            prev_hash = expected_hash
        
        return True
    
    # ==================== Statistics ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get marketplace statistics."""
        plugins = list(self._plugins.values())
        
        status_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        for plugin in plugins:
            status_counts[plugin.status] = status_counts.get(plugin.status, 0) + 1
            type_counts[plugin.metadata.plugin_type] = type_counts.get(plugin.metadata.plugin_type, 0) + 1
        
        return {
            "total_plugins": len(plugins),
            "by_status": status_counts,
            "by_type": type_counts,
        }


# Module-level convenience functions
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