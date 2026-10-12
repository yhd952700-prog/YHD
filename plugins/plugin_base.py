"""
Plugin Base for LiuHao AI OS Plugins

Defines the standard interface that all plugins must implement.
Provides:
- Plugin metadata (name, version, description, author, categories)
- Plugin lifecycle (initialize, activate, deactivate, shutdown)
- Plugin capability declaration
- Error handling contracts
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import time


@dataclass
class PluginMetadata:
    """Metadata describing a plugin.

    Attributes:
        name: Unique plugin name (kebab-case recommended).
        version: Semantic version string.
        description: Short description of what the plugin does.
        author: Plugin author name or organization.
        categories: List of category tags for discovery.
        keywords: List of search keywords.
        requires_internet: Whether the plugin requires network access.
        dependencies: List of other plugin names this depends on.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    categories: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    requires_internet: bool = False
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize metadata to a dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "categories": self.categories,
            "keywords": self.keywords,
            "requires_internet": self.requires_internet,
            "dependencies": self.dependencies,
        }


class PluginStatus:
    """Plugin lifecycle status constants."""

    UNLOADED = "unloaded"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class PluginError(Exception):
    """Base exception for plugin-related errors."""
    pass


class PluginInitError(PluginError):
    """Raised when a plugin fails to initialize."""
    pass


class PluginActivationError(PluginError):
    """Raised when a plugin fails to activate."""
    pass


class PluginExecutionError(PluginError):
    """Raised when a plugin execution fails."""
    pass


class BasePlugin(ABC):
    """
    Abstract base class for all LiuHao AI OS plugins.

    All plugins must extend this class and implement the core lifecycle
    methods along with their specific functionality.

    Lifecycle flow:
        load -> initialize -> activate <-> deactivate -> shutdown

    Example:
        class MyPlugin(BasePlugin):
            def __init__(self):
                super().__init__(PluginMetadata(
                    name="my-plugin",
                    version="1.0.0",
                    description="Does cool stuff",
                    author="LiuHao Team",
                    categories=["utility"],
                ))

            def initialize(self, config=None):
                # setup resources
                pass

            def activate(self):
                # start services
                pass

            def deactivate(self):
                # pause services
                pass

            def shutdown(self):
                # cleanup
                pass

            def get_capabilities(self):
                return ["do_stuff"]
    """

    def __init__(self, metadata: PluginMetadata):
        """Initialize the base plugin with metadata.

        Args:
            metadata: PluginMetadata instance describing the plugin.
        """
        self.metadata = metadata
        self._status = PluginStatus.LOADED
        self._error: Optional[str] = None
        self._initialized_at: Optional[float] = None
        self._activated_at: Optional[float] = None
        self._deactivated_at: Optional[float] = None
        self._call_count: int = 0
        self._config: Dict[str, Any] = {}

    # ==================== Lifecycle Methods ====================

    @abstractmethod
    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Initialize the plugin with optional configuration.

        Called once after the plugin is loaded. Use this to set up
        resources, validate dependencies, and prepare for activation.

        Args:
            config: Optional configuration dictionary.

        Returns:
            True if initialization succeeded, False otherwise.

        Raises:
            PluginInitError: If initialization fails critically.
        """
        raise NotImplementedError

    @abstractmethod
    def activate(self) -> bool:
        """
        Activate the plugin, making it ready for use.

        Called after successful initialization. Use this to start
        background services, open connections, etc.

        Returns:
            True if activation succeeded, False otherwise.

        Raises:
            PluginActivationError: If activation fails critically.
        """
        raise NotImplementedError

    @abstractmethod
    def deactivate(self) -> bool:
        """
        Deactivate the plugin, pausing its operation.

        The plugin should remain in a state where it can be
        re-activated without full re-initialization.

        Returns:
            True if deactivation succeeded, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> bool:
        """
        Shut down the plugin completely, cleaning up all resources.

        After shutdown, the plugin cannot be re-activated without
        re-initializing from scratch.

        Returns:
            True if shutdown succeeded, False otherwise.
        """
        raise NotImplementedError

    # ==================== Capability Declaration ====================

    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """
        Return a list of capability identifiers this plugin provides.

        Capabilities are string identifiers that other plugins or
        the core system can use to discover what a plugin can do.

        Returns:
            List of capability name strings.
        """
        raise NotImplementedError

    # ==================== Status & Info ====================

    @property
    def status(self) -> str:
        """Get the current plugin status."""
        return self._status

    @property
    def name(self) -> str:
        """Get the plugin name."""
        return self.metadata.name

    @property
    def version(self) -> str:
        """Get the plugin version."""
        return self.metadata.version

    def is_active(self) -> bool:
        """Check if the plugin is currently active and usable."""
        return self._status == PluginStatus.ACTIVE and self._error is None

    def has_error(self) -> bool:
        """Check if the plugin is in an error state."""
        return self._error is not None

    def get_error(self) -> Optional[str]:
        """Get the current error message, if any."""
        return self._error

    def get_stats(self) -> Dict[str, Any]:
        """Return plugin runtime statistics."""
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "status": self._status,
            "call_count": self._call_count,
            "initialized_at": self._initialized_at,
            "activated_at": self._activated_at,
            "deactivated_at": self._deactivated_at,
            "has_error": self._error is not None,
            "error": self._error,
        }

    def get_info(self) -> Dict[str, Any]:
        """Return full plugin info (metadata + status)."""
        info = self.metadata.to_dict()
        info.update({
            "status": self._status,
            "call_count": self._call_count,
            "has_error": self._error is not None,
        })
        return info

    # ==================== Internal Helpers ====================

    def _set_status(self, status: str) -> None:
        """Set the plugin status (internal use)."""
        self._status = status

    def _set_error(self, error: str) -> None:
        """Set an error state on the plugin."""
        self._error = error
        self._status = PluginStatus.ERROR

    def _clear_error(self) -> None:
        """Clear the error state."""
        self._error = None

    def _increment_call_count(self) -> None:
        """Increment the execution call counter."""
        self._call_count += 1

    def _ensure_active(self) -> None:
        """Ensure the plugin is active before executing operations.

        Raises:
            PluginExecutionError: If the plugin is not active.
        """
        if not self.is_active():
            raise PluginExecutionError(
                f"Plugin '{self.metadata.name}' is not active "
                f"(status: {self._status}, error: {self._error})"
            )
