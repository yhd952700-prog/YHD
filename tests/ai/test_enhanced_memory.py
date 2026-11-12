"""Tests for the enhanced memory system (semantic + episodic + working + meta)."""
from __future__ import annotations

import pytest

from src.ai.memory import (
    SemanticMemoryNetwork,
    SemanticEntity,
    SemanticRelation,
    EntityType,
    RelationType,
    EpisodicMemory,
    Episode,
    EpisodeOutcome,
    ReplayResult,
    WorkingMemory,
    WorkingMemoryItem,
    MemoryPriority,
    MetaMemory,
    MemoryConfidence,
    KnowledgeGap,
)


# =====================================================================
# Semantic Memory Network Tests
# =====================================================================

class TestSemanticMemoryNetwork:
    """Test the semantic network."""

    def test_add_and_get_entity(self):
        net = SemanticMemoryNetwork()
        entity = net.add_entity("Python", EntityType.TECHNOLOGY,
                                description="A programming language")

        assert entity.name == "Python"
        assert entity.entity_type == EntityType.TECHNOLOGY
        assert entity.description == "A programming language"

        retrieved = net.get_entity(entity.id)
        assert retrieved is not None
        assert retrieved.name == "Python"
        assert retrieved.access_count == 1  # Access count incremented

    def test_add_entity_duplicate_updates(self):
        """Adding the same entity twice should update, not duplicate."""
        net = SemanticMemoryNetwork()
        e1 = net.add_entity("Test", EntityType.CONCEPT, description="First")
        e2 = net.add_entity("Test", EntityType.CONCEPT, description="Second")

        assert e1.id == e2.id
        assert e2.description == "Second"
        assert net.entity_count == 1

    def test_search_exact_match(self):
        net = SemanticMemoryNetwork()
        net.add_entity("Python", EntityType.TECHNOLOGY)
        net.add_entity("Java", EntityType.TECHNOLOGY)
        net.add_entity("JavaScript", EntityType.TECHNOLOGY)

        results = net.search("Python")
        assert len(results) >= 1
        assert results[0].name == "Python"

    def test_search_prefix_match(self):
        net = SemanticMemoryNetwork()
        net.add_entity("Python", EntityType.TECHNOLOGY)
        net.add_entity("PyTorch", EntityType.TECHNOLOGY)

        results = net.search("Py")
        assert len(results) >= 2

    def test_search_by_type(self):
        net = SemanticMemoryNetwork()
        net.add_entity("Python", EntityType.TECHNOLOGY)
        net.add_entity("Linus", EntityType.PERSON)
        net.add_entity("React", EntityType.TECHNOLOGY)

        tech = net.get_by_type(EntityType.TECHNOLOGY)
        assert len(tech) == 2

        people = net.get_by_type(EntityType.PERSON)
        assert len(people) == 1

    def test_add_relation(self):
        net = SemanticMemoryNetwork()
        python = net.add_entity("Python", EntityType.TECHNOLOGY)
        web = net.add_entity("Web Development", EntityType.CATEGORY)

        rel = net.add_relation(python.id, web.id, RelationType.USED_FOR,
                               description="Python is used for web dev")

        assert rel is not None
        assert rel.relation_type == RelationType.USED_FOR
        assert rel.source_id == python.id
        assert rel.target_id == web.id

    def test_get_neighbors(self):
        net = SemanticMemoryNetwork()
        python = net.add_entity("Python", EntityType.TECHNOLOGY)
        web = net.add_entity("Web Dev", EntityType.CATEGORY)
        data = net.add_entity("Data Science", EntityType.CATEGORY)

        net.add_relation(python.id, web.id, RelationType.USED_FOR)
        net.add_relation(python.id, data.id, RelationType.USED_FOR)

        neighbors = net.get_neighbors(python.id)
        assert len(neighbors) == 2

    def test_spreading_activation(self):
        net = SemanticMemoryNetwork()
        a = net.add_entity("A", EntityType.CONCEPT)
        b = net.add_entity("B", EntityType.CONCEPT)
        c = net.add_entity("C", EntityType.CONCEPT)
        d = net.add_entity("D", EntityType.CONCEPT)

        net.add_relation(a.id, b.id, RelationType.RELATED_TO, weight=1.0)
        net.add_relation(b.id, c.id, RelationType.RELATED_TO, weight=0.8)
        net.add_relation(c.id, d.id, RelationType.RELATED_TO, weight=0.5)

        # Start from A, spread 2 steps
        activated = net.spreading_activation(["A"], steps=2, decay=0.7)

        # A, B, C should all be activated; D might not reach threshold
        activated_names = [e.name for e, _ in activated]
        assert "A" in activated_names
        assert "B" in activated_names
        assert "C" in activated_names

    def test_infer_transitive(self):
        net = SemanticMemoryNetwork()
        net.add_entity("Dog", EntityType.CATEGORY)
        net.add_entity("Mammal", EntityType.CATEGORY)
        net.add_entity("Animal", EntityType.CATEGORY)

        dog = net.search("Dog")[0]
        mammal = net.search("Mammal")[0]
        animal = net.search("Animal")[0]

        net.add_relation(dog.id, mammal.id, RelationType.IS_A)
        net.add_relation(mammal.id, animal.id, RelationType.IS_A)

        # Dog IS_A Mammal IS_A Animal → Dog IS_A Animal (transitive)
        inferred = net.infer_transitive("Dog", RelationType.IS_A, max_depth=3)
        inferred_names = [e.name for e in inferred]
        assert "Mammal" in inferred_names
        assert "Animal" in inferred_names

    def test_stats(self):
        net = SemanticMemoryNetwork()
        net.add_entity("Python", EntityType.TECHNOLOGY)
        net.add_entity("Java", EntityType.TECHNOLOGY)
        net.add_entity("Linus", EntityType.PERSON)

        stats = net.stats()
        assert stats["entity_count"] == 3
        assert stats["relation_count"] == 0
        assert "technology" in stats["entity_types"]
        assert stats["entity_types"]["technology"] == 2


