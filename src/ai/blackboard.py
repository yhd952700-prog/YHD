"""Blackboard System — shared knowledge space for multi-agent collaboration.

A blackboard is a shared data structure where agents can:
  - Post information, results, and observations
  - Read what other agents have contributed
  - Subscribe to changes in specific topics
  - Work on problems collectively (opportunistic problem-solving)

This implements the classic blackboard architecture pattern:
  Multiple specialists (agents) contribute to a shared problem space,
  each adding their piece of the puzzle when they have something useful.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import time
import uuid


class EntryType(str, Enum):
    """Types of blackboard entries."""
    FACT = "fact"                 # Established fact
    HYPOTHESIS = "hypothesis"     # Proposed idea/hypothesis
    RESULT = "result"             # Task result
    QUESTION = "question"         # Open question
    CONSTRAINT = "constraint"     # Constraint or requirement
    PROGRESS = "progress"         # Progress update
    DECISION = "decision"         # A decision that was made
    FEEDBACK = "feedback"         # Feedback on something


class ConfidenceLevel(str, Enum):
    """Confidence levels for blackboard entries."""
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SPECULATION = "speculation"


@dataclass
class BlackboardEntry:
    """An entry on the blackboard."""
    id: str
    author: str                           # Agent ID that posted this
    topic: str                            # Topic/category
    content: Any                          # The actual content
    entry_type: EntryType = EntryType.FACT
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    tags: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)  # Other entry IDs
    timestamp: float = field(default_factory=time.time)
    version: int = 1
    edited_by: List[str] = field(default_factory=list)   # Agents who edited
    upvotes: int = 0
    downvotes: int = 0
    voters: Set[str] = field(default_factory=set)

    @property
    def score(self) -> float:
        """Net helpfulness score."""
        return self.upvotes - self.downvotes


class Blackboard:
    """Shared blackboard for multi-agent collaboration.

    Agents can post, read, search, and vote on entries.
    Supports subscriptions for real-time updates.

    Usage::

        board = Blackboard("problem_space")

        # Agent posts a finding
        board.post("researcher_1", "market_size", "$10B",
                   entry_type=EntryType.FACT,
                   confidence=ConfidenceLevel.HIGH)

        # Another agent reads
        facts = board.get_by_topic("market_size")

        # Agent votes on an entry
        board.vote(entry_id, "analyst_1", upvote=True)

        # Subscribe to changes
        def handler(entry):
            print(f"New entry on {entry.topic}")
        board.subscribe("market_size", handler)
    """

    def __init__(self, name: str = "blackboard"):
        self.name = name
        self._entries: Dict[str, BlackboardEntry] = {}
        self._topic_index: Dict[str, List[str]] = {}    # topic -> [entry_ids]
        self._author_index: Dict[str, List[str]] = {}   # author -> [entry_ids]
        self._tag_index: Dict[str, List[str]] = {}      # tag -> [entry_ids]
        self._type_index: Dict[EntryType, List[str]] = {}  # type -> [entry_ids]

        # Subscribers: topic → list of callback functions
        self._subscribers: Dict[str, List[Callable[[BlackboardEntry], None]]] = {}
        self._global_subscribers: List[Callable[[BlackboardEntry], None]] = []

        self._created_at = time.time()

    # ------------------------------------------------------------------
    # Posting entries
    # ------------------------------------------------------------------

    def post(
        self,
        author: str,
        topic: str,
        content: Any,
        entry_type: EntryType = EntryType.FACT,
        confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
        tags: Optional[List[str]] = None,
        references: Optional[List[str]] = None,
    ) -> BlackboardEntry:
        """Post a new entry to the blackboard."""
        entry_id = f"bb_{uuid.uuid4().hex[:12]}"
        entry = BlackboardEntry(
            id=entry_id,
            author=author,
            topic=topic,
            content=content,
            entry_type=entry_type,
            confidence=confidence,
            tags=tags or [],
            references=references or [],
        )

        self._entries[entry_id] = entry
        self._index_entry(entry)
        self._notify_subscribers(entry)

        return entry

    def update(
        self,
        entry_id: str,
        editor: str,
        new_content: Any,
        new_confidence: Optional[ConfidenceLevel] = None,
    ) -> Optional[BlackboardEntry]:
        """Update an existing entry."""
        entry = self._entries.get(entry_id)
        if entry is None:
            return None

        entry.content = new_content
        entry.version += 1
        entry.edited_by.append(editor)
        if new_confidence is not None:
            entry.confidence = new_confidence

        return entry

    def get(self, entry_id: str) -> Optional[BlackboardEntry]:
        """Get an entry by ID."""
        return self._entries.get(entry_id)

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    def get_by_topic(self, topic: str) -> List[BlackboardEntry]:
        """Get all entries on a topic."""
        ids = self._topic_index.get(topic, [])
        return [self._entries[eid] for eid in ids if eid in self._entries]

    def get_by_author(self, author: str) -> List[BlackboardEntry]:
        """Get all entries by an author."""
        ids = self._author_index.get(author, [])
        return [self._entries[eid] for eid in ids if eid in self._entries]

    def get_by_type(self, entry_type: EntryType) -> List[BlackboardEntry]:
        """Get all entries of a type."""
        ids = self._type_index.get(entry_type, [])
        return [self._entries[eid] for eid in ids if eid in self._entries]

    def get_by_tag(self, tag: str) -> List[BlackboardEntry]:
        """Get all entries with a tag."""
        ids = self._tag_index.get(tag, [])
        return [self._entries[eid] for eid in ids if eid in self._entries]

    def search(
        self,
        query: str,
        topic: Optional[str] = None,
        entry_type: Optional[EntryType] = None,
        limit: int = 20,
    ) -> List[BlackboardEntry]:
        """Search entries by content keywords."""
        query_lower = query.lower()
        results: List[BlackboardEntry] = []

        candidates = list(self._entries.values())

        if topic:
            topic_ids = set(self._topic_index.get(topic, []))
            candidates = [e for e in candidates if e.id in topic_ids]

        if entry_type:
            type_ids = set(self._type_index.get(entry_type, []))
            candidates = [e for e in candidates if e.id in type_ids]

        for entry in candidates:
            # Search in content (string representation)
            content_str = str(entry.content).lower()
            if query_lower in content_str or query_lower in entry.topic.lower():
                results.append(entry)

        # Sort by score (votes) + recency
        results.sort(
            key=lambda e: (e.score, e.timestamp),
            reverse=True,
        )
        return results[:limit]

    # ------------------------------------------------------------------
    # Voting
    # ------------------------------------------------------------------

    def vote(
        self,
        entry_id: str,
        voter_id: str,
        upvote: bool,
    ) -> bool:
        """Vote on an entry (up or down).

        Each agent can vote only once per entry.
        Returns True if vote was recorded, False if already voted.
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            return False

        if voter_id in entry.voters:
            return False  # Already voted

        entry.voters.add(voter_id)
        if upvote:
            entry.upvotes += 1
        else:
            entry.downvotes += 1

        return True

    def top_entries(
        self,
        topic: Optional[str] = None,
        limit: int = 10,
    ) -> List[BlackboardEntry]:
        """Get the highest-scored entries."""
        if topic:
            entries = self.get_by_topic(topic)
        else:
            entries = list(self._entries.values())

        entries.sort(key=lambda e: e.score, reverse=True)
        return entries[:limit]

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(
        self,
        topic: str,
        callback: Callable[[BlackboardEntry], None],
    ) -> None:
        """Subscribe to new entries on a topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(callback)

    def subscribe_all(
        self,
        callback: Callable[[BlackboardEntry], None],
    ) -> None:
        """Subscribe to all new entries."""
        self._global_subscribers.append(callback)

    def _notify_subscribers(self, entry: BlackboardEntry) -> None:
        """Notify subscribers about a new entry."""
        # Global subscribers
        for cb in self._global_subscribers:
            try:
                cb(entry)
            except Exception:
                pass  # Don't let bad callbacks break the system

        # Topic-specific subscribers
        for cb in self._subscribers.get(entry.topic, []):
            try:
                cb(entry)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def topics(self) -> List[str]:
        return list(self._topic_index.keys())

    def stats(self) -> Dict[str, Any]:
        """Get blackboard statistics."""
        type_counts: Dict[str, int] = {}
        for entry in self._entries.values():
            t = entry.entry_type.value
            type_counts[t] = type_counts.get(t, 0) + 1

        author_counts: Dict[str, int] = {}
        for entry in self._entries.values():
            author_counts[entry.author] = author_counts.get(entry.author, 0) + 1

        return {
            "name": self.name,
            "entry_count": len(self._entries),
            "topic_count": len(self._topic_index),
            "author_count": len(self._author_index),
            "type_distribution": type_counts,
            "top_authors": sorted(
                author_counts.items(), key=lambda x: x[1], reverse=True
            )[:5],
            "age_seconds": round(time.time() - self._created_at, 2),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_entry(self, entry: BlackboardEntry) -> None:
        """Add entry to all indexes."""
        # Topic index
        if entry.topic not in self._topic_index:
            self._topic_index[entry.topic] = []
        self._topic_index[entry.topic].append(entry.id)

        # Author index
        if entry.author not in self._author_index:
            self._author_index[entry.author] = []
        self._author_index[entry.author].append(entry.id)

        # Type index
        if entry.entry_type not in self._type_index:
            self._type_index[entry.entry_type] = []
        self._type_index[entry.entry_type].append(entry.id)

        # Tag index
        for tag in entry.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            self._tag_index[tag].append(entry.id)
