"""
Tests for Perception / Observation / World Model (MASTER-SPEC 29-31).

Asserts REAL behaviour: deterministic TextPerceiver attributes, honest
NOT_IMPLEMENTED stub perceivers, and WorldModel upsert/query semantics.
"""

import pytest
from datetime import datetime, timezone

from src.ai.perception import (
    Observation,
    Perceiver,
    StubPerceiver,
    TextPerceiver,
    WorldModel,
)


class TestTextPerceiver:
    def test_returns_real_observation(self):
        obs = TextPerceiver().perceive("the quick brown fox")
        assert isinstance(obs, Observation)
        assert obs.source == "text"
        assert obs.state == "UNDERSTOOD"
        assert obs.confidence == 1.0
        assert obs.entity == "text"

    def test_word_and_char_counts_are_deterministic(self):
        obs = TextPerceiver().perceive("the quick brown fox")
        assert obs.attributes["word_count"] == 4
        # len("the quick brown fox") = 19
        assert obs.attributes["char_count"] == 19

    def test_keyword_hits_counted_case_insensitively(self):
        obs = TextPerceiver(keywords=["fox", "dog"]).perceive("The Fox saw a FOX")
        hits = obs.attributes["keyword_hits"]
        assert hits["fox"] == 2
        assert hits["dog"] == 0
        # first keyword that appears becomes the detected entity
        assert obs.entity == "fox"

    def test_empty_input_is_honestly_empty(self):
        obs = TextPerceiver().perceive("   ")
        assert obs.attributes["word_count"] == 0
        assert obs.state == "EMPTY"
        assert obs.confidence == 0.0


class TestStubPerceiver:
    def test_image_is_honestly_not_implemented(self):
        obs = StubPerceiver("image").perceive(b"\x89PNG garbage")
        assert obs.state == "NOT_IMPLEMENTED"
        assert obs.confidence == 0.0
        assert obs.entity is None
        assert obs.attributes == {}

    def test_video_and_audio_are_honestly_not_implemented(self):
        for modality in ("video", "audio", "browser", "screen"):
            obs = StubPerceiver(modality).perceive("anything")
            assert obs.state == "NOT_IMPLEMENTED"
            assert obs.confidence == 0.0


class TestWorldModel:
    def _obs(self, oid, entity, attrs, rels=None):
        return Observation(
            id=oid,
            source="text",
            timestamp=datetime.now(timezone.utc),
            entity=entity,
            attributes=attrs,
            relations=rels or [],
            state="UNDERSTOOD",
            confidence=1.0,
        )

    def test_apply_upserts_entities(self):
        wm = WorldModel()
        wm.apply(self._obs("o1", "user:alice", {"role": "admin"}))
        wm.apply(self._obs("o2", "user:bob", {"role": "dev"}))

        entities = wm.entities()
        assert len(entities) == 2
        ids = {e["id"] for e in entities}
        assert ids == {"user:alice", "user:bob"}

    def test_apply_records_relations_and_state(self):
        wm = WorldModel()
        wm.apply(
            self._obs(
                "o1",
                "user:alice",
                {"role": "admin"},
                rels=[{"subject": "user:alice", "predicate": "member_of", "object": "team:x"}],
            )
        )
        rels = wm.relationships()
        assert len(rels) == 1
        assert rels[0]["predicate"] == "member_of"
        assert rels[0]["source_observation"] == "o1"

        st = wm.state()
        assert st["entity_count"] == 1
        assert st["relationship_count"] == 1
        assert st["event_count"] == 1

    def test_apply_idempotent_attribute_overwrite(self):
        wm = WorldModel()
        wm.apply(self._obs("o1", "user:alice", {"role": "admin"}))
        wm.apply(self._obs("o2", "user:alice", {"role": "owner"}))
        # single entity, latest attributes win
        assert len(wm.entities()) == 1
        assert wm.entities()[0]["attributes"]["role"] == "owner"
        # both observations recorded as events
        assert wm.state()["event_count"] == 2


class TestPerceiverContract:
    def test_base_perceiver_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            Perceiver()  # type: ignore[abstract]
