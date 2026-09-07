"""
Checkpoint Manager for LiuHao AI OS Workflow

Manages LangGraph checkpoint persistence with:
- SQLite storage for checkpoint data
- Checkpoint creation, retrieval, and deletion
- Checkpoint types (NODE, EDGE, FULL)
- Versioned checkpoint storage
- Checkpoint expiration and cleanup
"""

import json
import sqlite3
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


class CheckpointType:
    """Types of checkpoints supported."""
    NODE = "node"          # Per-node checkpoint
    EDGE = "edge"          # Per-edge checkpoint
    FULL = "full"          # Full workflow snapshot


class CheckpointStatus:
    """Checkpoint status values."""
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"


@dataclass
class CheckpointMetadata:
    """Metadata for a checkpoint."""
    checkpoint_id: str
    workflow_id: str
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    checkpoint_type: str = CheckpointType.NODE
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    status: str = CheckpointStatus.ACTIVE
    description: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class CheckpointData:
    """The actual checkpoint data stored."""
    workflow_state: Dict[str, Any]
    input_data: Dict[str, Any]
    output_data: Dict[str, Any]
    node_stack: List[str]
    current_node: str
    remaining_edges: List[str]
    done: bool
    error: Optional[str] = None


# Type alias for checkpoint storage
CheckpointStore = Dict[str, CheckpointData]


