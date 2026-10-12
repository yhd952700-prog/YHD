"""
Plugin Registry for LiuHao AI OS Plugins

Provides:
- Plugin registration and unregistration
- Plugin discovery and listing
- Dependency resolution
- Status tracking
- Plugin lookup by name, category, or capability
"""

import importlib
import inspect
import os
import sys
from typing import Dict, Any, Optional, List, Set, Tuple
from pathlib import Path

from .plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginError,
    PluginInitError,
    PluginActivationError,
)


class PluginRegistry:
    """
    Central registry for managing plugin instances.

    Manages the full lifecycle of plugins:
    - Discovery (finding plugin modules on disk)
    - Registration (adding plugin instances to the registry)
    - Lifecycle control (initialize, activate, deactivate, shutdown)
    - Dependency resolution
    - Status tracking and queries

    Example:
        registry = PluginRegistry()
        registry.discover_plugins("plugins/")
        registry.initialize_all()
        registry.activate_all()

        plugin = registry.get_plugin("translate")
        result = plugin.translate("hello", "zh")
    """

    def __init__(self, plugins_dir: Optional[str] = None):
        """Initialize the plugin registry.

        Args:
            plugins_dir: Base directory for plugin discovery.
        """
        self._plugins: Dict[str, BasePlugin] = {}
        self._plugin_classes: Dict[str, type] = {}
        self._plugins_dir = Path(plugins_dir) if plugins_dir else None
        self._init_order: List[str] = []
        self._activation_order: List[str] = []

    # ==================== Plugin Registration ====================

    def register_plugin(self, plugin: BasePlugin) -> str:
        """Register a plugin instance.

        Args:
            plugin: An instance of a BasePlugin subclass.

        Returns:
            The plugin name.

        Raises:
            ValueError: If a plugin with the same name is already registered.
        """
        name = plugin.metadata.name
        if name in self._plugins:
            raise ValueError(f"Plugin '{name}' is already registered")

        self._plugins[name] = plugin
        return name

    def register_plugin_class(self, plugin_class: type) -> str:
        """Register a plugin class (for lazy instantiation).

        Args:
            plugin_class: A subclass of BasePlugin.

        Returns:
            The plugin class name.
        """
        if not issubclass(plugin_class, BasePlugin):
            raise TypeError(f"{plugin_class} is not a subclass of BasePlugin")

        class_name = plugin_class.__name__
        self._plugin_classes[class_name] = plugin_class
        return class_name

    def unregister_plugin(self, name: str) -> bool:
        """Unregister a plugin by name.

        Attempts to shut down the plugin first if it's active.

        Args:
            name: Plugin name to unregister.

        Returns:
            True if the plugin was found and unregistered.
        """
        if name not in self._plugins:
            return False

        plugin = self._plugins[name]
        try:
            if plugin.is_active():
                plugin.deactivate()
            if plugin.status != PluginStatus.SHUTDOWN:
                plugin.shutdown()
        except Exception:
            pass  # Best effort shutdown

        del self._plugins[name]
        return True

    # ==================== Plugin Discovery ====================

    def discover_plugins(self, directory: Optional[str] = None) -> int:
        """Discover and load plugins from a directory.

        Scans the directory for subdirectories containing *_plugin.py files,
        imports them, and registers any BasePlugin subclasses found.

        Args:
            directory: Directory to scan. Uses plugins_dir if not provided.

        Returns:
            Number of plugins discovered and registered.
        """
        search_dir = Path(directory) if directory else self._plugins_dir
        if not search_dir or not search_dir.exists():
            return 0

        discovered = 0
        search_path = str(search_dir.parent)
        if search_path not in sys.path:
            sys.path.insert(0, search_path)

        # Walk through subdirectories
        for item in search_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                # Look for plugin files in this directory
                for plugin_file in item.glob("*_plugin.py"):
                    try:
                        # Build module path
                        rel_path = plugin_file.relative_to(search_dir.parent)
                        module_path = str(rel_path.with_suffix("")).replace(
                            os.sep, "."
                        )

                        module = importlib.import_module(module_path)

                        # Find BasePlugin subclasses in the module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, BasePlugin)
                                and attr is not BasePlugin
                            ):
                                # Instantiate and register
                                try:
                                    plugin_instance = attr()
                                    self.register_plugin(plugin_instance)
                                    discovered += 1
                                except Exception as e:
                                    print(
                                        f"Warning: Could not instantiate "
                                        f"{attr_name}: {e}"
                                    )
                    except Exception as e:
                        print(
                            f"Warning: Failed to load plugin from "
                            f"{plugin_file}: {e}"
                        )

        return discovered

    # ==================== Lifecycle Management ====================

    def initialize_plugin(
        self, name: str, config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Initialize a single plugin.

        Args:
            name: Plugin name.
            config: Optional configuration dict.

        Returns:
            True if initialization succeeded.

        Raises:
            PluginError: If the plugin is not found.
        """
        plugin = self._get_plugin_or_raise(name)

        try:
            success = plugin.initialize(config)
            if success:
                plugin._set_status(PluginStatus.INITIALIZED)
                plugin._initialized_at = _now()
                self._init_order.append(name)
            return success
        except PluginInitError:
            raise
        except Exception as e:
            plugin._set_error(str(e))
            return False

    def activate_plugin(self, name: str) -> bool:
        """Activate a single plugin.

        Args:
            name: Plugin name.

        Returns:
            True if activation succeeded.

        Raises:
            PluginError: If the plugin is not found.
        """
        plugin = self._get_plugin_or_raise(name)

        if plugin.status == PluginStatus.LOADED:
            # Auto-initialize if not yet initialized
            if not self.initialize_plugin(name):
                return False

        try:
            success = plugin.activate()
            if success:
                plugin._set_status(PluginStatus.ACTIVE)
                plugin._activated_at = _now()
                plugin._clear_error()
                self._activation_order.append(name)
            return success
        except PluginActivationError:
            raise
        except Exception as e:
            plugin._set_error(str(e))
            return False

    def deactivate_plugin(self, name: str) -> bool:
        """Deactivate a single plugin.

        Args:
            name: Plugin name.

        Returns:
            True if deactivation succeeded.
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return False

        try:
            success = plugin.deactivate()
            if success:
                plugin._set_status(PluginStatus.INACTIVE)
                plugin._deactivated_at = _now()
            return success
        except Exception as e:
            plugin._set_error(str(e))
            return False

    def shutdown_plugin(self, name: str) -> bool:
        """Shut down a single plugin.

        Args:
            name: Plugin name.

        Returns:
            True if shutdown succeeded.
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return False

        try:
            success = plugin.shutdown()
            if success:
                plugin._set_status(PluginStatus.SHUTDOWN)
            return success
        except Exception as e:
            plugin._set_error(str(e))
            return False

    def initialize_all(self, configs: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, bool]:
        """Initialize all registered plugins.

        Args:
            configs: Optional dict mapping plugin names to configs.

        Returns:
            Dict mapping plugin names to initialization success.
        """
        results: Dict[str, bool] = {}
        configs = configs or {}

        for name in self._plugins:
            results[name] = self.initialize_plugin(
                name, configs.get(name)
            )

        return results

    def activate_all(self) -> Dict[str, bool]:
        """Activate all initialized plugins.

        Resolves dependencies so that plugins are activated in the
        correct order.

        Returns:
            Dict mapping plugin names to activation success.
        """
        results: Dict[str, bool] = {}
        activation_order = self._resolve_dependency_order()

        for name in activation_order:
            if name in self._plugins:
                results[name] = self.activate_plugin(name)

        return results

    def deactivate_all(self) -> Dict[str, bool]:
        """Deactivate all active plugins in reverse dependency order.

        Returns:
            Dict mapping plugin names to deactivation success.
        """
        results: Dict[str, bool] = {}
        activation_order = self._resolve_dependency_order()

        # Deactivate in reverse order
        for name in reversed(activation_order):
            if name in self._plugins:
                results[name] = self.deactivate_plugin(name)

        return results

    def shutdown_all(self) -> Dict[str, bool]:
        """Shut down all plugins.

        Returns:
            Dict mapping plugin names to shutdown success.
        """
        results: Dict[str, bool] = {}
        for name in list(self._plugins.keys()):
            results[name] = self.shutdown_plugin(name)
        return results

    # ==================== Dependency Resolution ====================

    def _resolve_dependency_order(self) -> List[str]:
        """Resolve plugin activation order based on dependencies.

        Uses topological sort to ensure dependencies are activated
        before the plugins that depend on them.

        Returns:
            List of plugin names in activation order.
        """
        # Build dependency graph
        graph: Dict[str, Set[str]] = {}
        all_plugins: Set[str] = set()

        for name, plugin in self._plugins.items():
            all_plugins.add(name)
            deps = set(plugin.metadata.dependencies)
            graph[name] = deps & all_plugins  # Only known deps

        # Topological sort (Kahn's algorithm)
        in_degree: Dict[str, int] = {name: 0 for name in graph}
        for deps in graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Start with plugins that have no dependents (leaf nodes)
        # Actually, we want deps first, so reverse: start with no dependencies
        queue = [name for name, deg in in_degree.items() if deg == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for name, deps in graph.items():
                if node in deps:
                    in_degree[name] -= 1
                    if in_degree[name] == 0:
                        queue.append(name)

        # Add any remaining (cyclic or isolated) in arbitrary order
        remaining = all_plugins - set(result)
        result.extend(sorted(remaining))

        return result

    def check_dependencies(self, name: str) -> Tuple[bool, List[str]]:
        """Check if all dependencies of a plugin are available and active.

        Args:
            name: Plugin name to check.

        Returns:
            Tuple of (all_satisfied, missing_dependency_names).
        """
        plugin = self._get_plugin_or_raise(name)
        missing: List[str] = []

        for dep_name in plugin.metadata.dependencies:
            dep = self._plugins.get(dep_name)
            if not dep:
                missing.append(dep_name)
            elif not dep.is_active():
                missing.append(dep_name)

        return len(missing) == 0, missing

    # ==================== Plugin Lookup ====================

    def get_plugin(self, name: str) -> Optional[BasePlugin]:
        """Get a plugin by name.

        Args:
            name: Plugin name.

        Returns:
            The plugin instance, or None if not found.
        """
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all registered plugins with their info.

        Returns:
            List of plugin info dictionaries.
        """
        return [plugin.get_info() for plugin in self._plugins.values()]

    def list_active_plugins(self) -> List[Dict[str, Any]]:
        """List all currently active plugins.

        Returns:
            List of active plugin info dictionaries.
        """
        return [
            plugin.get_info()
            for plugin in self._plugins.values()
            if plugin.is_active()
        ]

    def find_by_category(self, category: str) -> List[BasePlugin]:
        """Find plugins by category.

        Args:
            category: Category name to search for.

        Returns:
            List of matching plugin instances.
        """
        return [
            plugin
            for plugin in self._plugins.values()
            if category.lower() in [c.lower() for c in plugin.metadata.categories]
        ]

    def find_by_capability(self, capability: str) -> List[BasePlugin]:
        """Find plugins that provide a specific capability.

        Args:
            capability: Capability identifier.

        Returns:
            List of matching plugin instances.
        """
        results = []
        for plugin in self._plugins.values():
            try:
                caps = plugin.get_capabilities()
                if capability in caps:
                    results.append(plugin)
            except Exception:
                continue
        return results

    def has_plugin(self, name: str) -> bool:
        """Check if a plugin is registered.

        Args:
            name: Plugin name.

        Returns:
            True if the plugin is registered.
        """
        return name in self._plugins

    def get_plugin_count(self) -> int:
        """Get the total number of registered plugins."""
        return len(self._plugins)

    def get_active_count(self) -> int:
        """Get the number of active plugins."""
        return sum(1 for p in self._plugins.values() if p.is_active())

    # ==================== Internal Helpers ====================

    def _get_plugin_or_raise(self, name: str) -> BasePlugin:
        """Get a plugin or raise an error if not found."""
        plugin = self._plugins.get(name)
        if not plugin:
            raise PluginError(f"Plugin '{name}' not found in registry")
        return plugin


def _now() -> float:
    """Get current timestamp."""
    import time
    return time.time()


# Module-level singleton
_default_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the default plugin registry singleton."""
    global _default_registry
    if _default_registry is None:
        _default_registry = PluginRegistry()
    return _default_registry
