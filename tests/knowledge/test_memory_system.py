"""End-to-end tests for the Memory System refactor (src/knowledge/memory_system.py).

Uses an in-memory async SQLite database (shared connection via StaticPool) so
the DB-backed MemoryService is exercised for real — persist, retrieve, delete,
list — not merely mocked. Covers the permission/validation/audit contracts that
the refactor's callers depend on.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.knowledge.db import MemoryModel
from src.knowledge.memory_system import MemoryService, Memory, MemoryType
from src.core.errors import PermissionDeniedError, ValidationError, NotFoundError
from src.identity.models import User
from src.identity.rbac import Permission, RBACService
from src.identity.audit import AuditService, AuditAction


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: MemoryModel.__table__.create(sync, checkfirst=True))

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        rbac = RBACService()
        audit = AuditService()
        user = User(id="u1")
        rbac.grant(user, Permission.KNOWLEDGE_READ)
        rbac.grant(user, Permission.KNOWLEDGE_WRITE)
        service = MemoryService(session, rbac, audit)
        yield service, user, rbac, audit

    await engine.dispose()


async def test_store_returns_persisted_memory(env):
    service, user, _rbac, _audit = env
    mem = await service.store(user, MemoryType.SHORT_TERM, "project", "LiuHao OS")
    assert isinstance(mem, Memory)
    assert mem.id
    assert mem.key == "project"
    assert mem.value == "LiuHao OS"
    assert mem.memory_type == MemoryType.SHORT_TERM
    # Short-term memories get a ~1h expiration
    assert mem.expires_at is not None


async def test_store_and_retrieve_round_trips(env):
    service, user, _rbac, _audit = env
    stored = await service.store(user, MemoryType.LONG_TERM, "fact", {"answer": 42})
    fetched = await service.retrieve(user, "fact", memory_type=MemoryType.LONG_TERM)
    assert fetched is not None
    assert fetched.id == stored.id
    assert fetched.value == {"answer": 42}
    # retrieve bumps access_count
    assert fetched.access_count >= 1


async def test_store_requires_write_permission(env):
    service, user, rbac, audit = env
    # Revoke write; keep read.
    rbac.revoke(user, Permission.KNOWLEDGE_WRITE)
    with pytest.raises(PermissionDeniedError):
        await service.store(user, MemoryType.LONG_TERM, "k", "v")
    denied = [e for e in audit.entries if e.denied]
    assert denied, "permission-denied event should be audited"
    assert denied[0].resource_type == "memory"


async def test_retrieve_requires_read_permission(env):
    service, user, rbac, _audit = env
    await service.store(user, MemoryType.LONG_TERM, "k", "v")
    rbac.revoke(user, Permission.KNOWLEDGE_READ)
    with pytest.raises(PermissionDeniedError):
        await service.retrieve(user, "k")


async def test_empty_key_is_rejected(env):
    service, user, _rbac, _audit = env
    with pytest.raises(ValidationError):
        await service.store(user, MemoryType.LONG_TERM, "   ", "v")


async def test_confidence_out_of_range_is_rejected(env):
    service, user, _rbac, _audit = env
    with pytest.raises(ValidationError):
        await service.store(user, MemoryType.LONG_TERM, "k", "v", confidence=1.5)
    with pytest.raises(ValidationError):
        await service.store(user, MemoryType.LONG_TERM, "k", "v", confidence=-0.1)


async def test_delete_removes_memory(env):
    service, user, _rbac, _audit = env
    mem = await service.store(user, MemoryType.LONG_TERM, "k", "v")
    await service.delete(user, mem.id)
    assert await service.retrieve(user, "k") is None


async def test_delete_missing_raises_not_found(env):
    service, user, _rbac, _audit = env
    with pytest.raises(NotFoundError):
        await service.delete(user, "does-not-exist")


async def test_list_memories_filters_by_user_and_type(env):
    service, user, _rbac, _audit = env
    await service.store(user, MemoryType.SHORT_TERM, "s1", "x")
    await service.store(user, MemoryType.LONG_TERM, "l1", "y")
    await service.store(user, MemoryType.LONG_TERM, "l2", "z")
    long_term = await service.list_memories(user, memory_type=MemoryType.LONG_TERM)
    assert {m.key for m in long_term} == {"l1", "l2"}


async def test_audit_records_create_event(env):
    service, user, _rbac, audit = env
    await service.store(user, MemoryType.WORKING, "k", "v")
    creates = [e for e in audit.entries if e.action == AuditAction.CREATE]
    assert creates
    assert creates[0].resource_type == "memory"
    assert creates[0].user_id == user.id


async def test_store_agent_experience_persists_shared_memory(env):
    service, user, _rbac, _audit = env
    result = await service.store_agent_experience("emp1", "summarize", "did it well")
    assert result is not None
    assert result.memory_type == MemoryType.LONG_TERM
    recalled = await service.recall_agent_experience("summarize", limit=5)
    assert any(m.metadata.get("employee_id") == "emp1" for m in recalled)