# =====================================================================
# Episodic Memory Tests
# =====================================================================

class TestEpisodicMemory:
    """Test episodic memory."""

    def test_start_and_finish_episode(self):
        mem = EpisodicMemory()

        ep = mem.start_episode("Test episode", tags=["test"])
        assert ep.outcome == EpisodeOutcome.ONGOING
        assert ep.event_count == 0

        result = mem.finish_episode(ep.id, EpisodeOutcome.SUCCESS,
                                    lessons=["Test lesson"])
        assert result is not None
        assert result.outcome == EpisodeOutcome.SUCCESS
        assert len(result.lessons) == 1

    def test_add_events(self):
        mem = EpisodicMemory()
        ep = mem.start_episode("Test")

        e1 = mem.add_event(ep.id, "action", "Did something")
        e2 = mem.add_event(ep.id, "result", "Got result")

        assert e1 is not None
        assert e2 is not None

        retrieved = mem.get_episode(ep.id)
        assert retrieved is not None
        assert retrieved.event_count == 2

    def test_find_similar(self):
        mem = EpisodicMemory()

        ep1 = mem.start_episode("Fix login bug")
        mem.finish_episode(ep1.id, EpisodeOutcome.SUCCESS)

        ep2 = mem.start_episode("Build new feature")
        mem.finish_episode(ep2.id, EpisodeOutcome.SUCCESS)

        # Search for "bug" — should find the login bug episode
        similar = mem.find_similar("bug")
        assert len(similar) > 0
        assert "bug" in similar[0].goal.lower() or "login" in similar[0].goal.lower()

    def test_get_by_outcome(self):
        mem = EpisodicMemory()

        for i in range(3):
            ep = mem.start_episode(f"Success episode {i}")
            mem.finish_episode(ep.id, EpisodeOutcome.SUCCESS)

        for i in range(2):
            ep = mem.start_episode(f"Failure episode {i}")
            mem.finish_episode(ep.id, EpisodeOutcome.FAILURE)

        successes = mem.get_by_outcome(EpisodeOutcome.SUCCESS)
        failures = mem.get_by_outcome(EpisodeOutcome.FAILURE)

        assert len(successes) == 3
        assert len(failures) == 2

    def test_replay(self):
        mem = EpisodicMemory()
        ep = mem.start_episode("Test replay")
        mem.add_event(ep.id, "action", "Step 1")
        mem.add_event(ep.id, "result", "Step 2")
        mem.finish_episode(ep.id, EpisodeOutcome.SUCCESS,
                           lessons=["Lesson A", "Lesson B"])

        result = mem.replay(ep.id)

        assert isinstance(result, ReplayResult)
        assert result.events_replayed == 2
        assert len(result.lessons_extracted) == 2
        assert "Lesson A" in result.lessons_extracted

        # Replay count incremented
        retrieved = mem.get_episode(ep.id)
        assert retrieved.replay_count == 1

    def test_replay_batch(self):
        mem = EpisodicMemory()

        for i in range(5):
            ep = mem.start_episode(f"Episode {i}")
            mem.add_event(ep.id, "action", f"Step {i}")
            mem.finish_episode(ep.id, EpisodeOutcome.SUCCESS,
                               importance=0.5 + i * 0.1)

        results = mem.replay_batch(count=3)
        assert len(results) == 3

    def test_get_all_lessons(self):
        mem = EpisodicMemory()

        ep1 = mem.start_episode("Ep 1")
        mem.finish_episode(ep1.id, EpisodeOutcome.SUCCESS,
                           lessons=["Lesson 1"], importance=0.8)

        ep2 = mem.start_episode("Ep 2")
        mem.finish_episode(ep2.id, EpisodeOutcome.FAILURE,
                           lessons=["Lesson 2"], importance=0.9)

        lessons = mem.get_all_lessons(limit=10)
        assert len(lessons) == 2
        # Higher importance first
        assert lessons[0][1] >= lessons[1][1]

    def test_stats(self):
        mem = EpisodicMemory()

        ep = mem.start_episode("Test")
        mem.add_event(ep.id, "action", "x")
        mem.finish_episode(ep.id, EpisodeOutcome.SUCCESS)

        stats = mem.stats()
        assert stats["episode_count"] == 1
        assert stats["total_events"] == 1


