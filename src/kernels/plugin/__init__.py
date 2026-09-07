"""Plugin Registry — Central plugin management for LIUHAO X v3.0

The Plugin Kernel provides a centralized registry for discovering, loading,
and managing kernel plugins. Supports hot-loading, version compatibility,
and scope-aware plugin activation.

依据 Definition Lock §115: Plugin Registry 必须能够
- register_plugin(name, version, kernel_type, capabilities, compatibility)
- unregister_plugin(plugin_id)
- discover_plugins(kernel_type, scope, min_version, max_version)
- load_plugin(plugin_id) → PluginInterface
- list_active_plugins() → List[PluginInfo]
- Plugin version compatibility checking (semver-aware)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import threading
from pathlib import Path
import importlib.util
import json

from src.kernels._crosscutting import kernel_action


def _json_default(obj: Any) -> Any:
    """JSON serializer for types not natively supported.

    PluginInfo (and its nested state) may carry ``datetime`` fields such as
    ``loaded_at`` / ``unloaded_at`` that ``json.dump`` cannot serialize by
    default. Convert them to ISO-8601 strings on the way out so any code
    path that persists the registry index (unregister / activate / deactivate)
    does not crash with ``TypeError: Object of type datetime is not JSON
    serializable``.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# Valid L0-L7 scopes (same hierarchy as resource/capability/security kernels).
_VALID_SCOPES = {f"L{i}" for i in range(8)}


class PluginStatus(str, Enum):
    """Plugin lifecycle status."""
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    UNLOADED = "unloaded"


