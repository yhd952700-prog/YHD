"""
ORM Integration Models for LiuHao AI OS

Defines the data model for ORM integration with SQLAlchemy.
Provides model base classes, session management, and migration utilities.
"""

from typing import Dict, Any, Optional, List, Type, TypeVar
import uuid
import os

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON, BigInteger,
    ForeignKey,
)
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.orm import sessionmaker as _sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (
    Session, registry as _registry, sessionmaker
)
from sqlalchemy import select, func, text, inspect
from src._time import utc_now

# Type variable for generic model operations
M = TypeVar("M", bound="BaseModel")

# SQLAlchemy base
Base = declarative_base()


class BaseModel:
    """
    Base model providing common fields for all ORM models.

    Provides:
    - UUID primary key
    - Created/updated timestamps
    - Soft delete support
    """
    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
            if not c.name.startswith("_")
        }

    @classmethod
    def from_dict(cls: Type[M], data: Dict[str, Any]) -> M:
        """Create model instance from dictionary."""
        instance = cls()
        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        return instance

    def soft_delete(self) -> None:
        """Perform soft delete."""
        self.is_deleted = True
        self.deleted_at = utc_now()


# Registry for model registration
mapper_registry = _registry()


class ModelMixin:
    """Mixin providing common model operations."""

    @classmethod
    def create(cls: Type[M], **kwargs) -> M:
        """Create a new model instance and add to session."""
        instance = cls(**kwargs)
        return instance

    @classmethod
    def get_by_id(cls: Type[M], session: Session, id_: int) -> Optional[M]:
        """Get a model instance by ID."""
        return session.get(cls, id_)

    @classmethod
    def get_by_uuid(cls: Type[M], session: Session, uuid_str: str) -> Optional[M]:
        """Get a model instance by UUID."""
        statement = select(cls).where(cls.uuid == uuid_str)
        result = session.execute(statement).scalar_one_or_none()
        return result

    @classmethod
    def filter(
        cls: Type[M],
        session: Session,
        **filters
    ) -> List[M]:
        """Filter model instances by criteria."""
        statement = select(cls).filter_by(**filters)
        return list(session.execute(statement).scalars().all())

    @classmethod
    def count(
        cls: Type[M],
        session: Session,
        **filters
    ) -> int:
        """Count model instances matching filters."""
        statement = select(func.count()).select_from(cls).filter_by(**filters)
        return session.execute(statement).scalar_one()


# ==================== Session Management ====================

class SessionManager:
    """
    Session management for ORM operations.

    Provides:
    - Session lifecycle management
    - Transaction handling
    - Session pooling considerations
    """

    def __init__(self, engine, autoflush: bool = True, autocommit: bool = False):
        self.engine = engine
        self.session_factory = sessionmaker(
            bind=engine,
            autoflush=autoflush,
            autocommit=autocommit,
        )

    def get_session(self) -> Session:
        """Get a new session."""
        return self.session_factory()

    def commit(self, session: Session) -> None:
        """Commit a session."""
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise

    def rollback(self, session: Session) -> None:
        """Rollback a session."""
        session.rollback()

    def execute(self, session: Session, statement) -> Any:
        """Execute a SQL statement."""
        return session.execute(statement)


# ==================== Migration Utilities ====================

def create_table(model, engine) -> None:
    """Create a database table for a model."""
    if not inspect(engine).has_table(model.__tablename__):
        model.__table__.create(engine)


def drop_table(model, engine) -> None:
    """Drop a database table for a model."""
    model.__table__.drop(engine, checkfirst=True)


def add_index(model, column_name: str, engine) -> None:
    """Add an index to a model's table."""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{model.__tablename__}_{column_name} "
                          f"ON {model.__tablename__}({column_name})"))


# ==================== Common Model Mixins ====================

