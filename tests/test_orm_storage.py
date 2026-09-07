"""
Tests for ORM/Storage integration in LiuHao AI OS

Tests the 46 model classes (30 original + 16 added to mirror migrations 003-005)
and StorageManager repository pattern.
All tests use the existing SQLite database (liuhao_ai_os.db) with data cleanup.
"""

import pytest
import os
from datetime import datetime

from src.integrations.orm_models import (
    get_session, ALL_MODELS, gen_uuid,
    APIKey, JWTToken, RBACRole, RBACPermission,
    RBACUser, RBACRolePermission, RBACUserRole,
    AuditLog, Span, Metric, Alert,
    Plugin, PluginVersion, SandboxExecution, SandboxResult,
    PluginDependency, PluginConflict,
    Goal, Task, WorkflowExecution,
    MemoryItem, StorageEntry, DeploymentConfig, DeploymentRelease,
    AIModel, CostTracking, DeviceAdapter, ConfigSnapshot,
    Deployment, Budget,
)
from src.integrations.storage import get_storage, StorageManager, Repository, reset_storage


@pytest.fixture(autouse=True)
def cleanup_db():
    """Clean up test data before and after each test."""
    session = get_session()
    # Clean up any leftover test data
    for model in ALL_MODELS:
        try:
            session.query(model).filter(
                model.__table__.c.name.like("%Test%") if hasattr(model.__table__.c, 'name') else model.__table__.c.id.like("%test%")
            ).delete(synchronize_session=False)
        except:
            pass
    session.commit()
    session.close()
    
    yield
    
    # Cleanup after test
    session = get_session()
    for model in ALL_MODELS:
        try:
            session.query(model).delete(synchronize_session=False)
        except:
            pass
    session.commit()
    session.close()


class TestModelsImport:
    """Test that all 30 models are properly defined."""

    def test_all_models_loadable(self):
        """All 46 model classes (original 30 + 16 migration-mirrored) can be imported."""
        assert len(ALL_MODELS) == 46

    def test_all_models_have_tablename(self):
        """Every model has a __tablename__."""
        for model in ALL_MODELS:
            assert hasattr(model, "__tablename__")
            assert model.__tablename__ is not None

    def test_all_models_registered_in_base(self):
        """All models are registered in Base metadata."""
        from src.integrations.orm_models import Base
        for model in ALL_MODELS:
            assert model.__tablename__ in Base.metadata.tables

    def test_model_columns_match(self):
        """Verify key models have expected columns."""
        assert "id" in APIKey.__table__.columns
        assert "name" in APIKey.__table__.columns
        assert "key_hash" in APIKey.__table__.columns
        assert "status" in APIKey.__table__.columns

        assert "name" in AIModel.__table__.columns
        assert "provider" in AIModel.__table__.columns
        assert "model" in AIModel.__table__.columns
        assert "is_active" in AIModel.__table__.columns

        assert "username" in RBACUser.__table__.columns
        assert "email" in RBACUser.__table__.columns
        assert "is_active" in RBACUser.__table__.columns

        assert "name" in Plugin.__table__.columns
        assert "plugin_type" in Plugin.__table__.columns
        assert "is_active" in Plugin.__table__.columns

        assert "description" in Goal.__table__.columns
        assert "priority" in Goal.__table__.columns
        assert "status" in Goal.__table__.columns

        assert "trace_id" in Span.__table__.columns
        assert "name" in Span.__table__.columns


class TestUUIDGenerator:
    """Test UUID generation."""

    def test_gen_uuid_format(self):
        """UUIDs are valid format."""
        from src.integrations.orm_models import gen_uuid
        import uuid as uuid_mod
        result = gen_uuid()
        parsed = uuid_mod.UUID(result)
        assert str(parsed) == result

    def test_gen_uuid_unique(self):
        """Generated UUIDs are unique."""
        ids = {gen_uuid() for _ in range(100)}
        assert len(ids) == 100