@dataclass
class PluginInfo:
    """Metadata for a registered plugin."""
    plugin_id: str
    name: str
    version: str
    kernel_type: str
    capabilities: List[str]
    scope: str
    compatibility: Dict[str, Any]
    status: PluginStatus = PluginStatus.DISCOVERED
    loaded_at: Optional[datetime] = None
    unloaded_at: Optional[datetime] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class PluginRegistry:
    """Central plugin registry with discovery, loading, and lifecycle management."""

    def __init__(self, registry_path: str = "./plugins"):
        self._registry_path = Path(registry_path)
        self._plugins: Dict[str, PluginInfo] = {}
        self._loaded: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._registry_path.mkdir(parents=True, exist_ok=True)

        # Load existing registry index on init
        self._reload_index()

    def _reload_index(self) -> None:
        """Reload the plugin registry index from disk."""
        index_path = self._registry_path / "registry_index.json"
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pdata in data.get("plugins", []):
                info = PluginInfo(
                    plugin_id=pdata["plugin_id"],
                    name=pdata["name"],
                    version=pdata["version"],
                    kernel_type=pdata["kernel_type"],
                    capabilities=pdata["capabilities"],
                    scope=pdata["scope"],
                    compatibility=pdata.get("compatibility", {}),
                    status=PluginStatus(pdata.get("status", "discovered")),
                    loaded_at=(
                        datetime.fromisoformat(pdata["loaded_at"])
                        if pdata.get("loaded_at")
                        else None
                    ),
                    unloaded_at=(
                        datetime.fromisoformat(pdata["unloaded_at"])
                        if pdata.get("unloaded_at")
                        else None
                    ),
                    error=pdata.get("error"),
                    metadata=pdata.get("metadata", {}),
                )
                self._plugins[info.plugin_id] = info

    @kernel_action("plugin.register_plugin")
    def register_plugin(
        self,
        name: str,
        version: str,
        kernel_type: str,
        capabilities: List[str],
        scope: str,
        compatibility: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PluginInfo:
        """Register a new plugin in the registry (scope-validated L0-L7)."""
        if scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid plugin scope: {scope!r} (must be L0-L7)")
        with self._lock:
            plugin_id = f"{kernel_type}:{name}:{version}"
            compatibility = compatibility or {}
            metadata = metadata or {}

            info = PluginInfo(
                plugin_id=plugin_id,
                name=name,
                version=version,
                kernel_type=kernel_type,
                capabilities=capabilities,
                scope=scope,
                compatibility=compatibility,
                status=PluginStatus.REGISTERED,
                metadata=metadata,
            )

            self._plugins[plugin_id] = info
            self._save_index()

            audit_log(
                event_type="plugin_register",
                principal_id="system",
                permission="plugin:manage",
                scope="L1",
                result="allowed",
                reason=f"Plugin {name} v{version} registered for {kernel_type}",
            )

            return info

    @kernel_action("plugin.unregister_plugin")
    def unregister_plugin(self, plugin_id: str, reason: str = "") -> bool:
        """Unregister a plugin from the registry."""
        with self._lock:
            if plugin_id not in self._plugins:
                return False

            info = self._plugins[plugin_id]
            info.status = PluginStatus.UNLOADED
            info.unloaded_at = datetime.now(timezone.utc)

            # Remove from loaded if currently loaded
            if plugin_id in self._loaded:
                del self._loaded[plugin_id]

            self._save_index()

            audit_log(
                event_type="plugin_unregister",
                principal_id="system",
                permission="plugin:manage",
                scope="L1",
                result="allowed",
                reason=f"Plugin {info.name} v{info.version} unregistered: {reason}",
            )

            return True

    def discover_plugins(
        self,
        kernel_type: Optional[str] = None,
        scope: Optional[str] = None,
        min_version: Optional[str] = None,
        max_version: Optional[str] = None,
    ) -> List[PluginInfo]:
        """Discover plugins matching filter criteria."""
        with self._lock:
            results = []

            for info in self._plugins.values():
                # Kernel type filter
                if kernel_type and info.kernel_type != kernel_type:
                    continue

                # Scope filter
                if scope and info.scope != scope:
                    continue

                # Version filters (simple string comparison; semver-aware later)
                if min_version and info.version < min_version:
                    continue
                if max_version and info.version > max_version:
                    continue

                results.append(info)

            return results

    def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Get plugin info by ID."""
        with self._lock:
            return self._plugins.get(plugin_id)

    @kernel_action("plugin.activate_plugin")
    def activate_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Mark a plugin as loading/active and attempt to load it."""
        with self._lock:
            info = self._plugins.get(plugin_id)
            if not info:
                return None

            if info.status == PluginStatus.ACTIVE:
                # Already active, return existing info
                return info

            info.status = PluginStatus.LOADING

            # Attempt to load the plugin module
            try:
                plugin_dir = self._registry_path / "plugins" / plugin_id
                if plugin_dir.exists():
                    # Load the plugin's __init__.py
                    init_path = plugin_dir / "__init__.py"
                    if init_path.exists():
                        spec = importlib.util.spec_from_file_location(
                            f"plugin_{plugin_id}", init_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        # Check for PluginInterface
                        if hasattr(module, "PluginInterface"):
                            info.status = PluginStatus.ACTIVE
                            info.loaded_at = datetime.now(timezone.utc)

                            # Store loaded module
                            self._loaded[plugin_id] = module

                            audit_log(
                                event_type="plugin_activate",
                                principal_id="system",
                                permission="plugin:manage",
                                scope="L1",
                                result="allowed",
                                reason=f"Plugin {info.name} v{info.version} activated",
                            )
                        else:
                            info.status = PluginStatus.FAILED
                            info.error = "Plugin module missing PluginInterface"
                            raise ImportError("Missing PluginInterface")

                    else:
                        info.status = PluginStatus.FAILED
                        info.error = f"No __init__.py in {plugin_dir}"
                        raise FileNotFoundError(f"No __init__.py in {plugin_dir}")

                else:
                    # Try importing by plugin_id directly
                    try:
                        module = importlib.import_module(plugin_id)
                        if hasattr(module, "PluginInterface"):
                            info.status = PluginStatus.ACTIVE
                            info.loaded_at = datetime.now(timezone.utc)
                            self._loaded[plugin_id] = module
                            audit_log(
                                event_type="plugin_activate",
                                principal_id="system",
                                permission="plugin:manage",
                                scope="L1",
                                result="allowed",
                                reason=f"Plugin {info.name} v{info.version} activated",
                            )
                        else:
                            info.status = PluginStatus.FAILED
                            info.error = "Module missing PluginInterface"
                    except ImportError:
                        info.status = PluginStatus.FAILED
                        info.error = f"Could not import plugin {plugin_id}"

            except Exception as e:
                info.status = PluginStatus.FAILED
                info.error = str(e)
                audit_log(
                    event_type="plugin_activate_failed",
                    principal_id="system",
                    permission="plugin:manage",
                    scope="L1",
                    result="denied",
                    reason=f"Plugin {info.name} v{info.version} failed to activate: {e}",
                )

            self._save_index()
            return info

    @kernel_action("plugin.deactivate_plugin")
    def deactivate_plugin(self, plugin_id: str, reason: str = "") -> bool:
        """Deactivate a loaded plugin."""
        with self._lock:
            info = self._plugins.get(plugin_id)
            if not info or info.status != PluginStatus.ACTIVE:
                return False

            info.status = PluginStatus.DISCOVERED
            info.loaded_at = None

            if plugin_id in self._loaded:
                del self._loaded[plugin_id]

            audit_log(
                event_type="plugin_deactivate",
                principal_id="system",
                permission="plugin:manage",
                scope="L1",
                result="allowed",
                reason=f"Plugin {info.name} v{info.version} deactivated: {reason}",
            )

            self._save_index()
            return True

    def list_active_plugins(self) -> List[PluginInfo]:
        """List all currently active plugins."""
        with self._lock:
            return [
                info for info in self._plugins.values()
                if info.status == PluginStatus.ACTIVE
            ]

    def list_plugins_by_kernel(self, kernel_type: str) -> List[PluginInfo]:
        """List plugins for a specific kernel type."""
        with self._lock:
            return [
                info for info in self._plugins.values()
                if info.kernel_type == kernel_type and info.status in
                (PluginStatus.ACTIVE, PluginStatus.REGISTERED, PluginStatus.DISCOVERED)
            ]

    def compatibility_check(
        self, plugin_id: str, required_capabilities: List[str]
    ) -> Tuple[bool, List[str]]:
        """Check if a plugin has required capabilities."""
        with self._lock:
            info = self._plugins.get(plugin_id)
            if not info:
                return False, ["Plugin not found"]

            missing = []
            for cap in required_capabilities:
                if cap not in info.capabilities:
                    missing.append(cap)

            all_present = len(missing) == 0
            return all_present, missing

    def _save_index(self) -> None:
        """Persist plugin registry index to disk."""
        index_path = self._registry_path / "registry_index.json"
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_plugins": len(self._plugins),
            "active_plugins": len(self.list_active_plugins()),
            "plugins": [info.__dict__ for info in self._plugins.values()],
        }
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)


