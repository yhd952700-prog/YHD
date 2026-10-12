"""Enhanced Memory System for LiuHao AI OS.

Building on top of the kernel-level MemoryKernel, this module provides
higher-level memory capabilities that make the system truly capable of
learning and recalling:

  - **SemanticMemoryNetwork** — Knowledge-graph style memory with entities
    and relations. Not just key-value stores, but a graph of concepts.

  - **EpisodicMemory** — Experience replay. Stores sequences of events
    (episodes) and can replay them to learn from past experience.

  - **WorkingMemory** — Short-term context buffer with limited capacity
    and attentional prioritisation. Like human working memory (~7±2 items).

  - **MetaMemory** — Memory about memory. Tracks what the system knows,
    what it doesn't know, and how confident it is in its memories.

These are the "deep memory" capabilities that turn a simple key-value
store into a system that can genuinely learn from experience.
"""
from __future__ import annotations

from .semantic_network import (
    SemanticMemoryNetwork,
    SemanticEntity,
    SemanticRelation,
    EntityType,
    RelationType,
)
from .episodic import (
    EpisodicMemory,
    Episode,
    EpisodeOutcome,
    EpisodeSequence,
    ReplayResult,
)
from .working_memory import (
    WorkingMemory,
    WorkingMemoryItem,
    MemoryPriority,
)
from .meta_memory import (
    MetaMemory,
    MemoryConfidence,
    KnowledgeGap,
)

__all__ = [
    # Semantic
    "SemanticMemoryNetwork",
    "SemanticEntity",
    "SemanticRelation",
    "EntityType",
    "RelationType",
    # Episodic
    "EpisodicMemory",
    "Episode",
    "EpisodeSequence",
    "ReplayResult",
    # Working
    "WorkingMemory",
    "WorkingMemoryItem",
    "MemoryPriority",
    # Meta
    "MetaMemory",
    "MemoryConfidence",
    "KnowledgeGap",
]
