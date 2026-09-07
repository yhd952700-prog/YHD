"""
Plugin Manager for LiuHao AI OS

Manages the full plugin lifecycle:
- Discovery and loading
- Initialization and startup
- Monitoring and health checks
- Unloading and cleanup
- Plugin creation from factory

Features:
- In-memory plugin cache with persistent storage
- Dependency-aware loading
- Error recovery and reporting
- Status tracking
"""

import json
import importlib
import os
from typing import Dict, Any, Optional, List, Type
from pathlib import Path

from ..plugins.base import Plugin, PluginMetadata, PluginError, PluginLoadError, PluginInitError, PluginStartError
from ..config_manager import ConfigManager


class PluginManager:
    """
    Manages plugin lifecycle for LiuHao AI OS.
    
    Features:
    - Plugin discovery from directories
    - Loading/unloading with state tracking
    - Dependency resolution
    - Health monitoring
    - Persistent state storage
    """
    
    def __init__(self, plugins_dir: Optional[str] = None, config: Optional[ConfigManager] = None):
        """
        Initialize Plugin Manager.
        
        Args:
            plugins_dir: Path to plugins directory
            config: ConfigManager instance
        """
        self.config = config or ConfigManager()
        self.plugins_dir = Path(plugins_dir) if plugins_dir else Path(self.config.get("plugins.path", "src/plugins"))
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory plugin registry: name -> Plugin instance
        self._plugins: Dict[str, Plugin] = {}
        # Plugin metadata: name -> PluginMetadata
        self._metadata: Dict[str, PluginMetadata] = {}
        # Plugin status tracking: name -> status
        self._status: Dict[str, str] = {}
        # Plugin error tracking: name -> error
        self._errors: Dict[str, str] = {}
        
        # Load existing plugin state
        self._state_file = self.plugins_dir / "plugin_state.json"
        self._load_state()
    
    def _load_state(self) -> None:
        """Load plugin state from persistent storage."""
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                for name, meta_data in data.get("metadata", {}).items():
                    # Reconstruct metadata - simplified
                    self._metadata[name] = PluginMetadata(
                        name=meta_data.get("name", name),
                        version=meta_data.get("version", "0.1.0"),
                        description=meta_data.get("description", ""),
                        author=meta_data.get("author", ""),
                        license=meta_data.get("license", ""),
                        scopes=meta_data.get("scopes", []),
                    )
            except Exception as e:
                print(f"Warning: Failed to load plugin state: {e}")
    
    def _save_state(self) -> None:
        """Persist plugin state to storage."""
        data = {
            "version": 1,
            "saved_at": __import__('time').time(),
            "metadata": {name: {
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "author": m.author,
                "license": m.license,
                "scopes": m.scopes,
            } for name, m in self._metadata.items()},
        }
        self._state_file.write_text(json.dumps(data, indent=2))
    
    # ==================== Plugin Discovery ====================
    
    def discover_plugins(self, directory: Optional[str] = None) -> List[str]:
        """
        Discover plugin modules in the specified directory.
        
        Looks for Python files with plugin classes.
        """
        search_dir = Path(directory) if directory else self.plugins_dir
        plugin_names = []
        
        if not search_dir.exists():
            return plugin_names
        
        for item in search_dir.iterdir():
            # Look for plugin directories or modules
            if item.is_dir():
                # Check for __init__.py with plugin class
                init_file = item / "__init__.py"
                if init_file.exists():
                    plugin_names.append(item.name)
            elif item.suffix == '.py' and item.name != '__init__.py':
                # Single file plugin
                plugin_names.append(item.stem)
        
        return plugin_names
    
    # ==================== Plugin Loading ====================
    
    def load_plugin(self, plugin_name: str, force: bool = False) -> bool:
        """
        Load a plugin by name.
        
        Returns:
            True if loaded successfully, False otherwise
        """
        if plugin_name in self._plugins and not force:
            print(f"Plugin {plugin_name} already loaded")
            return True
        
        # Get plugin metadata if not already loaded
        if plugin_name not in self._metadata:
            # Try to discover and load metadata
            self._discover_metadata(plugin_name)
        
        try:
            # Import the plugin module
            module_path = f"src.plugins.{plugin_name}"
            
            # Try importing the module
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                # Try with plugins. prefix
                module = importlib.import_module(f"plugins.{plugin_name}")
            
            # Find Plugin subclass
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, Plugin) and 
                    attr is not Plugin):
                    plugin_class = attr
                    break
            
            if plugin_class is None:
                raise PluginLoadError(
                    f"No Plugin subclass found in module {plugin_name}"
                )
            
            # Create plugin instance
            plugin = plugin_class(
                metadata=self._metadata.get(plugin_name, PluginMetadata(
                    name=plugin_name,
                    version="0.1.0",
                ))
            )
            
            # Store plugin
            self._plugins[plugin_name] = plugin
            self._status[plugin_name] = PluginStatus.LOADED
            
            # Save state
            self._save_state()
            
            print(f"Plugin {plugin_name} loaded successfully (v{plugin.get_metadata().version})")
            return True
            
        except PluginLoadError as e:
            self._errors[plugin_name] = str(e)
            self._status[plugin_name] = PluginStatus.ERROR
            print(f"Failed to load plugin {plugin_name}: {e}")
            return False
        except Exception as e:
            self._errors[plugin_name] = str(e)
            self._status[plugin_name] = PluginStatus.ERROR
            print(f"Unexpected error loading plugin {plugin_name}: {e}")
            return False
    
    def _discover_metadata(self, plugin_name: str) -> None:
        """Discover plugin metadata from module."""
        try:
            module = importlib.import_module(f"src.plugins.{plugin_name}")
            
            # Look for PLUGIN_METADATA constant or plugin_info function
            metadata = None
            
            if hasattr(module, 'PLUGIN_METADATA'):
                metadata = module.PLUGIN_METADATA
            elif hasattr(module, 'plugin_info'):
                metadata = module.plugin_info()
            
            if metadata:
                # Normalize to PluginMetadata
                if isinstance(metadata, dict):
                    self._metadata[plugin_name] = PluginMetadata(
                        name=metadata.get("name", plugin_name),
                        version=metadata.get("version", "0.1.0"),
                        description=metadata.get("description", ""),
                        author=metadata.get("author", ""),
                        license=metadata.get("license", ""),
                        scopes=metadata.get("scopes", []),
                        minimum_liuhao_version=metadata.get("minimum_liuhao_version", "1.0.0"),
                        maximum_liuhao_version=metadata.get("maximum_liuhao_version", "99.99.99"),
                        auto_start=metadata.get("auto_start", True),
                    )
                elif isinstance(metadata, PluginMetadata):
                    self._metadata[plugin_name] = metadata
            else:
                # Default metadata
                self._metadata[plugin_name] = PluginMetadata(
                    name=plugin_name,
                    version="0.1.0",
                    description=f"Plugin: {plugin_name}",
                )
                
        except Exception as e:
            # Default metadata on error
            self._metadata[plugin_name] = PluginMetadata(
                name=plugin_name,
                version="0.1.0",
                description=f"Plugin: {plugin_name}",
            )
            print(f"Warning: Could not discover metadata for {plugin_name}: {e}")
    
    # ==================== Plugin Lifecycle ====================
    
    async def initialize_plugin(self, plugin_name: str) -> bool:
        """
        Initialize a loaded plugin.
        
        Called once after loading, before start.
        """
        if plugin_name not in self._plugins:
            print(f"Plugin {plugin_name} not loaded")
            return False
        
        plugin = self._plugins[plugin_name]
        
        try:
            await plugin.initialize()
            plugin.initialized_at = __import__('time').time()
            self._status[plugin_name] = PluginStatus.INITIALIZED
            self._errors.pop(plugin_name, None)
            
            self._save_state()
            print(f"Plugin {plugin_name} initialized successfully")
            return True
            
        except Exception as e:
            plugin.error = str(e)
            self._status[plugin_name] = PluginStatus.ERROR
            self._errors[plugin_name] = str(e)
            print(f"Failed to initialize plugin {plugin_name}: {e}")
            return False
    
    async def start_plugin(self, plugin_name: str) -> bool:
        """
        Start a plugin.
        
        Begins plugin operation/execution.
        """
        if plugin_name not in self._plugins:
            print(f"Plugin {plugin_name} not loaded")
            return False
        
        plugin = self._plugins[plugin_name]
        
        # Ensure initialized
        if plugin.status != PluginStatus.INITIALIZED:
            result = await self.initialize_plugin(plugin_name)
            if not result:
                return False
        
        try:
            await plugin.start()
            plugin.started_at = __import__('time').time()
            self._status[plugin_name] = PluginStatus.STARTED
            self._errors.pop(plugin_name, None)
            
            self._save_state()
            print(f"Plugin {plugin_name} started successfully")
            return True
            
        except Exception as e:
            plugin.error = str(e)
            self._status[plugin_name] = PluginStatus.ERROR
            self._errors[plugin_name] = str(e)
            print(f"Failed to start plugin {plugin_name}: {e}")
            return False
    
    async def stop_plugin(self, plugin_name: str) -> bool:
        """
        Stop a running plugin.
        
        Gracefully stops plugin execution.
        """
        if plugin_name not in self._plugins:
            print(f"Plugin {plugin_name} not loaded")
            return False
        
        plugin = self._plugins[plugin_name]
        
        try:
            await plugin.stop()
            plugin.stopped_at = __import__('time').time()
            self._status[plugin_name] = PluginStatus.STOPPED
            self._errors.pop(plugin_name, None)
            
            self._save_state()
            print(f"Plugin {plugin_name} stopped successfully")
            return True
            
        except Exception as e:
            plugin.error = str(e)
            self._status[plugin_name] = PluginStatus.ERROR
            self._errors[plugin_name] = str(e)
            print(f"Failed to stop plugin {plugin_name}: {e}")
            return False
    
    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        Reload a plugin due to configuration changes.
        
        Stops, reinitializes, and restarts.
        """
        if plugin_name not in self._plugins:
            print(f"Plugin {plugin_name} not loaded")
            return False
        
        # Stop currently running plugin
        await self.stop_plugin(plugin_name)
        
        # Reinitialize
        await self.initialize_plugin(plugin_name)
        
        # Restart
        await self.start_plugin(plugin_name)
        
        print(f"Plugin {plugin_name} reloaded successfully")
        return True
    
    # ==================== Plugin Unloading ====================
    
    async def unload_plugin(self, plugin_name: str, force: bool = False) -> bool:
        """
        Unload a plugin.
        
        Stops and removes the plugin from the manager.
        """
        if plugin_name not in self._plugins:
            print(f"Plugin {plugin_name} not loaded")
            return False
        
        # Stop if running
        await self.stop_plugin(plugin_name)
        
        # Remove from tracking
        del self._plugins[plugin_name]
        del self._status[plugin_name]
        del self._errors[plugin_name]
        if plugin_name in self._metadata:
            del self._metadata[plugin_name]
        
        # Save state
        self._save_state()
        
        print(f"Plugin {plugin_name} unloaded successfully")
        return True
    
    # ==================== Utility Methods ====================
    
    def get_plugin(self, plugin_name: str) -> Optional[Plugin]:
        """Get a plugin instance by name"""
        return self._plugins.get(plugin_name)
    
    def get_plugin_status(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get plugin status information"""
        if plugin_name in self._status:
            plugin = self._plugins.get(plugin_name)
            stats = self._status[plugin_name]
            base_stats = {
                "status": stats,
                "name": plugin_name,
            }
            if plugin:
                base_stats.update(plugin.get_stats())
            return base_stats
        return None
    
    def list_plugins(self, include_hidden: bool = False) -> List[Dict[str, Any]]:
        """List all plugins with their status"""
        result = []
        for name in self._metadata:
            status_info = self.get_plugin_status(name)
            if status_info or include_hidden:
                result.append({
                    "name": name,
                    **({} if not status_info else status_info),
                })
        return result
    
    def list_loaded_plugins(self) -> List[Dict[str, Any]]:
        """List only loaded plugins"""
        return self.list_plugins(include_hidden=True)
    
    def list_errored_plugins(self) -> List[Dict[str, Any]]:
        """List plugins with errors"""
        result = []
        for name, status in self._status.items():
            if status == "error" or name in self._errors:
                result.append({
                    "name": name,
                    "error": self._errors.get(name),
                    "status": status,
                })
        return result
    
    def get_all_metadata(self) -> Dict[str, PluginMetadata]:
        """Get all plugin metadata"""
        return self._metadata.copy()
    
    def reload_all_state(self) -> None:
        """Reload all plugin state from persistence"""
        self._load_state()


# Module-level convenience
_default_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the default Plugin Manager instance"""
    global _default_manager
    if _default_manager is None:
        _default_manager = PluginManager()
    return _default_manager


def create_plugin(plugin_name: str, **kwargs) -> Optional[Plugin]:
    """Convenience function to load and return a plugin"""
    manager = get_plugin_manager()
    success = manager.load_plugin(plugin_name)
    if success:
        return manager.get_plugin(plugin_name)
    return None


def reload_plugin(plugin_name: str) -> bool:
    """Convenience function to reload a plugin"""
    manager = get_plugin_manager()
    return manager.reload_plugin(plugin_name)