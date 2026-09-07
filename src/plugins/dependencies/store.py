"""
Plugin Dependencies Store for LiuHao AI OS

Provides:
- Dependency specification persistence
- Resolution result storage
- Conflict tracking
- Integrity verification
"""

from pathlib import Path
import json
import hashlib
import time
from typing import Dict, List, Optional, Any

from .models import (
    DependencySpec,
    DependencyResolution,
    DependencyConflict,
    PluginDependency,
)


class PluginDependenciesStore:
    """
    Plugin dependencies store with integrity verification.
    
    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Dependency specification management
    - Resolution tracking
    - Conflict detection
    """
    
    def __init__(self, storage_path: str = "data/plugins/dependencies/dependencies.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._dependencies: Dict[str, PluginDependency] = {}
        self._resolutions: Dict[str, DependencyResolution] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()
    
    def _load(self) -> None:
        """Load existing dependencies from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._dependencies = {
                    k: PluginDependency.from_dict(v) for k, v in data.get("dependencies", {}).items()
                }
                self._resolutions = {
                    k: DependencyResolution.from_dict(v) for k, v in data.get("resolutions", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                if not self._hash_chain or len(self._hash_chain) != len(self._dependencies):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load dependencies store: {e}")
                self._dependencies = {}
                self._resolutions = {}
                self._hash_chain = None
        else:
            self._dependencies = {}
            self._resolutions = {}
            self._hash_chain = None
    
    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current dependencies."""
        chain: List[str] = []
        sorted_ids = sorted(self._dependencies.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            dep = self._dependencies[eid]
            dep_dict = dep.to_dict()
            dep_dict["prev_hash"] = prev_hash
            dep_data = json.dumps(dep_dict, sort_keys=True, separators=(",", ":"))
            dep_hash = hashlib.sha256(dep_data.encode()).hexdigest()
            chain.append(dep_hash)
            prev_hash = dep_hash
        
        self._hash_chain = chain
        self._save()
    
    def _save(self) -> None:
        """Persist dependencies to storage."""
        if self._hash_chain is None:
            self._build_hash_chain()
        
        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "dependencies": {k: v.to_dict() for k, v in self._dependencies.items()},
            "resolutions": {k: v.to_dict() for k, v in self._resolutions.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    # ==================== Dependency Operations ====================
    
    def register(self, plugin_id: str, dependency: DependencySpec) -> str:
        """
        Register a dependency for a plugin.
        
        Args:
            plugin_id: The plugin ID
            dependency: The dependency specification
            
        Returns:
            The dependency key
        """
        key = f"{plugin_id}/{dependency.name}"
        plugin_dep = self._dependencies.get(plugin_id)
        if plugin_dep:
            # Add to existing dependencies
            plugin_dep.dependencies.append(dependency)
        else:
            # Create new plugin dependency
            plugin_dep = PluginDependency(plugin_id=plugin_id, dependencies=[dependency])
            self._dependencies[plugin_id] = plugin_dep
        
        self._save()
        return key
    
    def get(self, plugin_id: str) -> Optional[PluginDependency]:
        """Get plugin dependencies by plugin ID."""
        return self._dependencies.get(plugin_id)
    
    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[PluginDependency]:
        """List plugin dependencies with optional filters."""
        deps = list(self._dependencies.values())
        
        if not filters:
            return deps
        
        result = []
        for dep in deps:
            match = True
            for key, value in filters.items():
                if hasattr(dep, key):
                    dep_val = getattr(dep, key, None)
                    if dep_val != value:
                        match = False
                        break
                else:
                    match = False
                    break
            if match:
                result.append(dep)
        return result
    
    def add_conflict(self, plugin_id: str, conflict: DependencyConflict) -> str:
        """
        Add a conflict record for a plugin.
        
        Args:
            plugin_id: The plugin ID
            conflict: The conflict to record
            
        Returns:
            The conflict key
        """
        key = f"{plugin_id}/conflict"
        plugin_dep = self._dependencies.get(plugin_id)
        if plugin_dep:
            plugin_dep.conflict = conflict
        else:
            plugin_dep = PluginDependency(plugin_id=plugin_id, conflict=conflict)
            self._dependencies[plugin_id] = plugin_dep
        
        self._save()
        return key
    
    def get_conflict(self, plugin_id: str) -> Optional[DependencyConflict]:
        """Get conflict for a plugin."""
        plugin_dep = self._dependencies.get(plugin_id)
        if plugin_dep:
            return plugin_dep.conflict
        return None
    
    # ==================== Integrity ====================
    
    def verify_integrity(self) -> bool:
        """Verify the hash chain integrity."""
        if not self._dependencies or not self._hash_chain:
            return True
        
        chain = self._hash_chain
        if len(chain) != len(self._dependencies):
            return False
        
        sorted_ids = sorted(self._dependencies.keys())
        prev_hash = "genesis"
        for i, eid in enumerate(sorted_ids):
            dep = self._dependencies[eid]
            dep_dict = dep.to_dict()
            dep_dict["prev_hash"] = prev_hash
            dep_data = json.dumps(dep_dict, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(dep_data.encode()).hexdigest()
            
            if expected_hash != chain[i]:
                return False
            
            prev_hash = expected_hash
        
        return True
    
    # ==================== Statistics ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get dependencies statistics."""
        deps = list(self._dependencies.values())
        
        status_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        for dep in deps:
            # Count dependency types
            for d in dep.dependencies:
                t = d.dependency_type
                type_counts[t] = type_counts.get(t, 0) + 1
            # Count conflicts
            if dep.conflict:
                status_counts["has_conflict"] = status_counts.get("has_conflict", 0) + 1
        
        return {
            "total_plugins_with_deps": len(deps),
            "total_dependencies": sum(len(d.dependencies) for d in deps),
            "by_type": type_counts,
            "with_conflicts": status_counts.get("has_conflict", 0),
        }


# Module-level convenience functions
_default_store: Optional[PluginDependenciesStore] = None


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