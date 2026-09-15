"""
Memory System

Short-term, working, and long-term memory management.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from ..core.errors import NotFoundError, PermissionDeniedError, ValidationError
from ..identity.audit import AuditAction, AuditService
from ..identity.models import User
from ..identity.rbac import Permission, RBACService


class MemoryType(str, Enum):
    """Memory type"""

    SHORT_TERM = "short_term"  # Current conversation/session
    WORKING = "working"  # Current task/workflow
    LONG_TERM = "long_term"  # Validated persistent knowledge


@dataclass
class Memory:
    """Memory entry"""

    id: str
    memory_type: MemoryType

    # Content
    key: str
    value: Any

    # Context
    user_id: str
    session_id: Optional[str] = None
    task_id: Optional[str] = None

    # Source
    source: str = "user"
    confidence: float = 1.0

    # Lifecycle
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: Optional[datetime] = None
    accessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    access_count: int = 0

    # Status
    is_active: bool = True

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "value": self.value,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "accessed_at": self.accessed_at.isoformat(),
            "access_count": self.access_count,
            "is_active": self.is_active,
            "metadata": self.metadata,
        }


class MemoryService:
    """
    Memory Service

    Manages short-term, working, and long-term memory.
    """

    # Default expiration times
    SHORT_TERM_EXPIRATION = timedelta(hours=1)
    WORKING_EXPIRATION = timedelta(hours=24)
    # Long-term memory does not expire by default

    def __init__(
        self,
        session,  # AsyncSession
        rbac_service: RBACService,
        audit_service: AuditService,
    ):
        # Phase 4: Database integration
        self.session = session
        from .db import MemoryRepository

        self.repository = MemoryRepository(session)

        self.rbac = rbac_service
        self.audit = audit_service

    def _model_to_memory(self, model) -> Memory:
        """Convert MemoryModel to Memory dataclass"""
        import json

        # Deserialize content
        content_data = json.loads(model.content)

        return Memory(
            id=str(model.id),
            user_id=str(model.user_id),
            memory_type=MemoryType(model.memory_type),
            key=content_data.get("key", ""),
            value=content_data.get("value"),
            session_id=model.session_id,
            task_id=model.task_id,
            source="database",
            confidence=content_data.get("confidence", 1.0),
            created_at=model.created_at,
            expires_at=model.expires_at,
            accessed_at=model.last_accessed_at,
            access_count=model.access_count or 0,
            metadata=content_data.get("metadata", {}),
        )

    async def store(
        self,
        user: User,
        memory_type: MemoryType,
        key: str,
        value: Any,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        source: str = "user",
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Memory:
        """
        Store a memory

        Args:
            user: User storing the memory
            memory_type: Memory type
            key: Memory key
            value: Memory value
            session_id: Session ID (for short-term memory)
            task_id: Task ID (for working memory)
            source: Memory source
            confidence: Confidence level
            metadata: Additional metadata

        Returns:
            Memory: Stored memory

        Raises:
            PermissionDeniedError: If user lacks permission
            ValidationError: If validation fails
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_WRITE):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="store_memory",
                resource_type="memory",
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_WRITE permission")

        # Validate key
        if not key or len(key.strip()) == 0:
            raise ValidationError("Memory key cannot be empty")

        # Validate confidence
        if confidence < 0 or confidence > 1:
            raise ValidationError("Confidence must be between 0 and 1")

        # Determine expiration
        expires_at = None
        if memory_type == MemoryType.SHORT_TERM:
            expires_at = datetime.now(UTC) + self.SHORT_TERM_EXPIRATION
        elif memory_type == MemoryType.WORKING:
            expires_at = datetime.now(UTC) + self.WORKING_EXPIRATION
        # Long-term memory does not expire

        # Generate memory ID
        from uuid import uuid4

        memory_id = str(uuid4())

        # Create memory
        memory = Memory(
            id=memory_id,
            memory_type=memory_type,
            key=key,
            value=value,
            user_id=user.id,
            session_id=session_id,
            task_id=task_id,
            source=source,
            confidence=confidence,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        # Store memory
        # Store in database
        # Serialize key/value to content
        import json

        from .db import MemoryModel

        content_data = {
            "key": key,
            "value": value,
            "confidence": confidence,
            "metadata": metadata or {},
        }

        model = MemoryModel(
            id=memory_id,
            # MemoryModel.user_id is a String column (cross-DB safe): coerce
            # the integer User.id to str — PostgreSQL strictly rejects int.
            user_id=str(user.id),
            memory_type=memory_type.value,
            content=json.dumps(content_data),
            importance=confidence,
            session_id=session_id,
            task_id=task_id,
            expires_at=expires_at,
        )
        model = await self.repository.create(model)
        await self.session.commit()
        memory = self._model_to_memory(model)

        # Audit log
        await self.audit.log(
            self.session,
            action=AuditAction.CREATE,
            status="success",
            user_id=user.id,
            resource_type="memory",
            resource_id=memory_id,
            details={
                "memory_type": memory_type.value,
                "key": key,
                "session_id": session_id,
                "task_id": task_id,
            },
        )

        return memory

    async def retrieve(
        self,
        user: User,
        key: str,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Memory]:
        """
        Retrieve a memory

        Args:
            user: User retrieving the memory
            key: Memory key
            memory_type: Filter by memory type
            session_id: Filter by session
            task_id: Filter by task

        Returns:
            Memory: Retrieved memory or None

        Raises:
            PermissionDeniedError: If user lacks permission
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_READ):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="retrieve_memory",
                resource_type="memory",
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_READ permission")

        # Clean expired memories first
        await self._clean_expired()

        # Query database for matching memory
        import json

        from sqlalchemy import and_, select

        from .db import MemoryModel

        # Build query conditions
        conditions = [MemoryModel.user_id == str(user.id)]

        if memory_type:
            conditions.append(MemoryModel.memory_type == memory_type.value)
        if session_id:
            conditions.append(MemoryModel.session_id == session_id)
        if task_id:
            conditions.append(MemoryModel.task_id == task_id)

        # Query
        stmt = select(MemoryModel).where(and_(*conditions))
        result = await self.session.execute(stmt)
        models = result.scalars().all()

        # Find memory with matching key
        for model in models:
            content_data = json.loads(model.content)
            if content_data.get("key") == key:
                # Update access count
                model.access_count = (model.access_count or 0) + 1
                model.last_accessed_at = datetime.now(UTC)
                await self.session.commit()

                return self._model_to_memory(model)

        return None

    async def list_memories(
        self,
        user: User,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> List[Memory]:
        """
        List memories

        Args:
            user: User requesting the list
            memory_type: Filter by memory type
            session_id: Filter by session
            task_id: Filter by task

        Returns:
            List[Memory]: List of memories

        Raises:
            PermissionDeniedError: If user lacks permission
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_READ):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="list_memories",
                resource_type="memory",
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_READ permission")

        # Clean expired memories first
        await self._clean_expired()

        # Query database
        from sqlalchemy import and_, select

        from .db import MemoryModel

        # Build query conditions
        conditions = [MemoryModel.user_id == str(user.id)]

        if memory_type:
            conditions.append(MemoryModel.memory_type == memory_type.value)
        if session_id:
            conditions.append(MemoryModel.session_id == session_id)
        if task_id:
            conditions.append(MemoryModel.task_id == task_id)

        # Query
        stmt = select(MemoryModel).where(and_(*conditions))
        result = await self.session.execute(stmt)
        models = result.scalars().all()

        # Convert to Memory objects
        memories = [self._model_to_memory(model) for model in models]

        return memories

    async def delete(
        self,
        user: User,
        memory_id: str,
    ) -> None:
        """
        Delete a memory

        Args:
            user: User deleting the memory
            memory_id: Memory ID

        Raises:
            NotFoundError: If memory not found
            PermissionDeniedError: If user lacks permission
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_WRITE):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="delete_memory",
                resource_type="memory",
                resource_id=memory_id,
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_WRITE permission")

        # Query database for memory
        from sqlalchemy import select

        from .db import MemoryModel

        stmt = select(MemoryModel).where(MemoryModel.id == memory_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            raise NotFoundError(f"Memory not found: {memory_id}")

        # Verify ownership
        if str(model.user_id) != str(user.id) and not self.rbac.is_admin(user):
            raise PermissionDeniedError("Cannot delete another user's memory")

        # Delete from database
        await self.repository.delete(memory_id)
        await self.session.commit()

        # Audit log
        await self.audit.log(
            self.session,
            action=AuditAction.DELETE,
            status="success",
            user_id=user.id,
            resource_type="memory",
            resource_id=memory_id,
        )

    async def clear_session(
        self,
        user: User,
        session_id: str,
    ) -> int:
        """
        Clear all memories for a session

        Args:
            user: User clearing the session
            session_id: Session ID

        Returns:
            int: Number of memories cleared
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_WRITE):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="clear_session",
                resource_type="memory",
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_WRITE permission")

        await self._clean_expired()

        cleared = 0

        # Query and delete memories for session
        from sqlalchemy import select

        from .db import MemoryModel

        stmt = select(MemoryModel).where(
            MemoryModel.user_id == str(user.id), MemoryModel.session_id == session_id
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()

        for model in models:
            await self.repository.delete(model.id)
            cleared += 1

        if cleared > 0:
            await self.session.commit()

        return cleared

    async def _clean_expired(self) -> int:
        """Clean expired memories (uses database)"""
        from datetime import UTC, datetime

        from sqlalchemy import select

        from .db import MemoryModel

        now = datetime.now(UTC)

        # Find expired memories from database
        stmt = select(MemoryModel).where(
            MemoryModel.expires_at.isnot(None), MemoryModel.expires_at < now
        )
        result = await self.session.execute(stmt)
        expired_models = result.scalars().all()

        # Delete expired memories
        cleaned = 0
        for model in expired_models:
            await self.repository.delete(model.id)
            cleaned += 1

        if cleaned > 0:
            await self.session.commit()

        return cleaned

    async def clear_task(
        self,
        user: User,
        task_id: str,
    ) -> int:
        """
        Clear all memories for a task

        Args:
            user: User clearing memories
            task_id: Task ID

        Returns:
            int: Number of memories cleared

        Raises:
            PermissionDeniedError: If user lacks permission
        """
        # Permission check
        if not self.rbac.has_permission(user, Permission.KNOWLEDGE_WRITE):
            await self.audit.log_permission_denied(
                user_id=user.id,
                action="clear_task",
                resource_type="memory",
            )
            raise PermissionDeniedError("User lacks KNOWLEDGE_WRITE permission")

        await self._clean_expired()

        cleared = 0

        # Query and delete memories for task
        from sqlalchemy import select

        from .db import MemoryModel

        stmt = select(MemoryModel).where(
            MemoryModel.user_id == str(user.id), MemoryModel.task_id == task_id
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()

        for model in models:
            await self.repository.delete(model.id)
            cleared += 1

        if cleared > 0:
            await self.session.commit()

        return cleared

    async def clean_expired(self) -> int:
        """
        Clean expired memories (public method)

        Returns:
            int: Number of expired memories cleaned
        """
        return await self._clean_expired()

    async def store_agent_experience(self, employee_id: str, task_type: str, result_summary: str):
        """Store agent execution experience to shared knowledge base."""
        try:
            import json
            from uuid import uuid4
            from .db import MemoryModel
            model = MemoryModel(
                id=str(uuid4()),
                user_id="0",
                memory_type=MemoryType.LONG_TERM.value,
                content=json.dumps({"key": task_type, "value": result_summary, "metadata": {"employee_id": employee_id, "task_type": task_type, "shared": True}}),
                context=json.dumps({"employee_id": employee_id, "task_type": task_type, "shared": True}),
            )
            result = await self.repository.create(model)
            return self._model_to_memory(result) if result else None
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to store agent experience: {e}")
            return None

    async def recall_agent_experience(self, task_type: str, limit: int = 5, requester_trust_score: float = 1.0):
        """Recall shared agent experience by task type with trust-based access control."""
        TRUST_THRESHOLD = 0.3
        if requester_trust_score < TRUST_THRESHOLD:
            return []
        try:
            models = await self.repository.list_by_type(MemoryType.LONG_TERM.value, limit=limit)
            results = []
            for m in models:
                import json as _json
                ctx = _json.loads(m.context) if isinstance(m.context, str) else (m.context or {})
                mem = self._model_to_memory(m)
                mem.metadata = ctx
                meta = mem.metadata or {}
                if meta.get("task_type") == task_type and meta.get("shared"):
                    results.append(mem)
            return results
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to recall agent experience: {e}")
            return []
