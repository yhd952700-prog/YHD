"""Plugin Registry — Central plugin management for LIUHAO X v3.0

The Plugin Kernel provides a centralized registry for discovering, loading,
and managing kernel plugins. Supports hot-loading, version compatibility,
and scope-aware plugin activation.

依据 Definition Lock §115: Plugin Registry 必须能够
- register_plugin(name, version, kernel_type, capabilities, compatibility)
- unregister_plugin(plugin_id)
- discover_plugins(kernel_type, scope, min_version, max_version)
- list_active_plugins() → List[PluginInfo]
- load_plugin(plugin_id) → PluginInterface  # 已实现：真实 importlib 加载 + PluginInterface 子类实例化
- Plugin version compatibility checking (semver-aware)
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import threading
from pathlib import Path
import importlib
import importlib.util
import json

from packaging.version import InvalidVersion, Version

from src.kernels._crosscutting import kernel_action

# The real plugin contract lives in src.plugins.base. We alias it here as
# PluginInterface so the loader/activator can depend on a single, honest type.
# This import is safe: src.plugins.base only depends on abc/typing/dataclasses
# and never imports the kernel layer, so there is no circular dependency.
from src.plugins.base import Plugin as PluginInterface, PluginMetadata


def _version_ge(a: str, b: str) -> bool:
    """Return ``True`` when version ``a`` is >= version ``b`` (PEP 440).

    Version bounds used to be compared as plain strings, so ``"1.10.0"`` sorted
    *below* ``"1.9.0"`` and a ``min_version`` filter silently dropped valid
    plugins. Falls back to string comparison when either side is not PEP 440
    parseable, so exotic version strings keep working instead of raising.
    """
    try:
        return Version(a) >= Version(b)
    except InvalidVersion:
        return a >= b


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
    # Python import path of the module that contains the concrete
    # PluginInterface subclass (e.g. "src.plugins.builtin.example_capability_plugin").
    # Honest loader requires this to actually import + instantiate the plugin.
    module_path: Optional[str] = None


class PluginRegistry:
    """Central plugin registry with discovery, loading, and lifecycle management."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

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
                    module_path=pdata.get("module_path"),
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
        plugin_id: Optional[str] = None,
        module_path: Optional[str] = None,
    ) -> PluginInfo:
        """Register a new plugin in the registry (scope-validated L0-L7).

        ``plugin_id`` and ``module_path`` are optional: OS kernels keep the
        auto-generated ``kernel_type:name:version`` id, whereas built-in
        loadable plugins pass a clean id (no colons) and the import path of
        their ``PluginInterface`` subclass so the loader can actually import it.
        """
        if scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid plugin scope: {scope!r} (must be L0-L7)")
        with self._lock:
            if plugin_id is None:
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
                module_path=module_path,
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

                # Version filters -- semantic (PEP 440) comparison, not strings.
                if min_version and not _version_ge(info.version, min_version):
                    continue
                if max_version and not _version_ge(max_version, info.version):
                    continue

                results.append(info)

            return results

    def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Get plugin info by ID."""
        with self._lock:
            return self._plugins.get(plugin_id)

    def list_plugins(self) -> List[PluginInfo]:
        """Return a snapshot of every registered plugin (copy, thread-safe)."""
        with self._lock:
            return list(self._plugins.values())

    def load_plugin(self, plugin_id: str) -> Optional["PluginInterface"]:
        """Load a registered plugin into memory and return a real instance.

        Honest loader: looks up the plugin's ``module_path`` (stored on the
        ``PluginInfo`` or in its ``metadata``), imports the module via
        ``importlib.import_module``, finds the concrete ``PluginInterface``
        subclass, instantiates it, and caches it in ``self._loaded``. Returns
        ``None`` (and marks the plugin FAILED with a real reason) if anything is
        missing — never a fake / stub instance.
        """
        with self._lock:
            info = self._plugins.get(plugin_id)
            if not info:
                return None

            module_path = info.module_path or (info.metadata or {}).get("module_path")
            if not module_path:
                info.status = PluginStatus.FAILED
                info.error = "No module_path registered for plugin (cannot load)"
                self._save_index()
                return None

            try:
                module = importlib.import_module(module_path)
            except ImportError as exc:
                info.status = PluginStatus.FAILED
                info.error = f"Could not import module {module_path!r}: {exc}"
                self._save_index()
                return None

            # Find the concrete PluginInterface subclass (exclude the ABC itself).
            plugin_cls = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, PluginInterface)
                    and attr is not PluginInterface
                ):
                    plugin_cls = attr
                    break
            if plugin_cls is None:
                info.status = PluginStatus.FAILED
                info.error = f"No PluginInterface subclass found in {module_path!r}"
                self._save_index()
                return None

            try:
                metadata = PluginMetadata(
                    name=info.name,
                    version=info.version,
                    description=(info.metadata or {}).get("description", ""),
                )
                instance = plugin_cls(metadata)
            except Exception as exc:
                info.status = PluginStatus.FAILED
                info.error = f"Failed to instantiate {plugin_cls.__name__}: {exc}"
                self._save_index()
                return None

            self._loaded[plugin_id] = instance
            return instance

    @kernel_action("plugin.activate_plugin")
    def activate_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Mark a plugin ACTIVE by actually loading it into memory.

        Reuses :meth:`load_plugin` so the status transition always reflects a
        real, importable instance — never a placeholder.
        """
        with self._lock:
            info = self._plugins.get(plugin_id)
            if not info:
                return None

            if info.status == PluginStatus.ACTIVE and plugin_id in self._loaded:
                # Already active with a live instance, return existing info.
                return info

            instance = self.load_plugin(plugin_id)
            if instance is None:
                # load_plugin already recorded the reason in info.error + status.
                self._save_index()
                return info

            info.status = PluginStatus.ACTIVE
            info.loaded_at = datetime.now(timezone.utc)
            info.error = None
            self._loaded[plugin_id] = instance

            audit_log(
                event_type="plugin_activate",
                principal_id="system",
                permission="plugin:manage",
                scope="L1",
                result="allowed",
                reason=f"Plugin {info.name} v{info.version} activated (real instance loaded)",
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

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Global plugin registry instance
_global_plugin_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get or create the global plugin registry instance."""
    global _global_plugin_registry
    if _global_plugin_registry is None:
        _global_plugin_registry = PluginRegistry()
        _global_plugin_registry.initialize()  # 存在即 READY：构造完成即视为就绪
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


def register_builtin_plugins() -> None:
    """Register the shipped built-in plugins with clean, loadable ids.

    Called once at gateway boot (right after ``install_production_rules``).
    Idempotent: skips any id that is already registered, so re-running boot or
    tests never duplicates or clobbers an existing entry.

    Each built-in carries a ``module_path`` pointing at a real
    ``PluginInterface`` subclass, so ``load_plugin`` / ``activate_plugin`` can
    actually import and instantiate it — closing the loop that was previously
    left as ``# UNIMPLEMENTED``.
    """
    reg = get_plugin_registry()

    builtins = [
        PluginInfo(
            plugin_id="builtin.example_capability",
            name="example_capability",
            version="1.0.0",
            kernel_type="builtin",
            capabilities=["capability.inspect", "heartbeat"],
            scope="L1",
            compatibility={},
            status=PluginStatus.REGISTERED,
            metadata={
                "module_path": "src.plugins.builtin.example_capability_plugin",
                "description": "示例能力插件：返回真实的插件注册表心跳与已注册能力清单。",
            },
        ),
    ]

    for info in builtins:
        if reg.get_plugin(info.plugin_id) is None:
            # 仅写入内存注册表，不落盘到 registry_index.json：内置插件由代码定义、
            # 每次启动都会经 register_builtin_plugins 重新注册，不应被当成"用户安装的
            # 插件"持久化进 tracked 文件，避免污染 plugins/registry_index.json。
            reg._plugins[info.plugin_id] = info


# 此处曾有**导入期急切实例化**（``_global_plugin_registry = PluginRegistry()``
# 紧接一次 ``initialize()``）。它让「import 本模块」带上副作用：
# ``PluginRegistry.__init__`` 会 ``mkdir("./plugins")``（相对当前 cwd），并且与
# ``_registry.py`` 把本内核列为「12 个**惰性**单例」的契约相冲突。
# 现统一走 ``get_plugin_registry()`` 的惰性路径——该 getter 构造后立即
# ``initialize()``，因此「存在即 READY」的不变量仍然成立。


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