class TestRepositoryPattern:
    """Test the generic repository pattern."""

    @pytest.fixture
    def storage(self):
        reset_storage()
        return get_storage()

    def test_repository_create(self, storage):
        """Repository can create entities."""
        key = storage.api_keys.create(
            name="RepoTestKey",
            key_hash="testhash",
            encrypted_key="encrypted",
            scopes="[]",
            status="active",
        )
        assert key.id is not None
        assert key.name == "RepoTestKey"
        assert key.status == "active"

    def test_repository_get(self, storage):
        """Repository can retrieve entities by ID."""
        key = storage.api_keys.create(
            name="GetTestKey",
            key_hash="gethash",
            encrypted_key="enc",
            scopes="[]",
        )
        retrieved = storage.api_keys.get(key.id)
        assert retrieved is not None
        assert retrieved.name == "GetTestKey"

    def test_repository_update(self, storage):
        """Repository can update entities."""
        key = storage.api_keys.create(
            name="UpdateTestKey",
            key_hash="updatehash",
            encrypted_key="enc",
            scopes="[]",
            status="active",
        )
        storage.api_keys.update(key, status="deprecated", usage_count=5)
        assert key.status == "deprecated"
        assert key.usage_count == 5

    def test_repository_delete(self, storage):
        """Repository can soft delete entities."""
        key = storage.api_keys.create(
            name="DeleteTestKey",
            key_hash="deletehash",
            encrypted_key="enc",
            scopes="[]",
        )
        storage.api_keys.delete(key)
        assert key.is_deleted is True

    def test_repository_count(self, storage):
        """Repository can count entities."""
        storage.api_keys.create(
            name="CountTest1", key_hash="h1", encrypted_key="e1", scopes="[]"
        )
        storage.api_keys.create(
            name="CountTest2", key_hash="h2", encrypted_key="e2", scopes="[]"
        )
        count = storage.api_keys.count()
        assert count >= 2

    def test_repository_filter_by(self, storage):
        """Repository can filter entities."""
        storage.api_keys.create(
            name="FilterActive", key_hash="h1", encrypted_key="e1", scopes="[]", status="active"
        )
        storage.api_keys.create(
            name="FilterInactive", key_hash="h2", encrypted_key="e2", scopes="[]", status="revoked"
        )
        active = storage.api_keys.filter_by(status="active")
        names = [k.name for k in active]
        assert "FilterActive" in names
        assert "FilterInactive" not in names


class TestStorageManager:
    """Test StorageManager high-level operations."""

    @pytest.fixture
    def storage(self):
        reset_storage()
        return get_storage()

    def test_get_repository_singleton(self, storage):
        """get_repository returns consistent singleton per model."""
        repo1 = storage.get_repository(APIKey)
        repo2 = storage.get_repository(APIKey)
        assert repo1 is repo2

    def test_list_active_keys(self, storage):
        """list_active_keys filters by status and expiration."""
        storage.api_keys.create(
            name="ActiveKey", key_hash="h1", encrypted_key="e1", scopes="[]",
            status="active",
        )
        storage.api_keys.create(
            name="RevokedKey", key_hash="h2", encrypted_key="e2", scopes="[]",
            status="revoked",
        )
        active = storage.list_active_keys()
        names = [k.name for k in active]
        assert "ActiveKey" in names
        assert "RevokedKey" not in names

    def test_get_model(self, storage):
        """get_model finds model by provider and name (case-insensitive)."""
        storage.models.create(
            name="gpt-4", provider="openai", model="gpt-4"
        )
        found = storage.get_model("OPENAI", "GPT-4")
        assert found is not None
        assert found.name == "gpt-4"

    def test_get_active_models(self, storage):
        """get_active_models returns only active models."""
        storage.models.create(name="ActiveModel", provider="test", model="test-1", is_active=True)
        storage.models.create(name="InactiveModel", provider="test", model="test-2", is_active=False)
        active = storage.get_active_models()
        names = [m.name for m in active]
        assert "ActiveModel" in names
        assert "InactiveModel" not in names

    def test_get_user_roles(self, storage):
        """get_user_roles returns roles for a user."""
        role = storage.get_repository(RBACRole).create(name="admin", description="Admin role")
        user = storage.rbac_users.create(username="testuser", email="test@test.com")
        storage._session.add(RBACUserRole(user_id=user.id, role_id=role.id))
        storage._session.commit()

        roles = storage.get_user_roles(user.id)
        assert len(roles) == 1
        assert roles[0].name == "admin"

    def test_get_role_permissions(self, storage):
        """get_role_permissions returns permissions for a role."""
        perm = storage.rbac_permissions.create(
            name="read_api", description="Read access", resource="api", action="read"
        )
        role = storage.rbac_roles.create(name="reader", description="Reader role")
        storage._session.add(RBACRolePermission(role_id=role.id, permission_id=perm.id))
        storage._session.commit()

        perms = storage.get_role_permissions(role.id)
        assert len(perms) == 1
        assert perms[0].name == "read_api"