class CheckpointManager:
    """
    Manages LangGraph checkpoint persistence using SQLite.

    Features:
    - Persistent checkpoint storage
    - Checkpoint versioning
    - Automatic expiration
    - Checkpoint listing and filtering
    - Checkpoint cleanup
    """

    def __init__(self, db_path: Optional[str] = None,
                 expiration_days: int = 30):
        """
        Initialize Checkpoint Manager.

        Args:
            db_path: Path to SQLite database file
            expiration_days: Default checkpoint expiration in days
        """
        self.db_path = Path(db_path) if db_path else Path(
            "D:\\LiuHao-AI-OS\\data\\workflow_checkpoints.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.expiration_days = expiration_days

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database schema."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    node_id TEXT,
                    edge_id TEXT,
                    checkpoint_type TEXT NOT NULL DEFAULT 'node',
                    workflow_state TEXT NOT NULL,
                    input_data TEXT,
                    output_data TEXT,
                    node_stack TEXT,
                    current_node TEXT,
                    remaining_edges TEXT,
                    done INTEGER DEFAULT 0,
                    error TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    status TEXT NOT NULL DEFAULT 'active',
                    description TEXT,
                    tags TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflow_id
                ON checkpoints(workflow_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at
                ON checkpoints(expires_at)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON checkpoints(status)
            """)

            conn.commit()

    # ==================== Checkpoint Creation ====================

    def create_checkpoint(
        self,
        workflow_id: str,
        workflow_state: Dict[str, Any],
        input_data: Dict[str, Any],
        output_data: Dict[str, Any],
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        checkpoint_type: str = CheckpointType.NODE,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Create a new checkpoint.

        Args:
            workflow_id: Unique workflow identifier
            workflow_state: Current workflow state dict
            input_data: Input data to the workflow
            output_data: Output data from the workflow
            node_id: Current node ID (for NODE type)
            edge_id: Current edge ID (for EDGE type)
            checkpoint_type: Type of checkpoint (NODE/EDGE/FULL)
            description: Human-readable description
            tags: Optional tags for filtering

        Returns:
            checkpoint_id of the created checkpoint
        """
        checkpoint_id = f"ckpt_{int(time.time() * 1000)}_{uuid4().hex[:8]}"

        # Calculate expiration
        expires_at = time.time() + (self.expiration_days * 86400) if self.expiration_days else None

        # Serialize data to JSON
        workflow_state_json = json.dumps(workflow_state, default=self._json_default)
        input_data_json = json.dumps(input_data, default=self._json_default)
        output_data_json = json.dumps(output_data, default=self._json_default)

        # Serialize lists
        node_stack_json = json.dumps(node_id if node_id else [], default=self._json_default)
        remaining_edges_json = json.dumps(edge_id if edge_id else [], default=self._json_default)

        # Serialize tags
        tags_json = json.dumps(tags if tags else [], default=self._json_default)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                INSERT INTO checkpoints
                (checkpoint_id, workflow_id, node_id, edge_id, checkpoint_type,
                 workflow_state, input_data, output_data, node_stack,
                 current_node, remaining_edges, done, error, created_at,
                 expires_at, status, description, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                checkpoint_id,
                workflow_id,
                node_id,
                edge_id,
                checkpoint_type,
                workflow_state_json,
                input_data_json,
                output_data_json,
                node_stack_json,
                node_id or "",
                remaining_edges_json,
                False,  # done
                None,   # error
                time.time(),
                expires_at,
                CheckpointStatus.ACTIVE,
                description,
                tags_json,
            ))

            conn.commit()

        logger.info(f"Checkpoint created: {checkpoint_id} for workflow {workflow_id}")
        return checkpoint_id

    @staticmethod
    def _json_default(obj):
        """JSON serializer for objects not serializable by default json code."""
        if hasattr(obj, 'items'):
            return dict(obj)
        if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
            return list(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    # ==================== Checkpoint Retrieval ====================

    def get_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointData]:
        """
        Retrieve a checkpoint by ID.

        Args:
            checkpoint_id: The checkpoint ID to retrieve

        Returns:
            CheckpointData object or None if not found/expired
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        # Check expiration
        if row['expires_at'] and time.time() > row['expires_at']:
            # Mark as expired
            self._mark_expired(checkpoint_id)
            return None

        # Deserialize data
        try:
            workflow_state = json.loads(row['workflow_state']) if row['workflow_state'] else {}
            input_data = json.loads(row['input_data']) if row['input_data'] else {}
            output_data = json.loads(row['output_data']) if row['output_data'] else {}
            node_stack = json.loads(row['node_stack']) if row['node_stack'] else []
            remaining_edges = json.loads(row['remaining_edges']) if row['remaining_edges'] else []
            json.loads(row['tags']) if row['tags'] else []

            return CheckpointData(
                workflow_state=workflow_state,
                input_data=input_data,
                output_data=output_data,
                node_stack=node_stack,
                current_node=row['current_node'] or "",
                remaining_edges=remaining_edges,
                done=bool(row['done']),
                error=row['error'],
            )
        except json.JSONDecodeError as e:
            logger.error(f"Failed to deserialize checkpoint {checkpoint_id}: {e}")
            return None

    def list_checkpoints(
        self,
        workflow_id: Optional[str] = None,
        checkpoint_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List checkpoints with filtering.

        Args:
            workflow_id: Filter by workflow ID
            checkpoint_type: Filter by type (NODE/EDGE/FULL)
            status: Filter by status (active/expired/archived)
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of checkpoint metadata dicts
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT checkpoint_id, workflow_id, node_id, edge_id, checkpoint_type, "
            query += "created_at, expires_at, status, description, tags FROM checkpoints WHERE 1=1"
            params = []

            if workflow_id:
                query += " AND workflow_id = ?"
                params.append(workflow_id)

            if checkpoint_type:
                query += " AND checkpoint_type = ?"
                params.append(checkpoint_type)

            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        checkpoints = []
        for row in rows:
            checkpoints.append({
                "checkpoint_id": row['checkpoint_id'],
                "workflow_id": row['workflow_id'],
                "node_id": row['node_id'],
                "edge_id": row['edge_id'],
                "checkpoint_type": row['checkpoint_type'],
                "created_at": row['created_at'],
                "expires_at": row['expires_at'],
                "status": row['status'],
                "description": row['description'],
                "tags": json.loads(row['tags']) if row['tags'] else [],
            })

        return checkpoints

    # ==================== Checkpoint Restoration ====================

    def restore_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointData]:
        """
        Restore a checkpoint (mark as active, return data).

        Args:
            checkpoint_id: The checkpoint ID to restore

        Returns:
            CheckpointData if found and not expired, None otherwise
        """
        # First get the checkpoint
        ckpt_data = self.get_checkpoint(checkpoint_id)

        if ckpt_data:
            # Update status to active
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "UPDATE checkpoints SET status = ? WHERE checkpoint_id = ?",
                    (CheckpointStatus.ACTIVE, checkpoint_id)
                )
                conn.commit()

        return ckpt_data

    # ==================== Checkpoint Expiration & Cleanup ====================

    def _mark_expired(self, checkpoint_id: str) -> None:
        """Mark a checkpoint as expired in the database."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE checkpoints SET status = ? WHERE checkpoint_id = ?",
                (CheckpointStatus.EXPIRED, checkpoint_id)
            )
            conn.commit()

    def cleanup_expired(self) -> int:
        """
        Clean up expired checkpoints.

        Returns:
            Number of checkpoints removed
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            # Find expired checkpoints
            cursor = conn.execute(
                "SELECT checkpoint_id FROM checkpoints "
                "WHERE expires_at < ? AND status = ?",
                (time.time(), CheckpointStatus.ACTIVE)
            )
            expired_ids = [row[0] for row in cursor.fetchall()]

            if not expired_ids:
                return 0

            # Delete expired checkpoints
            placeholders = ",".join("?" * len(expired_ids))
            conn.execute(
                f"DELETE FROM checkpoints WHERE checkpoint_id IN ({placeholders})",
                expired_ids
            )

            # Also clean up expired but not yet deleted (status only)
            conn.execute(
                "UPDATE checkpoints SET status = ? WHERE expires_at < ? AND status = ?",
                (CheckpointStatus.EXPIRED, time.time(), CheckpointStatus.ACTIVE)
            )

            conn.commit()
            deleted_count = len(expired_ids)

            logger.info(f"Cleaned up {deleted_count} expired checkpoints")
            return deleted_count

    # ==================== Utility Methods ====================

    def checkpoint_exists(self, checkpoint_id: str) -> bool:
        """Check if a checkpoint exists and is not expired."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT status, expires_at FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,)
            )
            row = cursor.fetchone()

        if not row:
            return False

        # Check if expired
        if row['expires_at'] and time.time() > row['expires_at']:
            return False

        return row['status'] == CheckpointStatus.ACTIVE

    def get_checkpoint_count(self, status: Optional[str] = None) -> int:
        """Get the count of checkpoints, optionally filtered by status."""
        with sqlite3.connect(str(self.db_path)) as conn:
            query = "SELECT COUNT(*) FROM checkpoints WHERE 1=1"
            params = []

            if status:
                query += " AND status = ?"
                params.append(status)

            cursor = conn.execute(query, params)
            return cursor.fetchone()[0]

    def get_stats(self) -> Dict[str, Any]:
        """Get checkpoint statistics."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("""
                SELECT
                    status,
                    COUNT(*) as count,
                    AVG(CASE WHEN expires_at > 0 THEN
                        (expires_at - created_at) / 86400
                        ELSE NULL END) as avg_lifetime_days
                FROM checkpoints
                GROUP BY status
            """)

            rows = cursor.fetchall()

            total = sum(row[1] for row in rows)

            return {
                "total_checkpoints": total,
                "by_status": {
                    row[0]: {"count": row[1], "avg_lifetime_days": round(row[2] or 0, 1)}
                    for row in rows
                },
            }
