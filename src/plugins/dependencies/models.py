"""
Plugin Dependencies Models for LiuHao AI OS

Defines the data model for plugin dependency management.
Provides models for dependency specification, resolution, and conflict detection.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid


class DependencyType:
    """Dependency type enumeration."""
    HARD = "hard"     # 必须依赖
    SOFT = "soft"     # 可选依赖
    PEER = "peer"     # 同伴依赖 (兼容性)
    CONFLICT = "conflict"  # 已知冲突标记


class DependencyStatus:
    """Dependency resolution status."""
    RESOLVED = "resolved"
    PENDING = "pending"
    CONFLICT = "conflict"
    UNSATISFIABLE = "unsatisfiable"


class DependencySpec:
    """Specification of a plugin dependency."""
    
    def __init__(
        self,
        name: str,
        version: Optional[str] = None,
        version_range: Optional[str] = None,  # e.g., ">=1.0.0,<2.0.0"
        dependency_type: str = DependencyType.HARD,
        optional: bool = False,
        weak: bool = False,
    ):
        self.name = name
        self.version = version
        self.version_range = version_range
        self.dependency_type = dependency_type
        self.optional = optional
        self.weak = weak
        self.id = str(uuid.uuid4())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "version_range": self.version_range,
            "dependency_type": self.dependency_type,
            "optional": self.optional,
            "weak": self.weak,
            "id": self.id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencySpec":
        """Create from dictionary."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version"),
            version_range=data.get("version_range"),
            dependency_type=data.get("dependency_type", DependencyType.HARD),
            optional=data.get("optional", False),
            weak=data.get("weak", False),
        )


class DependencyResolution:
    """Result of dependency resolution."""
    
    def __init__(
        self,
        spec: DependencySpec,
        status: str = DependencyStatus.PENDING,
        resolved_version: Optional[str] = None,
        resolving_plugins: List[str] = None,
        conflict_reason: Optional[str] = None,
        resolved_at: Optional[datetime] = None,
    ):
        self.spec = spec
        self.status = status
        self.resolved_version = resolved_version
        self.resolving_plugins = resolving_plugins or []
        self.conflict_reason = conflict_reason
        self.resolved_at = resolved_at or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "spec": self.spec.to_dict() if self.spec else {},
            "status": self.status,
            "resolved_version": self.resolved_version,
            "resolving_plugins": self.resolving_plugins,
            "conflict_reason": self.conflict_reason,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencyResolution":
        """Create from dictionary."""
        from .models import DependencySpec
        spec = DependencySpec.from_dict(data.get("spec", {}))
        
        return cls(
            spec=spec,
            status=data.get("status", DependencyStatus.PENDING),
            resolved_version=data.get("resolved_version"),
            resolving_plugins=data.get("resolving_plugins", []),
            conflict_reason=data.get("conflict_reason"),
            resolved_at=datetime.fromisoformat(data.get("resolved_at")) if data.get("resolved_at") else None,
        )


class DependencyConflict:
    """Represents a dependency conflict."""
    
    def __init__(
        self,
        conflict_id: str,
        dependency_name: str,
        conflicting_versions: List[str],
        resolution: Optional[str] = None,
        description: str = "",
        suggested_fix: Optional[str] = None,
    ):
        self.conflict_id = conflict_id or str(uuid.uuid4())
        self.dependency_name = dependency_name
        self.conflicting_versions = conflicting_versions
        self.resolution = resolution
        self.description = description
        self.suggested_fix = suggested_fix
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "conflict_id": self.conflict_id,
            "dependency_name": self.dependency_name,
            "conflicting_versions": self.conflicting_versions,
            "resolution": self.resolution,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencyConflict":
        """Create from dictionary."""
        return cls(
            conflict_id=data.get("conflict_id"),
            dependency_name=data.get("dependency_name", ""),
            conflicting_versions=data.get("conflicting_versions", []),
            resolution=data.get("resolution"),
            description=data.get("description", ""),
            suggested_fix=data.get("suggested_fix"),
        )


class PluginDependency:
    """A plugin's dependency specification."""
    
    def __init__(
        self,
        plugin_id: str,
        dependencies: Optional[List[DependencySpec]] = None,
        conflict: Optional[DependencyConflict] = None,
    ):
        self.plugin_id = plugin_id
        self.dependencies = dependencies or []
        self.conflict = conflict
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plugin_id": self.plugin_id,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "conflict": self.conflict.to_dict() if self.conflict else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginDependency":
        """Create from dictionary."""
        from .models import DependencyConflict, DependencySpec
        
        conflict = DependencyConflict.from_dict(data.get("conflict")) if data.get("conflict") else None
        deps = [DependencySpec.from_dict(d) for d in data.get("dependencies", [])]
        
        return cls(
            plugin_id=data.get("plugin_id", ""),
            dependencies=deps,
            conflict=conflict,
        )