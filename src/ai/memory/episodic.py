"""Episodic Memory — store and replay sequences of experience.

Episodic memory is about *events* and *sequences*: "what happened,
in what order, in what context". This is different from semantic memory
(which is about facts and concepts).

Why episodic memory matters:
  1. **Experience replay** — replaying successful episodes to reinforce
     good patterns (inspired by biological memory replay during rest/sleep)
  2. **Learning from mistakes** — failed episodes are valuable too
  3. **Contextual recall** — "when was the last time I did X, and what
     happened then?"
  4. **Sequence prediction** — if A→B→C happened before, maybe A→B→C again

Each episode is a timestamped sequence of events with:
  - Goal / context
  - Sequence of actions/events
  - Outcome (success/failure/partial)
  - Lessons learned (extracted insights)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import time
import uuid


class EpisodeOutcome(str, Enum):
    """How an episode ended."""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    ONGOING = "ongoing"


@dataclass
class EpisodeEvent:
    """A single event within an episode."""
    id: str
    timestamp: float
    event_type: str       # "action", "observation", "thought", "result"
    content: str
    data: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0  # How long this event took


@dataclass
class Episode:
    """A complete episode — a sequence of events with a goal and outcome."""
    id: str
    goal: str
    start_time: float
    end_time: Optional[float] = None
    outcome: EpisodeOutcome = EpisodeOutcome.ONGOING
    events: List[EpisodeEvent] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    lessons: List[str] = field(default_factory=list)  # Extracted lessons
    tags: List[str] = field(default_factory=list)
    replay_count: int = 0
    importance: float = 0.5  # 0-1, how "important" this episode is

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    @property
    def event_count(self) -> int:
        return len(self.events)

    def add_event(
        self,
        event_type: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
        duration_ms: float = 0.0,
    ) -> EpisodeEvent:
        """Add an event to the episode."""
        event = EpisodeEvent(
            id=f"evt_{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            event_type=event_type,
            content=content,
            data=data or {},
            duration_ms=duration_ms,
        )
        self.events.append(event)
        return event

    def finish(self, outcome: EpisodeOutcome, lessons: Optional[List[str]] = None) -> None:
        """Mark the episode as finished."""
        self.end_time = time.time()
        self.outcome = outcome
        if lessons:
            self.lessons.extend(lessons)


@dataclass
class EpisodeSequence:
    """A sequence of related episodes (a "story arc")."""
    id: str
    title: str
    episodes: List[str] = field(default_factory=list)  # episode ids
    description: str = ""
    created_at: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)


@dataclass
class ReplayResult:
    """Result of an episode replay."""
    episode_id: str
    events_replayed: int
    lessons_extracted: List[str]
    insights: List[str]
    replay_time_ms: float


class EpisodicMemory:
    """Episodic memory — store and replay experience sequences.

    Usage::

        mem = EpisodicMemory()

        # Record an episode
        episode = mem.start_episode("Fix the login bug")
        episode.add_event("action", "Investigated error logs")
        episode.add_event("result", "Found typo in auth.py")
        episode.finish(EpisodeOutcome.SUCCESS,
                       lessons=["Always check error logs first"])

        # Replay for learning
        result = mem.replay(episode.id)
        print(result.lessons_extracted)

        # Find similar past episodes
        similar = mem.find_similar("login bug")
    """

    def __init__(
        self,
        max_episodes: int = 1000,
        max_events_per_episode: int = 1000,
        replay_importance_threshold: float = 0.3,
    ):
        self.max_episodes = max_episodes
        self.max_events_per_episode = max_events_per_episode
        self.replay_importance_threshold = replay_importance_threshold

        self._episodes: Dict[str, Episode] = {}
        self._sequences: Dict[str, EpisodeSequence] = {}

        # Indexes
        self._goal_index: Dict[str, List[str]] = {}  # keyword -> [episode_ids]
        self._outcome_index: Dict[EpisodeOutcome, List[str]] = {}  # outcome -> [ids]
        self._tag_index: Dict[str, List[str]] = {}  # tag -> [ids]

        # LRU order for eviction
        self._access_order: List[str] = []

    # ------------------------------------------------------------------
    # Episode recording
    # ------------------------------------------------------------------

    def start_episode(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Episode:
        """Start recording a new episode."""
        episode_id = f"ep_{uuid.uuid4().hex[:12]}"
        episode = Episode(
            id=episode_id,
            goal=goal,
            start_time=time.time(),
            context=context or {},
            tags=tags or [],
        )

        self._episodes[episode_id] = episode
        self._index_episode(episode)
        self._access_order.append(episode_id)

        # Evict if over capacity
        if len(self._episodes) > self.max_episodes:
            self._evict_oldest()

        return episode

    def finish_episode(
        self,
        episode_id: str,
        outcome: EpisodeOutcome,
        lessons: Optional[List[str]] = None,
        importance: Optional[float] = None,
    ) -> Optional[Episode]:
        """Finish an episode with outcome and lessons."""
        episode = self._episodes.get(episode_id)
        if episode is None:
            return None

        episode.finish(outcome, lessons)

        if importance is not None:
            episode.importance = importance
        else:
            # Auto-compute importance based on outcome and lessons
            if outcome == EpisodeOutcome.FAILURE:
                episode.importance = 0.8  # Failures are highly instructive
            elif outcome == EpisodeOutcome.SUCCESS and lessons:
                episode.importance = 0.7
            else:
                episode.importance = 0.4

        # Update outcome index
        if outcome not in self._outcome_index:
            self._outcome_index[outcome] = []
        if episode_id not in self._outcome_index[outcome]:
            self._outcome_index[outcome].append(episode_id)

        self._touch(episode_id)
        return episode

    def add_event(
        self,
        episode_id: str,
        event_type: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[EpisodeEvent]:
        """Add an event to an ongoing episode."""
        episode = self._episodes.get(episode_id)
        if episode is None:
            return None

        if len(episode.events) >= self.max_events_per_episode:
            # Trim: keep first and last N events
            keep_first = self.max_events_per_episode // 2
            keep_last = self.max_events_per_episode // 2 - 1
            episode.events = (
                episode.events[:keep_first] +
                [EpisodeEvent(
                    id="evt_truncated",
                    timestamp=time.time(),
                    event_type="system",
                    content=f"... {len(episode.events) - keep_first - keep_last} events truncated ...",
                )] +
                episode.events[-keep_last:]
            )

        event = episode.add_event(event_type, content, data)
        self._touch(episode_id)
        return event

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        """Get an episode by ID."""
        episode = self._episodes.get(episode_id)
        if episode:
            self._touch(episode_id)
        return episode

    def find_similar(
        self,
        query: str,
        outcome: Optional[EpisodeOutcome] = None,
        limit: int = 10,
    ) -> List[Episode]:
        """Find episodes with similar goals.

        Uses simple keyword matching for now; for production you'd want
        embedding-based semantic search.
        """
        query_words = set(self._tokenize(query.lower()))
        if not query_words:
            return []

        scored: List[Tuple[float, Episode]] = []

        for episode in self._episodes.values():
            # Filter by outcome if specified
            if outcome and episode.outcome != outcome:
                continue

            goal_words = set(self._tokenize(episode.goal.lower()))
            if not goal_words:
                continue

            # Jaccard similarity
            intersection = query_words & goal_words
            union = query_words | goal_words
            similarity = len(intersection) / len(union) if union else 0.0

            # Boost by importance
            score = similarity * (0.5 + 0.5 * episode.importance)
            scored.append((score, episode))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        results = [ep for score, ep in scored[:limit] if score > 0.05]
        return results

    def get_by_outcome(
        self,
        outcome: EpisodeOutcome,
        limit: int = 50,
    ) -> List[Episode]:
        """Get all episodes with a given outcome."""
        ids = self._outcome_index.get(outcome, [])
        results = []
        for eid in ids[-limit:]:
            ep = self._episodes.get(eid)
            if ep:
                results.append(ep)
        # Most recent first
        results.sort(key=lambda e: e.start_time, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Replay (the key feature!)
    # ------------------------------------------------------------------

    def replay(
        self,
        episode_id: str,
        extract_lessons: bool = True,
    ) -> Optional[ReplayResult]:
        """Replay an episode — simulate going through it again.

        Replay is not just "read the episode" — it's an active process
        that extracts lessons, strengthens patterns, and can generate
        new insights.

        Args:
            episode_id: ID of the episode to replay
            extract_lessons: Whether to extract/generate lessons

        Returns:
            ReplayResult with replay statistics
        """
        episode = self._episodes.get(episode_id)
        if episode is None:
            return None

        start_time = time.time()
        episode.replay_count += 1

        # Boost importance with each replay (successful replays reinforce)
        if episode.outcome == EpisodeOutcome.SUCCESS:
            episode.importance = min(1.0, episode.importance + 0.05)

        lessons = list(episode.lessons) if extract_lessons else []
        insights: List[str] = []

        # Generate insights from the episode
        if extract_lessons and episode.events:
            # Simple insight: what was the most time-consuming step?
            if any(e.duration_ms > 0 for e in episode.events):
                slowest = max(episode.events, key=lambda e: e.duration_ms)
                insights.append(
                    f"Slowest step: '{slowest.content}' took {slowest.duration_ms:.0f}ms"
                )

            # Insight: how many action vs result events?
            actions = sum(1 for e in episode.events if e.event_type == "action")
            results = sum(1 for e in episode.events if e.event_type == "result")
            if actions > 0:
                insights.append(
                    f"Episode had {actions} actions and {results} results"
                )

        replay_time = (time.time() - start_time) * 1000

        self._touch(episode_id)

        return ReplayResult(
            episode_id=episode_id,
            events_replayed=len(episode.events),
            lessons_extracted=lessons,
            insights=insights,
            replay_time_ms=replay_time,
        )

    def replay_batch(
        self,
        count: int = 5,
        prefer_outcome: Optional[EpisodeOutcome] = None,
        prefer_important: bool = True,
    ) -> List[ReplayResult]:
        """Replay a batch of episodes (like memory consolidation).

        This is analogous to memory replay during sleep — the brain replays
        important/recent experiences to consolidate them.

        Args:
            count: Number of episodes to replay
            prefer_outcome: Prefer episodes with this outcome
            prefer_important: Prioritise important episodes

        Returns:
            List of ReplayResult
        """
        # Select episodes to replay
        candidates = list(self._episodes.values())

        if prefer_outcome:
            candidates = [e for e in candidates if e.outcome == prefer_outcome]

        if prefer_important:
            # Sort by importance * recency
            now = time.time()
            candidates.sort(
                key=lambda e: e.importance * (1.0 / (1.0 + (now - e.start_time) / 86400)),
                reverse=True,
            )
        else:
            # Just recency
            candidates.sort(key=lambda e: e.start_time, reverse=True)

        results = []
        for ep in candidates[:count]:
            result = self.replay(ep.id)
            if result:
                results.append(result)

        return results

    def get_all_lessons(self, limit: int = 50) -> List[Tuple[str, float]]:
        """Extract all lessons from all episodes, weighted by importance.

        Returns list of (lesson, importance) tuples, sorted by importance.
        """
        lesson_scores: Dict[str, float] = {}

        for episode in self._episodes.values():
            for lesson in episode.lessons:
                current = lesson_scores.get(lesson, 0.0)
                lesson_scores[lesson] = max(current, episode.importance)

        sorted_lessons = sorted(
            lesson_scores.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_lessons[:limit]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def episode_count(self) -> int:
        return len(self._episodes)

    def stats(self) -> Dict[str, Any]:
        """Get episodic memory statistics."""
        total_events = sum(len(e.events) for e in self._episodes.values())
        total_replays = sum(e.replay_count for e in self._episodes.values())
        outcome_counts = {
            outcome.value: len(ids)
            for outcome, ids in self._outcome_index.items()
        }
        avg_importance = (
            sum(e.importance for e in self._episodes.values())
            / max(1, len(self._episodes))
        )

        return {
            "episode_count": len(self._episodes),
            "total_events": total_events,
            "total_replays": total_replays,
            "avg_events_per_episode": round(total_events / max(1, len(self._episodes)), 1),
            "outcomes": outcome_counts,
            "avg_importance": round(avg_importance, 3),
            "max_episodes": self.max_episodes,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenizer for keyword matching."""
        import re
        tokens = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower())
        return [t for t in tokens if len(t) >= 2]

    def _index_episode(self, episode: Episode) -> None:
        """Add episode to search indexes."""
        # Tag index
        for tag in episode.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            if episode.id not in self._tag_index[tag]:
                self._tag_index[tag].append(episode.id)

        # Goal keyword index
        for word in self._tokenize(episode.goal.lower()):
            if word not in self._goal_index:
                self._goal_index[word] = []
            if episode.id not in self._goal_index[word]:
                self._goal_index[word].append(episode.id)

    def _touch(self, episode_id: str) -> None:
        """Mark episode as recently accessed (LRU)."""
        if episode_id in self._access_order:
            self._access_order.remove(episode_id)
        self._access_order.append(episode_id)

    def _evict_oldest(self) -> None:
        """Evict the least important / least recently used episode."""
        if not self._episodes:
            return

        # Find the least important episode (among the oldest 20%)
        oldest_count = max(1, len(self._access_order) // 5)
        candidates = self._access_order[:oldest_count]

        # Evict the one with lowest importance
        to_evict = min(
            candidates,
            key=lambda eid: self._episodes[eid].importance if eid in self._episodes else 1.0
        )

        if to_evict in self._episodes:
            del self._episodes[to_evict]
        if to_evict in self._access_order:
            self._access_order.remove(to_evict)
