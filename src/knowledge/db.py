"""Database models and repository for the Memory System.

Reuses the repository-wide SQLAlchemy declarative ``Base`` from
``src.integrations.orm_models`` so the ``memories`` table lives in the same
metadata/engine as every other ORM model in the project (no second, parallel
ORM base). The Memory System refactor (``memory_system.py``) imports
``MemoryModel`` and ``MemoryRepository`` from here.
"""

from __future__ import annotations

import json
import uuid
from typing import List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from src._time import utc_now
from src.integrations.orm_models import Base


class MemoryModel(Base):
    """Persisted memory row.

    ``content`` is a JSON-encoded blob holding ``{key, value, confidence,
    metadata}``; ``context`` is a separate JSON blob used by the agent-experience
    sharing helpers. Keeping the user-facing fields JSON-encoded matches the
    refactor's ``_model_to_memory`` / serialization assumptions.
    """

    __tablename__ = "memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), nullable=False, index=True)
    memory_type = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    importance = Column(Float, nullable=True)
    session_id = Column(String(255), nullable=True, index=True)
    task_id = Column(String(255), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)


class MemoryRepository:
    """Async CRUD repository for :class:`MemoryModel`."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, model: MemoryModel) -> MemoryModel:
        """Persist (add + flush) a new memory row and return it."""
        self.session.add(model)
        await self.session.flush()
        return model

    async def delete(self, memory_id: str) -> None:
        """Delete a memory row by id (no-op if absent)."""
        stmt = select(MemoryModel).where(MemoryModel.id == memory_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            await self.session.delete(model)

    async def list_by_type(self, memory_type: str, limit: int = 10) -> List[MemoryModel]:
        """Return up to ``limit`` memories of a given type."""
        stmt = select(MemoryModel).where(MemoryModel.memory_type == memory_type).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


def serialize_content(key: str, value, confidence: float, metadata: Optional[dict]) -> str:
    """Helper to encode the JSON content blob stored on a memory row."""
    return json.dumps(
        {
            "key": key,
            "value": value,
            "confidence": confidence,
            "metadata": metadata or {},
        }
    )
