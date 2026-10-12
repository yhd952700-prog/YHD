"""Semantic Memory Network — knowledge-graph style memory.

The semantic network stores *concepts* (entities) and the *relationships*
between them. This is much richer than a key-value store:

  Key-value: "python" → "a programming language"
  Semantic:   Python --is_a--> ProgrammingLanguage
              Python --used_for--> WebDevelopment
              Python --invented_by--> GuidoVanRossum
              Python --similar_to--> JavaScript

Why this matters:
  1. **Inference** — can deduce new facts from existing ones
     (A is B, B can C → A can C)
  2. **Associative recall** — retrieving one concept activates related ones
  3. **Generalisation** — concepts form a hierarchy (is-a hierarchy)
  4. **Learning** — adding new concepts and relations grows knowledge

This is the declarative memory system — "what the system knows".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Iterable
import uuid
import time


class EntityType(str, Enum):
    """Categories of entities in the semantic network."""
    CONCEPT = "concept"           # Abstract ideas: "freedom", "recursion"
    PERSON = "person"             # People: "Ada Lovelace", "Linus Torvalds"
    ORGANIZATION = "organization"  # Companies, teams: "Google", "Python Software Foundation"
    TECHNOLOGY = "technology"     # Tech: "Python", "React", "Docker"
    LANGUAGE = "language"         # Programming/natural languages
    PROJECT = "project"           # Software projects, initiatives
    DOCUMENT = "document"         # Files, articles, specifications
    EVENT = "event"               # Historical events, milestones
    LOCATION = "location"         # Places, regions
    TOOL = "tool"                 # Tools, frameworks, libraries
    CATEGORY = "category"         # Taxonomic categories
    SKILL = "skill"               # Abilities, capabilities
    TASK = "task"                 # Tasks, goals, objectives
    OTHER = "other"               # Catch-all


class RelationType(str, Enum):
    """Types of relationships between entities."""
    # Taxonomic (is-a hierarchy)
    IS_A = "is_a"                     # Python IS_A ProgrammingLanguage
    INSTANCE_OF = "instance_of"       # This project IS_AN_INSTANCE_OF web_app
    SUBCLASS_OF = "subclass_of"       # Reptile SUBCLASS_OF Animal
    PART_OF = "part_of"               # Engine PART_OF Car

    # Functional / causal
    USED_FOR = "used_for"             # Python USED_FOR web development
    CREATED_BY = "created_by"         # Linux CREATED_BY Linus
    INVENTED_BY = "invented_by"       # Same, for inventions
    MAINTAINED_BY = "maintained_by"   # Project MAINTAINED_BY team
    DEPENDS_ON = "depends_on"         # App DEPENDS_ON database

    # Associative (weaker connections)
    SIMILAR_TO = "similar_to"         # Python SIMILAR_TO Ruby
    RELATED_TO = "related_to"         # General association
    CONTRASTS_WITH = "contrasts_with"  # Opposite / complementary

    # Temporal
    PRECEDES = "precedes"             # v1 PRECEDES v2
    FOLLOWED_BY = "followed_by"       # v1 FOLLOWED_BY v2

    # Spatial
    LOCATED_IN = "located_in"         # Office LOCATED_IN Beijing

    # Causal
    CAUSES = "causes"                 # Bug CAUSES crash
    ENABLES = "enables"               # Library ENABLES feature


@dataclass
class SemanticEntity:
    """A concept/entity in the semantic network."""
    id: str
    name: str
    entity_type: EntityType
    description: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    confidence: float = 1.0  # How sure we are this entity exists
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    source: str = "system"  # Where this knowledge came from

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, SemanticEntity):
            return self.id == other.id
        return False


@dataclass
class SemanticRelation:
    """A directed relationship between two entities."""
    id: str
    source_id: str       # Subject
    target_id: str       # Object
    relation_type: RelationType
    weight: float = 1.0   # Strength of the relationship (0-1)
    confidence: float = 1.0  # How sure we are this relation is true
    description: str = ""
    created_at: float = field(default_factory=time.time)
    access_count: int = 0
    source: str = "system"

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, SemanticRelation):
            return self.id == other.id
        return False


class SemanticMemoryNetwork:
    """A semantic network — entities and their relationships.

    This is a graph-based memory that supports:
      - Adding entities and relations
      - Looking up entities by name/type
      - Traversing relations (neighbors, paths)
      - Spreading activation for associative recall
      - Inference via transitive relations

    Usage::

        net = SemanticMemoryNetwork()

        # Add entities
        python = net.add_entity("Python", EntityType.TECHNOLOGY,
                                description="A programming language")
        web = net.add_entity("Web Development", EntityType.CATEGORY)

        # Add relations
        net.add_relation(python.id, web.id, RelationType.USED_FOR,
                         description="Python is used for web development")

        # Query
        results = net.search("python")
        neighbors = net.get_neighbors(python.id)

        # Spreading activation
        activated = net.spreading_activation(["Python"], steps=2)
    """

    def __init__(self, max_entities: int = 10000, max_relations: int = 50000):
        self.max_entities = max_entities
        self.max_relations = max_relations

        self._entities: Dict[str, SemanticEntity] = {}
        self._relations: Dict[str, SemanticRelation] = {}

        # Indexes for fast lookup
        self._name_index: Dict[str, List[str]] = {}  # name_lower -> [entity_ids]
        self._type_index: Dict[EntityType, Set[str]] = {}  # type -> set of entity_ids
        self._alias_index: Dict[str, List[str]] = {}  # alias_lower -> [entity_ids]

        # Adjacency lists (outgoing and incoming)
        self._outgoing: Dict[str, List[str]] = {}  # entity_id -> [relation_ids]
        self._incoming: Dict[str, List[str]] = {}  # entity_id -> [relation_ids]

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    def add_entity(
        self,
        name: str,
        entity_type: EntityType,
        description: str = "",
        attributes: Optional[Dict[str, Any]] = None,
        aliases: Optional[List[str]] = None,
        confidence: float = 1.0,
        source: str = "system",
    ) -> SemanticEntity:
        """Add an entity to the network.

        If an entity with the same name and type already exists,
        update it instead of creating a duplicate.
        """
        # Check for existing entity (same name + type)
        existing = self._find_exact(name, entity_type)
        if existing is not None:
            # Update existing
            existing.description = description or existing.description
            if attributes:
                existing.attributes.update(attributes)
            if aliases:
                for alias in aliases:
                    if alias not in existing.aliases:
                        existing.aliases.append(alias)
                        self._add_to_alias_index(alias, existing.id)
            existing.confidence = max(existing.confidence, confidence)
            existing.updated_at = time.time()
            return existing

        # Create new entity
        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
        entity = SemanticEntity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            description=description,
            attributes=attributes or {},
            aliases=aliases or [],
            confidence=confidence,
            source=source,
        )

        self._entities[entity_id] = entity
        self._add_to_name_index(name, entity_id)
        self._add_to_type_index(entity_type, entity_id)
        for alias in entity.aliases:
            self._add_to_alias_index(alias, entity_id)

        # Initialise adjacency lists
        self._outgoing[entity_id] = []
        self._incoming[entity_id] = []

        return entity

    def get_entity(self, entity_id: str) -> Optional[SemanticEntity]:
        """Get an entity by ID."""
        entity = self._entities.get(entity_id)
        if entity:
            entity.access_count += 1
            entity.updated_at = time.time()
        return entity

    def search(
        self,
        query: str,
        entity_type: Optional[EntityType] = None,
        limit: int = 10,
    ) -> List[SemanticEntity]:
        """Search for entities by name or alias.

        Returns results sorted by relevance (exact match first, then prefix,
        then substring).
        """
        query_lower = query.lower()
        candidates: Dict[str, int] = {}  # entity_id -> score

        # Exact name match
        if query_lower in self._name_index:
            for eid in self._name_index[query_lower]:
                candidates[eid] = candidates.get(eid, 0) + 100

        # Exact alias match
        if query_lower in self._alias_index:
            for eid in self._alias_index[query_lower]:
                candidates[eid] = candidates.get(eid, 0) + 80

        # Prefix match on names
        for name_lower, eids in self._name_index.items():
            if name_lower.startswith(query_lower):
                for eid in eids:
                    candidates[eid] = candidates.get(eid, 0) + 50

        # Substring match
        for name_lower, eids in self._name_index.items():
            if query_lower in name_lower:
                for eid in eids:
                    candidates[eid] = candidates.get(eid, 0) + 20

        # Filter by type if specified
        if entity_type is not None:
            type_entities = self._type_index.get(entity_type, set())
            candidates = {
                eid: score for eid, score in candidates.items()
                if eid in type_entities
            }

        # Sort by score descending
        sorted_ids = sorted(candidates.keys(), key=lambda eid: candidates[eid], reverse=True)

        # Return entities
        results = []
        for eid in sorted_ids[:limit]:
            entity = self._entities.get(eid)
            if entity:
                entity.access_count += 1
                results.append(entity)

        return results

    def get_by_type(self, entity_type: EntityType, limit: int = 100) -> List[SemanticEntity]:
        """Get all entities of a given type."""
        type_ids = self._type_index.get(entity_type, set())
        results = []
        for eid in list(type_ids)[:limit]:
            entity = self._entities.get(eid)
            if entity:
                results.append(entity)
        return results

    # ------------------------------------------------------------------
    # Relation operations
    # ------------------------------------------------------------------

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        weight: float = 1.0,
        confidence: float = 1.0,
        description: str = "",
        source: str = "system",
    ) -> Optional[SemanticRelation]:
        """Add a directed relation between two entities.

        Returns None if either entity doesn't exist.
        """
        if source_id not in self._entities or target_id not in self._entities:
            return None

        # Check for existing relation (same source, target, type)
        existing = self._find_relation(source_id, target_id, relation_type)
        if existing is not None:
            existing.weight = max(existing.weight, weight)
            existing.confidence = max(existing.confidence, confidence)
            if description:
                existing.description = description
            return existing

        rel_id = f"rel_{uuid.uuid4().hex[:12]}"
        relation = SemanticRelation(
            id=rel_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            weight=weight,
            confidence=confidence,
            description=description,
            source=source,
        )

        self._relations[rel_id] = relation
        self._outgoing[source_id].append(rel_id)
        self._incoming[target_id].append(rel_id)

        return relation

    def get_relation(self, relation_id: str) -> Optional[SemanticRelation]:
        """Get a relation by ID."""
        relation = self._relations.get(relation_id)
        if relation:
            relation.access_count += 1
        return relation

    def get_neighbors(
        self,
        entity_id: str,
        relation_type: Optional[RelationType] = None,
        direction: str = "both",  # "out", "in", "both"
    ) -> List[Tuple[SemanticEntity, SemanticRelation]]:
        """Get entities directly connected to this one.

        Returns list of (entity, relation) tuples.
        """
        results: List[Tuple[SemanticEntity, SemanticRelation]] = []

        if direction in ("out", "both"):
            for rel_id in self._outgoing.get(entity_id, []):
                rel = self._relations.get(rel_id)
                if rel is None:
                    continue
                if relation_type and rel.relation_type != relation_type:
                    continue
                target = self._entities.get(rel.target_id)
                if target:
                    results.append((target, rel))

        if direction in ("in", "both"):
            for rel_id in self._incoming.get(entity_id, []):
                rel = self._relations.get(rel_id)
                if rel is None:
                    continue
                if relation_type and rel.relation_type != relation_type:
                    continue
                source = self._entities.get(rel.source_id)
                if source:
                    results.append((source, rel))

        # Sort by weight descending
        results.sort(key=lambda x: x[1].weight, reverse=True)

        # Increment access counts
        for _, rel in results:
            rel.access_count += 1

        return results

    # ------------------------------------------------------------------
    # Graph traversal / inference
    # ------------------------------------------------------------------

    def spreading_activation(
        self,
        start_names: List[str],
        steps: int = 2,
        decay: float = 0.7,
        threshold: float = 0.1,
        limit: int = 50,
    ) -> List[Tuple[SemanticEntity, float]]:
        """Spreading activation for associative recall.

        Starting from a set of entities, activate their neighbors,
        then the neighbors' neighbors, with activation decaying at each step.
        This models how thinking about one thing reminds you of related things.

        Args:
            start_names: Names of entities to start from
            steps: Number of traversal steps
            decay: Activation multiplier at each step (0-1)
            threshold: Minimum activation to include in results
            limit: Maximum number of results

        Returns:
            List of (entity, activation_score) tuples, sorted by activation
        """
        activation: Dict[str, float] = {}  # entity_id -> activation level

        # Initial activation from start entities
        for name in start_names:
            matches = self.search(name, limit=3)
            for entity in matches:
                activation[entity.id] = activation.get(entity.id, 0) + 1.0

        # Spread activation
        current_front = dict(activation)  # entities active at current step

        for _ in range(steps):
            next_front: Dict[str, float] = {}

            for entity_id, act_level in current_front.items():
                neighbors = self.get_neighbors(entity_id, direction="both")
                for neighbor, relation in neighbors:
                    # Activation = current * decay * relation_weight
                    new_activation = act_level * decay * relation.weight
                    if new_activation >= threshold:
                        next_front[neighbor.id] = max(
                            next_front.get(neighbor.id, 0),
                            new_activation,
                        )

            # Add to total activation (don't overwrite; take max)
            for eid, act in next_front.items():
                activation[eid] = max(activation.get(eid, 0), act)

            current_front = next_front
            if not current_front:
                break

        # Remove start entities from results? No, include them
        # Sort by activation descending
        sorted_entities = sorted(
            activation.items(), key=lambda x: x[1], reverse=True
        )

        # Build result list
        results = []
        for eid, act in sorted_entities[:limit]:
            entity = self._entities.get(eid)
            if entity:
                results.append((entity, act))

        return results

    def find_path(
        self,
        start_name: str,
        end_name: str,
        max_depth: int = 4,
    ) -> Optional[List[SemanticRelation]]:
        """Find a path between two entities (BFS).

        Returns the list of relations that form the path, or None if no path.
        """
        start_matches = self.search(start_name, limit=1)
        end_matches = self.search(end_name, limit=1)

        if not start_matches or not end_matches:
            return None

        start_id = start_matches[0].id
        end_id = end_matches[0].id

        if start_id == end_id:
            return []

        # BFS
        from collections import deque
        visited = {start_id}
        queue: deque = deque([(start_id, [])])  # (entity_id, path)

        while queue:
            current, path = queue.popleft()

            if len(path) >= max_depth:
                continue

            for rel_id in self._outgoing.get(current, []):
                rel = self._relations.get(rel_id)
                if rel is None:
                    continue

                if rel.target_id == end_id:
                    return path + [rel]

                if rel.target_id not in visited:
                    visited.add(rel.target_id)
                    queue.append((rel.target_id, path + [rel]))

        return None

    def infer_transitive(
        self,
        entity_name: str,
        relation_type: RelationType,
        max_depth: int = 3,
    ) -> List[SemanticEntity]:
        """Infer transitive relations.

        If A IS_A B and B IS_A C, then A IS_A C.
        Returns all entities reachable via transitive relations.

        Args:
            entity_name: Starting entity name
            relation_type: The relation type to follow transitively
            max_depth: Maximum traversal depth
        """
        matches = self.search(entity_name, limit=1)
        if not matches:
            return []

        start_id = matches[0].id
        visited: Set[str] = set()
        results: List[SemanticEntity] = []

        def dfs(current_id: str, depth: int):
            if depth >= max_depth or current_id in visited:
                return
            visited.add(current_id)

            for rel_id in self._outgoing.get(current_id, []):
                rel = self._relations.get(rel_id)
                if rel is None or rel.relation_type != relation_type:
                    continue

                target = self._entities.get(rel.target_id)
                if target and target.id not in visited:
                    results.append(target)
                    dfs(target.id, depth + 1)

        dfs(start_id, 0)
        return results

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def relation_count(self) -> int:
        return len(self._relations)

    def stats(self) -> Dict[str, Any]:
        """Get network statistics."""
        type_counts: Dict[str, int] = {}
        for etype, eids in self._type_index.items():
            type_counts[etype.value] = len(eids)

        relation_counts: Dict[str, int] = {}
        for rel in self._relations.values():
            rtype = rel.relation_type.value
            relation_counts[rtype] = relation_counts.get(rtype, 0) + 1

        # Compute average degree
        total_out = sum(len(v) for v in self._outgoing.values())
        avg_degree = total_out / max(1, len(self._entities))

        return {
            "entity_count": len(self._entities),
            "relation_count": len(self._relations),
            "entity_types": type_counts,
            "relation_types": relation_counts,
            "avg_out_degree": round(avg_degree, 2),
            "max_entities": self.max_entities,
            "max_relations": self.max_relations,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_exact(self, name: str, entity_type: EntityType) -> Optional[SemanticEntity]:
        """Find an entity by exact name and type."""
        name_lower = name.lower()
        if name_lower not in self._name_index:
            return None

        type_entities = self._type_index.get(entity_type, set())
        for eid in self._name_index[name_lower]:
            if eid in type_entities:
                return self._entities.get(eid)
        return None

    def _find_relation(
        self, source_id: str, target_id: str, relation_type: RelationType
    ) -> Optional[SemanticRelation]:
        """Find an existing relation with the same source/target/type."""
        for rel_id in self._outgoing.get(source_id, []):
            rel = self._relations.get(rel_id)
            if rel and rel.target_id == target_id and rel.relation_type == relation_type:
                return rel
        return None

    def _add_to_name_index(self, name: str, entity_id: str) -> None:
        name_lower = name.lower()
        if name_lower not in self._name_index:
            self._name_index[name_lower] = []
        if entity_id not in self._name_index[name_lower]:
            self._name_index[name_lower].append(entity_id)

    def _add_to_alias_index(self, alias: str, entity_id: str) -> None:
        alias_lower = alias.lower()
        if alias_lower not in self._alias_index:
            self._alias_index[alias_lower] = []
        if entity_id not in self._alias_index[alias_lower]:
            self._alias_index[alias_lower].append(entity_id)

    def _add_to_type_index(self, entity_type: EntityType, entity_id: str) -> None:
        if entity_type not in self._type_index:
            self._type_index[entity_type] = set()
        self._type_index[entity_type].add(entity_id)