# Global plugin registry instance
_global_plugin_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get or create the global plugin registry instance."""
    global _global_plugin_registry
    if _global_plugin_registry is None:
        _global_plugin_registry = PluginRegistry()
    return _global_plugin_registry


def plugin_register(
    name, version, kernel_type, capabilities, scope,
    compatibility=None, metadata=None,
):
    """Convenience function to register a plugin."""
    return get_plugin_registry().register_plugin(
        name=name,
        version=version,
        kernel_type=kernel_type,
        capabilities=capabilities,
        scope=scope,
        compatibility=compatibility,
        metadata=metadata,
    )


def plugin_discover(
    kernel_type=None, scope=None, min_version=None, max_version=None,
):
    """Convenience function to discover plugins."""
    return get_plugin_registry().discover_plugins(
        kernel_type=kernel_type,
        scope=scope,
        min_version=min_version,
        max_version=max_version,
    )


def plugin_activate(plugin_id: str):
    """Convenience function to activate a plugin."""
    return get_plugin_registry().activate_plugin(plugin_id)


def plugin_deactivate(plugin_id: str, reason: str = ""):
    """Convenience function to deactivate a plugin."""
    return get_plugin_registry().deactivate_plugin(plugin_id, reason)


def plugin_list_active():
    """Convenience function to list active plugins."""
    return get_plugin_registry().list_active_plugins()


def plugin_list_by_kernel(kernel_type: str):
    """Convenience function to list plugins by kernel type."""
    return get_plugin_registry().list_plugins_by_kernel(kernel_type)


def plugin_compatibility_check(plugin_id: str, required_capabilities: List[str]) -> Tuple[bool, List[str]]:
    """Convenience function to check plugin capabilities."""
    return get_plugin_registry().compatibility_check(plugin_id, required_capabilities)


# FIX: Initialize plugin registry after definition
_global_plugin_registry = PluginRegistry()


# Plugin audit logging helper
def audit_log(event_type, principal_id, permission, scope="L1", result="unknown",
              reason="", correlation_id=None, metadata=None):
    """Log plugin-related audit events."""
    from src.security.audit_policy import audit_log as _audit_log
    return _audit_log(
        event_type=event_type,
        principal_id=principal_id,
        permission=permission,
        scope=scope,
        result=result,
        reason=reason,
        correlation_id=correlation_id,
        metadata=metadata,
    )