class TestCRUDOperations:
    """Test CRUD for key entity types."""

    @pytest.fixture
    def storage(self):
        reset_storage()
        return get_storage()

    def test_api_key_full_lifecycle(self, storage):
        """Full CRUD lifecycle for API keys."""
        # Create
        key = storage.api_keys.create(
            name="LifecycleKey", key_hash="hash123",
            encrypted_key="enc", scopes='["read"]', status="active",
        )
        # Read
        retrieved = storage.api_keys.get(key.id)
        assert retrieved.name == "LifecycleKey"
        # Update
        storage.api_keys.update(key, status="revoked", usage_count=42)
        assert key.status == "revoked"
        assert key.usage_count == 42
        # Delete
        storage.api_keys.delete(key)
        assert key.is_deleted is True

    def test_ai_model_crud(self, storage):
        """Full CRUD lifecycle for AI models."""
        model = storage.models.create(
            name="LifecycleModel", provider="openai", model="test-model",
            cost_per_1k_tokens=0.03, max_concurrency=5,
        )
        retrieved = storage.models.get(model.id)
        assert retrieved is not None
        assert retrieved.provider == "openai"

        storage.models.update(model, is_active=False)
        assert model.is_active is False

        storage.models.delete(model)
        assert model.is_deleted is True

    def test_rbac_user_crud(self, storage):
        """Full CRUD lifecycle for RBAC users."""
        user = storage.rbac_users.create(
            username="lifecycle_user", email="lifecycle@test.com",
            is_active=True, is_superuser=False,
        )
        retrieved = storage.rbac_users.get(user.id)
        assert retrieved.username == "lifecycle_user"

        storage.rbac_users.update(user, is_superuser=True)
        assert user.is_superuser is True

        storage.rbac_users.delete(user)
        assert user.is_deleted is True

    def test_plugin_crud(self, storage):
        """Full CRUD lifecycle for plugins."""
        plugin = storage.plugins.create(
            name="test-plugin", description="A test plugin",
            plugin_type="extension", status="active", is_active=True,
        )
        retrieved = storage.plugins.get(plugin.id)
        assert retrieved is not None
        assert retrieved.name == "test-plugin"

    def test_goal_and_task_crud(self, storage):
        """Goal and Task CRUD with FK relationship."""
        goal = storage.goals.create(
            description="Test goal", priority="high", status="pending"
        )
        task = storage.tasks.create(
            goal_id=goal.id, description="Test task", task_type="general"
        )
        retrieved = storage.tasks.get(task.id)
        assert retrieved.goal_id == goal.id

    def test_memory_item_crud(self, storage):
        """Memory item CRUD."""
        item = storage.memory_items.create(
            content="Test memory content", tier="semantic",
            importance=0.8, user_id="user-123",
        )
        retrieved = storage.memory_items.get(item.id)
        assert retrieved is not None
        assert retrieved.content == "Test memory content"

    def test_audit_log_crud(self, storage):
        """Audit log CRUD."""
        log = storage._session.query(AuditLog).get  # Just verify model works
        entry = storage.get_repository(AuditLog).create(
            event_type="test_event", source="test_source",
            severity="low", status="success", message="Test message",
        )
        retrieved = storage.get_repository(AuditLog).get(entry.id)
        assert retrieved.event_type == "test_event"

    def test_metric_crud(self, storage):
        """Metric CRUD."""
        metric = storage.metrics.create(
            name="test_metric", description="A test metric",
            unit="count", kind="gauge",
        )
        retrieved = storage.metrics.get(metric.id)
        assert retrieved.name == "test_metric"

    def test_span_crud(self, storage):
        """Span (tracing) CRUD."""
        span = storage.spans.repository = storage.get_repository(Span)
        entry = span.create(
            trace_id="trace-123", name="test_span", kind="internal",
            start_time="1000", end_time="2000",
        )
        retrieved = span.get(entry.span_id)
        assert retrieved.trace_id == "trace-123"