class TimestampMixin:
    """Mixin that adds created/updated timestamps."""
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class ActiveQueryMixin:
    """Mixin that provides active (non-deleted) query filtering."""

    @classmethod
    def active_query(cls, session: Session):
        """Get query for non-deleted instances."""
        statement = select(cls).where(not cls.is_deleted)
        return session.execute(statement).scalars().all()

    @classmethod
    def active_count(cls, session: Session, **filters) -> int:
        """Count non-deleted instances matching filters."""
        statement = select(func.count()).select_from(cls).where(
            not cls.is_deleted
        ).filter_by(**filters)
        return session.execute(statement).scalar_one()


class SoftDeleteMixin:
    """Mixin that adds soft delete support with automatic filtering."""

    @classmethod
    def deleted_query(cls, session: Session):
        """Get query including soft-deleted instances."""
        statement = select(cls).where(True)  # Return all
        return session.execute(statement).scalars().all()

    @classmethod
    def with_soft_delete(
        cls, session: Session, include_deleted: bool = False
    ):
        """Get query with soft delete control."""
        if include_deleted:
            statement = select(cls)
        else:
            statement = select(cls).where(not cls.is_deleted)
        return session.execute(statement).scalars().all()


# ==================== Export Functions ====================

def init_models(engine, models: List[Type] = None) -> SessionManager:
    """
    Initialize ORM models with a database engine.

    Args:
        engine: SQLAlchemy engine
        models: List of model classes to register (None = auto-discover)

    Returns:
        SessionManager instance
    """
    # Create all tables
    if models:
        for model in models:
            model.__table__.create(engine, checkfirst=True)
    else:
        Base.metadata.create_all(engine)

    return SessionManager(engine)


# ==================== ORM Model Definitions ====================
# Table models matching the alembic migration (001_init_all_tables.py)
# All use UUID primary keys + BaseModel base class


def gen_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


# --- Security Models ---

class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(64), nullable=False, index=True)
    encrypted_key = Column(Text, nullable=False)
    scopes = Column("scopes", JSON, nullable=False, default="[]")  # JSON stored as text
    status = Column(String(50), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    rate_limit_rpm = Column(Integer, nullable=False, default=60)
    rate_limit_tpm = Column(Integer, nullable=False, default=10000)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")  # JSON
    rotated_from = Column(String(36), ForeignKey("api_keys.id"), nullable=True)
    rotation_count = Column(Integer, nullable=False, default=0)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<APIKey(name='{self.name}', status='{self.status}')>"


class JWTToken(Base):
    __tablename__ = "jwt_tokens"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    jti = Column(String(36), nullable=False, unique=True, index=True)
    token_type = Column(String(20), nullable=False)  # access, refresh
    subject = Column(String(255), nullable=False, index=True)
    issued_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(255), nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class RBACRole(Base):
    __tablename__ = "rbac_roles"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)


class RBACPermission(Base):
    __tablename__ = "rbac_permissions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    resource = Column(String(100), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class RBACRolePermission(Base):
    __tablename__ = "rbac_role_permissions"
    role_id = Column(String(36), ForeignKey("rbac_roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(String(36), ForeignKey("rbac_permissions.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class RBACUser(Base):
    __tablename__ = "rbac_users"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    username = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_superuser = Column(Boolean, nullable=False, default=False)
    last_login_at = Column(DateTime, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)

    def to_dict(self):
        d = self.__dict__.copy()
        d.pop("_sa_instance_state", None)
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k) or k in [c.name for c in cls.__table__.columns]})


