"""
Plugin Marketplace Models for LiuHao AI OS

Defines the data model for a plugin marketplace platform.
Provides model classes for plugin metadata, versions, and marketplace operations.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid


class PluginStatus:
    """Plugin status enumeration."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    UNPUBLISHED = "unpublished"


class PluginType:
    """Plugin type enumeration."""
    CORE = "core"
    INTEGRATION = "integration"
    EXTENSION = "extension"
    PROVIDER = "provider"
    MONITOR = "monitor"


class PluginMetadata:
    """Metadata for a plugin."""
    
    def __init__(
        self,
        name: str,
        version: str,
        description: str,
        plugin_type: str = PluginType.EXTENSION,
        author: str = "",
        homepage: str = "",
        license: str = "",
        keywords: List[str] = None,
        compatibility: str = ">=1.0.0",
        entry_points: Optional[Dict[str, Any]] = None,
        tags: List[str] = None,
    ):
        self.name = name
        self.version = version
        self.description = description
        self.plugin_type = plugin_type
        self.author = author
        self.homepage = homepage
        self.license = license
        self.keywords = keywords or []
        self.compatibility = compatibility
        self.entry_points = entry_points or {}
        self.tags = tags or []
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "plugin_type": self.plugin_type,
            "author": self.author,
            "homepage": self.homepage,
            "license": self.license,
            "keywords": self.keywords,
            "compatibility": self.compatibility,
            "entry_points": self.entry_points,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginMetadata":
        """Create from dictionary."""
        metadata = cls(
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            plugin_type=data.get("plugin_type", PluginType.EXTENSION),
            author=data.get("author", ""),
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            keywords=data.get("keywords", []),
            compatibility=data.get("compatibility", ">=1.0.0"),
            entry_points=data.get("entry_points", {}),
            tags=data.get("tags", []),
        )
        metadata.created_at = datetime.fromisoformat(data.get("created_at", datetime.now().isoformat()))
        metadata.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        return metadata


class PluginVersion:
    """Version information for a plugin."""
    
    def __init__(
        self,
        version: str,
        version_id: str,
        release_notes: str,
        changelog: str,
        upload_url: str,
        status: str = PluginStatus.PENDING,
        released_at: Optional[datetime] = None,
        file_size: int = 0,
        md5_hash: str = "",
    ):
        self.version = version
        self.version_id = version_id or str(uuid.uuid4())
        self.release_notes = release_notes
        self.changelog = changelog
        self.upload_url = upload_url
        self.status = status
        self.released_at = released_at or datetime.now()
        self.file_size = file_size
        self.md5_hash = md5_hash
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "version": self.version,
            "version_id": self.version_id,
            "release_notes": self.release_notes,
            "changelog": self.changelog,
            "upload_url": self.upload_url,
            "status": self.status,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "file_size": self.file_size,
            "md5_hash": self.md5_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginVersion":
        """Create from dictionary."""
        return cls(
            version=data.get("version", "0.1.0"),
            version_id=data.get("version_id"),
            release_notes=data.get("release_notes", ""),
            changelog=data.get("changelog", ""),
            upload_url=data.get("upload_url", ""),
            status=data.get("status", PluginStatus.PENDING),
            released_at=datetime.fromisoformat(data.get("released_at")) if data.get("released_at") else None,
            file_size=data.get("file_size", 0),
            md5_hash=data.get("md5_hash", ""),
        )


class Plugin:
    """Core plugin model."""
    
    def __init__(
        self,
        plugin_id: str,
        name: str,
        description: str,
        metadata: PluginMetadata,
        current_version: PluginVersion,
        status: str = PluginStatus.PENDING,
        is_active: bool = False,
        tags: List[str] = None,
        dependencies: List[str] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ):
        self.plugin_id = plugin_id or str(uuid.uuid4())
        self.name = name
        self.description = description
        self.metadata = metadata
        self.current_version = current_version
        self.status = status
        self.is_active = is_active
        self.tags = tags or []
        self.dependencies = dependencies or []
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata.to_dict() if self.metadata else {},
            "current_version": self.current_version.to_dict() if self.current_version else {},
            "status": self.status,
            "is_active": self.is_active,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plugin":
        """Create from dictionary."""
        metadata = PluginMetadata.from_dict(data.get("metadata", {}))
        current_version = PluginVersion.from_dict(data.get("current_version", {}))
        
        return cls(
            plugin_id=data.get("plugin_id"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            metadata=metadata,
            current_version=current_version,
            status=data.get("status", PluginStatus.PENDING),
            is_active=data.get("is_active", False),
            tags=data.get("tags", []),
            dependencies=data.get("dependencies", []),
            created_at=datetime.fromisoformat(data.get("created_at")) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data.get("updated_at")) if data.get("updated_at") else None,
        )