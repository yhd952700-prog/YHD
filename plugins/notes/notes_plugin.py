"""
Notes Plugin for LiuHao AI OS

Provides note CRUD, tagging, full-text search, note linking,
version history, and export capabilities.

Capabilities:
- create_note: Create a new note
- update_note: Update a note
- delete_note: Delete a note
- get_note: Get a note by ID
- search_notes: Full-text search
- add_tag: Add tag to note
- list_tags: List all tags
- get_notes_by_tag: Get notes with a specific tag
- link_notes: Link two notes together
- get_backlinks: Get notes linking to this note
- get_version_history: Get note version history
- export_note: Export a note (Markdown, HTML, TXT)
"""

import uuid
import re
import time
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class NoteVersion:
    """A historical version of a note."""
    version: int
    title: str
    content: str
    created_at: float
    change_summary: str = ""


@dataclass
class Note:
    """A note item."""
    id: str
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    tags: List[str] = field(default_factory=list)
    linked_notes: List[str] = field(default_factory=list)  # note IDs
    versions: List[NoteVersion] = field(default_factory=list)
    current_version: int = 1
    pinned: bool = False
    favorite: bool = False
    word_count: int = 0
    character_count: int = 0


class NotesPlugin(BasePlugin):
    """Notes management plugin.

    Supports note CRUD, tagging, full-text search, note linking,
    version history, and multiple export formats.

    Example:
        plugin = NotesPlugin()
        plugin.initialize()
        plugin.activate()

        note = plugin.create_note(title="Ideas", content="Some thoughts...")
        results = plugin.search_notes("thoughts")
    """

    def __init__(self) -> None:
        """Initialize the notes plugin."""
        metadata = PluginMetadata(
            name="notes",
            version="1.0.0",
            description="Notes management with search and version history",
            author="LiuHao AI OS Team",
            categories=["productivity", "notes", "writing"],
            keywords=["notes", "notebook", "search", "tags", "wiki"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._notes: Dict[str, Note] = {}
        self._tag_index: Dict[str, Set[str]] = {}  # tag -> set of note IDs

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the notes plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the notes plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the notes plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the notes plugin."""
        try:
            self._notes.clear()
            self._tag_index.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "create_note",
            "update_note",
            "delete_note",
            "get_note",
            "search_notes",
            "add_tag",
            "remove_tag",
            "list_tags",
            "get_notes_by_tag",
            "link_notes",
            "unlink_notes",
            "get_backlinks",
            "get_version_history",
            "revert_version",
            "export_note",
            "get_stats",
        ]

    # ==================== Note CRUD ====================

    def create_note(
        self,
        title: str,
        content: str = "",
        tags: Optional[List[str]] = None,
        pinned: bool = False,
    ) -> Note:
        """Create a new note.

        Args:
            title: Note title.
            content: Note content.
            tags: List of tag strings.
            pinned: Whether to pin the note.

        Returns:
            The created Note.
        """
        self._ensure_active()
        self._increment_call_count()

        note_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        note = Note(
            id=note_id,
            title=title,
            content=content,
            created_at=now,
            updated_at=now,
            tags=tags or [],
            pinned=pinned,
            word_count=len(content.split()),
            character_count=len(content),
        )

        # Create initial version
        note.versions.append(NoteVersion(
            version=1,
            title=title,
            content=content,
            created_at=time.time(),
            change_summary="Initial version",
        ))

        self._notes[note_id] = note
        self._update_tag_index(note_id, tags or [])
        return note

    def update_note(
        self,
        note_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        change_summary: str = "",
    ) -> Optional[Note]:
        """Update a note's title or content.

        Automatically creates a version history entry.

        Args:
            note_id: Note ID.
            title: New title (optional).
            content: New content (optional).
            change_summary: Description of the change.

        Returns:
            Updated Note or None.
        """
        self._ensure_active()
        self._increment_call_count()

        note = self._notes.get(note_id)
        if not note:
            return None

        old_title = note.title
        old_content = note.content

        changed = False

        if title is not None and title != note.title:
            note.title = title
            changed = True

        if content is not None and content != note.content:
            note.content = content
            note.word_count = len(content.split())
            note.character_count = len(content)
            changed = True

        if changed:
            note.current_version += 1
            note.updated_at = datetime.now()
            note.versions.append(NoteVersion(
                version=note.current_version,
                title=note.title,
                content=note.content,
                created_at=time.time(),
                change_summary=change_summary,
            ))

            # Keep last 50 versions
            if len(note.versions) > 50:
                note.versions = note.versions[-50:]

        return note

    def delete_note(self, note_id: str) -> bool:
        """Delete a note.

        Args:
            note_id: Note ID.

        Returns:
            True if note was found and deleted.
        """
        self._ensure_active()
        self._increment_call_count()

        note = self._notes.get(note_id)
        if not note:
            return False

        # Remove from tag index
        for tag in note.tags:
            if tag in self._tag_index:
                self._tag_index[tag].discard(note_id)
                if not self._tag_index[tag]:
                    del self._tag_index[tag]

        # Remove backlinks from other notes
        for other in self._notes.values():
            if note_id in other.linked_notes:
                other.linked_notes.remove(note_id)

        del self._notes[note_id]
        return True

    def get_note(self, note_id: str) -> Optional[Note]:
        """Get a note by ID."""
        self._ensure_active()
        return self._notes.get(note_id)

    def list_notes(
        self,
        sort_by: str = "updated_at",
        sort_desc: bool = True,
        limit: Optional[int] = None,
        pinned_only: bool = False,
    ) -> List[Note]:
        """List all notes.

        Args:
            sort_by: Sort field (updated_at, created_at, title, word_count).
            sort_desc: Sort descending.
            limit: Maximum number of notes to return.
            pinned_only: Only return pinned notes.

        Returns:
            List of Note objects.
        """
        self._ensure_active()
        self._increment_call_count()

        notes = list(self._notes.values())

        if pinned_only:
            notes = [n for n in notes if n.pinned]

        sort_key_map = {
            "updated_at": lambda n: n.updated_at,
            "created_at": lambda n: n.created_at,
            "title": lambda n: n.title.lower(),
            "word_count": lambda n: n.word_count,
        }
        key_func = sort_key_map.get(sort_by, lambda n: n.updated_at)
        notes.sort(key=key_func, reverse=sort_desc)

        if limit:
            notes = notes[:limit]

        return notes

    # ==================== Search ====================

    def search_notes(
        self,
        query: str,
        search_title: bool = True,
        search_content: bool = True,
        case_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Full-text search across notes.

        Args:
            query: Search query string.
            search_title: Search in note titles.
            search_content: Search in note content.
            case_sensitive: Whether search is case-sensitive.

        Returns:
            List of dicts with note info and match score.
        """
        self._ensure_active()
        self._increment_call_count()

        if not query:
            return []

        results: List[Dict[str, Any]] = []

        for note in self._notes.values():
            score = 0.0
            title_hits = 0
            content_hits = 0

            title_text = note.title if case_sensitive else note.title.lower()
            content_text = note.content if case_sensitive else note.content.lower()
            search_query = query if case_sensitive else query.lower()

            if search_title:
                title_hits = title_text.count(search_query)
                if title_hits > 0:
                    score += title_hits * 10  # Title matches weigh more

            if search_content:
                content_hits = content_text.count(search_query)
                if content_hits > 0:
                    score += content_hits

            if score > 0:
                # Find snippet from content
                snippet = ""
                if content_hits > 0:
                    idx = content_text.find(search_query)
                    if idx >= 0:
                        start = max(0, idx - 50)
                        end = min(len(note.content), idx + len(query) + 50)
                        snippet = note.content[start:end]
                        if start > 0:
                            snippet = "..." + snippet
                        if end < len(note.content):
                            snippet = snippet + "..."

                results.append({
                    "note_id": note.id,
                    "title": note.title,
                    "score": score,
                    "title_hits": title_hits,
                    "content_hits": content_hits,
                    "snippet": snippet,
                    "tags": note.tags,
                    "updated_at": note.updated_at,
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    # ==================== Tags ====================

    def add_tag(self, note_id: str, tag: str) -> bool:
        """Add a tag to a note."""
        note = self._notes.get(note_id)
        if not note:
            return False
        if tag not in note.tags:
            note.tags.append(tag)
            note.updated_at = datetime.now()
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(note_id)
        return True

    def remove_tag(self, note_id: str, tag: str) -> bool:
        """Remove a tag from a note."""
        note = self._notes.get(note_id)
        if not note or tag not in note.tags:
            return False
        note.tags.remove(tag)
        note.updated_at = datetime.now()
        if tag in self._tag_index:
            self._tag_index[tag].discard(note_id)
            if not self._tag_index[tag]:
                del self._tag_index[tag]
        return True

    def list_tags(self) -> List[Dict[str, Any]]:
        """List all tags with their counts."""
        return [
            {"tag": tag, "count": len(note_ids)}
            for tag, note_ids in sorted(self._tag_index.items())
        ]

    def get_notes_by_tag(self, tag: str) -> List[Note]:
        """Get all notes with a specific tag."""
        self._ensure_active()
        note_ids = self._tag_index.get(tag, set())
        return [self._notes[nid] for nid in note_ids if nid in self._notes]

    # ==================== Note Linking ====================

    def link_notes(self, note_id_1: str, note_id_2: str) -> bool:
        """Link two notes together (bidirectional).

        Args:
            note_id_1: First note ID.
            note_id_2: Second note ID.

        Returns:
            True if both notes exist and were linked.
        """
        n1 = self._notes.get(note_id_1)
        n2 = self._notes.get(note_id_2)

        if not n1 or not n2 or note_id_1 == note_id_2:
            return False

        if note_id_2 not in n1.linked_notes:
            n1.linked_notes.append(note_id_2)
            n1.updated_at = datetime.now()

        if note_id_1 not in n2.linked_notes:
            n2.linked_notes.append(note_id_1)
            n2.updated_at = datetime.now()

        return True

    def unlink_notes(self, note_id_1: str, note_id_2: str) -> bool:
        """Unlink two notes."""
        n1 = self._notes.get(note_id_1)
        n2 = self._notes.get(note_id_2)

        if not n1 or not n2:
            return False

        if note_id_2 in n1.linked_notes:
            n1.linked_notes.remove(note_id_2)
            n1.updated_at = datetime.now()

        if note_id_1 in n2.linked_notes:
            n2.linked_notes.remove(note_id_1)
            n2.updated_at = datetime.now()

        return True

    def get_backlinks(self, note_id: str) -> List[Note]:
        """Get all notes that link to this note."""
        self._ensure_active()
        return [
            n for n in self._notes.values()
            if note_id in n.linked_notes
        ]

    # ==================== Version History ====================

    def get_version_history(self, note_id: str) -> List[Dict[str, Any]]:
        """Get version history for a note.

        Returns list of version info dicts (without full content).
        """
        note = self._notes.get(note_id)
        if not note:
            return []

        return [
            {
                "version": v.version,
                "title": v.title,
                "created_at": v.created_at,
                "change_summary": v.change_summary,
                "content_length": len(v.content),
            }
            for v in reversed(note.versions)
        ]

    def revert_version(self, note_id: str, version: int) -> bool:
        """Revert a note to a previous version.

        Args:
            note_id: Note ID.
            version: Version number to revert to.

        Returns:
            True if reversion succeeded.
        """
        note = self._notes.get(note_id)
        if not note:
            return False

        target_version = None
        for v in note.versions:
            if v.version == version:
                target_version = v
                break

        if not target_version:
            return False

        # Create new version with the old content
        note.title = target_version.title
        note.content = target_version.content
        note.word_count = len(target_version.content.split())
        note.character_count = len(target_version.content)
        note.current_version += 1
        note.updated_at = datetime.now()

        note.versions.append(NoteVersion(
            version=note.current_version,
            title=note.title,
            content=note.content,
            created_at=time.time(),
            change_summary=f"Reverted to version {version}",
        ))

        return True

    # ==================== Export ====================

    def export_note(
        self,
        note_id: str,
        format: str = "markdown",
    ) -> Optional[str]:
        """Export a note to a string format.

        Args:
            note_id: Note ID.
            format: Export format ('markdown', 'html', 'txt').

        Returns:
            Exported note string, or None if note not found.
        """
        note = self._notes.get(note_id)
        if not note:
            return None

        if format == "markdown":
            return self._export_markdown(note)
        elif format == "html":
            return self._export_html(note)
        elif format == "txt":
            return self._export_txt(note)
        else:
            raise ValueError(f"Unknown export format: {format}")

    def _export_markdown(self, note: Note) -> str:
        """Export note as Markdown."""
        lines = [f"# {note.title}", ""]

        if note.tags:
            tag_str = " ".join(f"#{tag}" for tag in note.tags)
            lines.append(tag_str)
            lines.append("")

        lines.append(note.content)

        if note.linked_notes:
            lines.append("")
            lines.append("## Linked Notes")
            for lid in note.linked_notes:
                linked = self._notes.get(lid)
                if linked:
                    lines.append(f"- [[{linked.title}]]")

        lines.append("")
        lines.append(f"---")
        lines.append(f"*Created: {note.created_at.isoformat()}*")
        lines.append(f"*Updated: {note.updated_at.isoformat()}*")
        lines.append(f"*Version: {note.current_version}*")

        return "\n".join(lines)

    def _export_html(self, note: Note) -> str:
        """Export note as HTML."""
        # Simple text to HTML conversion
        content_html = note.content.replace("\n", "<br>\n")

        tags_html = ""
        if note.tags:
            tag_spans = " ".join(
                f'<span class="tag">{tag}</span>' for tag in note.tags
            )
            tags_html = f'<div class="tags">{tag_spans}</div>'

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{note.title}</title>
    <style>
        body {{ font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; }}
        .tags {{ margin: 1rem 0; }}
        .tag {{ background: #e0e0e0; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.9rem; }}
        .meta {{ color: #666; font-size: 0.9rem; margin-top: 2rem; border-top: 1px solid #eee; padding-top: 1rem; }}
    </style>
</head>
<body>
    <h1>{note.title}</h1>
    {tags_html}
    <div class="content">
        {content_html}
    </div>
    <div class="meta">
        <p>Created: {note.created_at.isoformat()}</p>
        <p>Updated: {note.updated_at.isoformat()}</p>
        <p>Version: {note.current_version}</p>
        <p>Words: {note.word_count}</p>
    </div>
</body>
</html>"""

    def _export_txt(self, note: Note) -> str:
        """Export note as plain text."""
        lines = [note.title, "=" * len(note.title), ""]

        if note.tags:
            lines.append("Tags: " + ", ".join(note.tags))
            lines.append("")

        lines.append(note.content)
        lines.append("")
        lines.append("-" * 40)
        lines.append(f"Created: {note.created_at.isoformat()}")
        lines.append(f"Updated: {note.updated_at.isoformat()}")
        lines.append(f"Version: {note.current_version}")
        lines.append(f"Word count: {note.word_count}")

        return "\n".join(lines)

    # ==================== Stats ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get notes statistics."""
        total = len(self._notes)
        total_words = sum(n.word_count for n in self._notes.values())
        total_chars = sum(n.character_count for n in self._notes.values())
        total_tags = len(self._tag_index)
        pinned = sum(1 for n in self._notes.values() if n.pinned)

        return {
            "total_notes": total,
            "total_tags": total_tags,
            "total_words": total_words,
            "total_characters": total_chars,
            "pinned_notes": pinned,
            "avg_words_per_note": round(total_words / total, 1) if total > 0 else 0,
        }

    def _update_tag_index(self, note_id: str, tags: List[str]) -> None:
        """Update the tag index for a note."""
        for tag in tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(note_id)