class RBACUserRole(Base):
    __tablename__ = "rbac_user_roles"
    user_id = Column(String(36), ForeignKey("rbac_users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(String(36), ForeignKey("rbac_roles.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    event_type = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=utc_now, index=True)
    source = Column(String(100), nullable=False)
    user_id = Column(String(36), ForeignKey("rbac_users.id"), nullable=True, index=True)
    session_id = Column(String(36), nullable=True, index=True)
    severity = Column(String(20), nullable=False, default="medium")
    status = Column(String(20), nullable=False, default="success")
    message = Column(Text, nullable=True)
    details = Column(JSON, nullable=False, default="{}")
    request_id = Column(String(36), nullable=True, index=True)
    trace_id = Column(String(36), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)


# --- Observability Models ---

class Span(Base):
    __tablename__ = "spans"
    span_id = Column(String(36), primary_key=True, default=gen_uuid)
    trace_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kind = Column(String(20), nullable=False, default="internal")
    start_time = Column("start_time", BigInteger, nullable=False)  # nanoseconds as int stored as text
    end_time = Column("end_time", BigInteger, nullable=False)
    duration_ns = Column(BigInteger, nullable=True)
    attributes = Column(JSON, nullable=False, default="{}")
    status = Column(String(20), nullable=False, default="unset")
    status_message = Column(Text, nullable=True)
    parent_span_id = Column(String(36), nullable=True, index=True)
    trace_state = Column(Text, nullable=True)
    dropped_attributes_count = Column(Integer, nullable=False, default=0)
    events = Column(JSON, nullable=False, default="[]")
    links = Column(JSON, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class Metric(Base):
    __tablename__ = "metrics"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    unit = Column(String(50), nullable=True)
    kind = Column(String(20), nullable=False, default="gauge")
    aggregation_temporality = Column(String(20), nullable=False, default="delta")
    metric_type = Column(String(20), nullable=False, default="double")
    data_points = Column(JSON, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    alert_type = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    severity = Column(String(20), nullable=False, default="medium")
    message = Column(Text, nullable=True)
    source = Column(String(100), nullable=False)
    metric_name = Column(String(255), nullable=True)
    threshold_value = Column(Float, nullable=True)
    threshold_operator = Column(String(10), nullable=True)
    state = Column(String(20), nullable=False, default="firing")
    fired_at = Column(DateTime, nullable=False, default=utc_now, index=True)
    resolved_at = Column(DateTime, nullable=True)
    tags = Column(JSON, nullable=False, default="{}")
    extra = Column(JSON, nullable=False, default="{}")


# --- Plugin System Models ---

class Plugin(Base):
    __tablename__ = "plugins"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    plugin_type = Column(String(50), nullable=False, default="extension")
    author = Column(String(100), nullable=True)
    homepage = Column(String(255), nullable=True)
    license = Column(String(100), nullable=True)
    keywords = Column(JSON, nullable=False, default="[]")
    compatibility = Column(String(50), nullable=False, default=">=1.0.0")
    entry_points = Column(JSON, nullable=False, default="{}")
    tags = Column(JSON, nullable=False, default="[]")
    status = Column(String(20), nullable=False, default="pending")
    is_active = Column(Boolean, nullable=False, default=False)
    current_version_id = Column(String(36), nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)


class PluginVersion(Base):
    __tablename__ = "plugin_versions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    release_notes = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)
    upload_url = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    released_at = Column(DateTime, nullable=True)
    file_size = Column("file_size", BigInteger, nullable=False, default=0)
    md5_hash = Column(String(32), nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class SandboxExecution(Base):
    __tablename__ = "sandbox_executions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    sandbox_id = Column(String(36), nullable=False, index=True)
    entry_point = Column(String(255), nullable=False)
    arguments = Column(JSON, nullable=False, default="[]")
    working_directory = Column(String(500), nullable=True)
    environment_variables = Column(JSON, nullable=False, default="{}")
    resource_limits = Column(JSON, nullable=False, default="{}")
    timeout = Column(Integer, nullable=False, default=60)
    status = Column(String(20), nullable=False, default="pending")
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    output = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class SandboxResult(Base):
    __tablename__ = "sandbox_results"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    execution_id = Column(String(36), ForeignKey("sandbox_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    success = Column(Boolean, nullable=False)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    execution_time = Column(Float, nullable=True)
    resource_usage = Column(JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class PluginDependency(Base):
    __tablename__ = "plugin_dependencies"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    dependency_name = Column(String(100), nullable=False)
    version = Column(String(50), nullable=True)
    version_range = Column(String(100), nullable=True)
    dependency_type = Column(String(20), nullable=False, default="hard")
    optional = Column(Boolean, nullable=False, default=False)
    weak = Column(Boolean, nullable=False, default=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)


class PluginConflict(Base):
    __tablename__ = "plugin_conflicts"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    dependency_name = Column(String(100), nullable=False)
    conflicting_versions = Column(JSON, nullable=False, default="[]")
    resolution = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


# --- AI / Workflow Models ---

class Goal(Base):
    __tablename__ = "goals"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    description = Column(Text, nullable=False)
    priority = Column(String(20), nullable=False, default="medium")
    status = Column(String(20), nullable=False, default="pending")
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    goal_id = Column(String(36), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    task_type = Column(String(50), nullable=False, default="general")
    depends_on = Column(JSON, nullable=False, default="[]")
    status = Column(String(20), nullable=False, default="pending")
    assigned_agent = Column(String(100), nullable=True)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    workflow_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    input_data = Column(JSON, nullable=False, default="{}")
    output_data = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    checkpoint_data = Column(JSON, nullable=True)
    thread_id = Column(String(36), nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


# --- Knowledge / Memory Models ---

class MemoryItem(Base):
    __tablename__ = "memory_items"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    content = Column(Text, nullable=False)
    tier = Column(String(20), nullable=False, default="semantic")
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    access_count = Column(Integer, nullable=False, default=0)
    importance = Column(Float, nullable=False, default=0.5)
    tags = Column(JSON, nullable=False, default="[]")
    user_id = Column(String(36), nullable=False, default="default", index=True)
    session_id = Column(String(36), nullable=True, index=True)
    agent_id = Column(String(36), nullable=True, index=True)


class StorageEntry(Base):
    __tablename__ = "storage_entries"
    key = Column(String(500), primary_key=True)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    tags = Column(JSON, nullable=False, default="{}")
    ttl = Column(Float, nullable=True)


class DeploymentConfig(Base):
    __tablename__ = "deployment_configs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    target = Column(String(50), nullable=False)
    strategy = Column(String(20), nullable=False, default="rolling")
    docker_config = Column(JSON, nullable=False, default="{}")
    health_check = Column(JSON, nullable=True)
    environment = Column(JSON, nullable=False, default="{}")
    secrets = Column(JSON, nullable=False, default="{}")
    resources = Column(JSON, nullable=False, default="{}")
    autoscaling = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class DeploymentRelease(Base):
    __tablename__ = "deployment_releases"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    version = Column(String(50), nullable=False)
    commit_hash = Column(String(64), nullable=True)
    deployed_at = Column(DateTime, nullable=False, default=utc_now)
    deployed_by = Column(String(100), nullable=True)
    config_id = Column(String(36), ForeignKey("deployment_configs.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    logs = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)


class AIModel(Base):
    __tablename__ = "ai_models"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    capabilities = Column(JSON, nullable=False, default="{}")
    cost_per_1k_tokens = Column(Float, nullable=True)
    max_concurrency = Column(Integer, nullable=False, default=10)
    is_active = Column(Boolean, nullable=False, default=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        d = self.__dict__.copy()
        d.pop("_sa_instance_state", None)
        return d


class CostTracking(Base):
    __tablename__ = "cost_tracking"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    model = Column(String(100), nullable=False, index=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    timestamp = Column(DateTime, nullable=False, default=utc_now, index=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class DeviceAdapter(Base):
    __tablename__ = "device_adapters"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True, index=True)
    type = Column(String(50), nullable=False)  # filesystem, browser, shell
    config = Column(JSON, nullable=False, default="{}")
    status = Column(String(20), nullable=False, default="active")
    last_health_check = Column(DateTime, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class ConfigSnapshot(Base):
    __tablename__ = "config_snapshots"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    config_data = Column(JSON, nullable=False, default="{}")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class Deployment(Base):
    __tablename__ = "deployments"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    version = Column(String(50), nullable=False)
    commit_hash = Column(String(64), nullable=True)
    deployed_at = Column(DateTime, nullable=False, default=utc_now)
    deployed_by = Column(String(100), nullable=True)
    config_snapshot_id = Column(String(36), ForeignKey("config_snapshots.id"), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    rolled_back_from = Column(String(36), nullable=True)
    rollback_reason = Column(Text, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class Budget(Base):
    __tablename__ = "budgets"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    limit_usd = Column(Float, nullable=False)
    period = Column(String(20), nullable=False, default="monthly")
    alert_threshold = Column(Float, nullable=False, default=0.8)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


# ==================== Extra tables (defined by migrations 003-005) ====================
# These models mirror the tables created by alembic migrations 003-005 so that
# Base.metadata fully covers the migrated schema.

class PluginCategory(Base):
    __tablename__ = "plugin_categories"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    parent_id = Column(String(36), ForeignKey("plugin_categories.id"), nullable=True)
    status = Column(String(20), nullable=False, default="active")
    is_active = Column(Boolean, nullable=False, default=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class PluginConfiguration(Base):
    __tablename__ = "plugin_configurations"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(String(36), ForeignKey("plugin_categories.id"), nullable=True)
    config_key = Column(String(255), nullable=False)
    config_value = Column(Text, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    is_sensitive = Column(Boolean, nullable=False, default=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    version = Column(String(50), nullable=False, default="1.0.0")
    definition = Column(JSON, nullable=False, default="{}")
    status = Column(String(20), nullable=False, default="draft")
    is_active = Column(Boolean, nullable=False, default=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class WorkflowExecutionLog(Base):
    __tablename__ = "workflow_execution_logs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    workflow_definition_id = Column(String(36), ForeignKey("workflow_definitions.id"), nullable=False)
    trigger_type = Column(String(50), nullable=False)
    input_data = Column(JSON, nullable=False, default="{}")
    output_data = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, default=utc_now)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), nullable=False)
    session_key = Column(String(255), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="active")
    current_task = Column(String(255), nullable=True)
    context_data = Column(JSON, nullable=False, default="{}")
    last_heartbeat = Column(DateTime, nullable=False, default=utc_now)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=True)


class AgentTaskQueue(Base):
    __tablename__ = "agent_task_queue"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    task_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    priority = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


class WorkflowStepExecution(Base):
    __tablename__ = "workflow_step_executions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    workflow_execution_id = Column(String(36), ForeignKey("workflow_execution_logs.id"), nullable=False)
    step_name = Column(String(100), nullable=False)
    step_order = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, default=utc_now)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    output_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)


class AgentCapability(Base):
    __tablename__ = "agent_capabilities"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), ForeignKey("agent_sessions.agent_id"), nullable=False)
    capability_name = Column(String(100), nullable=False)
    capability_level = Column(String(20), nullable=False, default="basic")
    is_active = Column(Boolean, nullable=False, default=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    granted_at = Column(DateTime, nullable=False, default=utc_now)
    revoked_at = Column(DateTime, nullable=True)


class AgentAuditLog(Base):
    __tablename__ = "agent_audit_log"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(JSON, nullable=False, default="{}")
    timestamp = Column(DateTime, nullable=False, default=utc_now)
    ip_address = Column(String(45), nullable=True)


class ResourceConsumption(Base):
    __tablename__ = "resource_consumption"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), nullable=True)
    resource_type = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    unit = Column(String(20), nullable=False, default="units")
    timestamp = Column(DateTime, nullable=False, default=utc_now)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class StatusTransitionLog(Base):
    __tablename__ = "status_transition_logs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=False)
    from_status = Column(String(20), nullable=False)
    to_status = Column(String(20), nullable=False)
    transitioned_at = Column(DateTime, nullable=False, default=utc_now)
    transitioned_by = Column(String(100), nullable=True)
    reason = Column(Text, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class PluginExecutionHistory(Base):
    __tablename__ = "plugin_execution_history"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plugin_id = Column(String(36), ForeignKey("plugins.id"), nullable=False)
    execution_id = Column(String(36), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, default=utc_now)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, unique=True)
    preference_key = Column(String(100), nullable=False)
    preference_value = Column(Text, nullable=False)
    category = Column(String(50), nullable=False, default="general")
    is_public = Column(Boolean, nullable=False, default=False)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, unique=True)
    notification_type = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    channels = Column(JSON, nullable=False, default="[]")
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class SystemConfiguration(Base):
    __tablename__ = "system_configurations"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    config_key = Column(String(100), nullable=False, unique=True)
    config_value = Column(Text, nullable=False)
    config_type = Column(String(20), nullable=False, default="string")
    is_sensitive = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class AuditTrail(Base):
    __tablename__ = "audit_trail"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=True)
    action = Column(String(20), nullable=False)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    changed_at = Column(DateTime, nullable=False, default=utc_now)
    changed_by = Column(String(100), nullable=True)
    ip_address = Column(String(45), nullable=True)
    meta_data = Column("metadata", JSON, nullable=False, default="{}")


# ==================== Session & Engine Management ====================

_DEFAULT_DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./liuhao_ai_os.db")


def get_engine(db_url: Optional[str] = None):
    """Get or create the SQLAlchemy engine."""
    db_url = db_url or _DEFAULT_DB_URL
    return _create_engine(db_url, connect_args={"check_same_thread": False} if "sqlite" in db_url else {})


# Engine singleton
_engine = None


def get_session() -> Session:
    """Get a new database session (uses default engine)."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    SessionLocal = _sessionmaker(bind=_engine, autoflush=True, autocommit=False)
    return SessionLocal()


# SessionLocal for backwards compatibility
SessionLocal = get_session

# Collect all model classes for auto-registration
ALL_MODELS = [
    APIKey, JWTToken, RBACRole, RBACPermission, RBACRolePermission,
    RBACUser, RBACUserRole, AuditLog,
    Span, Metric, Alert,
    Plugin, PluginVersion, SandboxExecution, SandboxResult,
    PluginDependency, PluginConflict,
    Goal, Task, WorkflowExecution,
    MemoryItem, StorageEntry, DeploymentConfig, DeploymentRelease,
    AIModel, CostTracking, DeviceAdapter, ConfigSnapshot,
    Deployment, Budget,
    # Tables defined by migrations 003-005
    PluginCategory, PluginConfiguration, WorkflowDefinition, WorkflowExecutionLog,
    AgentSession, AgentTaskQueue, WorkflowStepExecution, AgentCapability,
    AgentAuditLog, ResourceConsumption, StatusTransitionLog,
    PluginExecutionHistory, UserPreference, NotificationSetting,
    SystemConfiguration, AuditTrail,
]

# Export Base for model inheritance
__all__ = [
    "Base",
    "BaseModel",
    "ModelMixin",
    "mapper_registry",
    "SessionManager",
    "create_table",
    "drop_table",
    "add_index",
    "init_models",
    "TimestampMixin",
    "ActiveQueryMixin",
    "SoftDeleteMixin",
    # Model classes
    "APIKey",
    "JWTToken",
    "RBACRole",
    "RBACPermission",
    "RBACRolePermission",
    "RBACUser",
    "RBACUserRole",
    "AuditLog",
    "Span",
    "Metric",
    "Alert",
    "Plugin",
    "PluginVersion",
    "SandboxExecution",
    "SandboxResult",
    "PluginDependency",
    "PluginConflict",
    "Goal",
    "Task",
    "WorkflowExecution",
    "MemoryItem",
    "StorageEntry",
    "DeploymentConfig",
    "DeploymentRelease",
    "AIModel",
    "CostTracking",
    "DeviceAdapter",
    "ConfigSnapshot",
    "Deployment",
    "Budget",
    # Tables defined by migrations 003-005
    "PluginCategory", "PluginConfiguration", "WorkflowDefinition", "WorkflowExecutionLog",
    "AgentSession", "AgentTaskQueue", "WorkflowStepExecution", "AgentCapability",
    "AgentAuditLog", "ResourceConsumption", "StatusTransitionLog",
    "PluginExecutionHistory", "UserPreference", "NotificationSetting",
    "SystemConfiguration", "AuditTrail",
    "ALL_MODELS",
    "gen_uuid",
]
