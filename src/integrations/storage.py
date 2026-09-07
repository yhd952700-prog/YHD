"""
ORM-based Storage for LiuHao AI OS

Provides repository pattern access to database-backed storage,
replacing JSON file storage with SQLAlchemy ORM.

Usage:
    from src.integrations.storage import get_storage

    storage = get_storage()
    key = storage.create_api_key(name="MyKey", scopes=["read"])
    model = storage.get_model("openai", "gpt-4")
"""

from typing import Any, Dict, List, Optional, Type, TypeVar, Generic
from datetime import datetime
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select, func, and_, or_, desc
import logging

from src.integrations.orm_models import (
    BaseModel, get_session, AIModel, APIKey, RBACUser, RBACRole, RBACPermission,
    RBACUserRole, RBACRolePermission, Plugin, Goal, Task, Span,
    MemoryItem, Metric, AuditLog,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Repository(Generic[T]):
    """Generic repository pattern for ORM models."""

    def __init__(self, session: Session, model_class: Type[T]):
        self._session = session
        self._model = model_class

    def get(self, id: Any) -> Optional[T]:
        """Get entity by primary key."""
        return self._session.get(self._model, id)

    def get_by_uuid(self, uuid: str) -> Optional[T]:
        """Get entity by UUID."""
        stmt = select(self._model).where(self._model.uuid == uuid)
        return self._session.execute(stmt).scalar_one_or_none()

    def get_all(self, limit: int = 100, offset: int = 0) -> List[T]:
        """Get all entities."""
        stmt = select(self._model).offset(offset).limit(limit)
        return self._session.execute(stmt).scalars().all()

    def get_active(self, limit: int = 100) -> List[T]:
        """Get non-deleted entities."""
        stmt = (
            select(self._model)
            .where(not self._model.is_deleted)
            .order_by(desc(self._model.created_at))
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()

    def filter_by(self, **kwargs) -> List[T]:
        """Filter entities by column values."""
        stmt = select(self._model)
        for key, value in kwargs.items():
            if hasattr(self._model, key):
                stmt = stmt.where(getattr(self._model, key) == value)
        return self._session.execute(stmt).scalars().all()

    def search(self, field: str, pattern: str, limit: int = 50) -> List[T]:
        """Search entities by field pattern (LIKE)."""
        stmt = (
            select(self._model)
            .where(getattr(self._model, field).ilike(f"%{pattern}%"))
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()

    def count(self, **kwargs) -> int:
        """Count entities matching criteria."""
        stmt = select(func.count()).select_from(self._model)
        for key, value in kwargs.items():
            if hasattr(self._model, key):
                stmt = stmt.where(getattr(self._model, key) == value)
        return self._session.execute(stmt).scalar()

    def create(self, **kwargs) -> T:
        """Create a new entity."""
        entity = self._model(**kwargs)
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def create_batch(self, items: List[Dict[str, Any]]) -> List[T]:
        """Create multiple entities in batch."""
        entities = [self._model(**item) for item in items]
        self._session.add_all(entities)
        self._session.commit()
        for e in entities:
            self._session.refresh(e)
        return entities

    def update(self, entity: T, **kwargs) -> T:
        """Update an entity."""
        for key, value in kwargs.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def delete(self, entity: T) -> None:
        """Soft delete an entity."""
        entity.is_deleted = True
        entity.deleted_at = datetime.now()
        self._session.commit()

    def hard_delete(self, entity: T) -> None:
        """Permanently delete an entity."""
        self._session.delete(entity)
        self._session.commit()

    def to_dict(self, entity: T) -> Dict[str, Any]:
        """Convert entity to dictionary."""
        if hasattr(entity, "to_dict"):
            return entity.to_dict()
        d = entity.__dict__.copy()
        d.pop("_sa_instance_state", None)
        return d


class StorageManager:
    """
    High-level storage manager providing repository access
    to all ORM-backed tables.

    Replaces JSON file storage with SQLAlchemy ORM.
    """

    def __init__(self, session: Optional[Session] = None):
        self._session = session or get_session()
        self._repos: Dict[str, Repository] = {}

    @property
    def session(self) -> Session:
        return self._session

    def get_repository(self, model_class: Type[T]) -> Repository[T]:
        """Get or create a repository for a model class."""
        name = model_class.__name__
        if name not in self._repos:
            self._repos[name] = Repository(self._session, model_class)
        return self._repos[name]

    # --- API Key Management ---

    @property
    def api_keys(self) -> Repository[APIKey]:
        return self.get_repository(APIKey)

    def list_active_keys(self) -> List[APIKey]:
        """List all active (non-expired, non-revoked) API keys."""
        stmt = (
            select(APIKey)
            .where(
                and_(
                    APIKey.status == "active",
                    or_(
                        APIKey.expires_at.is_(None),
                        APIKey.expires_at > datetime.now(),
                    ),
                )
            )
            .order_by(desc(APIKey.created_at))
        )
        return self._session.execute(stmt).scalars().all()

    def increment_key_usage(self, key_id: str) -> None:
        """Increment usage counter for an API key."""
        key = self.api_keys.get(key_id)
        if key:
            key.usage_count += 1
            key.last_used_at = datetime.now()
            self._session.commit()

    # --- AI Model Management ---

    @property
    def models(self) -> Repository[AIModel]:
        return self.get_repository(AIModel)

    def get_model(self, provider: str, model_name: str) -> Optional[AIModel]:
        """Get a specific model by provider and name."""
        stmt = (
            select(AIModel)
            .where(
                and_(
                    func.lower(AIModel.provider) == func.lower(provider),
                    func.lower(AIModel.model) == func.lower(model_name),
                )
            )
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def get_active_models(self) -> List[AIModel]:
        """Get all active models."""
        stmt = select(AIModel).where(AIModel.is_active)
        return self._session.execute(stmt).scalars().all()

    # --- RBAC Management ---

    @property
    def rbac_users(self) -> Repository[RBACUser]:
        return self.get_repository(RBACUser)

    @property
    def rbac_roles(self) -> Repository[RBACRole]:
        return self.get_repository(RBACRole)

    @property
    def rbac_permissions(self) -> Repository[RBACPermission]:
        return self.get_repository(RBACPermission)

    def get_user_roles(self, user_id: str) -> List[RBACRole]:
        """Get all roles for a user."""
        stmt = (
            select(RBACRole)
            .join(RBACUserRole, RBACUserRole.role_id == RBACRole.id)
            .where(RBACUserRole.user_id == user_id)
        )
        return self._session.execute(stmt).scalars().all()

    def get_role_permissions(self, role_id: str) -> List[RBACPermission]:
        """Get all permissions for a role."""
        stmt = (
            select(RBACPermission)
            .join(
                RBACRolePermission,
                RBACRolePermission.permission_id == RBACPermission.id,
            )
            .where(RBACRolePermission.role_id == role_id)
        )
        return self._session.execute(stmt).scalars().all()

    # --- Plugin Management ---

    @property
    def plugins(self) -> Repository[Plugin]:
        return self.get_repository(Plugin)

    def get_plugin_with_versions(self, plugin_name: str) -> Optional[Plugin]:
        """Get plugin with all versions loaded."""
        stmt = (
            select(Plugin)
            .options(selectinload(Plugin.versions))
            .where(Plugin.name == plugin_name)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    # --- Goal/Task Management ---

    @property
    def goals(self) -> Repository[Goal]:
        return self.get_repository(Goal)

    @property
    def tasks(self) -> Repository[Task]:
        return self.get_repository(Task)

    def get_user_goals(self, user_id: str) -> List[Goal]:
        """Get all goals for a user."""
        stmt = select(Goal).where(Goal.user_id == user_id)
        return self._session.execute(stmt).scalars().all()

    # --- Observability ---

    @property
    def spans(self) -> Repository[Span]:
        return self.get_repository(Span)

    @property
    def metrics(self) -> Repository[Metric]:
        return self.get_repository(Metric)

    @property
    def audit_logs(self) -> Repository[AuditLog]:
        return self.get_repository(AuditLog)

    def list_recent_spans(self, limit: int = 100) -> List[Span]:
        """Get recent spans ordered by start time."""
        stmt = (
            select(Span)
            .order_by(desc(Span.start_time))
            .limit(limit)
        )
        return self._session.execute(stmt).scalars().all()

    # --- Memory ---

    @property
    def memory_items(self) -> Repository[MemoryItem]:
        return self.get_repository(MemoryItem)

    def get_memories_by_user(self, user_id: str) -> List[MemoryItem]:
        """Get memories by user ID."""
        stmt = select(MemoryItem).where(MemoryItem.user_id == user_id)
        return self._session.execute(stmt).scalars().all()


# Singleton storage manager
_storage_manager: Optional[StorageManager] = None


def get_storage(session: Optional[Session] = None) -> StorageManager:
    """Get the singleton StorageManager."""
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = StorageManager(session=session)
    return _storage_manager


def reset_storage():
    """Reset the singleton storage manager (for testing)."""
    global _storage_manager
    if _storage_manager:
        _storage_manager._session.close()
    _storage_manager = None
