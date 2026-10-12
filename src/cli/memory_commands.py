"""Memory management CLI commands for LiuHao AI OS.

Provides command-line interface for memory system operations:
- memory add, memory get, memory search, memory delete
- memory list, memory stats, memory clear
- memory export, memory import
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    """Types of memory entries."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"
    META = "meta"


@dataclass
class MemoryEntry:
    """A single memory entry."""
    memory_id: str
    content: str
    memory_type: MemoryType
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_accessed: Optional[str] = None
    access_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryManager:
    """In-memory memory manager for CLI operations.

    Provides CRUD, search, and import/export operations on a
    simulated memory store.
    """

    def __init__(self) -> None:
        self._memories: Dict[str, MemoryEntry] = {}

    def add_memory(
        self,
        content: str,
        memory_type: str = "episodic",
        category: str = "general",
        tags: Optional[List[str]] = None,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a new memory entry.

        Args:
            content: Memory content text.
            memory_type: Type of memory.
            category: Memory category.
            tags: Optional tag list.
            importance: Importance score (0.0 - 1.0).
            metadata: Optional metadata dict.

        Returns:
            Dict with created memory details.
        """
        mem_id = str(uuid.uuid4())
        entry = MemoryEntry(
            memory_id=mem_id,
            content=content,
            memory_type=MemoryType(memory_type),
            category=category,
            tags=tags or [],
            importance=importance,
            metadata=metadata or {},
        )
        self._memories[mem_id] = entry
        return {
            "id": entry.memory_id,
            "content": entry.content,
            "type": entry.memory_type.value,
            "category": entry.category,
            "tags": entry.tags,
            "importance": entry.importance,
            "created_at": entry.created_at,
        }

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        """Get a memory entry by ID.

        Args:
            memory_id: Memory identifier.

        Returns:
            Dict with memory details.

        Raises:
            ValueError: If memory not found.
        """
        entry = self._memories.get(memory_id)
        if not entry:
            raise ValueError(f"Memory not found: {memory_id}")
        entry.access_count += 1
        entry.last_accessed = datetime.utcnow().isoformat()
        return {
            "id": entry.memory_id,
            "content": entry.content,
            "type": entry.memory_type.value,
            "category": entry.category,
            "tags": entry.tags,
            "importance": entry.importance,
            "created_at": entry.created_at,
            "last_accessed": entry.last_accessed,
            "access_count": entry.access_count,
            "metadata": entry.metadata,
        }

    def search_memory(
        self,
        query: str,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search memories by keyword.

        Args:
            query: Search query string.
            memory_type: Optional type filter.
            category: Optional category filter.
            limit: Maximum results.

        Returns:
            Dict with search results.
        """
        query_lower = query.lower()
        results = []
        for entry in self._memories.values():
            if memory_type and entry.memory_type.value != memory_type:
                continue
            if category and entry.category != category:
                continue
            if query_lower in entry.content.lower() or any(
                query_lower in t.lower() for t in entry.tags
            ):
                results.append(entry)
        results.sort(key=lambda e: e.importance, reverse=True)
        results = results[:limit]
        return {
            "query": query,
            "total": len(results),
            "limit": limit,
            "items": [
                {
                    "id": e.memory_id,
                    "content": e.content,
                    "type": e.memory_type.value,
                    "category": e.category,
                    "tags": e.tags,
                    "importance": e.importance,
                    "created_at": e.created_at,
                }
                for e in results
            ],
        }

    def delete_memory(self, memory_id: str) -> Dict[str, Any]:
        """Delete a memory entry.

        Args:
            memory_id: Memory identifier.

        Returns:
            Dict confirming deletion.

        Raises:
            ValueError: If memory not found.
        """
        if memory_id not in self._memories:
            raise ValueError(f"Memory not found: {memory_id}")
        del self._memories[memory_id]
        return {"status": "deleted", "memory_id": memory_id}

    def list_memories(
        self,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List memories with optional filtering.

        Args:
            memory_type: Filter by type.
            category: Filter by category.
            limit: Result limit.
            offset: Pagination offset.

        Returns:
            Dict with paginated memory list.
        """
        entries = list(self._memories.values())
        if memory_type:
            entries = [e for e in entries if e.memory_type.value == memory_type]
        if category:
            entries = [e for e in entries if e.category == category]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        total = len(entries)
        entries = entries[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": e.memory_id,
                    "content": e.content[:80] + "..." if len(e.content) > 80 else e.content,
                    "type": e.memory_type.value,
                    "category": e.category,
                    "tags": e.tags,
                    "importance": e.importance,
                    "created_at": e.created_at,
                }
                for e in entries
            ],
        }

    def memory_stats(self) -> Dict[str, Any]:
        """Get memory system statistics.

        Returns:
            Dict with memory statistics.
        """
        by_type: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        total_accesses = 0
        for entry in self._memories.values():
            by_type[entry.memory_type.value] = by_type.get(entry.memory_type.value, 0) + 1
            by_category[entry.category] = by_category.get(entry.category, 0) + 1
            total_accesses += entry.access_count
        return {
            "total_memories": len(self._memories),
            "by_type": by_type,
            "by_category": by_category,
            "total_accesses": total_accesses,
            "avg_importance": (
                sum(e.importance for e in self._memories.values()) / len(self._memories)
                if self._memories
                else 0
            ),
        }

    def clear_memory(
        self,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Clear memories (optionally filtered).

        Args:
            memory_type: Clear only this type.
            category: Clear only this category.

        Returns:
            Dict with count of cleared entries.
        """
        to_delete = []
        for mem_id, entry in self._memories.items():
            if memory_type and entry.memory_type.value != memory_type:
                continue
            if category and entry.category != category:
                continue
            to_delete.append(mem_id)
        for mem_id in to_delete:
            del self._memories[mem_id]
        return {"cleared": len(to_delete), "remaining": len(self._memories)}

    def export_memory(
        self,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Export memories to a serializable format.

        Args:
            memory_type: Optional type filter.
            category: Optional category filter.

        Returns:
            Dict with export data.
        """
        entries = []
        for entry in self._memories.values():
            if memory_type and entry.memory_type.value != memory_type:
                continue
            if category and entry.category != category:
                continue
            entries.append({
                "id": entry.memory_id,
                "content": entry.content,
                "type": entry.memory_type.value,
                "category": entry.category,
                "tags": entry.tags,
                "importance": entry.importance,
                "created_at": entry.created_at,
                "access_count": entry.access_count,
                "metadata": entry.metadata,
            })
        return {
            "format": "liuhao-memory-v1",
            "exported_at": datetime.utcnow().isoformat(),
            "count": len(entries),
            "memories": entries,
        }

    def import_memory(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Import memories from exported data.

        Args:
            data: Exported memory data dict.

        Returns:
            Dict with import summary.
        """
        imported = 0
        skipped = 0
        for item in data.get("memories", []):
            if "id" in item and item["id"] in self._memories:
                skipped += 1
                continue
            mem_id = item.get("id", str(uuid.uuid4()))
            entry = MemoryEntry(
                memory_id=mem_id,
                content=item.get("content", ""),
                memory_type=MemoryType(item.get("type", "episodic")),
                category=item.get("category", "general"),
                tags=item.get("tags", []),
                importance=float(item.get("importance", 0.5)),
                created_at=item.get("created_at", datetime.utcnow().isoformat()),
                access_count=item.get("access_count", 0),
                metadata=item.get("metadata", {}),
            )
            self._memories[mem_id] = entry
            imported += 1
        return {"imported": imported, "skipped": skipped, "total": len(self._memories)}


# =====================================================================
# CLI Parser
# =====================================================================


def register_memory_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register memory subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_mem = subparsers.add_parser("memory", help="Memory system management")
    mem_sub = p_mem.add_subparsers(dest="memory_command", required=True)

    # memory add
    p_add = mem_sub.add_parser("add", help="Add a new memory")
    p_add.add_argument("content", help="Memory content text")
    p_add.add_argument(
        "--type",
        default="episodic",
        choices=["episodic", "semantic", "procedural", "working", "meta"],
        help="Memory type",
    )
    p_add.add_argument("--category", default="general", help="Memory category")
    p_add.add_argument("--tag", action="append", dest="tags", help="Add a tag (repeatable)")
    p_add.add_argument("--importance", type=float, default=0.5, help="Importance (0.0-1.0)")
    p_add.add_argument("--metadata", help="JSON string with metadata")

    # memory get
    p_get = mem_sub.add_parser("get", help="Get a memory by ID")
    p_get.add_argument("memory_id", help="Memory identifier")

    # memory search
    p_search = mem_sub.add_parser("search", help="Search memories")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--type", help="Filter by memory type")
    p_search.add_argument("--category", help="Filter by category")
    p_search.add_argument("--limit", type=int, default=10, help="Result limit")

    # memory delete
    p_delete = mem_sub.add_parser("delete", help="Delete a memory")
    p_delete.add_argument("memory_id", help="Memory identifier")

    # memory list
    p_list = mem_sub.add_parser("list", help="List memories")
    p_list.add_argument("--type", help="Filter by memory type")
    p_list.add_argument("--category", help="Filter by category")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # memory stats
    mem_sub.add_parser("stats", help="Show memory system statistics")

    # memory clear
    p_clear = mem_sub.add_parser("clear", help="Clear memories")
    p_clear.add_argument("--type", help="Clear only this type")
    p_clear.add_argument("--category", help="Clear only this category")
    p_clear.add_argument("--force", action="store_true", help="Confirm clear operation")

    # memory export
    p_export = mem_sub.add_parser("export", help="Export memories to JSON")
    p_export.add_argument("--type", help="Filter by memory type")
    p_export.add_argument("--category", help="Filter by category")
    p_export.add_argument("--output", help="Output file path (prints to stdout if not set)")

    # memory import
    p_import = mem_sub.add_parser("import", help="Import memories from JSON file")
    p_import.add_argument("file", help="Path to memory export JSON file")


# =====================================================================
# Command Handler
# =====================================================================


def handle_memory_command(
    args: argparse.Namespace, manager: MemoryManager
) -> int:
    """Dispatch a memory CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: MemoryManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.memory_command == "add":
            metadata = json.loads(args.metadata) if args.metadata else None
            result = manager.add_memory(
                content=args.content,
                memory_type=args.type,
                category=args.category,
                tags=args.tags,
                importance=args.importance,
                metadata=metadata,
            )
        elif args.memory_command == "get":
            result = manager.get_memory(args.memory_id)
        elif args.memory_command == "search":
            result = manager.search_memory(
                query=args.query,
                memory_type=args.type,
                category=args.category,
                limit=args.limit,
            )
        elif args.memory_command == "delete":
            result = manager.delete_memory(args.memory_id)
        elif args.memory_command == "list":
            result = manager.list_memories(
                memory_type=args.type,
                category=args.category,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.memory_command == "stats":
            result = manager.memory_stats()
        elif args.memory_command == "clear":
            if not args.force:
                print(json.dumps({"error": "Use --force to confirm memory clear"}, indent=2))
                return 1
            result = manager.clear_memory(
                memory_type=getattr(args, "type", None),
                category=args.category,
            )
        elif args.memory_command == "export":
            result = manager.export_memory(
                memory_type=getattr(args, "type", None),
                category=args.category,
            )
            if getattr(args, "output", None):
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                result = {"status": "exported", "file": args.output, "count": result["count"]}
        elif args.memory_command == "import":
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = manager.import_memory(data)
        else:
            print(f"Unknown memory command: {args.memory_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