# =====================================================================
# Working Memory Tests
# =====================================================================

class TestWorkingMemory:
    """Test working memory."""

    def test_add_and_get(self):
        wm = WorkingMemory(capacity=5)

        item = wm.add("Hello world", item_type="fact",
                       priority=MemoryPriority.HIGH)

        assert item.content == "Hello world"
        assert item.priority == MemoryPriority.HIGH

        retrieved = wm.get(item.id)
        assert retrieved is not None
        assert retrieved.access_count == 1  # Access count incremented

    def test_capacity_limit(self):
        """Working memory should respect capacity limit."""
        wm = WorkingMemory(capacity=3)

        items = []
        for i in range(5):
            item = wm.add(f"Item {i}", priority=MemoryPriority.MEDIUM)
            items.append(item)

        # Should only have 3 items (capacity)
        assert wm.item_count == 3
        assert wm.is_full

    def test_priority_eviction(self):
        """Lower priority items should be evicted first."""
        wm = WorkingMemory(capacity=3)

        # Add 3 items with different priorities
        low = wm.add("Low priority", priority=MemoryPriority.LOW)
        med = wm.add("Medium priority", priority=MemoryPriority.MEDIUM)
        high = wm.add("High priority", priority=MemoryPriority.HIGH)

        # Add a 4th high-priority item — should evict the low one
        new_high = wm.add("New high", priority=MemoryPriority.HIGH)

        assert wm.item_count == 3
        assert wm.get(low.id) is None  # Low priority evicted
        assert wm.get(high.id) is not None  # High priority kept
        assert wm.get(new_high.id) is not None  # New one added

    def test_active_items_sorted(self):
        wm = WorkingMemory(capacity=5)

        wm.add("Low", priority=MemoryPriority.LOW)
        wm.add("Critical", priority=MemoryPriority.CRITICAL)
        wm.add("Medium", priority=MemoryPriority.MEDIUM)

        active = wm.active_items()
        assert len(active) == 3
        # Highest priority first
        assert active[0].priority == MemoryPriority.CRITICAL

    def test_get_by_type(self):
        wm = WorkingMemory(capacity=10)

        wm.add("Goal A", item_type="goal", priority=MemoryPriority.CRITICAL)
        wm.add("Fact B", item_type="fact")
        wm.add("Goal C", item_type="goal")

        goals = wm.get_by_type("goal")
        assert len(goals) == 2

    def test_decay(self):
        """Items should decay over time."""
        wm = WorkingMemory(capacity=5, decay_interval_ms=1)  # Fast decay for test

        item = wm.add("Test", priority=MemoryPriority.LOW)
        initial_activation = item.activation

        # Manually trigger decay after a tiny delay
        import time
        time.sleep(0.01)
        wm.decay()

        assert item.activation < initial_activation

    def test_focus_and_defocus(self):
        wm = WorkingMemory(capacity=5)
        item = wm.add("Test", priority=MemoryPriority.MEDIUM)

        initial = item.activation
        wm.focus(item.id)
        assert item.activation > initial

        wm.defocus(item.id)
        assert item.activation < initial + 0.15  # Less than after focus

    def test_chunking(self):
        wm = WorkingMemory(capacity=10)

        i1 = wm.add("A")
        i2 = wm.add("B")
        i3 = wm.add("C")

        chunk_id = wm.chunk([i1.id, i2.id, i3.id], "ABC group")
        assert chunk_id is not None

        stats = wm.stats()
        assert stats["chunks"] == 1

    def test_maintenance_rehearsal(self):
        wm = WorkingMemory(capacity=5)

        wm.add("Critical item", priority=MemoryPriority.CRITICAL)
        wm.add("High item", priority=MemoryPriority.HIGH)
        wm.add("Low item", priority=MemoryPriority.LOW)

        refreshed = wm.maintenance_rehearsal()
        assert refreshed == 2  # Critical + High

    def test_stats(self):
        wm = WorkingMemory(capacity=5)
        wm.add("Test", item_type="fact", priority=MemoryPriority.MEDIUM)

        stats = wm.stats()
        assert stats["item_count"] == 1
        assert stats["capacity"] == 5
        assert stats["utilization"] == 0.2
        assert "medium" in stats["priority_distribution"]


