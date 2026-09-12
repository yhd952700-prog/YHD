"""
Perception / Observation / World Model for LiuHao AI OS (MASTER-SPEC 29-31).

§29 Perception ("ADA eyes"): turns Vision/Image/Video/Audio/Document/Browser/Screen
    input into a uniform understanding signal.
§30 Observation Model: one uniform output record for every perception.
§31 World Model: a verifiable, queryable model of world state.

Honesty contract (CODER-CONTRACT / §158 NO FAKE AI):
    Modalities we cannot truly understand (image/video/audio/...) are served by
    StubPerceiver, which returns state="NOT_IMPLEMENTED" and confidence=0.0.
    It never fabricates entities, attributes or understanding.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .observability import observe
from .audit import audited


@dataclass
class Observation:
    """§30 Observation Model - uniform output of every Perceiver.

    Fields:
        id:           stable observation identifier (UUID)
        source:       modality that produced this observation (text/image/video/...)
        timestamp:    UTC time the observation was created
        entity:       primary entity the observation is about (None if unknown)
        attributes:   structured key/value understanding of the input
        relations:    list of relation dicts {subject, predicate, object, ...}
        state:        processing state, e.g. UNDERSTOOD / EMPTY / NOT_IMPLEMENTED
        confidence:   [0.0, 1.0] model confidence in the understanding
        provenance:   traceability metadata (modality, perceiver, raw refs)
    """

    id: str
    source: str
    timestamp: datetime
    entity: Optional[str]
    attributes: Dict[str, Any] = field(default_factory=dict)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    state: str = "NEW"
    confidence: float = 1.0
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "entity": self.entity,
            "attributes": self.attributes,
            "relations": self.relations,
            "state": self.state,
            "confidence": self.confidence,
            "provenance": self.provenance,
        }


class Perceiver(ABC):
    """§29 Perceiver base class. Subclasses implement perceive()."""

    modality: str = "base"

    @abstractmethod
    def perceive(self, data: Any) -> Observation:
        """Turn raw modality input into a single Observation."""
        ...


class TextPerceiver(Perceiver):
    """Real, deterministic perceiver for text / document input.

    Extracts reproducible attributes (char/word counts, keyword hit counts)
    and never guesses at meaning beyond what is literally present.
    """

    modality = "text"

    def __init__(self, keywords: Optional[List[str]] = None):
        # keywords lower-cased once for deterministic, case-insensitive matching
        self.keywords: List[str] = [k.lower() for k in (keywords or [])]

    @observe("text_perceiver.perceive")
    @audited("p11.perception.perceive", module="src.ai.perception")
    def perceive(self, data: Any) -> Observation:
        text = data if isinstance(data, str) else str(data)
        char_count = len(text)
        words = text.split()
        word_count = len(words)

        keyword_hits: Dict[str, int] = {}
        lowered = text.lower()
        for kw in self.keywords:
            keyword_hits[kw] = len(re.findall(re.escape(kw), lowered))

        attributes: Dict[str, Any] = {
            "char_count": char_count,
            "word_count": word_count,
            "keyword_hits": keyword_hits,
        }

        # Entity: first keyword that actually appears, else a generic "text" tag.
        entity: Optional[str] = None
        for kw in self.keywords:
            if keyword_hits.get(kw, 0) > 0:
                entity = kw
                break
        if entity is None:
            entity = "text"

        # State/confidence are honest: empty input means nothing was understood.
        if word_count == 0:
            state = "EMPTY"
            confidence = 0.0
        else:
            state = "UNDERSTOOD"
            confidence = 1.0

        return Observation(
            id=str(uuid.uuid4()),
            source="text",
            timestamp=datetime.now(timezone.utc),
            entity=entity,
            attributes=attributes,
            relations=[],
            state=state,
            confidence=confidence,
            provenance={
                "modality": "text",
                "perceiver": "TextPerceiver",
                "keywords": self.keywords,
            },
        )


class StubPerceiver(Perceiver):
    """Honest placeholder for not-yet-implemented modalities.

    Image / Video / Audio / Browser / Screen understanding is NOT implemented.
    Per §158 we must not fake comprehension: the returned Observation carries
    state="NOT_IMPLEMENTED" and confidence=0.0 and an empty understanding.
    """

    def __init__(self, modality: str):
        self.modality = modality

    @observe("stub_perceiver.perceive")
    def perceive(self, data: Any) -> Observation:
        return Observation(
            id=str(uuid.uuid4()),
            source=self.modality,
            timestamp=datetime.now(timezone.utc),
            entity=None,
            attributes={},
            relations=[],
            state="NOT_IMPLEMENTED",
            confidence=0.0,
            provenance={
                "modality": self.modality,
                "perceiver": "StubPerceiver",
                "note": "modality understanding not implemented; no fabrication",
            },
        )


class WorldModel:
    """§31 World Model - a verifiable, queryable model of world state.

    Maintains entities, relationships and a chronological event log of every
    Observation that has been applied. apply() is idempotent per-entity for
    attributes (latest overwrite) so the model reflects current state.
    """

    def __init__(self) -> None:
        self._entities: Dict[str, Dict[str, Any]] = {}
        self._relationships: List[Dict[str, Any]] = []
        self._events: List[Observation] = []

    @observe("world_model.apply")
    def apply(self, observation: Observation) -> None:
        """Ingest an Observation: upsert its entity + record its relations."""
        self._events.append(observation)

        if observation.entity is not None:
            ent = self._entities.setdefault(
                observation.entity,
                {"id": observation.entity, "attributes": {}, "last_seen": None},
            )
            ent["attributes"].update(observation.attributes)
            ent["last_seen"] = observation.timestamp.isoformat()

        for rel in observation.relations:
            recorded = dict(rel)
            recorded.setdefault("source_observation", observation.id)
            self._relationships.append(recorded)

    def entities(self) -> List[Dict[str, Any]]:
        return [dict(v) for v in self._entities.values()]

    def relationships(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._relationships]

    def events(self) -> List[Observation]:
        return list(self._events)

    def state(self) -> Dict[str, Any]:
        return {
            "entity_count": len(self._entities),
            "relationship_count": len(self._relationships),
            "event_count": len(self._events),
            "entities": self.entities(),
            "relationships": self.relationships(),
        }
