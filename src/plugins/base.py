"""
Plugin Base for LiuHao AI OS

Defines the standard interface that all plugins must implement.
Provides:
- Plugin lifecycle methods (initialize, start, stop, reload)
- Plugin metadata schema
- Plugin versioning
- Error handling contracts
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import time


@dataclass
class PluginMetadata:
    """Metadata describing a plugin."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    url: str = ""
    
    # Capabilities
    scopes: List[str] = field(default_factory=list)
    resource_types: List[str] = field(default_factory=list)
    
    # Compatibility
    minimum_liuhao_version: str = "1.0.0"
    maximum_liuhao_version: str = "99.99.99"
    
    # Lifecycle
    auto_start: bool = True
    requires_internet: bool = False
    is_singleton: bool = False


class PluginStatus:
    """Plugin execution status."""
    LOADED = "loaded"
    INITIALIZED = "initialized"
    STARTED = "started"
    STOPPED = "stopped"
    ERROR = "error"
    UNLOADED = "unloaded"


class PluginError(Exception):
    """Base exception for plugin-related errors."""
    pass


class PluginLoadError(PluginError):
    """Raised when a plugin fails to load."""
    pass


class PluginInitError(PluginError):
    """Raised when a plugin fails to initialize."""
    pass


class PluginStartError(PluginError):
    """Raised when a plugin fails to start."""
    pass


class Plugin(ABC):
    """
    Abstract base class for all LiuHao AI OS plugins.
    
    All plugins must extend this class and implement the core lifecycle methods.
    """
    
    def __init__(self, metadata: PluginMetadata):
        self.metadata = metadata
        self.status = PluginStatus.LOADED
        self.initialized_at: Optional[float] = None
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.error: Optional[str] = None
        self.run_count: int = 0
        
    @abstractmethod
    async def initialize(self) -> None:
        """ called once after loading, before start """
        raise NotImplementedError
    
    @abstractmethod
    async def start(self) -> None:
        """ called to begin plugin operation """
        raise NotImplementedError
    
    @abstractmethod
    async def stop(self) -> None:
        """ called to gracefully stop the plugin """
        raise NotImplementedError
    
    @abstractmethod
    async def reload(self) -> None:
        """ called when plugin configuration changes """
        raise NotImplementedError
    
    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """ execute the plugin with given context """
        raise NotImplementedError
    
    def get_metadata(self) -> PluginMetadata:
        """ Return plugin metadata """
        return self.metadata
    
    def get_status(self) -> str:
        """ Return current plugin status """
        return self.status
    
    def get_stats(self) -> Dict[str, Any]:
        """ Return plugin execution statistics """
        return {
            "status": self.status,
            "name": self.metadata.name,
            "version": self.metadata.version,
            "run_count": self.run_count,
            "initialized_at": self.initialized_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "has_error": self.error is not None,
        }
    
    def can_execute(self) -> bool:
        """ Check if plugin is in a state to execute """
        return self.status == PluginStatus.STARTED and self.error is None


# Convenience function
def create_plugin(plugin_type: str, metadata: PluginMetadata, **kwargs) -> "Plugin":
    """Factory function to create plugin instances"""
    # This will be extended as plugins are registered
    from .manager import PluginManager
    return PluginManager.create_plugin(plugin_type, metadata, **kwargs)