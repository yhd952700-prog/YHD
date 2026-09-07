"""
Plugin Registry for LiuHao AI OS

Provides:
- Plugin metadata storage and retrieval
- Compatibility checking
- Version management
- Plugin registration API
"""

import json
import os
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

from ..plugins.base import PluginMetadata, PluginError


class PluginRegistry:
    """
    Registry for plugin metadata and compatibility information.
    
    Manages:
    - Plugin metadata persistence
    - Version compatibility checks
    - Plugin discovery and listing
    - Registration API for third-party plugins
    """
    
    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize Plugin Registry.
        
        Args:
            registry_path: Path to registry JSON file
        """
        self.registry_path = Path(registry_path) if registry_path else Path(
            self._get_default_registry_path()
        )
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._load_registry()
    
    def _get_default_registry_path(self) -> str:
        """Get the default registry file path."""
        # Use the D: drive as per user convention
        return "D:\\LiuHao-AI-OS\\data\\plugins\\registry.json"
    
    def _load_registry(self) -> None:
        """Load registry from persistent storage."""
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text())
                self._registry = data.get("plugins", {})
            except Exception as e:
                print(f"Warning: Failed to load plugin registry: {e}")
                self._registry = {}
    
    def _save_registry(self) -> None:
        """Save registry to persistent storage."""
        data = {
            "version": 1,
            "saved_at": __import__('time').time(),
            "plugins": self._registry,
        }
        self.registry_path.write_text(json.dumps(data, indent=2))
    
    # ==================== Plugin Registration ====================
    
    def register_plugin(self, metadata: PluginMetadata, 
                       compatibility: Optional[Dict[str, Any]] = None) -> str:
        """
        Register a plugin in the registry.
        
        Args:
            metadata: PluginMetadata object
            compatibility: Compatibility information
            
        Returns:
            Plugin ID (name)
        """
        plugin_id = metadata.name.lower().replace(" ", "_")
        
        self._registry[plugin_id] = {
            "name": metadata.name,
            "version": metadata.version,
            "description": metadata.description,
            "author": metadata.author,
            "license": metadata.license,
            "scopes": metadata.scopes,
            "minimum_liuhao_version": metadata.minimum_liuhao_version,
            "maximum_liuhao_version": metadata.maximum_liuhao_version,
            "auto_start": metadata.auto_start,
            "requires_internet": metadata.requires_internet,
            "is_singleton": metadata.is_singleton,
            "resource_types": metadata.resource_types,
            "registered_at": __import__('time').time(),
        }
        
        self._save_registry()
        print(f"Plugin registered: {metadata.name} v{metadata.version}")
        return plugin_id
    
    def unregister_plugin(self, plugin_id: str) -> bool:
        """Unregister a plugin from the registry."""
        if plugin_id in self._registry:
            del self._registry[plugin_id]
            self._save_registry()
            print(f"Plugin unregistered: {plugin_id}")
            return True
        return False
    
    # ==================== Compatibility Checking ====================
    
    def check_compatibility(self, plugin_id: str, 
                          liuhao_version: str = "1.0.0") -> Tuple[bool, str]:
        """
        Check if a plugin is compatible with the current LiuHao OS version.
        
        Returns:
            (is_compatible, reason_message)
        """
        if plugin_id not in self._registry:
            return False, f"Plugin {plugin_id} not found in registry"
        
        plugin_info = self._registry[plugin_id]
        
        # Simple version comparison
        # Parse versions as tuples for comparison
        try:
            current_parts = [int(x) for x in liuhao_version.split(".")]
            min_parts = [int(x) for x in plugin_info.get("minimum_liuhao_version", "1.0.0").split(".")]
            max_parts = [int(x) for x in plugin_info.get("maximum_liuhao_version", "99.99.99").split(".")]
            
            # Check minimum version
            min_ok = current_parts >= min_parts
            
            # Check maximum version  
            max_ok = current_parts <= max_parts
            
            if min_ok and max_ok:
                return True, "Compatible"
            elif not min_ok:
                return False, f"Plugin requires LiuHao OS v{plugin_info['minimum_liuhao_version']} or newer, "
            else:
                return False, f"Plugin requires LiuHao OS v{plugin_info['minimum_liuhao_version']} or older, "
                
        except (ValueError, IndexError):
            # If version parsing fails, assume compatible
            return True, "Version check could not be performed, assuming compatible"
    
    def check_all_compatible(self, liuhao_version: str = "1.0.0") -> Dict[str, Tuple[bool, str]]:
        """Check compatibility for all registered plugins."""
        results = {}
        for plugin_id in self._registry:
            results[plugin_id] = self.check_compatibility(plugin_id, liuhao_version)
        return results
    
    # ==================== Plugin Lookup ====================
    
    def get_plugin_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get plugin registration information."""
        return self._registry.get(plugin_id)
    
    def list_plugins(self, include_hidden: bool = False) -> List[Dict[str, Any]]:
        """List all registered plugins."""
        plugins = []
        for plugin_id, info in self._registry.items():
            # Add compatibility info
            compat, reason = self.check_compatibility(plugin_id)
            info["compatible"] = compat
            info["compatibility_reason"] = reason
            plugins.append(info)
        return plugins
    
    def plugin_exists(self, plugin_id: str) -> bool:
        """Check if a plugin is registered."""
        return plugin_id in self._registry
    
    # ==================== Persistence ====================
    
    def reload(self) -> None:
        """Reload registry from disk."""
        self._load_registry()
    
    def export_registry(self, path: Optional[str] = None) -> str:
        """Export registry to a JSON file path."""
        export_path = Path(path) if path else self.registry_path
        export_path.write_text(json.dumps(self._registry, indent=2))
        return str(export_path)
    
    def import_registry(self, path: str) -> int:
        """Import registry from a JSON file. Returns number of plugins imported."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                imported = json.load(f)
            
            count = 0
            for plugin_id, info in imported.get("plugins", {}).items():
                self._registry[plugin_id] = info
                count += 1
            
            self._save_registry()
            return count
        except Exception as e:
            print(f"Error importing registry: {e}")
            return 0


# Module-level convenience
_default_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the default Plugin Registry instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = PluginRegistry()
    return _default_registry


def register_plugin(metadata: PluginMetadata, 
                    compatibility: Optional[Dict[str, Any]] = None) -> str:
    """Convenience function to register a plugin."""
    return get_plugin_registry().register_plugin(metadata, compatibility)


def check_plugin_compatibility(plugin_id: str, 
                               liuhao_version: str = "1.0.0") -> Tuple[bool, str]:
    """Convenience function to check plugin compatibility."""
    return get_plugin_registry().check_compatibility(plugin_id, liuhao_version)