# =====================================================================
# Meta-Memory Tests
# =====================================================================

class TestMetaMemory:
    """Test meta-memory."""

    def test_add_belief(self):
        meta = MetaMemory()
        belief = meta.add_belief("Python is a language",
                                 confidence=MemoryConfidence.HIGH,
                                 sources=["docs"])

        assert belief.claim == "Python is a language"
        assert belief.confidence == MemoryConfidence.HIGH
        assert len(belief.sources) == 1

    def test_add_belief_duplicate_strengthens(self):
        """Adding the same belief again should strengthen it."""
        meta = MetaMemory()
        b1 = meta.add_belief("X is true", confidence=MemoryConfidence.LOW)
        initial_score = b1.confidence_score

        b2 = meta.add_belief("X is true", sources=["another source"])

        assert b1.id == b2.id
        assert b2.evidence_count == 2
        assert b2.confidence_score > initial_score  # Strengthened

    def test_confidence_in_topic(self):
        meta = MetaMemory()
        meta.add_belief("Python is great", confidence=MemoryConfidence.CERTAIN)
        meta.add_belief("Python is easy", confidence=MemoryConfidence.HIGH)

        conf = meta.confidence_in_topic("python")
        assert conf in (MemoryConfidence.CERTAIN, MemoryConfidence.HIGH)

    def test_add_gap(self):
        meta = MetaMemory()
        gap = meta.add_gap("quantum computing",
                           description="Need to learn",
                           importance=0.8,
                           urgency=0.3)

        assert gap.topic == "quantum computing"
        assert gap.importance == 0.8
        assert gap.encounter_count == 1

    def test_add_gap_duplicate_increments(self):
        meta = MetaMemory()
        g1 = meta.add_gap("quantum")
        g2 = meta.add_gap("quantum")

        assert g1.id == g2.id
        assert g2.encounter_count == 2

    def test_top_gaps(self):
        meta = MetaMemory()
        meta.add_gap("low", importance=0.2, urgency=0.2)
        meta.add_gap("high", importance=0.9, urgency=0.9)
        meta.add_gap("medium", importance=0.5, urgency=0.5)

        top = meta.top_gaps(limit=2)
        assert len(top) == 2
        assert top[0].topic == "high"  # Highest priority first

    def test_knows_enough_about(self):
        meta = MetaMemory()
        meta.add_belief("Python is a language", confidence=MemoryConfidence.CERTAIN)

        assert meta.knows_enough_about("python", threshold=0.8) is True
        assert meta.knows_enough_about("unknown_topic") is False

    def test_should_look_up(self):
        meta = MetaMemory()
        # No beliefs about this → should look up
        assert meta.should_look_up("unknown topic") is True

        meta.add_belief("known thing is true", confidence=MemoryConfidence.CERTAIN)
        assert meta.should_look_up("known thing") is False

    def test_close_gap(self):
        meta = MetaMemory()
        gap = meta.add_gap("test gap")
        assert gap.status == "open"

        result = meta.close_gap(gap.id)
        assert result is True
        assert gap.status == "filled"

    def test_record_recall(self):
        meta = MetaMemory()
        meta.record_recall_attempt(True)
        meta.record_recall_attempt(True)
        meta.record_recall_attempt(False)

        assert meta._total_recalls == 3
        assert meta.recall_accuracy == pytest.approx(2/3)

    def test_stats(self):
        meta = MetaMemory()
        meta.add_belief("Fact A", confidence=MemoryConfidence.HIGH)
        meta.add_gap("Gap A")

        stats = meta.stats()
        assert stats["belief_count"] == 1
        assert stats["gap_count"] == 1
        assert "high" in stats["confidence_distribution"]
